import re
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import httpx
import pytest
import structlog
from fastapi import Response
from harness import ApiHarness
from klick.core.config import Settings
from klick.identity.deps import clear_session_cookies, set_session_cookies
from klick.identity.service import IssuedSession
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.db

PASSWORD = "correct-horse-battery"
BASE = "/api/v1"


def _dsn(url: str) -> str:
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


async def _register(
    api: ApiHarness, email: str = "ana@example.com", password: str = PASSWORD
) -> httpx.Response:
    return await api.client.post(
        f"{BASE}/auth/register",
        json={"email": email, "password": password, "display_name": "Ana"},
    )


def _token_from_email(api: ApiHarness, index: int = -1) -> str:
    match = re.search(r"token=([\w\-]+)", api.emails.sent[index].body)
    assert match, api.emails.sent[index].body
    return match.group(1)


async def _login(
    api: ApiHarness, email: str = "ana@example.com", password: str = PASSWORD
) -> httpx.Response:
    return await api.client.post(f"{BASE}/auth/login", json={"email": email, "password": password})


async def _registered_and_logged_in(api: ApiHarness) -> str:
    assert (await _register(api)).status_code == 202
    r = await _login(api)
    assert r.status_code == 200, r.text
    csrf: str = r.json()["csrf_token"]
    return csrf


async def _sql(api: ApiHarness, query: str, *args: object) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(_dsn(api.db_urls["owner"]))
    try:
        return await conn.fetch(query, *args)
    finally:
        await conn.close()


# ---------------------------------------------------------------------------------- registro


async def test_register_is_generic_and_hashes_with_argon2id(api: ApiHarness) -> None:
    r = await _register(api)
    assert r.status_code == 202
    rows = await _sql(api, "select password_hash, email_verified_at from users")
    assert len(rows) == 1
    assert rows[0]["password_hash"].startswith("$argon2id$")
    assert PASSWORD not in rows[0]["password_hash"]
    assert rows[0]["email_verified_at"] is None
    assert len(api.emails.sent) == 1
    assert "verify-email?token=" in api.emails.sent[0].body


async def test_register_duplicate_email_gives_same_response_without_leaking(
    api: ApiHarness,
) -> None:
    first = await _register(api)
    second = await _register(api, email="ANA@example.com")  # citext: mismo correo
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    assert (await _sql(api, "select count(*) as n from users"))[0]["n"] == 1
    assert "Ya tienes una cuenta" in api.emails.sent[-1].subject
    assert "token=" not in api.emails.sent[-1].body


async def test_register_validates_input(api: ApiHarness) -> None:
    r = await api.client.post(
        f"{BASE}/auth/register",
        json={"email": "ana@example.com", "password": "corta", "display_name": "Ana"},
    )
    assert r.status_code == 422
    r = await _register(api, password="ana@example.com-and-more")
    assert r.status_code == 422
    assert r.json()["code"] == "weak_password"
    r = await api.client.post(
        f"{BASE}/auth/register",
        json={
            "email": "x@example.com",
            "password": PASSWORD,
            "display_name": "X",
            "is_admin": True,
        },
    )
    assert r.status_code == 422  # extra=forbid


async def test_verify_email_is_single_use(api: ApiHarness) -> None:
    await _register(api)
    token = _token_from_email(api)
    r = await api.client.post(f"{BASE}/auth/email/verify", json={"token": token})
    assert r.status_code == 204
    await _login(api)
    assert (await api.client.get(f"{BASE}/me")).json()["email_verified"] is True
    again = await api.client.post(f"{BASE}/auth/email/verify", json={"token": token})
    assert again.status_code == 400
    assert again.json()["code"] == "invalid_token"
    bogus = await api.client.post(f"{BASE}/auth/email/verify", json={"token": "x" * 40})
    assert bogus.status_code == 400


async def test_verify_email_token_expires(api: ApiHarness) -> None:
    await _register(api)
    token = _token_from_email(api)
    await _sql(api, "update auth_tokens set expires_at = now() - interval '1 minute'")
    r = await api.client.post(f"{BASE}/auth/email/verify", json={"token": token})
    assert r.status_code == 400


# ------------------------------------------------------------------------------------ login


