"""Inicio de sesión con Google (OIDC, Authorization Code + PKCE) y proveedor fake de desarrollo."""

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx
import jwt
import structlog
from redis.asyncio import Redis

from klick.core.config import Settings
from klick.core.db import Database
from klick.core.errors import ApiError
from klick.core.ids import new_id
from klick.identity import repo
from klick.identity.models import User, UserIdentity

log = structlog.get_logger("klick.oidc")

PROVIDER = "google"
STATE_TTL_S = 600
GOOGLE_DISCOVERY = "https://accounts.google.com/.well-known/openid-configuration"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")


@dataclass(frozen=True)
class OidcIdentity:
    subject: str
    email: str
    email_verified: bool
    name: str | None


class OidcProvider(Protocol):
    name: str

    async def authorization_url(
        self, *, state: str, nonce: str, code_challenge: str, redirect_uri: str, hint: str | None
    ) -> str: ...

    async def exchange(
        self, *, code: str, code_verifier: str, redirect_uri: str, nonce: str
    ) -> OidcIdentity: ...


def _unauthorized(detail: str) -> ApiError:
    return ApiError(401, "oidc_failed", "Sign-in failed", detail)


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


class FakeOidcProvider:
    """Solo desarrollo y CI. El "código" es ``fake:<correo>``; no hay red ni credenciales."""

    name = "fake"

    def __init__(self, default_email: str = "dev.google@klick.local") -> None:
        self._default_email = default_email

    async def authorization_url(
        self, *, state: str, nonce: str, code_challenge: str, redirect_uri: str, hint: str | None
    ) -> str:
        email = (hint or self._default_email).strip().lower()
        return f"{redirect_uri}?{urlencode({'code': f'fake:{email}', 'state': state})}"

    async def exchange(
        self, *, code: str, code_verifier: str, redirect_uri: str, nonce: str
    ) -> OidcIdentity:
        if not code.startswith("fake:"):
            raise _unauthorized("Código no válido.")
        email = code.removeprefix("fake:")
        subject = "fake-" + hashlib.sha256(email.encode()).hexdigest()[:24]
        return OidcIdentity(
            subject=subject, email=email, email_verified=True, name=email.split("@")[0]
        )


