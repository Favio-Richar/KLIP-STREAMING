import re
import uuid
from datetime import UTC, datetime

import asyncpg
import pyotp
import pytest
from actors import PASSWORD, Users, add_member, create_org
from harness import ApiHarness
from klick.core.audit import record_audit
from klick.core.config import Settings
from klick.core.db import Database
from sqlalchemy import text
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.db


def _dsn(url: str) -> str:
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


async def _sql(api: ApiHarness, query: str, *args: object) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(_dsn(api.db_urls["owner"]))
    try:
        return await conn.fetch(query, *args)
    finally:
        await conn.close()


async def _actions(api: ApiHarness) -> list[str]:
    rows = await _sql(api, "select action from audit_events order by created_at, id")
    return [r["action"] for r in rows]


async def test_org_lifecycle_is_audited_with_actor_and_request(
    api: ApiHarness, users: Users
) -> None:
    ana, vic = await users.make("ana@example.com"), await users.make("vic@example.com")
    org = await create_org(ana)
    await add_member(api, ana, org["id"], vic, "viewer")
    members = {
        m["email"]: m["id"] for m in (await ana.call("GET", "/members", org=org["id"])).json()
    }
    await ana.call(
        "PATCH", f"/members/{members[vic.email]}", org=org["id"], json={"role": "editor"}
    )
    await ana.call("PATCH", f"/orgs/{org['id']}", json={"name": "Nuevo"})
    tok = (
        await ana.call(
            "POST", "/me/api-tokens", org=org["id"], json={"name": "t", "scopes": ["media:read"]}
        )
    ).json()
    await ana.call("DELETE", f"/me/api-tokens/{tok['id']}", org=org["id"])
    await ana.call("DELETE", f"/members/{members[vic.email]}", org=org["id"])

    actions = await _actions(api)
    for expected in (
        "auth.registered",
        "auth.login",
        "org.created",
        "invitation.created",
        "invitation.accepted",
        "member.role_changed",
        "org.updated",
        "api_token.created",
        "api_token.revoked",
        "member.removed",
    ):
        assert expected in actions, (expected, actions)

    row = (
        await _sql(
            api,
            "select * from audit_events where action = 'member.role_changed'",
        )
    )[0]
    assert str(row["org_id"]) == org["id"] and row["actor_type"] == "user"
    assert str(row["actor_id"]) == ana.user_id and row["resource_type"] == "membership"
    assert row["request_id"] and row["ip"] is not None and row["user_agent"]
    diff = row["diff"]
    assert '"from": "viewer"' in diff and '"to": "editor"' in diff
    tok_row = (await _sql(api, "select * from audit_events where action = 'api_token.created'"))[0]
    assert "klk_" not in str(tok_row["diff"]) and tok["token"] not in str(tok_row["diff"])


async def test_security_events_are_audited_without_secrets(api: ApiHarness, users: Users) -> None:
    ana = await users.make("ana@example.com")
    enroll = (await ana.call("POST", "/auth/mfa/totp/enroll")).json()
    totp = pyotp.TOTP(enroll["secret"])
    confirm = await ana.call("POST", "/auth/mfa/totp/confirm", json={"code": totp.now()})
    codes = confirm.json()["recovery_codes"]
    await ana.call("POST", "/auth/mfa/totp/disable", json={"password": PASSWORD, "code": codes[0]})
    await ana.call("POST", "/auth/logout")
    actions = await _actions(api)
    assert {"auth.mfa_enabled", "auth.mfa_disabled", "auth.logout"} <= set(actions)
    dump = repr(await _sql(api, "select * from audit_events"))
    for secret in (enroll["secret"], codes[0], PASSWORD):
        assert secret not in dump


async def test_refresh_reuse_is_audited(api: ApiHarness, users: Users) -> None:
    ana = await users.make("ana@example.com")
    old = ana.client.cookies["klick_refresh"]
    assert (await ana.call("POST", "/auth/refresh")).status_code == 200
    ana.client.cookies.set("klick_refresh", old, domain="testserver.local", path="/api/v1/auth")
    assert (await ana.call("POST", "/auth/refresh")).status_code == 401
    assert "auth.refresh_reuse_detected" in await _actions(api)


