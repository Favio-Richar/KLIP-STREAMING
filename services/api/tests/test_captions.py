import copy
import json
import uuid
from typing import Any

import pytest
from actors import Actor, Users, add_member, create_org
from harness import ApiHarness
from media_helpers import make_asset, sql

pytestmark = pytest.mark.db

CUES = [
    {"start_ms": 0, "end_ms": 1500, "text": "Hola a todos"},
    {"start_ms": 2000, "end_ms": 4000, "text": "Línea uno\nLínea dos"},
]


async def _setup(
    api: ApiHarness, users: Users, email: str = "ana@example.com"
) -> tuple[Actor, str, str]:
    ana = await users.make(email)
    org = (await create_org(ana, f"Org {email}"))["id"]
    asset = str(await make_asset(api, org, ana.user_id, status="ready", duration_ms=60_000))
    r = await ana.call(
        "POST", "/clips", org=org, json={"source_asset_id": asset, "start_ms": 0, "end_ms": 9000}
    )
    assert r.status_code == 201, r.text
    return ana, org, r.json()["id"]


async def _track(ana: Actor, org: str, clip: str) -> dict[str, Any]:
    r = await ana.call("POST", f"/clips/{clip}/caption-tracks", org=org, json={})
    assert r.status_code == 201, r.text
    data: dict[str, Any] = r.json()
    return data


async def _put(ana: Actor, org: str, track: str, cues: Any, revision: int | None = 1) -> Any:
    headers = {} if revision is None else {"If-Match": f'"{revision}"'}
    return await ana.call(
        "PUT", f"/caption-tracks/{track}/cues", org=org, json={"cues": cues}, headers=headers
    )


async def _use(ana: Actor, org: str, clip: str, track: str) -> None:
    doc = (await ana.call("GET", f"/clips/{clip}/edit-project", org=org)).json()
    document = copy.deepcopy(doc["document"])
    document["captions"] = {"track_id": track}
    r = await ana.call(
        "PATCH",
        f"/clips/{clip}/edit-project",
        org=org,
        json={"document": document},
        headers={"If-Match": f'"{doc["revision"]}"'},
    )
    assert r.status_code == 200, r.text


# ------------------------------------------------------------------------------- crear


async def test_create_list_and_get_a_track(api: ApiHarness, users: Users) -> None:
    ana, org, clip = await _setup(api, users)
    track = await _track(ana, org, clip)
    assert (track["clip_id"], track["language"], track["source"]) == (clip, "es", "manual")
    assert (track["revision"], track["cues"], track["status"]) == (1, [], "ready")
    listing = (await ana.call("GET", f"/clips/{clip}/caption-tracks", org=org)).json()
    assert [t["id"] for t in listing] == [track["id"]]
    got = await ana.call("GET", f"/caption-tracks/{track['id']}", org=org)
    assert got.status_code == 200 and got.headers["etag"] == '"1"'
    english = await ana.call(
        "POST", f"/clips/{clip}/caption-tracks", org=org, json={"language": "en"}
    )
    assert english.status_code == 201 and english.json()["language"] == "en"
    bad = await ana.call(
        "POST", f"/clips/{clip}/caption-tracks", org=org, json={"language": "ESPAÑOL!"}
    )
    assert bad.status_code == 422
    missing = await ana.call("POST", f"/clips/{uuid.uuid4()}/caption-tracks", org=org, json={})
    assert missing.status_code == 404


async def test_a_clip_admits_a_limited_number_of_tracks(api: ApiHarness, users: Users) -> None:
    ana, org, clip = await _setup(api, users)
    for _ in range(5):
        await _track(ana, org, clip)
    over = await ana.call("POST", f"/clips/{clip}/caption-tracks", org=org, json={})
    assert over.status_code == 409 and over.json()["code"] == "too_many_caption_tracks"


# ------------------------------------------------------------------------------ editar


async def test_replace_cues_bumps_the_revision_and_returns_the_new_etag(
    api: ApiHarness, users: Users
) -> None:
    ana, org, clip = await _setup(api, users)
    track = await _track(ana, org, clip)
    r = await _put(ana, org, track["id"], CUES)
    assert r.status_code == 200, r.text
    assert (r.json()["revision"], r.headers["etag"]) == (2, '"2"')
    assert [c["text"] for c in r.json()["cues"]] == ["Hola a todos", "Línea uno\nLínea dos"]
    again = await _put(ana, org, track["id"], [CUES[0]], revision=2)
    assert again.status_code == 200 and len(again.json()["cues"]) == 1
    cleared = await _put(ana, org, track["id"], [], revision=3)
    assert cleared.status_code == 200 and cleared.json()["cues"] == []


