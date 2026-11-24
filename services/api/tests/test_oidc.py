import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import asyncpg
import httpx
import jwt
import pyotp
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from harness import ApiHarness
from jwt.algorithms import RSAAlgorithm
from klick.core.config import Settings
from klick.core.errors import ApiError
from klick.identity.oidc import (
    FakeOidcProvider,
    GoogleOidcProvider,
    build_oidc_provider,
    pkce_challenge,
    safe_next,
)
from pydantic import SecretStr
from sqlalchemy.engine import make_url

PASSWORD = "correct-horse-battery"
BASE = "/api/v1"
WEB = "http://web.test"


# ------------------------------------------------------------------ unidad (sin base de datos)


def test_pkce_challenge_matches_rfc7636_example() -> None:
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert pkce_challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/projects/1", "/projects/1"),
        ("//evil.example", "/"),
        ("https://evil.example", "/"),
        ("/\\evil.example", "/"),
        (None, "/"),
        ("", "/"),
    ],
)
def test_safe_next_blocks_open_redirects(value: str | None, expected: str) -> None:
    assert safe_next(value) == expected


def test_provider_selection_rules() -> None:
    assert isinstance(build_oidc_provider(Settings(environment="test")), FakeOidcProvider)
    assert build_oidc_provider(Settings(environment="test", feature_fake_adapters=False)) is None
    real = Settings(
        environment="test",
        google_oauth_client_id="client-id",
        google_oauth_client_secret=SecretStr("client-secret"),
    )
    assert isinstance(build_oidc_provider(real), GoogleOidcProvider)
    prod = Settings(
        environment="production",
        app_secret_key=SecretStr("a-real-production-secret-0123456789abcdef"),
        encryption_master_key=SecretStr("another-real-production-secret-0123456789"),
        s3_secret_key=SecretStr("a-real-production-s3-secret-0123456789"),
        feature_fake_adapters=True,
    )
    assert build_oidc_provider(prod) is None  # el fake nunca se activa en producción


_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
CLIENT_ID = "klick-client.apps.googleusercontent.com"


def _jwks() -> dict[str, Any]:
    jwk = RSAAlgorithm.to_jwk(_KEY.public_key(), as_dict=True)
    jwk.update({"kid": "k1", "alg": "RS256", "use": "sig"})
    return {"keys": [jwk]}


def _id_token(key: Any = _KEY, **overrides: Any) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "1234567890",
        "email": "Ana@Example.com",
        "email_verified": True,
        "name": "Ana Pérez",
        "nonce": "nonce-1",
        "iat": now,
        "exp": now + 300,
    }
    claims.update({k: v for k, v in overrides.items() if v is not ...})
    for k, v in overrides.items():
        if v is ...:
            claims.pop(k, None)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"})


def _google(token_response: httpx.Response) -> GoogleOidcProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "authorization_endpoint": "https://accounts.example/auth",
                    "token_endpoint": "https://accounts.example/token",
                    "jwks_uri": "https://accounts.example/jwks",
                },
            )
        if url.endswith("/jwks"):
            return httpx.Response(200, json=_jwks())
        if url.endswith("/token"):
            assert request.method == "POST"
            form = parse_qs(request.content.decode())
            assert form["code_verifier"] == ["verifier-1"]
            assert form["grant_type"] == ["authorization_code"]
            return token_response
        return httpx.Response(404)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GoogleOidcProvider(
        CLIENT_ID, "secret", http=http, discovery_url="https://x/openid-configuration"
    )


async def _exchange(provider: GoogleOidcProvider, nonce: str = "nonce-1") -> Any:
    return await provider.exchange(
        code="auth-code", code_verifier="verifier-1", redirect_uri="http://api/cb", nonce=nonce
    )


async def test_google_authorization_url_uses_pkce_state_and_nonce() -> None:
    provider = _google(httpx.Response(200, json={}))
    url = await provider.authorization_url(
        state="st", nonce="no", code_challenge="ch", redirect_uri="http://api/cb", hint=None
    )
    q = parse_qs(urlparse(url).query)
    assert q["code_challenge_method"] == ["S256"] and q["code_challenge"] == ["ch"]
    assert q["state"] == ["st"] and q["nonce"] == ["no"]
    assert q["scope"] == ["openid email profile"] and q["response_type"] == ["code"]


