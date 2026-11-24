"""Documento de edición (F1-06): fixtures compartidos con TypeScript, paridad esquema ↔ Pydantic y
migraciones. Los mismos archivos de `packages/video-project-schema/fixtures` los prueba Vitest."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from klick.edit_project import (
    CURRENT_VERSION,
    EditProject,
    EditProjectInvalid,
    MigrationError,
    migrate_edit_project,
    parse_edit_project,
)
from klick.edit_project.validate import SCHEMA_PATH, effective_ms, semantic_issues
from pydantic import ValidationError

PKG = Path(__file__).resolve().parents[3] / "packages" / "video-project-schema"
FIXTURES = PKG / "fixtures"
MANIFEST: list[dict[str, Any]] = json.loads(
    (FIXTURES / "manifest.json").read_text(encoding="utf-8")
)


def load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


VALID = [m["file"] for m in MANIFEST if m["valid"]]
INVALID = [(m["file"], set(m["errors"])) for m in MANIFEST if not m["valid"]]


def test_manifest_covers_every_fixture_file() -> None:
    on_disk = {f"{d}/{p.name}" for d in ("valid", "invalid") for p in (FIXTURES / d).glob("*.json")}
    assert on_disk == {m["file"] for m in MANIFEST}, "cada fixture debe estar en el manifiesto"
    assert len(VALID) >= 6 and len(INVALID) >= 25


def test_packaged_schema_is_the_single_source() -> None:
    """El esquema que usa el servidor es copia exacta de la fuente (`generate` lo mantiene)."""
    source = (PKG / "edit-project.schema.json").read_text(encoding="utf-8")
    assert SCHEMA_PATH.read_text(encoding="utf-8").replace("\r\n", "\n") == source.replace(
        "\r\n", "\n"
    )


@pytest.mark.parametrize("name", VALID)
def test_valid_documents_parse(name: str) -> None:
    doc = parse_edit_project(load(name))
    assert isinstance(doc, EditProject) and doc.schema_version == "1.0"
    assert semantic_issues(doc) == []


@pytest.mark.parametrize(("name", "codes"), INVALID)
def test_invalid_documents_are_rejected_with_the_expected_codes(name: str, codes: set[str]) -> None:
    with pytest.raises(EditProjectInvalid) as exc:
        parse_edit_project(load(name))
    assert exc.value.codes == codes
    assert all(i.path and i.message for i in exc.value.issues)


@pytest.mark.parametrize(("name", "codes"), [(n, c) for n, c in INVALID if c == {"schema"}])
def test_the_generated_pydantic_models_also_reject_every_structural_fixture(
    name: str, codes: set[str]
) -> None:
    """Paridad esquema ↔ Pydantic: lo estructural que rechaza el JSON Schema, lo rechazan los modelos."""
    with pytest.raises(ValidationError):
        EditProject.model_validate(load(name))


def test_explicit_nulls_are_rejected_even_though_pydantic_alone_would_accept_them() -> None:
    doc = load("valid/minimal.json")
    doc["canvas"]["background"] = None
    assert EditProject.model_validate(doc).canvas.background is None  # límite del generador
    with pytest.raises(EditProjectInvalid) as exc:  # el validador lo cierra con el JSON Schema
        parse_edit_project(doc)
    assert exc.value.codes == {"schema"}
    item_null = load("valid/minimal.json")
    item_null["timeline"]["tracks"][0]["items"][0]["speed"] = None
    with pytest.raises(EditProjectInvalid):
        parse_edit_project(item_null)


def test_defaults_are_applied_when_fields_are_omitted() -> None:
    doc = parse_edit_project(load("valid/minimal.json"))
    item = doc.timeline.tracks[0].items[0]
    assert (item.speed, item.volume, item.fade_in_ms, item.fade_out_ms) == (1, 1, 0, 0)  # type: ignore[union-attr]
    assert doc.canvas.background == "#000000" and doc.audio.normalize.enabled is True
    assert (doc.audio.normalize.target_lufs, doc.audio.normalize.true_peak_db) == (-14, -1.5)


def test_all_problems_are_reported_at_once() -> None:
    doc = load("valid/minimal.json")
    item = doc["timeline"]["tracks"][0]["items"][0]
    item.update(source_id="nope", fade_in_ms=30000, crop={"x": 0.9, "y": 0, "w": 0.5, "h": 1})
    with pytest.raises(EditProjectInvalid) as exc:
        parse_edit_project(doc)
    assert exc.value.codes == {"unknown_source", "fade_too_long", "crop_out_of_bounds"}


def test_issue_paths_point_at_the_offending_element() -> None:
    with pytest.raises(EditProjectInvalid) as exc:
        parse_edit_project(load("invalid/dangling-source.json"))
    assert exc.value.issues[0].path == "timeline.tracks[0].items[0].source_id"
    with pytest.raises(EditProjectInvalid) as exc2:
        parse_edit_project(load("invalid/negative-time.json"))
    assert exc2.value.issues[0].path == "timeline.tracks[0].items[0].start_ms"


def test_effective_duration_accounts_for_speed() -> None:
    doc = parse_edit_project(load("valid/speed-up.json"))
    item = doc.timeline.tracks[0].items[0]
    assert effective_ms(item) == 28400  # type: ignore[arg-type]


def test_item_limit_across_tracks() -> None:
    doc = load("valid/minimal.json")
    doc["timeline"]["tracks"] = [
        {
            "id": f"v{t}",
            "kind": "video",
            "items": [
                {
                    "id": f"i{t}_{n}",
                    "source_id": "s1",
                    "source_in_ms": n * 10,
                    "source_out_ms": n * 10 + 10,
                    "start_ms": n * 10,
                }
                for n in range(500)
            ],
        }
        for t in range(5)
    ]
    with pytest.raises(EditProjectInvalid) as exc:
        parse_edit_project(doc)
    assert exc.value.codes == {"too_many_items"}


@pytest.mark.parametrize("junk", [None, [], "texto", 5, {"schema_version": "1.0"}])
def test_garbage_input_never_crashes_the_validator(junk: Any) -> None:
    with pytest.raises(EditProjectInvalid):
        parse_edit_project(junk)


# ------------------------------------------------------------------------------ migraciones


def _legacy(doc: dict[str, Any]) -> dict[str, Any]:
    doc["timeline"]["duration_ms"] = round(doc["timeline"].pop("duration_s") * 1000)
    return doc


def _rename(doc: dict[str, Any]) -> dict[str, Any]:
    doc["note"] = "migrado"
    return doc


def test_migration_is_the_identity_at_the_current_version_and_never_mutates_the_input() -> None:
    doc = load("valid/shorts-follow.json")
    before = copy.deepcopy(doc)
    out = migrate_edit_project(doc)
    assert out == before and out is not doc and doc == before
    assert CURRENT_VERSION == "1.0"
    parse_edit_project(out)


def test_migrations_apply_in_chain_and_stamp_the_version() -> None:
    old = load("valid/minimal.json")
    old["schema_version"] = "0.8"
    old["timeline"]["duration_s"] = old["timeline"].pop("duration_ms") / 1000
    registry = {"0.8": ("0.9", _legacy), "0.9": ("1.0", _rename)}
    before = copy.deepcopy(old)
    out = migrate_edit_project(old, registry=registry)
    assert old == before  # entrada intacta
    assert out["schema_version"] == "1.0"
    assert out["timeline"]["duration_ms"] == 28400 and out["note"] == "migrado"
    out.pop("note")
    parse_edit_project(out)  # el resultado ya es un documento válido de la versión actual


@pytest.mark.parametrize(
    ("version", "registry", "code"),
    [
        ("2.0", {}, "document_from_the_future"),
        ("1.1", {}, "document_from_the_future"),
        ("0.5", {}, "no_migration_path"),
        ("0.5", {"0.5": ("0.6", lambda d: d), "0.6": ("0.5", lambda d: d)}, "migration_cycle"),
        ("abc", {}, "invalid_version"),
        ("1", {}, "invalid_version"),
    ],
)
def test_migration_errors(version: str, registry: dict[str, Any], code: str) -> None:
    doc = {"schema_version": version}
    with pytest.raises(MigrationError) as exc:
        migrate_edit_project(doc, registry=registry)
    assert exc.value.code == code


@pytest.mark.parametrize("doc", [{}, {"schema_version": 1}, None, []])
def test_migration_requires_a_declared_version(doc: Any) -> None:
    with pytest.raises(MigrationError) as exc:
        migrate_edit_project(doc)
    assert exc.value.code == "missing_version"
