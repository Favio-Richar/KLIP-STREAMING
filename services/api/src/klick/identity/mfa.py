"""MFA TOTP con códigos de recuperación (RFC 6238; ADR-017)."""

import hashlib
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import pyotp
import structlog
from redis.asyncio import Redis

from klick.core.config import Settings
from klick.core.crypto import Crypto, DecryptionError
from klick.core.db import Database
from klick.core.errors import ApiError
from klick.core.ids import new_id
from klick.core.ratelimit import RateLimiter, rate_limited
from klick.identity import repo
from klick.identity.models import MfaFactor, MfaRecoveryCode, User
from klick.identity.security import hash_token, new_opaque_token, verify_password

log = structlog.get_logger("klick.mfa")

ISSUER = "Klick"
STEP_S = 30
WINDOW = 1  # tolera ±1 intervalo de 30 s por desfase de reloj
RECOVERY_CODE_COUNT = 10
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # sin caracteres ambiguos
CHALLENGE_TTL_S = 300
CHALLENGE_MAX_ATTEMPTS = 5


@dataclass(frozen=True)
class TotpEnrollment:
    secret: str
    otpauth_uri: str


@dataclass(frozen=True)
class MfaChallenge:
    token: str


def _now() -> datetime:
    return datetime.now(UTC)


def _aad(user_id: uuid.UUID) -> bytes:
    return f"mfa:totp:{user_id}".encode()


def _normalize_recovery(code: str) -> str:
    return code.strip().lower().replace("-", "").replace(" ", "")


def _hash_recovery(code: str) -> str:
    return hash_token("recovery:" + _normalize_recovery(code))


def generate_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    return [
        "-".join("".join(secrets.choice(_ALPHABET) for _ in range(5)) for _ in range(2))
        for _ in range(count)
    ]


def _invalid_code() -> ApiError:
    return ApiError(401, "invalid_mfa_code", "Invalid code", "El código no es correcto.")


