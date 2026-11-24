"""Interfaz `StorageBackend` y su implementación S3 (MinIO en local; S3/R2 en nube; ADR-005).

Las URL firmadas se generan con el endpoint PÚBLICO (el que alcanza el navegador); las
operaciones del servidor (HEAD, borrado, multipart) usan el endpoint interno.
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol

import boto3
import structlog
from botocore.client import Config
from botocore.exceptions import ClientError

from klick.core.config import Settings
from klick.storage.keys import validate_key

log = structlog.get_logger("klick.storage")

MIN_PART_SIZE = 5 * 1024 * 1024  # mínimo de S3 para todas las partes menos la última
MAX_PARTS = 10_000
MAX_URL_TTL_S = 24 * 3600


@dataclass(frozen=True)
class ObjectInfo:
    size: int
    etag: str
    content_type: str | None


@dataclass(frozen=True)
class CompletedPart:
    part_number: int
    etag: str


class StorageBackend(Protocol):
    bucket: str

    async def ensure_bucket(self) -> None: ...
    async def ping(self) -> None: ...
    async def head(self, key: str) -> ObjectInfo | None: ...
    async def put_bytes(self, key: str, data: bytes, content_type: str | None = None) -> None: ...
    async def get_bytes(self, key: str, max_bytes: int = 50 * 1024 * 1024) -> bytes: ...
    async def delete(self, key: str) -> None: ...
    async def delete_many(self, keys: list[str]) -> None: ...
    def presign_get(
        self,
        key: str,
        *,
        expires_s: int,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> str: ...
    def presign_put(self, key: str, *, expires_s: int, content_type: str | None = None) -> str: ...
    async def create_multipart(self, key: str, content_type: str | None = None) -> str: ...
    def presign_part(
        self, key: str, upload_id: str, part_number: int, *, expires_s: int
    ) -> str: ...
    async def list_parts(self, key: str, upload_id: str) -> list[CompletedPart]: ...
    async def complete_multipart(
        self, key: str, upload_id: str, parts: list[CompletedPart]
    ) -> None: ...
    async def abort_multipart(self, key: str, upload_id: str) -> None: ...


def _ttl(expires_s: int) -> int:
    if not 1 <= expires_s <= MAX_URL_TTL_S:
        raise ValueError(f"expires_s debe estar entre 1 y {MAX_URL_TTL_S}")
    return expires_s


def _is_missing(exc: ClientError) -> bool:
    code = str(exc.response.get("Error", {}).get("Code", ""))
    return code in ("404", "NoSuchKey", "NotFound", "NoSuchUpload")


class S3Backend:
    def __init__(self, settings: Settings) -> None:
        self.bucket = settings.s3_bucket
        common: dict[str, Any] = {
            "aws_access_key_id": settings.s3_access_key,
            "aws_secret_access_key": settings.s3_secret_key.get_secret_value(),
            "region_name": settings.s3_region,
            "config": Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        }
        self._client: Any = boto3.client("s3", endpoint_url=settings.s3_endpoint, **common)
        self._public: Any = boto3.client("s3", endpoint_url=settings.s3_public_endpoint, **common)

    # ------------------------------------------------------------------------ servidor

    async def ensure_bucket(self) -> None:
        def run() -> None:
            try:
                self._client.head_bucket(Bucket=self.bucket)
            except ClientError as exc:
                if not _is_missing(exc):
                    raise
                self._client.create_bucket(Bucket=self.bucket)

        await asyncio.to_thread(run)

    async def ping(self) -> None:
        await asyncio.to_thread(self._client.head_bucket, Bucket=self.bucket)

    async def head(self, key: str) -> ObjectInfo | None:
        validate_key(key)

        def run() -> ObjectInfo | None:
            try:
                r = self._client.head_object(Bucket=self.bucket, Key=key)
            except ClientError as exc:
                if _is_missing(exc):
                    return None
                raise
            return ObjectInfo(
                int(r["ContentLength"]), str(r["ETag"]).strip('"'), r.get("ContentType")
            )

        return await asyncio.to_thread(run)

    async def put_bytes(self, key: str, data: bytes, content_type: str | None = None) -> None:
        validate_key(key)
        extra = {"ContentType": content_type} if content_type else {}
        await asyncio.to_thread(
            self._client.put_object, Bucket=self.bucket, Key=key, Body=data, **extra
        )

    async def get_bytes(self, key: str, max_bytes: int = 50 * 1024 * 1024) -> bytes:
        validate_key(key)

        def run() -> bytes:
            r = self._client.get_object(Bucket=self.bucket, Key=key)
            if int(r["ContentLength"]) > max_bytes:
                raise ValueError("el objeto supera el máximo permitido para lectura en memoria")
            body: bytes = r["Body"].read()
            return body

        return await asyncio.to_thread(run)

    async def delete(self, key: str) -> None:
        """Idempotente: borrar algo que no existe no es un error."""
        validate_key(key)
        await asyncio.to_thread(self._client.delete_object, Bucket=self.bucket, Key=key)

    async def delete_many(self, keys: list[str]) -> None:
        for key in keys:
            validate_key(key)
        for i in range(0, len(keys), 1000):
            chunk = keys[i : i + 1000]
            await asyncio.to_thread(
                self._client.delete_objects,
                Bucket=self.bucket,
                Delete={"Objects": [{"Key": k} for k in chunk], "Quiet": True},
            )

    # ---------------------------------------------------------------------- URL firmadas

    def presign_get(
        self,
        key: str,
        *,
        expires_s: int,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> str:
        validate_key(key)
        params: dict[str, Any] = {"Bucket": self.bucket, "Key": key}
        if filename:
            safe = filename.replace('"', "").replace("\r", "").replace("\n", "")[:200]
            params["ResponseContentDisposition"] = f'attachment; filename="{safe}"'
        if content_type:
            params["ResponseContentType"] = content_type
        return str(
            self._public.generate_presigned_url(
                "get_object", Params=params, ExpiresIn=_ttl(expires_s)
            )
        )

    def presign_put(self, key: str, *, expires_s: int, content_type: str | None = None) -> str:
        validate_key(key)
        params: dict[str, Any] = {"Bucket": self.bucket, "Key": key}
        if content_type:
            params["ContentType"] = content_type  # el cliente debe enviar exactamente este tipo
        return str(
            self._public.generate_presigned_url(
                "put_object", Params=params, ExpiresIn=_ttl(expires_s)
            )
        )

    # ------------------------------------------------------------------------ multipart

    async def create_multipart(self, key: str, content_type: str | None = None) -> str:
        validate_key(key)
        extra = {"ContentType": content_type} if content_type else {}
        r = await asyncio.to_thread(
            self._client.create_multipart_upload, Bucket=self.bucket, Key=key, **extra
        )
        return str(r["UploadId"])

    def presign_part(self, key: str, upload_id: str, part_number: int, *, expires_s: int) -> str:
        validate_key(key)
        if not 1 <= part_number <= MAX_PARTS:
            raise ValueError(f"part_number debe estar entre 1 y {MAX_PARTS}")
        return str(
            self._public.generate_presigned_url(
                "upload_part",
                Params={
                    "Bucket": self.bucket,
                    "Key": key,
                    "UploadId": upload_id,
                    "PartNumber": part_number,
                },
                ExpiresIn=_ttl(expires_s),
            )
        )

    async def list_parts(self, key: str, upload_id: str) -> list[CompletedPart]:
        validate_key(key)

        def run() -> list[CompletedPart]:
            parts: list[CompletedPart] = []
            marker = 0
            while True:
                r = self._client.list_parts(
                    Bucket=self.bucket, Key=key, UploadId=upload_id, PartNumberMarker=marker
                )
                parts += [
                    CompletedPart(int(p["PartNumber"]), str(p["ETag"]).strip('"'))
                    for p in r.get("Parts", [])
                ]
                if not r.get("IsTruncated"):
                    return parts
                marker = int(r["NextPartNumberMarker"])

        return await asyncio.to_thread(run)

    async def complete_multipart(
        self, key: str, upload_id: str, parts: list[CompletedPart]
    ) -> None:
        validate_key(key)
        ordered = sorted(parts, key=lambda p: p.part_number)
        await asyncio.to_thread(
            self._client.complete_multipart_upload,
            Bucket=self.bucket,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={
                "Parts": [{"PartNumber": p.part_number, "ETag": p.etag} for p in ordered]
            },
        )

    async def abort_multipart(self, key: str, upload_id: str) -> None:
        validate_key(key)

        def run() -> None:
            try:
                self._client.abort_multipart_upload(Bucket=self.bucket, Key=key, UploadId=upload_id)
            except ClientError as exc:
                if not _is_missing(exc):
                    raise

        await asyncio.to_thread(run)