async def test_stale_revision_is_rejected_without_overwriting(
    api: ApiHarness, users: Users
) -> None:
    ana, org, clip = await _setup(api, users)
    track = await _track(ana, org, clip)
    assert (await _put(ana, org, track["id"], CUES)).status_code == 200
    stale = await _put(ana, org, track["id"], [CUES[0]], revision=1)
    assert stale.status_code == 412 and stale.json()["code"] == "revision_mismatch"
    assert stale.headers["etag"] == '"2"'
    current = (await ana.call("GET", f"/caption-tracks/{track['id']}", org=org)).json()
    assert len(current["cues"]) == 2  # no se pisó
    assert (await _put(ana, org, track["id"], CUES, revision=None)).status_code == 428
    assert (await ana.call(
        "PUT", f"/caption-tracks/{track['id']}/cues", org=org, json={"cues": CUES},
        headers={"If-Match": "abc"},
    )).status_code == 400  # fmt: skip


@pytest.mark.parametrize(
    ("cues", "code"),
    [
        ([{"start_ms": 1000, "end_ms": 1000, "text": "x"}], "invalid_range"),
        ([{"start_ms": 2000, "end_ms": 1000, "text": "x"}], "invalid_range"),
        (
            [
                {"start_ms": 0, "end_ms": 2000, "text": "a"},
                {"start_ms": 1500, "end_ms": 3000, "text": "b"},
            ],
            "cues_overlap",
        ),
        (
            [
                {"start_ms": 3000, "end_ms": 4000, "text": "b"},
                {"start_ms": 0, "end_ms": 1000, "text": "a"},
            ],
            "cues_overlap",
        ),
        ([{"start_ms": 0, "end_ms": 1000, "text": "a\nb\nc"}], "too_many_lines"),
        ([{"start_ms": 0, "end_ms": 1000, "text": "x" * 61}], "line_too_long"),
        (
            [
                {
                    "start_ms": 0,
                    "end_ms": 1000,
                    "text": "hola",
                    "words": [{"start_ms": 0, "end_ms": 2000, "text": "hola"}],
                }
            ],
            "word_out_of_range",
        ),
    ],
)
async def test_invalid_cues_report_a_stable_code_and_position(
    api: ApiHarness, users: Users, cues: list[dict[str, Any]], code: str
) -> None:
    ana, org, clip = await _setup(api, users)
    track = await _track(ana, org, clip)
    r = await _put(ana, org, track["id"], cues)
    assert r.status_code == 422 and r.json()["code"] == "invalid_cues"
    assert code in {e["code"] for e in r.json()["errors"]}
    assert all("index" in e for e in r.json()["errors"])
    assert (await ana.call("GET", f"/caption-tracks/{track['id']}", org=org)).json()[
        "revision"
    ] == 1


@pytest.mark.parametrize(
    "cue",
    [
        {"start_ms": 0, "end_ms": 1000, "text": ""},
        {"start_ms": 0, "end_ms": 1000, "text": "   \n  "},
        {"start_ms": 0, "end_ms": 1000, "text": "x" * 201},
        {"start_ms": 0, "end_ms": 1000, "text": "mal\x00texto"},
        {"start_ms": 0, "end_ms": 1000, "text": "mal\x1b[31mtexto"},
        {"start_ms": -1, "end_ms": 1000, "text": "x"},
        {"start_ms": 0, "end_ms": 86_400_001, "text": "x"},
        {"start_ms": 0, "end_ms": 1000, "text": "x", "extra": 1},
    ],
)
async def test_malformed_cues_are_rejected_by_the_schema(
    api: ApiHarness, users: Users, cue: dict[str, Any]
) -> None:
    ana, org, clip = await _setup(api, users)
    track = await _track(ana, org, clip)
    assert (await _put(ana, org, track["id"], [cue])).status_code == 422


async def test_too_many_cues_are_rejected(api: ApiHarness, users: Users) -> None:
    ana, org, clip = await _setup(api, users)
    track = await _track(ana, org, clip)
    many = [{"start_ms": i * 10, "end_ms": i * 10 + 5, "text": "x"} for i in range(1001)]
    r = await _put(ana, org, track["id"], many)
    assert r.status_code == 422