async def test_login_sets_hardened_cookies_and_me_works(api: ApiHarness) -> None:
    await _register(api)
    r = await _login(api)
    assert r.status_code == 200
    raw = r.headers.get_list("set-cookie")
    by_name = {c.split("=", 1)[0]: c.lower() for c in raw}
    assert set(by_name) == {"klick_session", "klick_refresh", "klick_csrf"}
    assert "httponly" in by_name["klick_session"] and "samesite=lax" in by_name["klick_session"]
    assert (
        "httponly" in by_name["klick_refresh"] and "path=/api/v1/auth" in by_name["klick_refresh"]
    )
    assert "httponly" not in by_name["klick_csrf"]
    me = await api.client.get(f"{BASE}/me")
    assert me.status_code == 200
    assert me.json()["email"] == "ana@example.com"
    assert "password" not in me.text


def test_cookies_are_secure_in_production() -> None:
    settings = Settings(environment="production")
    issued = IssuedSession(
        user=None,  # type: ignore[arg-type]
        session_id=uuid.uuid4(),
        access_token="a",
        refresh_token="r",
        csrf_token="c",
        refresh_expires_at=datetime.now(UTC),
    )
    response = Response()
    set_session_cookies(response, settings, issued)
    cookies = response.headers.getlist("set-cookie")
    assert len(cookies) == 3
    assert all("secure" in c.lower() for c in cookies)
    cleared = Response()
    clear_session_cookies(cleared, settings)
    assert all("secure" in c.lower() for c in cleared.headers.getlist("set-cookie"))


async def test_login_errors_do_not_reveal_whether_account_exists(api: ApiHarness) -> None:
    await _register(api)
    wrong = await _login(api, password="wrong-password-1")
    unknown = await _login(api, email="nadie@example.com")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["code"] == unknown.json()["code"] == "invalid_credentials"
    assert wrong.json()["detail"] == unknown.json()["detail"]


async def test_inactive_user_cannot_login(api: ApiHarness) -> None:
    await _register(api)
    await _sql(api, "update users set is_active = false")
    assert (await _login(api)).status_code == 401


async def test_progressive_lockout_after_repeated_failures(api: ApiHarness) -> None:
    await _register(api)
    statuses = [(await _login(api, password=f"bad-password-{i}")).status_code for i in range(5)]
    assert statuses[:4] == [401, 401, 401, 401]
    assert statuses[4] == 429
    locked = await _login(api)  # contraseña correcta, pero bloqueada
    assert locked.status_code == 429
    assert int(locked.headers["retry-after"]) > 0


async def test_successful_login_resets_failure_counter(api: ApiHarness) -> None:
    await _register(api)
    for i in range(3):
        await _login(api, password=f"bad-password-{i}")
    assert (await _login(api)).status_code == 200
    for i in range(3):
        assert (await _login(api, password=f"bad-again-{i}")).status_code == 401


async def test_register_rate_limited_per_email(api: ApiHarness) -> None:
    codes = [(await _register(api)).status_code for _ in range(4)]
    assert codes[:3] == [202, 202, 202]
    assert codes[3] == 429


# ---------------------------------------------------------------------------- protección CSRF


async def test_unsafe_requests_with_cookie_require_csrf(api: ApiHarness) -> None:
    csrf = await _registered_and_logged_in(api)
    sessions = (await api.client.get(f"{BASE}/me/sessions")).json()
    sid = sessions[0]["id"]
    missing = await api.client.delete(f"{BASE}/me/sessions/{sid}")
    assert missing.status_code == 403
    assert missing.json()["code"] == "csrf_failed"
    wrong = await api.client.delete(f"{BASE}/me/sessions/{sid}", headers={"X-CSRF-Token": "x" * 64})
    assert wrong.status_code == 403
    ok = await api.client.delete(f"{BASE}/me/sessions/{sid}", headers={"X-CSRF-Token": csrf})
    assert ok.status_code == 204


async def test_me_requires_authentication(api: ApiHarness) -> None:
    r = await api.client.get(f"{BASE}/me")
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/problem+json")