async def test_google_valid_id_token_is_accepted() -> None:
    provider = _google(httpx.Response(200, json={"id_token": _id_token()}))
    identity = await _exchange(provider)
    assert identity.subject == "1234567890"
    assert identity.email == "ana@example.com"
    assert identity.email_verified is True


@pytest.mark.parametrize(
    "token",
    [
        _id_token(aud="other-client"),
        _id_token(nonce="replayed"),
        _id_token(exp=int(time.time()) - 10),
        _id_token(key=_OTHER_KEY),
        _id_token(iss="https://evil.example"),
        _id_token(email=...),
        _id_token(sub=...),
    ],
    ids=["aud", "nonce", "expired", "signature", "issuer", "no-email", "no-sub"],
)
async def test_google_rejects_invalid_id_tokens(token: str) -> None:
    provider = _google(httpx.Response(200, json={"id_token": token}))
    with pytest.raises(ApiError) as err:
        await _exchange(provider)
    assert err.value.code == "oidc_failed"


async def test_google_reports_unverified_email_and_failed_exchange() -> None:
    provider = _google(httpx.Response(200, json={"id_token": _id_token(email_verified=False)}))
    assert (await _exchange(provider)).email_verified is False
    failing = _google(httpx.Response(400, json={"error": "invalid_grant"}))
    with pytest.raises(ApiError):
        await _exchange(failing)
    empty = _google(httpx.Response(200, json={}))
    with pytest.raises(ApiError):
        await _exchange(empty)


# --------------------------------------------------------------- integración con la API (fake)

pytestmark_db = pytest.mark.db


def _dsn(url: str) -> str:
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


async def _sql(api: ApiHarness, query: str, *args: object) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(_dsn(api.db_urls["owner"]))
    try:
        return await conn.fetch(query, *args)
    finally:
        await conn.close()


async def _start(api: ApiHarness, **params: str) -> httpx.Response:
    return await api.client.get(f"{BASE}/auth/google/start", params=params)


async def _callback_from(api: ApiHarness, start: httpx.Response) -> httpx.Response:
    location = start.headers["location"]
    assert location.startswith(api.settings.public_api_url)
    path = urlparse(location)
    return await api.client.get(f"{path.path}?{path.query}")


async def _google_login(api: ApiHarness, email: str, **params: str) -> httpx.Response:
    start = await _start(api, fake_email=email, **params)
    assert start.status_code == 302
    return await _callback_from(api, start)


@pytestmark_db
async def test_google_login_creates_verified_user_without_password(api: ApiHarness) -> None:
    r = await _google_login(api, "nueva@example.com")
    assert r.status_code == 302
    assert r.headers["location"] == f"{WEB}/"
    names = {c.split("=", 1)[0] for c in r.headers.get_list("set-cookie")}
    assert {"klick_session", "klick_refresh", "klick_csrf"} <= names
    me = await api.client.get(f"{BASE}/me")
    assert me.status_code == 200 and me.json()["email_verified"] is True
    user = (await _sql(api, "select password_hash, email_verified_at from users"))[0]
    assert user["password_hash"] is None and user["email_verified_at"] is not None
    ident = (await _sql(api, "select provider, subject from user_identities"))[0]
    assert ident["provider"] == "google"


@pytestmark_db
async def test_second_google_login_reuses_the_same_account(api: ApiHarness) -> None:
    await _google_login(api, "nueva@example.com")
    api.client.cookies.clear()
    await _google_login(api, "nueva@example.com")
    assert (await _sql(api, "select count(*) as n from users"))[0]["n"] == 1
    assert (await _sql(api, "select count(*) as n from user_identities"))[0]["n"] == 1


@pytestmark_db
async def test_next_param_is_honored_only_for_internal_paths(api: ApiHarness) -> None:
    r = await _google_login(api, "a@example.com", next="/projects/42")
    assert r.headers["location"] == f"{WEB}/projects/42"
    api.client.cookies.clear()
    r = await _google_login(api, "b@example.com", next="//evil.example/steal")
    assert r.headers["location"] == f"{WEB}/"


