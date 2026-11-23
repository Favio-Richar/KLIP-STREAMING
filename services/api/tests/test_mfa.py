import time

import asyncpg
import httpx
import pyotp
import pytest
from harness import ApiHarness
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.db

PASSWORD = "correct-horse-battery"
BASE = "/api/v1"
EMAIL = "ana@example.com"


def _dsn(url: str) -> str:
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


async def _sql(api: ApiHarness, query: str, *args: object) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(_dsn(api.db_urls["owner"]))
    try:
        return await conn.fetch(query, *args)
    finally:
        await conn.close()


async def _logged_in(api: ApiHarness) -> str:
    await api.client.post(
        f"{BASE}/auth/register",
        json={"email": EMAIL, "password": PASSWORD, "display_name": "Ana"},
    )
    r = await api.client.post(f"{BASE}/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert r.status_code == 200
    return str(r.json()["csrf_token"])


async def _post(
    api: ApiHarness, path: str, csrf: str | None, body: dict[str, object] | None = None
) -> httpx.Response:
    headers = {"X-CSRF-Token": csrf} if csrf else {}
    return await api.client.post(f"{BASE}{path}", json=body or {}, headers=headers)


async def _enable_mfa(api: ApiHarness) -> tuple[str, pyotp.TOTP, list[str]]:
    csrf = await _logged_in(api)
    enroll = await _post(api, "/auth/mfa/totp/enroll", csrf)
    assert enroll.status_code == 200, enroll.text
    totp = pyotp.TOTP(enroll.json()["secret"])
    confirm = await _post(api, "/auth/mfa/totp/confirm", csrf, {"code": totp.now()})
    assert confirm.status_code == 200, confirm.text
    return csrf, totp, confirm.json()["recovery_codes"]


def _next_code(totp: pyotp.TOTP) -> str:
    """Código del siguiente intervalo: válido por la ventana ±1 y posterior al ya usado."""
    return totp.at(int(time.time()) + 30)


async def _fresh_login(api: ApiHarness) -> httpx.Response:
    api.client.cookies.clear()
    return await api.client.post(f"{BASE}/auth/login", json={"email": EMAIL, "password": PASSWORD})


# ------------------------------------------------------------------------------- alta


async def test_enroll_stores_secret_encrypted_and_requires_auth_and_csrf(api: ApiHarness) -> None:
    anon = await api.client.post(f"{BASE}/auth/mfa/totp/enroll")
    assert anon.status_code == 401
    csrf = await _logged_in(api)
    no_csrf = await _post(api, "/auth/mfa/totp/enroll", None)
    assert no_csrf.status_code == 403
    r = await _post(api, "/auth/mfa/totp/enroll", csrf)
    assert r.status_code == 200
    body = r.json()
    assert body["otpauth_uri"].startswith("otpauth://totp/Klick:")
    assert "issuer=Klick" in body["otpauth_uri"]
    stored = (await _sql(api, "select secret_encrypted, confirmed_at from mfa_factors"))[0]
    assert body["secret"].encode() not in bytes(stored["secret_encrypted"])
    assert stored["confirmed_at"] is None
    assert (await api.client.get(f"{BASE}/me")).json()["mfa_enabled"] is False


async def test_confirm_activates_mfa_and_returns_hashed_recovery_codes(api: ApiHarness) -> None:
    csrf = await _logged_in(api)
    enroll = await _post(api, "/auth/mfa/totp/enroll", csrf)
    totp = pyotp.TOTP(enroll.json()["secret"])
    bad = await _post(api, "/auth/mfa/totp/confirm", csrf, {"code": "000000"})
    assert bad.status_code == 401
    assert bad.json()["code"] == "invalid_mfa_code"
    ok = await _post(api, "/auth/mfa/totp/confirm", csrf, {"code": totp.now()})
    assert ok.status_code == 200
    codes = ok.json()["recovery_codes"]
    assert len(codes) == 10 and len(set(codes)) == 10
    hashes = [r["code_hash"] for r in await _sql(api, "select code_hash from mfa_recovery_codes")]
    assert len(hashes) == 10
    assert not any(c in h or c.replace("-", "") in h for c in codes for h in hashes)
    assert (await api.client.get(f"{BASE}/me")).json()["mfa_enabled"] is True
    again = await _post(api, "/auth/mfa/totp/enroll", csrf)
    assert again.status_code == 409


async def test_confirm_without_enrollment_is_404(api: ApiHarness) -> None:
    csrf = await _logged_in(api)
    r = await _post(api, "/auth/mfa/totp/confirm", csrf, {"code": "123456"})
    assert r.status_code == 404


async def test_confirm_is_locked_after_repeated_failures(api: ApiHarness) -> None:
    csrf = await _logged_in(api)
    await _post(api, "/auth/mfa/totp/enroll", csrf)
    statuses = [
        (await _post(api, "/auth/mfa/totp/confirm", csrf, {"code": f"11111{i}"})).status_code
        for i in range(6)
    ]
    assert statuses[:4] == [401, 401, 401, 401]
    assert 429 in statuses[4:]


# ------------------------------------------------------------------------------ login


async def test_login_with_mfa_requires_second_factor(api: ApiHarness) -> None:
    _, totp, _ = await _enable_mfa(api)
    r = await _fresh_login(api)
    assert r.status_code == 200
    body = r.json()
    assert body["mfa_required"] is True and "user" not in body
    assert not r.headers.get_list("set-cookie")
    assert (await api.client.get(f"{BASE}/me")).status_code == 401

    done = await api.client.post(
        f"{BASE}/auth/mfa/verify", json={"mfa_token": body["mfa_token"], "code": _next_code(totp)}
    )
    assert done.status_code == 200, done.text
    assert done.json()["user"]["mfa_enabled"] is True
    assert (await api.client.get(f"{BASE}/me")).status_code == 200
    # El desafío es de un solo uso.
    again = await api.client.post(
        f"{BASE}/auth/mfa/verify", json={"mfa_token": body["mfa_token"], "code": _next_code(totp)}
    )
    assert again.status_code == 401
    assert again.json()["code"] == "mfa_challenge_expired"


async def test_totp_code_cannot_be_replayed(api: ApiHarness) -> None:
    _, totp, _ = await _enable_mfa(api)
    first = (await _fresh_login(api)).json()["mfa_token"]
    code = _next_code(totp)
    ok = await api.client.post(f"{BASE}/auth/mfa/verify", json={"mfa_token": first, "code": code})
    assert ok.status_code == 200
    second = (await _fresh_login(api)).json()["mfa_token"]
    replay = await api.client.post(
        f"{BASE}/auth/mfa/verify", json={"mfa_token": second, "code": code}
    )
    assert replay.status_code == 401
    assert replay.json()["code"] == "invalid_mfa_code"


async def test_code_used_to_confirm_cannot_be_reused_to_login(api: ApiHarness) -> None:
    csrf = await _logged_in(api)
    enroll = await _post(api, "/auth/mfa/totp/enroll", csrf)
    totp = pyotp.TOTP(enroll.json()["secret"])
    code = totp.now()
    await _post(api, "/auth/mfa/totp/confirm", csrf, {"code": code})
    token = (await _fresh_login(api)).json()["mfa_token"]
    r = await api.client.post(f"{BASE}/auth/mfa/verify", json={"mfa_token": token, "code": code})
    assert r.status_code == 401


async def test_challenge_dies_after_too_many_wrong_codes(api: ApiHarness) -> None:
    _, totp, _ = await _enable_mfa(api)
    token = (await _fresh_login(api)).json()["mfa_token"]
    codes = []
    for i in range(5):
        r = await api.client.post(
            f"{BASE}/auth/mfa/verify", json={"mfa_token": token, "code": f"00000{i}"}
        )
        codes.append((r.status_code, r.json()["code"]))
    assert codes[-1][1] in ("mfa_challenge_expired", "rate_limited")
    good = await api.client.post(
        f"{BASE}/auth/mfa/verify", json={"mfa_token": token, "code": _next_code(totp)}
    )
    assert good.status_code in (401, 429)


async def test_verify_requires_exactly_one_factor_and_a_token(api: ApiHarness) -> None:
    both = await api.client.post(
        f"{BASE}/auth/mfa/verify",
        json={"mfa_token": "x" * 30, "code": "123456", "recovery_code": "abcde-fghjk"},
    )
    assert both.status_code == 422
    none = await api.client.post(f"{BASE}/auth/mfa/verify", json={"code": "123456"})
    assert none.status_code == 401


async def test_recovery_code_logs_in_once(api: ApiHarness) -> None:
    _, _, recovery = await _enable_mfa(api)
    token = (await _fresh_login(api)).json()["mfa_token"]
    ok = await api.client.post(
        f"{BASE}/auth/mfa/verify", json={"mfa_token": token, "recovery_code": recovery[0]}
    )
    assert ok.status_code == 200
    token2 = (await _fresh_login(api)).json()["mfa_token"]
    reuse = await api.client.post(
        f"{BASE}/auth/mfa/verify", json={"mfa_token": token2, "recovery_code": recovery[0]}
    )
    assert reuse.status_code == 401
    other = await api.client.post(
        f"{BASE}/auth/mfa/verify",
        json={"mfa_token": token2, "recovery_code": recovery[1].upper().replace("-", " ")},
    )
    assert other.status_code == 200  # tolera mayúsculas y separadores
    used = await _sql(api, "select count(*) as n from mfa_recovery_codes where used_at is not null")
    assert used[0]["n"] == 2


# ------------------------------------------------------------------------------- baja


async def test_disable_requires_password_and_code(api: ApiHarness) -> None:
    csrf, _, recovery = await _enable_mfa(api)
    no_pw = await _post(api, "/auth/mfa/totp/disable", csrf, {"code": recovery[0]})
    assert no_pw.status_code == 401
    wrong_pw = await _post(
        api, "/auth/mfa/totp/disable", csrf, {"password": "wrong-password-1", "code": recovery[0]}
    )
    assert wrong_pw.status_code == 401
    bad_code = await _post(
        api, "/auth/mfa/totp/disable", csrf, {"password": PASSWORD, "code": "000000"}
    )
    assert bad_code.status_code == 401
    ok = await _post(
        api, "/auth/mfa/totp/disable", csrf, {"password": PASSWORD, "code": recovery[0]}
    )
    assert ok.status_code == 204
    assert (await _sql(api, "select count(*) as n from mfa_factors"))[0]["n"] == 0
    assert (await _sql(api, "select count(*) as n from mfa_recovery_codes"))[0]["n"] == 0
    login = await _fresh_login(api)
    assert login.json()["mfa_required"] is False
    assert login.status_code == 200
