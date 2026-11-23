"""Primitivas de seguridad: argon2id, tokens opacos, JWT de acceso y CSRF."""

import asyncio
import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()  # argon2id con los parámetros por defecto de RFC 9106 (perfil bajo)
# Hash de referencia para igualar el tiempo de respuesta cuando el usuario no existe.
_DUMMY_HASH = _hasher.hash("klick-timing-equalizer")

ACCESS_ALGORITHM = "HS256"


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(_hasher.hash, password)


async def verify_password(password_hash: str | None, password: str) -> bool:
    """Verifica en un hilo. Con ``password_hash=None`` gasta el mismo tiempo y devuelve False."""

    def _check() -> bool:
        try:
            return _hasher.verify(password_hash or _DUMMY_HASH, password) and bool(password_hash)
        except (VerificationError, InvalidHashError):
            return False

    return await asyncio.to_thread(_check)


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def new_opaque_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Los tokens se guardan solo como SHA-256 (tienen 256 bits de entropía)."""
    return hashlib.sha256(token.encode()).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def issue_access_token(
    *, secret: str, issuer: str, user_id: uuid.UUID, session_id: uuid.UUID, ttl_s: int
) -> str:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "iss": issuer,
        "sub": str(user_id),
        "sid": str(session_id),
        "iat": now,
        "exp": now + timedelta(seconds=ttl_s),
        "typ": "access",
    }
    return jwt.encode(claims, secret, algorithm=ACCESS_ALGORITHM)


def decode_access_token(token: str, *, secret: str, issuer: str) -> dict[str, Any] | None:
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            secret,
            algorithms=[ACCESS_ALGORITHM],
            issuer=issuer,
            options={"require": ["exp", "sub", "sid", "iss"]},
        )
    except jwt.PyJWTError:
        return None
    if claims.get("typ") != "access":
        return None
    return claims