@pytestmark_db
async def test_callback_requires_cookie_state_and_is_single_use(api: ApiHarness) -> None:
    start = await _start(api, fake_email="a@example.com")
    location = urlparse(start.headers["location"])
    url = f"{location.path}?{location.query}"

    # sin cookie de estado (otro navegador): rechazado
    stranger = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://testserver"
    )
    async with stranger:
        denied = await stranger.get(url)
    assert denied.status_code == 302 and "error=oidc_failed" in denied.headers["location"]

    ok = await api.client.get(url)
    assert ok.status_code == 302 and "error" not in ok.headers["location"]

    # repetir el mismo callback no vuelve a funcionar
    api.client.cookies.clear()
    api.client.cookies.set(
        "klick_oidc",
        parse_qs(location.query)["state"][0],
        domain="testserver.local",
        path="/api/v1/auth",
    )
    replay = await api.client.get(url)
    assert "error=oidc_failed" in replay.headers["location"]

    forged = await api.client.get(f"{location.path}?code=fake:evil@example.com&state=nope")
    assert "error=oidc_failed" in forged.headers["location"]


@pytestmark_db
async def test_verified_local_account_is_linked_not_duplicated(api: ApiHarness) -> None:
    await api.client.post(
        f"{BASE}/auth/register",
        json={"email": "ana@example.com", "password": PASSWORD, "display_name": "Ana"},
    )
    import re  # noqa: PLC0415

    token = re.search(r"token=([\w\-]+)", api.emails.sent[0].body)
    assert token
    await api.client.post(f"{BASE}/auth/email/verify", json={"token": token.group(1)})
    await _google_login(api, "ana@example.com")
    assert (await _sql(api, "select count(*) as n from users"))[0]["n"] == 1
    user = (await _sql(api, "select password_hash from users"))[0]
    assert user["password_hash"] is not None  # conserva su contraseña


@pytestmark_db
async def test_unverified_local_registration_is_taken_over_not_trusted(api: ApiHarness) -> None:
    """Pre-secuestro: alguien registra el correo de la víctima sin verificarlo."""
    await api.client.post(
        f"{BASE}/auth/register",
        json={"email": "victima@example.com", "password": PASSWORD, "display_name": "Atacante"},
    )
    login = await api.client.post(
        f"{BASE}/auth/login", json={"email": "victima@example.com", "password": PASSWORD}
    )
    assert login.status_code == 200
    attacker_cookies = dict(api.client.cookies)
    assert attacker_cookies

    api.client.cookies.clear()
    await _google_login(api, "victima@example.com")  # la víctima real entra con Google

    row = (await _sql(api, "select password_hash, email_verified_at from users"))[0]
    assert row["password_hash"] is None and row["email_verified_at"] is not None
    active = await _sql(
        api, "select count(*) as n from sessions where revoked_at is null and rotated_from is null"
    )
    assert active[0]["n"] == 1  # solo la sesión nueva de la víctima
    api.client.cookies.clear()
    retry = await api.client.post(
        f"{BASE}/auth/login", json={"email": "victima@example.com", "password": PASSWORD}
    )
    assert retry.status_code == 401  # la contraseña del atacante ya no sirve


@pytestmark_db
async def test_google_login_respects_mfa(api: ApiHarness) -> None:
    await _google_login(api, "mfa@example.com")
    csrf = (await api.client.get(f"{BASE}/me")).status_code
    assert csrf == 200
    token = api.client.cookies["klick_csrf"]
    enroll = await api.client.post(f"{BASE}/auth/mfa/totp/enroll", headers={"X-CSRF-Token": token})
    totp = pyotp.TOTP(enroll.json()["secret"])
    await api.client.post(
        f"{BASE}/auth/mfa/totp/confirm", json={"code": totp.now()}, headers={"X-CSRF-Token": token}
    )

    api.client.cookies.clear()
    r = await _google_login(api, "mfa@example.com")
    assert r.status_code == 302 and r.headers["location"] == f"{WEB}/login/mfa"
    cookies = {c.split("=", 1)[0] for c in r.headers.get_list("set-cookie")}
    assert "klick_mfa" in cookies and "klick_session" not in cookies
    assert (await api.client.get(f"{BASE}/me")).status_code == 401

    done = await api.client.post(
        f"{BASE}/auth/mfa/verify", json={"code": totp.at(int(time.time()) + 30)}
    )
    assert done.status_code == 200, done.text
    assert (await api.client.get(f"{BASE}/me")).status_code == 200


@pytestmark_db
async def test_google_disabled_when_provider_missing(api: ApiHarness) -> None:
    api.app.state.oidc_service._provider = None  # noqa: SLF001
    r = await _start(api)
    assert r.status_code == 503
    assert r.json()["code"] == "provider_not_configured"