async def test_tampered_and_expired_access_tokens_are_rejected(api: ApiHarness) -> None:
    await _registered_and_logged_in(api)
    good = api.client.cookies["klick_session"]
    api.client.cookies.set("klick_session", good[:-3] + "abc", domain="testserver.local", path="/")
    assert (await api.client.get(f"{BASE}/me")).status_code == 401

    api.client.cookies.clear()
    expired_app = Settings(
        environment="test",
        database_url=api.settings.database_url,
        redis_url=api.settings.redis_url,
        app_secret_key=api.settings.app_secret_key,
        access_token_ttl_s=-10,
    )
    from klick.identity.security import issue_access_token  # noqa: PLC0415

    rows = await _sql(api, "select id, user_id from sessions limit 1")
    token = issue_access_token(
        secret=expired_app.app_secret_key.get_secret_value(),
        issuer=expired_app.jwt_issuer,
        user_id=rows[0]["user_id"],
        session_id=rows[0]["id"],
        ttl_s=-10,
    )
    api.client.cookies.set("klick_session", token, domain="testserver.local", path="/")
    assert (await api.client.get(f"{BASE}/me")).status_code == 401


# --------------------------------------------------------------------------- refresco rotatorio


async def test_refresh_rotates_and_old_token_is_dead(api: ApiHarness) -> None:
    csrf = await _registered_and_logged_in(api)
    old_refresh = api.client.cookies["klick_refresh"]
    r = await api.client.post(f"{BASE}/auth/refresh", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text
    new_csrf = r.json()["csrf_token"]
    assert new_csrf != csrf
    assert api.client.cookies["klick_refresh"] != old_refresh
    assert (await api.client.get(f"{BASE}/me")).status_code == 200
    rows = await _sql(api, "select count(*) as n, count(rotated_from) as r from sessions")
    assert rows[0]["n"] == 2 and rows[0]["r"] == 1
    assert (await _sql(api, "select count(distinct family_id) as f from sessions"))[0]["f"] == 1


async def test_refresh_reuse_revokes_whole_family(api: ApiHarness) -> None:
    csrf = await _registered_and_logged_in(api)
    stolen_refresh = api.client.cookies["klick_refresh"]
    r = await api.client.post(f"{BASE}/auth/refresh", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    # El atacante (o un cliente desfasado) presenta el refresh ya rotado.
    api.client.cookies.set(
        "klick_refresh", stolen_refresh, domain="testserver.local", path="/api/v1/auth"
    )
    reuse = await api.client.post(f"{BASE}/auth/refresh", headers={"X-CSRF-Token": csrf})
    assert reuse.status_code == 401
    assert reuse.json()["code"] == "refresh_reuse_detected"
    assert (await _sql(api, "select count(*) as n from sessions where revoked_at is null"))[0][
        "n"
    ] == 0
    # El access token emitido tras la rotación también quedó invalidado.
    assert (await api.client.get(f"{BASE}/me")).status_code == 401


async def test_refresh_requires_csrf_and_valid_cookie(api: ApiHarness) -> None:
    await _registered_and_logged_in(api)
    r = await api.client.post(f"{BASE}/auth/refresh")
    assert r.status_code == 403
    api.client.cookies.clear()
    r = await api.client.post(f"{BASE}/auth/refresh", headers={"X-CSRF-Token": "x"})
    assert r.status_code == 401
    assert r.json()["code"] == "invalid_refresh_token"


async def test_refresh_fails_for_expired_session(api: ApiHarness) -> None:
    csrf = await _registered_and_logged_in(api)
    await _sql(api, "update sessions set expires_at = now() - interval '1 second'")
    r = await api.client.post(f"{BASE}/auth/refresh", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 401
    assert r.json()["code"] == "session_expired"


# -------------------------------------------------------------------------------------- logout


async def test_logout_revokes_session_and_clears_cookies(api: ApiHarness) -> None:
    csrf = await _registered_and_logged_in(api)
    r = await api.client.post(f"{BASE}/auth/logout", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 204
    assert (await api.client.get(f"{BASE}/me")).status_code == 401
    assert (await _sql(api, "select count(*) as n from sessions where revoked_at is null"))[0][
        "n"
    ] == 0
    again = await api.client.post(f"{BASE}/auth/logout")  # idempotente sin cookies
    assert again.status_code == 204


async def test_logout_requires_csrf(api: ApiHarness) -> None:
    await _registered_and_logged_in(api)
    r = await api.client.post(f"{BASE}/auth/logout")
    assert r.status_code == 403
    assert (await api.client.get(f"{BASE}/me")).status_code == 200


async def test_sessions_list_and_cross_user_revocation_is_denied(api: ApiHarness) -> None:
    csrf = await _registered_and_logged_in(api)
    listed = (await api.client.get(f"{BASE}/me/sessions")).json()
    assert len(listed) == 1 and listed[0]["current"] is True
    r = await api.client.delete(
        f"{BASE}/me/sessions/{uuid.uuid4()}", headers={"X-CSRF-Token": csrf}
    )
    assert r.status_code == 404

    other = await api.client.post(
        f"{BASE}/auth/register",
        json={"email": "bruno@example.com", "password": PASSWORD, "display_name": "Bruno"},
    )
    assert other.status_code == 202
    foreign = await _sql(
        api, "select s.id from sessions s join users u on u.id = s.user_id limit 1"
    )
    assert foreign  # la sesión de Ana existe; Bruno aún no tiene ninguna
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://testserver"
    ) as bruno:
        login = await bruno.post(
            f"{BASE}/auth/login", json={"email": "bruno@example.com", "password": PASSWORD}
        )
        r = await bruno.delete(
            f"{BASE}/me/sessions/{foreign[0]['id']}",
            headers={"X-CSRF-Token": login.json()["csrf_token"]},
        )
        assert r.status_code == 404
    assert (await api.client.get(f"{BASE}/me")).status_code == 200


# ------------------------------------------------------------------------------ recuperación


async def test_forgot_password_does_not_reveal_accounts(api: ApiHarness) -> None:
    await _register(api)
    api.emails.sent.clear()
    known = await api.client.post(f"{BASE}/auth/password/forgot", json={"email": "ana@example.com"})
    unknown = await api.client.post(
        f"{BASE}/auth/password/forgot", json={"email": "nadie@example.com"}
    )
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()
    assert len(api.emails.sent) == 1
    assert api.emails.sent[0].to == "ana@example.com"


async def test_reset_password_flow(api: ApiHarness) -> None:
    csrf = await _registered_and_logged_in(api)
    assert csrf
    await api.client.post(f"{BASE}/auth/password/forgot", json={"email": "ana@example.com"})
    token = _token_from_email(api)
    new_password = "another-long-passphrase"

    weak = await api.client.post(
        f"{BASE}/auth/password/reset", json={"token": token, "new_password": "short"}
    )
    assert weak.status_code == 422

    ok = await api.client.post(
        f"{BASE}/auth/password/reset", json={"token": token, "new_password": new_password}
    )
    assert ok.status_code == 204
    # sesiones previas revocadas
    assert (await api.client.get(f"{BASE}/me")).status_code == 401
    assert (await _login(api)).status_code == 401
    assert (await _login(api, password=new_password)).status_code == 200
    # de un solo uso
    reuse = await api.client.post(
        f"{BASE}/auth/password/reset",
        json={"token": token, "new_password": "yet-another-long-pass"},
    )
    assert reuse.status_code == 400


async def test_new_reset_token_invalidates_previous_one(api: ApiHarness) -> None:
    await _register(api)
    await api.client.post(f"{BASE}/auth/password/forgot", json={"email": "ana@example.com"})
    first = _token_from_email(api)
    await api.client.post(f"{BASE}/auth/password/forgot", json={"email": "ana@example.com"})
    r = await api.client.post(
        f"{BASE}/auth/password/reset", json={"token": first, "new_password": "brand-new-passphrase"}
    )
    assert r.status_code == 400


async def test_reset_token_expires(api: ApiHarness) -> None:
    await _register(api)
    await api.client.post(f"{BASE}/auth/password/forgot", json={"email": "ana@example.com"})
    token = _token_from_email(api)
    await _sql(
        api,
        "update auth_tokens set expires_at = $1 where purpose = 'reset_password'",
        datetime.now(UTC) - timedelta(minutes=1),
    )
    r = await api.client.post(
        f"{BASE}/auth/password/reset", json={"token": token, "new_password": "brand-new-passphrase"}
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------------- secretos


async def test_secrets_never_reach_logs(api: ApiHarness) -> None:
    secret_password = "never log this passphrase"
    with structlog.testing.capture_logs() as logs:
        await _register(api, password=secret_password)
        await _login(api, password=secret_password)
        await _login(api, password="never log this wrong one")
        csrf = api.client.cookies.get("klick_csrf") or ""
        refresh_cookie = api.client.cookies.get("klick_refresh") or ""
    dump = repr(logs)
    assert secret_password not in dump
    assert "never log this wrong one" not in dump
    assert refresh_cookie not in dump
    assert csrf not in dump
    # y la base solo guarda hashes de refresh tokens
    rows = await _sql(api, "select refresh_token_hash from sessions")
    assert all(refresh_cookie not in r["refresh_token_hash"] for r in rows)
