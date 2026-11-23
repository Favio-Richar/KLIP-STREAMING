"""Cifrado de sobre AES-256-GCM (ADR-014): clave de datos por registro, envuelta con la maestra."""

import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_VERSION = b"\x01"
_NONCE = 12
_WRAPPED = 32 + 16  # clave de datos de 32 bytes + etiqueta GCM


class DecryptionError(Exception):
    pass


class Crypto:
    def __init__(self, master_key: str) -> None:
        # HKDF acepta cualquier cadena; en producción debe ser aleatoria y de al menos 32 bytes.
        self._master = HKDF(
            algorithm=hashes.SHA256(), length=32, salt=None, info=b"klick-master-v1"
        ).derive(master_key.encode())

    def encrypt(self, plaintext: bytes, aad: bytes) -> bytes:
        """``aad`` liga el texto cifrado a su contexto (p. ej. ``b"mfa:<user_id>"``)."""
        data_key = os.urandom(32)
        wrap_nonce, data_nonce = os.urandom(_NONCE), os.urandom(_NONCE)
        wrapped = AESGCM(self._master).encrypt(wrap_nonce, data_key, b"klick-dek")
        body = AESGCM(data_key).encrypt(data_nonce, plaintext, aad)
        return _VERSION + wrap_nonce + wrapped + data_nonce + body

    def decrypt(self, blob: bytes, aad: bytes) -> bytes:
        try:
            if blob[:1] != _VERSION:
                raise DecryptionError("unsupported version")
            offset = 1
            wrap_nonce = blob[offset : offset + _NONCE]
            offset += _NONCE
            wrapped = blob[offset : offset + _WRAPPED]
            offset += _WRAPPED
            data_nonce = blob[offset : offset + _NONCE]
            offset += _NONCE
            data_key = AESGCM(self._master).decrypt(wrap_nonce, wrapped, b"klick-dek")
            return AESGCM(data_key).decrypt(data_nonce, blob[offset:], aad)
        except InvalidTag as exc:
            raise DecryptionError("authentication failed") from exc