class MfaService:
    def __init__(
        self,
        db: Database,
        redis: Redis,
        limiter: RateLimiter,
        crypto: Crypto,
        settings: Settings,
    ) -> None:
        self._db = db
        self._redis = redis
        self._limiter = limiter
        self._crypto = crypto
        self._s = settings

    # ----------------------------------------------------------------------- estado

    async def is_enabled(self, user_id: uuid.UUID) -> bool:
        async with self._db.global_session() as s:
            return await repo.get_totp_factor(s, user_id, confirmed=True) is not None

    # ------------------------------------------------------------------- alta/baja

    async def enroll(self, user: User) -> TotpEnrollment:
        async with self._db.global_session() as s:
            if await repo.get_totp_factor(s, user.id, confirmed=True) is not None:
                raise ApiError(
                    409,
                    "mfa_already_enabled",
                    "MFA already enabled",
                    "Ya activaste la verificación en dos pasos.",
                )
            await repo.delete_unconfirmed_factors(s, user.id)
            secret = pyotp.random_base32()
            await repo.add_factor(
                s,
                MfaFactor(
                    id=new_id(),
                    user_id=user.id,
                    type="totp",
                    secret_encrypted=self._crypto.encrypt(secret.encode(), _aad(user.id)),
                ),
            )
        uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=ISSUER)
        return TotpEnrollment(secret=secret, otpauth_uri=uri)

    async def confirm(self, user: User, code: str) -> list[str]:
        """Activa el factor con el primer código válido y devuelve los códigos de recuperación."""
        await self._enforce_attempts(user.id)
        async with self._db.global_session() as s:
            factor = await repo.get_totp_factor(s, user.id, confirmed=False, for_update=True)
            if factor is None:
                raise ApiError(
                    404,
                    "mfa_not_enrolled",
                    "No pending enrollment",
                    "Primero inicia la configuración.",
                )
            step = self._verify_totp(factor, user.id, code)
            if step is None:
                await self._limiter.register_failure("mfa", str(user.id))
                raise _invalid_code()
            factor.confirmed_at = _now()
            factor.last_used_step = step
            codes = generate_recovery_codes()
            await repo.replace_recovery_codes(
                s,
                user.id,
                [
                    MfaRecoveryCode(id=new_id(), user_id=user.id, code_hash=_hash_recovery(c))
                    for c in codes
                ],
            )
        await self._limiter.clear_failures("mfa", str(user.id))
        log.info("mfa_enabled", user_id=str(user.id))
        return codes

    async def disable(self, user: User, *, password: str | None, code: str) -> None:
        """Exige la contraseña (si la cuenta tiene) y un código TOTP o de recuperación."""
        await self._enforce_attempts(user.id)
        if user.password_hash is not None:
            if not password or not await verify_password(user.password_hash, password):
                await self._limiter.register_failure("mfa", str(user.id))
                raise ApiError(
                    401,
                    "invalid_credentials",
                    "Invalid credentials",
                    "La contraseña no es correcta.",
                )
        if not await self._check_second_factor(user.id, code):
            await self._limiter.register_failure("mfa", str(user.id))
            raise _invalid_code()
        async with self._db.global_session() as s:
            await repo.delete_mfa(s, user.id)
        await self._limiter.clear_failures("mfa", str(user.id))
        log.info("mfa_disabled", user_id=str(user.id))

    # ---------------------------------------------------------- desafío del login

    async def create_challenge(self, user_id: uuid.UUID) -> MfaChallenge:
        token = new_opaque_token()
        await self._redis.set(
            self._challenge_key(token),
            json.dumps({"user_id": str(user_id), "attempts": 0}),
            ex=CHALLENGE_TTL_S,
        )
        return MfaChallenge(token=token)

    async def verify_challenge(
        self, *, token: str, code: str | None, recovery_code: str | None
    ) -> uuid.UUID:
        """Devuelve el usuario si el segundo factor es válido. El desafío es de un solo uso."""
        key = self._challenge_key(token)
        raw = await self._redis.get(key)
        if raw is None:
            raise ApiError(
                401, "mfa_challenge_expired", "Challenge expired", "Vuelve a iniciar sesión."
            )
        data = json.loads(raw)
        user_id = uuid.UUID(data["user_id"])
        await self._enforce_attempts(user_id)

        ok = await self._check_second_factor(
            user_id, code or recovery_code or "", recovery=bool(recovery_code)
        )
        if not ok:
            await self._limiter.register_failure("mfa", str(user_id))
            attempts = int(data["attempts"]) + 1
            if attempts >= CHALLENGE_MAX_ATTEMPTS:
                await self._redis.delete(key)
                raise ApiError(
                    401,
                    "mfa_challenge_expired",
                    "Challenge expired",
                    "Demasiados intentos. Vuelve a iniciar sesión.",
                )
            data["attempts"] = attempts
            await self._redis.set(key, json.dumps(data), keepttl=True)
            raise _invalid_code()
        # GETDEL-like: si dos peticiones compiten, solo una consume el desafío.
        if await self._redis.delete(key) != 1:
            raise ApiError(
                401, "mfa_challenge_expired", "Challenge expired", "Vuelve a iniciar sesión."
            )
        await self._limiter.clear_failures("mfa", str(user_id))
        return user_id

    # ------------------------------------------------------------------ internos

    def _challenge_key(self, token: str) -> str:
        return f"mfa_ch:{hashlib.sha256(token.encode()).hexdigest()}"

    async def _enforce_attempts(self, user_id: uuid.UUID) -> None:
        locked = await self._limiter.lock_remaining("mfa", str(user_id))
        if locked:
            raise rate_limited(locked)

    def _verify_totp(self, factor: MfaFactor, user_id: uuid.UUID, code: str) -> int | None:
        """Devuelve el intervalo que coincide (y es posterior al último usado) o None."""
        code = code.strip().replace(" ", "")
        if not (code.isdigit() and len(code) == 6):
            return None
        try:
            secret = self._crypto.decrypt(factor.secret_encrypted, _aad(user_id)).decode()
        except DecryptionError:
            log.error("mfa_secret_undecryptable", user_id=str(user_id))
            return None
        totp = pyotp.TOTP(secret, interval=STEP_S)
        current = int(time.time()) // STEP_S
        last = factor.last_used_step if factor.last_used_step is not None else -1
        for step in range(current - WINDOW, current + WINDOW + 1):
            candidate = totp.at(step * STEP_S)
            if secrets.compare_digest(candidate, code) and step > last:
                return step
        return None

    async def _check_second_factor(
        self, user_id: uuid.UUID, code: str, *, recovery: bool | None = None
    ) -> bool:
        """Valida TOTP o código de recuperación y lo consume atómicamente."""
        looks_like_totp = code.strip().replace(" ", "").isdigit()
        use_recovery = (not looks_like_totp) if recovery is None else recovery
        async with self._db.global_session() as s:
            if use_recovery:
                return await repo.consume_recovery_code(s, user_id, _hash_recovery(code), _now())
            factor = await repo.get_totp_factor(s, user_id, confirmed=True, for_update=True)
            if factor is None:
                return False
            step = self._verify_totp(factor, user_id, code)
            if step is None:
                return False
            factor.last_used_step = step
            return True
