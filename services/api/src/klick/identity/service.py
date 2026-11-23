"""Casos de uso de identidad (ADR-017). Único punto de acceso a las tablas de identidad."""

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from klick.core.config import Settings
from klick.core.db import Database
from klick.core.email import Email, EmailSender
from klick.core.errors import ApiError
from klick.core.ids import new_id
from klick.core.ratelimit import RateLimiter, rate_limited
from klick.identity import repo
from klick.identity.mfa import MfaChallenge, MfaService
from klick.identity.models import AuthToken, Session, User
from klick.identity.security import (
    decode_access_token,
    hash_password,
    hash_token,
    issue_access_token,
    needs_rehash,
    new_opaque_token,
    verify_password,
)

log = structlog.get_logger("klick.identity")

PURPOSE_VERIFY = "verify_email"
PURPOSE_RESET = "reset_password"

REGISTER_ACCEPTED = "Si el correo puede registrarse, te enviamos un mensaje para confirmarlo."
FORGOT_ACCEPTED = "Si el correo existe, te enviamos instrucciones para recuperar el acceso."


@dataclass(frozen=True)
class IssuedSession:
    user: User
    session_id: uuid.UUID
    access_token: str
    refresh_token: str
    csrf_token: str
    refresh_expires_at: datetime


@dataclass(frozen=True)
class AuthContext:
    user: User
    session_id: uuid.UUID
    mfa_enabled: bool = False


def _now() -> datetime:
    return datetime.now(UTC)


def csrf_for_session(secret: str, session_id: uuid.UUID) -> str:
    """Token anti-CSRF ligado a la sesión (HMAC); no requiere almacenamiento."""
    return hmac.new(secret.encode(), f"csrf:{session_id}".encode(), hashlib.sha256).hexdigest()


def _invalid_credentials() -> ApiError:
    return ApiError(
        401, "invalid_credentials", "Invalid credentials", "Correo o contraseña incorrectos."
    )


def unauthenticated(code: str = "unauthenticated") -> ApiError:
    return ApiError(401, code, "Authentication required", "Inicia sesión para continuar.")