# ------------------------------------------------------------- documento y versiones


async def test_the_document_may_only_cite_a_track_of_its_own_clip(
    api: ApiHarness, users: Users
) -> None:
    ana, org, clip = await _setup(api, users)
    foreign = await _track(ana, org, clip)
    # una pista de otro clip de la misma organización tampoco vale
    asset = str(await make_asset(api, org, ana.user_id, status="ready", duration_ms=60_000))
    sibling = (
        await ana.call(
            "POST",
            "/clips",
            org=org,
            json={"source_asset_id": asset, "start_ms": 0, "end_ms": 5000},
        )
    ).json()["id"]
    doc = (await ana.call("GET", f"/clips/{sibling}/edit-project", org=org)).json()
    document = copy.deepcopy(doc["document"])
    document["captions"] = {"track_id": foreign["id"]}
    r = await ana.call(
        "PATCH",
        f"/clips/{sibling}/edit-project",
        org=org,
        json={"document": document},
        headers={"If-Match": '"1"'},
    )
    assert r.status_code == 422
    assert {e["code"] for e in r.json()["errors"]} == {"unknown_caption_track"}


async def test_a_version_freezes_the_cues_and_changing_them_makes_a_new_version(
    api: ApiHarness, users: Users
) -> None:
    ana, org, clip = await _setup(api, users)
    track = await _track(ana, org, clip)
    await _put(ana, org, track["id"], CUES)
    await _use(ana, org, clip, track["id"])

    v1 = (await ana.call("POST", f"/clips/{clip}/versions", org=org, json={})).json()
    frozen = await sql(api, "select captions from clip_versions where id = $1", uuid.UUID(v1["id"]))
    snap = frozen[0]["captions"]
    snap = snap if isinstance(snap, dict) else json.loads(snap)
    assert snap["track_id"] == track["id"] and len(snap["cues"]) == 2 and snap["revision"] == 2

    # cambiar los subtítulos NO altera la versión ya congelada…
    await _put(ana, org, track["id"], [CUES[0]], revision=2)
    again = await sql(api, "select captions from clip_versions where id = $1", uuid.UUID(v1["id"]))
    assert again[0]["captions"] == frozen[0]["captions"]
    # …y un render pide una versión nueva porque los cues cambiaron aunque el documento no
    job = await ana.call("POST", f"/clips/{clip}/render", org=org, json={})
    assert job.status_code == 202, job.text
    assert job.json()["clip_version_id"] != v1["id"]
    again_job = await ana.call("POST", f"/clips/{clip}/render", org=org, json={})
    assert again_job.json()["id"] == job.json()["id"]  # sin cambios: el mismo render (idempotente)


async def test_a_clip_without_captions_keeps_a_null_snapshot(api: ApiHarness, users: Users) -> None:
    ana, org, clip = await _setup(api, users)
    v = (await ana.call("POST", f"/clips/{clip}/versions", org=org, json={})).json()
    row = await sql(api, "select captions from clip_versions where id = $1", uuid.UUID(v["id"]))
    assert row[0]["captions"] is None


# ------------------------------------------------------------ permisos y aislamiento


async def test_permissions_and_cross_organization_isolation(api: ApiHarness, users: Users) -> None:
    ana, org, clip = await _setup(api, users)
    track = await _track(ana, org, clip)
    viewer = await users.make("viewer@example.com")
    await add_member(api, ana, org, viewer, "viewer")
    assert (await viewer.call("GET", f"/caption-tracks/{track['id']}", org=org)).status_code == 200
    assert (await viewer.call("GET", f"/clips/{clip}/caption-tracks", org=org)).status_code == 200
    for method, path, body in [
        ("POST", f"/clips/{clip}/caption-tracks", {}),
        ("PUT", f"/caption-tracks/{track['id']}/cues", {"cues": CUES}),
    ]:
        r = await viewer.call(method, path, org=org, json=body, headers={"If-Match": '"1"'})
        assert r.status_code == 403 and r.json()["errors"] == [{"permission": "clips:write"}]

    bob = await users.make("bob@example.com")
    org_b = (await create_org(bob, "Org B"))["id"]
    probes: list[tuple[str, str, Any]] = [
        ("GET", f"/caption-tracks/{track['id']}", None),
        ("GET", f"/clips/{clip}/caption-tracks", None),
        ("POST", f"/clips/{clip}/caption-tracks", {}),
        ("PUT", f"/caption-tracks/{track['id']}/cues", {"cues": CUES}),
    ]
    for method, path, body in probes:
        r = await bob.call(method, path, org=org_b, json=body, headers={"If-Match": '"1"'})
        assert r.status_code == 404, (method, path, r.status_code)
    assert (await ana.call("GET", f"/caption-tracks/{track['id']}", org=org)).json()["cues"] == []