class GoogleOidcProvider:
    name = "google"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        http: httpx.AsyncClient | None = None,
        discovery_url: str = GOOGLE_DISCOVERY,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http or httpx.AsyncClient(timeout=10)
        self._discovery_url = discovery_url
        self._config: dict[str, Any] | None = None

    async def _discovery(self) -> dict[str, Any]:
        if self._config is None:
            r = await self._http.get(self._discovery_url)
            r.raise_for_status()
            self._config = r.json()
        return self._config

    async def authorization_url(
        self, *, state: str, nonce: str, code_challenge: str, redirect_uri: str, hint: str | None
    ) -> str:
        cfg = await self._discovery()
        params = {
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "access_type": "online",
            "prompt": "select_account",
        }
        return f"{cfg['authorization_endpoint']}?{urlencode(params)}"

    async def exchange(
        self, *, code: str, code_verifier: str, redirect_uri: str, nonce: str
    ) -> OidcIdentity:
        cfg = await self._discovery()
        r = await self._http.post(
            cfg["token_endpoint"],
            data={
                "code": code,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
                "code_verifier": code_verifier,
            },
        )
        if r.status_code != 200:
            log.warning("google_token_exchange_failed", status=r.status_code)
            raise _unauthorized("Google rechazó el inicio de sesión.")
        id_token = r.json().get("id_token")
        if not id_token:
            raise _unauthorized("Google no devolvió identidad.")
        return await self._verify_id_token(id_token, nonce)

    async def _verify_id_token(self, id_token: str, nonce: str) -> OidcIdentity:
        cfg = await self._discovery()
        jwks = (await self._http.get(cfg["jwks_uri"])).json()
        try:
            header = jwt.get_unverified_header(id_token)
            key_data = next(k for k in jwks["keys"] if k.get("kid") == header.get("kid"))
            key = jwt.PyJWK.from_dict(key_data).key
            claims = jwt.decode(
                id_token,
                key,
                algorithms=["RS256"],
                audience=self._client_id,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except (jwt.PyJWTError, StopIteration, KeyError):
            raise _unauthorized("La identidad de Google no es válida.") from None
        if claims["iss"] not in GOOGLE_ISSUERS:
            raise _unauthorized("Emisor no válido.")
        if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise _unauthorized("La respuesta no corresponde a esta solicitud.")
        email = claims.get("email")
        if not email:
            raise _unauthorized("Tu cuenta de Google no comparte un correo.")
        return OidcIdentity(
            subject=str(claims["sub"]),
            email=str(email).lower(),
            email_verified=claims.get("email_verified") is True,
            name=claims.get("name"),
        )


def build_oidc_provider(settings: Settings) -> OidcProvider | None:
    """Real si hay credenciales; fake si FEATURE_FAKE_ADAPTERS y no es producción; si no, None."""
    if settings.google_oauth_client_id and settings.google_oauth_client_secret.get_secret_value():
        return GoogleOidcProvider(
            settings.google_oauth_client_id, settings.google_oauth_client_secret.get_secret_value()
        )
    if settings.feature_fake_adapters and not settings.is_production:
        return FakeOidcProvider()
    return None


@dataclass(frozen=True)
class OidcStart:
    url: str
    state: str


@dataclass(frozen=True)
class OidcResult:
    user: User
    next_path: str


def safe_next(value: str | None) -> str:
    """Solo rutas relativas internas: evita redirecciones abiertas."""
    if value and value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value[:200]
    return "/"


class OidcService:
    def __init__(
        self, db: Database, redis: Redis, provider: OidcProvider | None, settings: Settings
    ) -> None:
        self._db = db
        self._redis = redis
        self._provider = provider
        self._s = settings

    @property
    def redirect_uri(self) -> str:
        return f"{self._s.public_api_url}/api/v1/auth/google/callback"

    def _require_provider(self) -> OidcProvider:
        if self._provider is None:
            raise ApiError(
                503,
                "provider_not_configured",
                "Provider not configured",
                "El acceso con Google no está configurado.",
            )
        return self._provider

    async def start(self, *, next_path: str | None, hint: str | None) -> OidcStart:
        provider = self._require_provider()
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(16)
        verifier = secrets.token_urlsafe(48)
        await self._redis.set(
            self._key(state),
            json.dumps({"nonce": nonce, "verifier": verifier, "next": safe_next(next_path)}),
            ex=STATE_TTL_S,
        )
        url = await provider.authorization_url(
            state=state,
            nonce=nonce,
            code_challenge=pkce_challenge(verifier),
            redirect_uri=self.redirect_uri,
            hint=hint if provider.name == "fake" else None,
        )
        return OidcStart(url=url, state=state)

    async def callback(self, *, code: str, state: str, state_cookie: str | None) -> OidcResult:
        provider = self._require_provider()
        # El cookie liga el flujo al navegador que lo inició (defensa contra login CSRF).
        if not state_cookie or not secrets.compare_digest(state, state_cookie):
            raise _unauthorized("La solicitud de inicio de sesión no es válida o expiró.")
        raw = await self._redis.getdel(self._key(state))
        if raw is None:
            raise _unauthorized("La solicitud de inicio de sesión no es válida o expiró.")
        saved = json.loads(raw)
        identity = await provider.exchange(
            code=code,
            code_verifier=saved["verifier"],
            redirect_uri=self.redirect_uri,
            nonce=saved["nonce"],
        )
        if not identity.email_verified:
            raise _unauthorized("Tu correo de Google no está verificado.")
        user = await self._resolve_user(identity)
        return OidcResult(user=user, next_path=saved["next"])

    def _key(self, state: str) -> str:
        return f"oidc:{hashlib.sha256(state.encode()).hexdigest()}"

    async def _resolve_user(self, identity: OidcIdentity) -> User:
        now = datetime.now(UTC)
        async with self._db.global_session() as s:
            linked = await repo.get_identity(s, PROVIDER, identity.subject)
            if linked is not None:
                user = await repo.get_user(s, linked.user_id)
                if user is None or not user.is_active:
                    raise _unauthorized("Esta cuenta no está disponible.")
                return user

            user = await repo.get_user_by_email(s, identity.email)
            if user is not None:
                if not user.is_active:
                    raise _unauthorized("Esta cuenta no está disponible.")
                if user.email_verified_at is None:
                    # Registro local que nunca probó controlar el correo: se descarta su
                    # contraseña y sus sesiones (previene el secuestro previo al registro).
                    user.password_hash = None
                    user.email_verified_at = now
                    await repo.revoke_all_for_user(s, user.id, now)
                    log.warning("oidc_took_over_unverified_account", user_id=str(user.id))
            else:
                user = await repo.add_user(
                    s,
                    User(
                        id=new_id(),
                        email=identity.email,
                        password_hash=None,
                        display_name=(identity.name or identity.email.split("@")[0])[:80],
                        email_verified_at=now,
                    ),
                )
            await repo.add_identity(
                s,
                UserIdentity(
                    id=new_id(),
                    user_id=user.id,
                    provider=PROVIDER,
                    subject=identity.subject,
                    email=identity.email,
                ),
            )
            return user