async def test_password_reset_and_session_revocation_are_audited(
    api: ApiHarness, users: Users
) -> None:
    ana = await users.make("ana@example.com")
    sid = (await ana.call("GET", "/me/sessions")).json()[0]["id"]
    await ana.call("DELETE", f"/me/sessions/{sid}")
    await ana.client.post("/api/v1/auth/password/forgot", json={"email": ana.email})
    mail = [m for m in api.emails.sent if "reset-password" in m.body][-1]
    token = re.search(r"token=([\w\-]+)", mail.body)
    assert token
    await ana.client.post(
        "/api/v1/auth/password/reset",
        json={"token": token.group(1), "new_password": "a-new-passphrase-1"},
    )
    actions = await _actions(api)
    assert "auth.session_revoked" in actions and "auth.password_reset" in actions


async def test_audit_log_is_append_only_for_every_role(api: ApiHarness, users: Users) -> None:
    ana = await users.make("ana@example.com")
    await create_org(ana)
    for role in ("app", "system"):
        db = Database(
            Settings(database_url=api.db_urls[role], database_url_system=api.db_urls["system"])
        )
        try:
            for sql in (
                "update audit_events set action = 'x'",
                "delete from audit_events",
                "truncate audit_events",
            ):
                with pytest.raises(Exception, match="permission denied|append-only"):
                    async with db.global_session() as s:
                        await s.execute(text(sql))
        finally:
            await db.dispose()
    # El trigger lo impide incluso al dueño (superusuario en desarrollo).
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match="append-only"):
        await _sql(api, "update audit_events set action = 'x'")
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match="append-only"):
        await _sql(api, "delete from audit_events")
    assert await _actions(api)  # los registros siguen ahí


async def test_partitions_cannot_be_read_or_written_directly(api: ApiHarness, users: Users) -> None:
    ana = await users.make("ana@example.com")
    await create_org(ana)
    name = f"audit_events_{datetime.now(UTC).year}_{datetime.now(UTC).month:02d}"
    db = Database(Settings(database_url=api.db_urls["app"]))
    try:
        for sql in (
            f"select * from {name}",
            f"insert into {name} (id, actor_type, action) values (gen_random_uuid(), 'system', 'x')",
        ):
            with pytest.raises(Exception, match="permission denied"):
                async with db.global_session() as s:
                    await s.execute(text(sql))
    finally:
        await db.dispose()


async def test_audit_is_rolled_back_with_a_failed_transaction(api: ApiHarness) -> None:
    db: Database = api.app.state.db
    org = await _sql(api, "select 1")  # asegura conexión
    assert org is not None
    with pytest.raises(RuntimeError):
        async with db.global_session() as s:
            await record_audit(s, action="never.persisted", actor_type="system")
            raise RuntimeError("boom")
    assert "never.persisted" not in await _actions(api)


async def test_diff_scrubs_sensitive_keys(api: ApiHarness) -> None:
    db: Database = api.app.state.db
    async with db.global_session() as s:
        await record_audit(
            s,
            action="x.scrub",
            actor_type="system",
            resource_id=uuid.uuid4(),
            diff={"password": "hunter2", "api_token": "klk_abc", "recovery_codes": ["a"], "ok": 1},
        )
    row = (await _sql(api, "select diff from audit_events where action = 'x.scrub'"))[0]
    assert "hunter2" not in row["diff"] and "klk_abc" not in row["diff"]
    assert '"ok": 1' in row["diff"]


async def test_audit_events_of_other_orgs_are_invisible_to_the_app_role(
    api: ApiHarness, users: Users
) -> None:
    ana, bruno = await users.make("ana@example.com"), await users.make("bruno@example.com")
    a = uuid.UUID((await create_org(ana, "Org A"))["id"])
    b = uuid.UUID((await create_org(bruno, "Org B"))["id"])
    db = Database(Settings(database_url=api.db_urls["app"]))
    try:
        async with db.tenant_session(a) as s:
            orgs = (
                (await s.execute(text("select distinct org_id from audit_events"))).scalars().all()
            )
        assert orgs == [a] and b not in orgs
        async with db.global_session() as s:  # sin contexto: nada (incluye los eventos sin org)
            assert (await s.execute(text("select count(*) from audit_events"))).scalar_one() == 0
    finally:
        await db.dispose()


async def test_audit_trail_survives_deleting_the_organization(
    api: ApiHarness, users: Users
) -> None:
    ana = await users.make("ana@example.com")
    org = (await create_org(ana))["id"]
    before = await _sql(
        api, "select count(*) as n from audit_events where org_id = $1", uuid.UUID(org)
    )
    assert before[0]["n"] >= 1
    await _sql(
        api, "delete from organizations where id = $1", uuid.UUID(org)
    )  # sin FK: no se bloquea
    after = await _sql(
        api, "select count(*) as n from audit_events where org_id = $1", uuid.UUID(org)
    )
    assert after[0]["n"] == before[0]["n"]