async def test_audit_never_stores_the_subtitle_text(api: ApiHarness, users: Users) -> None:
    ana, org, clip = await _setup(api, users)
    track = await _track(ana, org, clip)
    await _put(
        ana, org, track["id"], [{"start_ms": 0, "end_ms": 900, "text": "secreto-confidencial"}]
    )
    events = await sql(
        api, "select action, diff::text as d from audit_events where org_id = $1", uuid.UUID(org)
    )
    assert {"caption.created", "caption.updated"} <= {e["action"] for e in events}
    assert all("secreto-confidencial" not in (e["d"] or "") for e in events)
    outbox = await sql(
        api, "select payload::text as p from outbox_events where org_id = $1", uuid.UUID(org)
    )
    assert all("secreto-confidencial" not in o["p"] for o in outbox)


async def test_database_constraints(api: ApiHarness, users: Users) -> None:
    import asyncpg  # noqa: PLC0415

    ana, org, clip = await _setup(api, users)
    track = await _track(ana, org, clip)
    tid = uuid.UUID(track["id"])
    with pytest.raises(asyncpg.CheckViolationError):
        await sql(api, "update caption_tracks set cues = '{}'::jsonb where id = $1", tid)
    with pytest.raises(asyncpg.CheckViolationError):
        await sql(api, "update caption_tracks set source = 'inventada' where id = $1", tid)
    with pytest.raises(asyncpg.CheckViolationError):
        await sql(api, "update caption_tracks set language = 'Español' where id = $1", tid)
    await sql(api, "update caption_tracks set revision = 5 where id = $1", tid)
    with pytest.raises(asyncpg.CheckViolationError):
        await sql(api, "update caption_tracks set revision = 3 where id = $1", tid)
    with pytest.raises(asyncpg.CheckViolationError):
        await sql(
            api,
            "insert into caption_tracks (id, org_id, language) values ($1, $2, 'es')",
            uuid.uuid4(),
            uuid.UUID(org),
        )  # fmt: skip  (sin clip ni asset)


async def test_a_pasted_tab_becomes_a_space(api: ApiHarness, users: Users) -> None:
    ana, org, clip = await _setup(api, users)
    track = await _track(ana, org, clip)
    r = await _put(ana, org, track["id"], [{"start_ms": 0, "end_ms": 900, "text": "Hola\tmundo"}])
    assert r.status_code == 200, r.text
    assert r.json()["cues"][0]["text"] == "Hola mundo"


async def test_tracks_of_a_deleted_clip_are_not_accessible(api: ApiHarness, users: Users) -> None:
    ana, org, clip = await _setup(api, users)
    track = await _track(ana, org, clip)
    assert (await ana.call("DELETE", f"/clips/{clip}", org=org)).status_code in (200, 204)
    assert (await ana.call("GET", f"/caption-tracks/{track['id']}", org=org)).status_code == 404
    assert (await _put(ana, org, track["id"], CUES)).status_code == 404
    assert (await ana.call("GET", f"/clips/{clip}/caption-tracks", org=org)).status_code == 404


async def test_the_track_limit_holds_under_concurrency(api: ApiHarness, users: Users) -> None:
    import asyncio  # noqa: PLC0415

    ana, org, clip = await _setup(api, users)
    for _ in range(3):
        await _track(ana, org, clip)
    results = await asyncio.gather(
        *(ana.call("POST", f"/clips/{clip}/caption-tracks", org=org, json={}) for _ in range(6))
    )
    codes = sorted(r.status_code for r in results)
    assert codes == [201, 201, 409, 409, 409, 409], codes
    n = (
        await sql(
            api, "select count(*) as n from caption_tracks where clip_id = $1", uuid.UUID(clip)
        )
    )[0]["n"]
    assert n == 5