class IdentityService:
    def __init__(
        self,
        db: Database,
        limiter: RateLimiter,
        email: EmailSender,
        settings: Settings,
        mfa: MfaService,
    ) -> None:
        self._db = db
        self._limiter = limiter
        self._email = email
        self._s = settings
        self._mfa = mfa

    # ------------------------------------------------------------------ registro y correo

    async def register(
        self, *, email: str, password: str, display_name: str, ip: str | None
    ) -> None:
        """Respuesta idéntica exista o no el correo (sin enumeración de cuentas)."""
        await self._limiter.enforce(
            "register_ip", ip or "unknown", limit=self._s.rate_limit_register_ip, window_s=3600
        )
        await self._limiter.enforce("register_email", email, limit=3, window_s=3600)
        if email.split("@")[0] in password.lower() or password.lower() == email:
            raise ApiError(
                422, "weak_password", "Weak password", "La contraseña no puede contener tu correo."
            )
        password_hash = await hash_password(password)  # siempre, para igualar tiempos

        created: User | None = None
        token: str | None = None
        try:
            async with self._db.global_session() as s:
                if await repo.get_user_by_email(s, email) is None:
                    created = await repo.add_user(
                        s,
                        User(
                            id=new_id(),
                            email=email,
                            password_hash=password_hash,
                            display_name=display_name,
                        ),
                    )
                    token = await self._create_auth_token(
                        s, created.id, PURPOSE_VERIFY, timedelta(hours=self._s.verify_email_ttl_h)
                    )
        except IntegrityError:  # carrera: otro registro del mismo correo ganó
            created = None

        if created is not None and token is not None:
            await self._send_verification(created.email, created.display_name, token)
        else:
            await self._email.send(
                Email(
                    to=email,
                    subject="Ya tienes una cuenta en Klick",
                    body=(
                        "Alguien intentó registrar este correo en Klick, pero ya tiene una cuenta.\n"
                        f"Si fuiste tú, inicia sesión o recupera tu contraseña en "
                        f"{self._s.public_web_url}/forgot-password.\n"
                        "Si no fuiste tú, puedes ignorar este mensaje."
                    ),
                )
            )
        log.info("register_requested", created=created is not None)

    async def verify_email(self, token: str) -> None:
        now = _now()
        async with self._db.global_session() as s:
            row = await repo.get_auth_token(s, hash_token(token), PURPOSE_VERIFY, for_update=True)
            if row is None or row.used_at is not None or row.expires_at <= now:
                raise ApiError(
                    400, "invalid_token", "Invalid token", "El enlace no es válido o ya expiró."
                )
            row.used_at = now
            user = await repo.get_user(s, row.user_id)
            if user is not None and user.email_verified_at is None:
                user.email_verified_at = now
        log.info("email_verified")

    async def _create_auth_token(
        self, s: AsyncSession, user_id: uuid.UUID, purpose: str, ttl: timedelta
    ) -> str:
        await repo.invalidate_auth_tokens(s, user_id, purpose, _now())
        token = new_opaque_token()
        await repo.add_auth_token(
            s,
            AuthToken(
                id=new_id(),
                user_id=user_id,
                purpose=purpose,
                token_hash=hash_token(token),
                expires_at=_now() + ttl,
            ),
        )
        return token

    async def _send_verification(self, to: str, name: str, token: str) -> None:
        await self._email.send(
            Email(
                to=to,
                subject="Confirma tu correo en Klick",
                body=(
                    f"Hola {name},\n\nConfirma tu correo para terminar de crear tu cuenta:\n"
                    f"{self._s.public_web_url}/verify-email?token={token}\n\n"
                    f"El enlace vence en {self._s.verify_email_ttl_h} horas."
                ),
            )
        )

    # ------------------------------------------------------------------ inicio de sesión

    async def login(
        self, *, email: str, password: str, ip: str | None, user_agent: str | None
    ) -> IssuedSession | MfaChallenge:
        await self._limiter.enforce(
            "login_ip", ip or "unknown", limit=self._s.rate_limit_login_ip, window_s=300
        )
        locked = await self._limiter.lock_remaining("login", email)
        if locked:
            raise rate_limited(locked)

        issued: IssuedSession | None = None
        async with self._db.global_session() as s:
            user = await repo.get_user_by_email(s, email)
            stored_hash = user.password_hash if user and user.is_active else None
            ok = await verify_password(stored_hash, password)
            if not ok or user is None:
                lock = await self._limiter.register_failure("login", email)
                log.info("login_failed", locked_for_s=lock)
                if lock:
                    raise rate_limited(lock)
                raise _invalid_credentials()
            if needs_rehash(user.password_hash or ""):
                user.password_hash = await hash_password(password)
            has_mfa = await repo.get_totp_factor(s, user.id, confirmed=True) is not None
            if not has_mfa:
                user.last_login_at = _now()
                issued = await self._open_session(
                    s, user, family_id=new_id(), rotated_from=None, ip=ip, user_agent=user_agent
                )
        await self._limiter.clear_failures("login", email)
        if issued is None:
            log.info("login_mfa_required", user_id=str(user.id))
            return await self._mfa.create_challenge(user.id)
        log.info("login_succeeded", user_id=str(user.id))
        return issued

    async def complete_mfa_login(
        self,
        *,
        token: str,
        code: str | None,
        recovery_code: str | None,
        ip: str | None,
        user_agent: str | None,
    ) -> IssuedSession:
        user_id = await self._mfa.verify_challenge(
            token=token, code=code, recovery_code=recovery_code
        )
        return await self.start_session(user_id, ip=ip, user_agent=user_agent)

    async def start_session(
        self, user_id: uuid.UUID, *, ip: str | None, user_agent: str | None
    ) -> IssuedSession:
        """Abre una sesión nueva (familia nueva) tras una autenticación ya comprobada."""
        async with self._db.global_session() as s:
            user = await repo.get_user(s, user_id)
            if user is None or not user.is_active:
                raise _invalid_credentials()
            user.last_login_at = _now()
            issued = await self._open_session(
                s, user, family_id=new_id(), rotated_from=None, ip=ip, user_agent=user_agent
            )
        log.info("session_started", user_id=str(user_id))
        return issued

    async def login_with_user(
        self, user: User, *, ip: str | None, user_agent: str | None
    ) -> IssuedSession | MfaChallenge:
        """Para proveedores externos (Google): respeta el MFA del usuario si lo tiene."""
        if await self._mfa.is_enabled(user.id):
            return await self._mfa.create_challenge(user.id)
        return await self.start_session(user.id, ip=ip, user_agent=user_agent)

    async def _open_session(
        self,
        s: AsyncSession,
        user: User,
        *,
        family_id: uuid.UUID,
        rotated_from: uuid.UUID | None,
        ip: str | None,
        user_agent: str | None,
    ) -> IssuedSession:
        refresh = new_opaque_token()
        expires = _now() + timedelta(days=self._s.refresh_token_ttl_days)
        row = await repo.add_session(
            s,
            Session(
                id=new_id(),
                user_id=user.id,
                refresh_token_hash=hash_token(refresh),
                family_id=family_id,
                user_agent=(user_agent or "")[:300] or None,
                ip=ip,
                expires_at=expires,
                rotated_from=rotated_from,
            ),
        )
        secret = self._s.app_secret_key.get_secret_value()
        return IssuedSession(
            user=user,
            session_id=row.id,
            access_token=issue_access_token(
                secret=secret,
                issuer=self._s.jwt_issuer,
                user_id=user.id,
                session_id=row.id,
                ttl_s=self._s.access_token_ttl_s,
            ),
            refresh_token=refresh,
            csrf_token=csrf_for_session(secret, row.id),
            refresh_expires_at=expires,
        )

    # ------------------------------------------------------------------ refresco rotatorio

    async def refresh(
        self, *, refresh_token: str, csrf_header: str | None, ip: str | None, user_agent: str | None
    ) -> IssuedSession:
        """Rota el refresh token. Reutilizar uno ya rotado revoca toda la familia."""
        await self._limiter.enforce("refresh_ip", ip or "unknown", limit=60, window_s=300)
        now = _now()
        secret = self._s.app_secret_key.get_secret_value()
        reuse_detected = False
        issued: IssuedSession | None = None
        async with self._db.global_session() as s:
            row = await repo.get_session_by_refresh_hash(
                s, hash_token(refresh_token), for_update=True
            )
            if row is None:
                raise unauthenticated("invalid_refresh_token")
            self._check_csrf(secret, row.id, csrf_header)
            if row.revoked_at is not None:
                await repo.revoke_family(s, row.family_id, now)
                reuse_detected = True
            elif row.expires_at <= now:
                raise unauthenticated("session_expired")
            else:
                user = await repo.get_user(s, row.user_id)
                if user is None or not user.is_active:
                    await repo.revoke_family(s, row.family_id, now)
                    reuse_detected = True
                else:
                    row.revoked_at = now
                    issued = await self._open_session(
                        s,
                        user,
                        family_id=row.family_id,
                        rotated_from=row.id,
                        ip=ip,
                        user_agent=user_agent,
                    )
        # Se lanza fuera del bloque para que la revocación de la familia se confirme.
        if reuse_detected or issued is None:
            log.warning("refresh_reuse_detected")
            raise unauthenticated("refresh_reuse_detected")
        return issued

    def _check_csrf(self, secret: str, session_id: uuid.UUID, header: str | None) -> None:
        expected = csrf_for_session(secret, session_id)
        if not header or not hmac.compare_digest(header, expected):
            raise ApiError(
                403, "csrf_failed", "CSRF check failed", "Falta o no coincide el token CSRF."
            )

    async def logout(self, *, refresh_token: str | None, csrf_header: str | None) -> None:
        if not refresh_token:
            return
        secret = self._s.app_secret_key.get_secret_value()
        async with self._db.global_session() as s:
            row = await repo.get_session_by_refresh_hash(s, hash_token(refresh_token))
            if row is None:
                return
            self._check_csrf(secret, row.id, csrf_header)
            await repo.revoke_family(s, row.family_id, _now())

    # ------------------------------------------------------------------ autenticación de peticiones

    async def authenticate(
        self, *, access_token: str, csrf_header: str | None, unsafe_method: bool
    ) -> AuthContext:
        secret = self._s.app_secret_key.get_secret_value()
        claims = decode_access_token(access_token, secret=secret, issuer=self._s.jwt_issuer)
        if claims is None:
            raise unauthenticated()
        try:
            user_id, session_id = uuid.UUID(claims["sub"]), uuid.UUID(claims["sid"])
        except ValueError:
            raise unauthenticated() from None
        if unsafe_method:
            self._check_csrf(secret, session_id, csrf_header)
        async with self._db.global_session() as s:
            row = await repo.get_session(s, session_id)
            if row is None or row.user_id != user_id or row.revoked_at is not None:
                raise unauthenticated()
            if row.expires_at <= _now():
                raise unauthenticated("session_expired")
            user = await repo.get_user(s, user_id)
            if user is None or not user.is_active:
                raise unauthenticated()
            mfa_enabled = await repo.get_totp_factor(s, user_id, confirmed=True) is not None
            return AuthContext(user=user, session_id=session_id, mfa_enabled=mfa_enabled)

    # ------------------------------------------------------------------ recuperación

    async def forgot_password(self, *, email: str, ip: str | None) -> None:
        await self._limiter.enforce("forgot_ip", ip or "unknown", limit=10, window_s=3600)
        await self._limiter.enforce("forgot_email", email, limit=3, window_s=3600)
        token: str | None = None
        name = ""
        async with self._db.global_session() as s:
            user = await repo.get_user_by_email(s, email)
            if user is not None and user.is_active:
                name = user.display_name
                token = await self._create_auth_token(
                    s, user.id, PURPOSE_RESET, timedelta(hours=self._s.reset_password_ttl_h)
                )
        if token is not None:
            await self._email.send(
                Email(
                    to=email,
                    subject="Recupera tu acceso a Klick",
                    body=(
                        f"Hola {name},\n\nPara elegir una nueva contraseña entra aquí:\n"
                        f"{self._s.public_web_url}/reset-password?token={token}\n\n"
                        f"El enlace vence en {self._s.reset_password_ttl_h} hora. "
                        "Si no lo pediste, ignora este mensaje."
                    ),
                )
            )
        log.info("password_reset_requested")

    async def reset_password(self, *, token: str, new_password: str) -> None:
        now = _now()
        new_hash = await hash_password(new_password)
        async with self._db.global_session() as s:
            row = await repo.get_auth_token(s, hash_token(token), PURPOSE_RESET, for_update=True)
            if row is None or row.used_at is not None or row.expires_at <= now:
                raise ApiError(
                    400, "invalid_token", "Invalid token", "El enlace no es válido o ya expiró."
                )
            user = await repo.get_user(s, row.user_id)
            if user is None or not user.is_active:
                raise ApiError(
                    400, "invalid_token", "Invalid token", "El enlace no es válido o ya expiró."
                )
            if user.email.split("@")[0] in new_password.lower():
                raise ApiError(
                    422,
                    "weak_password",
                    "Weak password",
                    "La contraseña no puede contener tu correo.",
                )
            row.used_at = now
            user.password_hash = new_hash
            if user.email_verified_at is None:
                user.email_verified_at = now  # recibió el enlace en su correo
            await repo.revoke_all_for_user(s, user.id, now)
        await self._limiter.clear_failures("login", user.email)
        log.info("password_reset", user_id=str(user.id))

    # ------------------------------------------------------------------ sesiones del usuario

    async def list_sessions(self, ctx: AuthContext) -> list[tuple[Session, bool]]:
        async with self._db.global_session() as s:
            rows = await repo.list_active_sessions(s, ctx.user.id, _now())
            return [(r, r.id == ctx.session_id) for r in rows]

    async def revoke_session(self, ctx: AuthContext, session_id: uuid.UUID) -> None:
        async with self._db.global_session() as s:
            row = await repo.get_session(s, session_id)
            if row is None or row.user_id != ctx.user.id:
                raise ApiError(404, "not_found", "Resource not found")
            await repo.revoke_family(s, row.family_id, _now())
