import pytest
from klick.core.config import Settings
from klick.core.crypto import Crypto, DecryptionError
from pydantic import SecretStr, ValidationError


def test_roundtrip_and_ciphertext_hides_plaintext() -> None:
    crypto = Crypto("master-key-for-tests")
    blob = crypto.encrypt(b"JBSWY3DPEHPK3PXP", b"mfa:user-1")
    assert b"JBSWY3DPEHPK3PXP" not in blob
    assert crypto.decrypt(blob, b"mfa:user-1") == b"JBSWY3DPEHPK3PXP"


def test_encryption_is_randomized() -> None:
    crypto = Crypto("master-key-for-tests")
    assert crypto.encrypt(b"same", b"ctx") != crypto.encrypt(b"same", b"ctx")


def test_wrong_context_tampering_and_wrong_key_are_rejected() -> None:
    crypto = Crypto("master-key-for-tests")
    blob = crypto.encrypt(b"secret", b"mfa:user-1")
    with pytest.raises(DecryptionError):
        crypto.decrypt(blob, b"mfa:user-2")  # ligado a su contexto
    tampered = bytearray(blob)
    tampered[-1] ^= 0x01
    with pytest.raises(DecryptionError):
        crypto.decrypt(bytes(tampered), b"mfa:user-1")
    with pytest.raises(DecryptionError):
        Crypto("another-master-key").decrypt(blob, b"mfa:user-1")
    with pytest.raises(DecryptionError):
        crypto.decrypt(b"\x09" + blob[1:], b"mfa:user-1")


def test_production_rejects_default_secrets() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="production")
    ok = Settings(
        environment="production",
        app_secret_key=SecretStr("a-real-production-secret-0123456789abcdef"),
        encryption_master_key=SecretStr("another-real-production-secret-0123456789"),
    )
    assert ok.secure_cookies is True
