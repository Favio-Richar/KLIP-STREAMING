"""Generador del grafo de render (F1-08), parte pura: instantáneas, determinismo y seguridad.

Las pruebas con FFmpeg real (que el resultado se vea y suene como el documento) están en
`test_render_ffmpeg.py`. Para actualizar las instantáneas tras un cambio intencional:
`UPDATE_SNAPSHOTS=1 pytest services/api/tests/test_render_graph.py`.
"""

import copy
import json
import math
import os
from pathlib import Path
from typing import Any

import pytest
from klick.edit_project import parse_edit_project
from klick.render.exprs import (
    RenderPlanError,
    atempo_chain,
    color,
    even,
    interpolation,
    num,
    safe_path,
    secs,
)
from klick.render.graph import (
    LoudnormMeasure,
    RenderInputs,
    SourceInfo,
    TextStyle,
    build_render_plan,
)
from klick.render.presets import SYSTEM_PRESETS, Preset

REPO = Path(__file__).resolve().parents[3]
FIXTURES = REPO / "packages" / "video-project-schema" / "fixtures"
SNAPSHOTS = Path(__file__).resolve().parent / "snapshots" / "render"
MANIFEST: list[dict[str, Any]] = json.loads(
    (FIXTURES / "manifest.json").read_text(encoding="utf-8")
)
VALID = [m["file"] for m in MANIFEST if m["valid"]]
PRESET_BY_CANVAS = {
    (1080, 1920): "9x16_1080_30",
    (1920, 1080): "16x9_1080_30",
    (1080, 1080): "1x1_1080_30",
    (1080, 1350): "4x5_1080_30",
}


def doc_of(name: str) -> Any:
    return parse_edit_project(json.loads((FIXTURES / name).read_text(encoding="utf-8")))


def inputs_for(doc: Any, **over: Any) -> RenderInputs:
    sources = {
        s.id: SourceInfo(
            path=f"/work/in/{s.id}",
            width=1920 if s.kind == "video" else 640 if s.kind == "image" else None,
            height=1080 if s.kind == "video" else 480 if s.kind == "image" else None,
            has_audio=s.kind in ("video", "audio"),
            demuxer={"video": "mov,mp4,m4a,3gp,3g2,mj2", "audio": "wav", "image": "image2"}[s.kind],
        )
        for s in doc.sources
    }
    base: dict[str, Any] = {
        "sources": sources,
        "output": "/work/out/render.mp4",
        "aux_dir": "/work/aux",
        "ass_path": "/work/aux/captions.ass" if doc.captions else None,
    }
    return RenderInputs(**{**base, **over})


def plan_of(name: str, **over: Any) -> Any:
    doc = doc_of(name)
    preset = SYSTEM_PRESETS[PRESET_BY_CANVAS[(doc.canvas.width, doc.canvas.height)]]
    return doc, build_render_plan(doc, preset, inputs_for(doc, **over))


def snapshot(name: str, data: Any) -> None:
    path = SNAPSHOTS / f"{name}.json"
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if os.environ.get("UPDATE_SNAPSHOTS") or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if not os.environ.get("UPDATE_SNAPSHOTS"):
            pytest.fail(f"instantánea nueva creada: {path.name} (revísala y vuelve a ejecutar)")
        return
    assert path.read_text(encoding="utf-8").replace("\r\n", "\n") == text, (
        f"la instantánea {path.name} cambió: revisa el diff; si es intencional, "
        "ejecuta con UPDATE_SNAPSHOTS=1"
    )


# ---------------------------------------------------------------------------- instantáneas


@pytest.mark.parametrize("name", VALID)
def test_render_plan_snapshots(name: str) -> None:
    _, plan = plan_of(name)
    snapshot(
        name.removeprefix("valid/").removesuffix(".json"),
        {
            "duration_ms": plan.duration_ms,
            "has_audio": plan.has_audio,
            "measure_args": plan.measure_args,
            "render_args": plan.render_args,
            "aux_files": plan.aux_files,
        },
    )


@pytest.mark.parametrize("name", VALID)
def test_plans_are_deterministic(name: str) -> None:
    _, first = plan_of(name)
    _, second = plan_of(name)
    assert first == second and first.args_hash == second.args_hash
    assert len(first.args_hash) == 64


def test_the_hash_changes_with_any_relevant_change() -> None:
    _, base = plan_of("valid/shorts-follow.json")
    doc = doc_of("valid/shorts-follow.json")
    other = copy.deepcopy(doc)
    other.timeline.tracks[0].items[0].volume = 0.5
    preset = SYSTEM_PRESETS["9x16_1080_30"]
    changed = build_render_plan(other, preset, inputs_for(other))
    assert changed.args_hash != base.args_hash
    crf = build_render_plan(doc, Preset("9x16_1080_30", 1080, 1920, 30, crf=18), inputs_for(doc))
    assert crf.args_hash != base.args_hash


# ------------------------------------------------------------------------------ estructura


def test_every_command_is_hardened_and_reads_inputs_with_a_forced_demuxer() -> None:
    for name in VALID:
        _, plan = plan_of(name)
        for args in (plan.render_args, plan.measure_args):
            if args is None:
                continue
            assert args[0] == "ffmpeg" and "-nostdin" in args
            assert args[args.index("-protocol_whitelist") + 1] == "file,pipe"
            for i, arg in enumerate(args):
                if arg == "-i":
                    assert args[i - 2] == "-f" or args[i - 4] == "-f" or "image2" in args[i - 8 : i]
        assert plan.render_args[-1] == "/work/out/render.mp4"


def test_output_matches_the_preset_and_the_timeline_duration() -> None:
    doc, plan = plan_of("valid/shorts-follow.json")
    a = plan.render_args
    assert a[a.index("-t") + 1] == "28.4" and plan.duration_ms == 28400
    assert a[a.index("-r") + 1] == "30" and a[a.index("-crf") + 1] == "20"
    assert a[a.index("-c:v") + 1] == "libx264" and a[a.index("-c:a") + 1] == "aac"
    assert a[a.index("-b:a") + 1] == "192k" and "+faststart" in a
    assert (
        a[a.index("-map") + 1] == "[vout]"
        and a[a.index("-map", a.index("-map") + 1) + 1] == "[aout]"
    )
    assert doc.canvas.width == 1080


def test_canvas_and_preset_may_differ_and_the_output_is_rescaled() -> None:
    doc = doc_of("valid/minimal.json")  # lienzo 9:16
    plan = build_render_plan(doc, SYSTEM_PRESETS["16x9_1080_30"], inputs_for(doc))
    graph = plan.render_args[plan.render_args.index("-filter_complex") + 1]
    assert "scale=1920:1080:flags=lanczos" in graph


def test_fit_layout_pads_and_follow_layout_covers() -> None:
    _, fit = plan_of("valid/minimal.json")
    graph = fit.render_args[fit.render_args.index("-filter_complex") + 1]
    assert "force_original_aspect_ratio=decrease" in graph and "pad=1080:1920" in graph
    assert "color=0x0B0D10" in graph  # fondo del fit
    _, follow = plan_of("valid/shorts-follow.json")
    g2 = follow.render_args[follow.render_args.index("-filter_complex") + 1]
    assert "force_original_aspect_ratio=increase,crop=1080:1920" in g2
    assert "crop=iw*0.316:ih*1:iw*0.28:ih*0" in g2  # recorte fijo del ítem


def test_split_layout_stacks_two_lanes_without_a_dangling_base() -> None:
    _, plan = plan_of("valid/split-layout.json")
    graph = plan.render_args[plan.render_args.index("-filter_complex") + 1]
    assert "vstack=inputs=2" in graph
    assert "s=1080x768" in graph and "s=1080x1152" in graph  # 40 % / 60 % de 1920
    assert graph.count("color=") == 2  # solo los dos carriles: ninguna base sin usar


def test_keyframes_become_a_zoompan_with_interpolated_expressions() -> None:
    _, plan = plan_of("valid/shorts-follow.json")
    graph = plan.render_args[plan.render_args.index("-filter_complex") + 1]
    assert "zoompan=z='" in graph and ":d=1:s=606x1080:fps=30" in graph
    assert "if(lt(it,12)" in graph  # tramo entre 0 s y 12 s


def test_speed_and_fades_in_the_audio_chain() -> None:
    doc = doc_of("valid/speed-up.json")
    plan = build_render_plan(doc, SYSTEM_PRESETS["9x16_1080_30"], inputs_for(doc))
    graph = plan.render_args[plan.render_args.index("-filter_complex") + 1]
    assert "setpts=(PTS-STARTPTS)/2" in graph and "atempo=2," in graph
    _, shorts = plan_of("valid/shorts-follow.json")
    g2 = shorts.render_args[shorts.render_args.index("-filter_complex") + 1]
    assert "afade=t=out:st=28.2:d=0.2" in g2  # fundido de salida de 200 ms al final


def test_audio_mix_with_ducking_uses_a_sidechain() -> None:
    _, plan = plan_of("valid/audio-mix.json")
    graph = plan.render_args[plan.render_args.index("-filter_complex") + 1]
    assert "asplit=2" in graph and "sidechaincompress=threshold=0.04:ratio=8" in graph
    assert "volume=0.3" in graph and "afade=t=in:st=0:d=1" in graph


def test_loudnorm_is_two_pass() -> None:
    doc, first = plan_of("valid/shorts-follow.json")
    assert first.measure_args is not None
    m_graph = first.measure_args[first.measure_args.index("-filter_complex") + 1]
    assert "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json" in m_graph
    assert "-vn" in first.measure_args and first.measure_args[-3:] == ["-f", "null", "-"]
    g1 = first.render_args[first.render_args.index("-filter_complex") + 1]
    assert "loudnorm=I=-14:TP=-1.5:LRA=11[" in g1 or g1.endswith(
        "LRA=11[aout]"
    )  # sin medición: 1 pasada

    measured = LoudnormMeasure(-23.1, -4.2, 6.5, -33.4, 0.3)
    second = build_render_plan(
        doc, SYSTEM_PRESETS["9x16_1080_30"], inputs_for(doc, loudnorm=measured)
    )
    g2 = second.render_args[second.render_args.index("-filter_complex") + 1]
    assert (
        "measured_I=-23.1:measured_TP=-4.2:measured_LRA=6.5:measured_thresh=-33.4:offset=0.3:linear=true"
        in g2
    )
    assert second.args_hash != first.args_hash


def test_the_measure_pass_logs_at_info_level_and_parses_loudnorm_json() -> None:
    _, plan = plan_of("valid/shorts-follow.json")
    assert plan.measure_args is not None
    i = plan.measure_args.index("-loglevel")
    assert (
        plan.measure_args[i + 1] == "info"
        and plan.render_args[plan.render_args.index("-loglevel") + 1] == "error"
    )
    from klick.render.graph import parse_loudnorm  # noqa: PLC0415

    payload = {
        "input_i": "-23.54",
        "input_tp": "-4.10",
        "input_lra": "6.20",
        "input_thresh": "-34.00",
        "target_offset": "0.54",
    }
    out = "ruido\n" + json.dumps(payload, indent=1) + "\n"
    m = parse_loudnorm(out)
    assert (m.input_i, m.input_tp, m.input_lra, m.input_thresh, m.target_offset) == (
        -23.54,
        -4.1,
        6.2,
        -34.0,
        0.54,
    )
    for bad in (
        "",
        "sin json",
        '{"input_i": "-inf"}',
        '{"input_i": "x"}',
        '{"input_i":"-inf","input_tp":"0","input_lra":"0","input_thresh":"0","target_offset":"0"}',
    ):
        with pytest.raises(RenderPlanError):
            parse_loudnorm(bad)


def test_normalization_can_be_disabled_and_silence_is_added_when_there_is_no_audio() -> None:
    doc = doc_of("valid/minimal.json")
    off = copy.deepcopy(doc)
    off.audio.normalize.enabled = False
    plan = build_render_plan(off, SYSTEM_PRESETS["9x16_1080_30"], inputs_for(off))
    assert plan.measure_args is None
    assert "loudnorm" not in plan.render_args[plan.render_args.index("-filter_complex") + 1]
    mute_inputs = inputs_for(
        doc, sources={"s1": SourceInfo("/work/in/s1", 1920, 1080, False, "mp4")}
    )
    silent = build_render_plan(doc, SYSTEM_PRESETS["9x16_1080_30"], mute_inputs)
    g = silent.render_args[silent.render_args.index("-filter_complex") + 1]
    assert "anullsrc=r=48000:cl=stereo:d=28.4[aout]" in g
    assert silent.measure_args is None and silent.has_audio is False


def test_captions_and_images_and_text_layers() -> None:
    _, plan = plan_of("valid/shorts-follow.json")
    graph = plan.render_args[plan.render_args.index("-filter_complex") + 1]
    assert "ass=filename=/work/aux/captions.ass" in graph
    assert (
        "drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:textfile=/work/aux/text_x1.txt"
        in graph
    )
    assert "expansion=none" in graph and "enable='between(t,0,2.5)'" in graph
    assert plan.aux_files == {"/work/aux/text_x1.txt": "¡Mira esto!"}
    _, img = plan_of("valid/image-overlay.json")
    g = img.render_args[img.render_args.index("-filter_complex") + 1]
    assert (
        "overlay=x=main_w*0.85-overlay_w/2:y=main_h*0.08-overlay_h/2:enable='between(t,1,9)'" in g
    )
    assert "-loop" in img.render_args and "-framerate" in img.render_args


# -------------------------------------------------------------------------------- seguridad


HOSTILE = "hola'; [0:v]movie=/etc/passwd[x];drawtext=text='%{eval\\:1}' ,\n\"`$(rm -rf /)`\\"


def test_user_text_never_reaches_the_filter_graph() -> None:
    doc = doc_of("valid/shorts-follow.json")
    hostile = copy.deepcopy(doc)
    hostile.timeline.tracks[1].items[0].text = HOSTILE
    plan = build_render_plan(hostile, SYSTEM_PRESETS["9x16_1080_30"], inputs_for(hostile))
    everything = " ".join(plan.render_args)
    for fragment in ("passwd", "movie=", "rm -rf", "%{eval", "$(", "hola"):
        assert fragment not in everything, fragment
    assert (
        plan.aux_files["/work/aux/text_x1.txt"] == HOSTILE
    )  # el texto viaja intacto, en un archivo


def test_unsafe_paths_and_colors_are_rejected() -> None:
    for bad in ("/work/a b", "/work/a'b", "/work/a:b", "/work/a,b", "/work/[x]", "/work/a;b", ""):
        with pytest.raises(RenderPlanError):
            safe_path(bad)
    assert safe_path("/work/aux/text_x1.txt") == "/work/aux/text_x1.txt"
    for bad in ("red", "#12345", "#GGGGGG", "0xFFFFFF", "#FFFFFF;movie=x", ""):
        with pytest.raises(RenderPlanError):
            color(bad)
    assert color("#0b0d10") == "0x0B0D10"
    doc = doc_of("valid/shorts-follow.json")
    with pytest.raises(RenderPlanError):
        build_render_plan(
            doc, SYSTEM_PRESETS["9x16_1080_30"], inputs_for(doc, output="/work/a b.mp4")
        )
    with pytest.raises(RenderPlanError):
        build_render_plan(
            doc, SYSTEM_PRESETS["9x16_1080_30"], inputs_for(doc, ass_path="/x/a'b.ass")
        )
    with pytest.raises(RenderPlanError):
        build_render_plan(
            doc,
            SYSTEM_PRESETS["9x16_1080_30"],
            inputs_for(doc, styles={"x": TextStyle(color="#FFF")}, font_file="/f ont.ttf"),
        )


def test_style_values_are_validated_before_reaching_the_graph() -> None:
    doc = doc_of("valid/shorts-follow.json")
    style_ref = doc.timeline.tracks[1].items[0].style_ref
    bad = {style_ref: TextStyle(color="red")}
    with pytest.raises(RenderPlanError):
        build_render_plan(doc, SYSTEM_PRESETS["9x16_1080_30"], inputs_for(doc, styles=bad))
    good = {style_ref: TextStyle(font_size_frac=0.08, color="#FFD400", border_px=5)}
    plan = build_render_plan(doc, SYSTEM_PRESETS["9x16_1080_30"], inputs_for(doc, styles=good))
    graph = plan.render_args[plan.render_args.index("-filter_complex") + 1]
    assert "fontsize=154" in graph and "fontcolor=0xFFD400" in graph and "borderw=5" in graph


def test_missing_inputs_and_zoom_without_dimensions_fail_early() -> None:
    doc = doc_of("valid/shorts-follow.json")
    with pytest.raises(RenderPlanError, match="falta el archivo"):
        build_render_plan(doc, SYSTEM_PRESETS["9x16_1080_30"], inputs_for(doc, sources={}))
    no_dims = {"s1": SourceInfo("/work/in/s1", None, None, True, "mp4")}
    with pytest.raises(RenderPlanError, match="dimensiones"):
        build_render_plan(doc, SYSTEM_PRESETS["9x16_1080_30"], inputs_for(doc, sources=no_dims))


# --------------------------------------------------------------------------- utilidades


def test_number_and_time_formatting_is_stable() -> None:
    assert (num(1.0), num(0.5), num(-0.0), num(2.50000), num(1 / 3, 4)) == (
        "1",
        "0.5",
        "0",
        "2.5",
        "0.3333",
    )
    assert (secs(0), secs(28400), secs(1500), secs(1)) == ("0", "28.4", "1.5", "0.001")
    assert even(1079) == 1080 and even(1) == 2 and even(100.2) == 100
    with pytest.raises(RenderPlanError):
        num(math.inf)


@pytest.mark.parametrize(
    ("speed", "stages"),
    [(1.0, 0), (2.0, 1), (4.0, 2), (0.5, 1), (0.25, 2), (1.5, 1), (0.3, 2)],
)
def test_atempo_chain(speed: float, stages: int) -> None:
    got = atempo_chain(speed)
    assert len(got) == stages
    assert all(0.5 <= float(s.split("=")[1]) <= 2 for s in got)  # límites de atempo
    product = 1.0
    for stage in got:
        product *= float(stage.split("=")[1])
    assert product == pytest.approx(speed)
    with pytest.raises(RenderPlanError):
        atempo_chain(5)


def _evaluate(expr: str, **variables: float) -> float:
    """Evalúa una expresión de FFmpeg generada por `interpolation` (solo en pruebas)."""

    def if_(c: float, a: float, b: float) -> float:
        return a if c else b

    scope = {"if_": if_, "lt": lambda a, b: 1.0 if a < b else 0.0, "pow": math.pow, **variables}
    return float(eval(expr.replace("if(", "if_("), {"__builtins__": {}}, scope))  # noqa: S307


def test_interpolation_follows_the_keyframes_and_easings() -> None:
    times, values = [0.0, 10.0, 20.0], [1.0, 3.0, 2.0]
    linear = interpolation(times, values, ["linear"] * 3)
    for t, want in [(-5, 1), (0, 1), (5, 2), (10, 3), (15, 2.5), (20, 2), (99, 2)]:
        assert _evaluate(linear, it=t) == pytest.approx(want, abs=1e-6), t
    eased = interpolation([0, 10], [0, 1], ["in", "linear"])
    assert _evaluate(eased, it=5) == pytest.approx(0.25, abs=1e-6)  # in: p²
    out = interpolation([0, 10], [0, 1], ["out", "linear"])
    assert _evaluate(out, it=5) == pytest.approx(0.75, abs=1e-6)  # out: 1-(1-p)²
    inout = interpolation([0, 10], [0, 1], ["inout", "linear"])
    assert _evaluate(inout, it=2.5) == pytest.approx(0.125, abs=1e-6)
    assert _evaluate(inout, it=7.5) == pytest.approx(0.875, abs=1e-6)
    assert interpolation([3.0], [1.5], ["linear"]) == "1.5"
    with pytest.raises(RenderPlanError):
        interpolation([0, 0], [1, 2], ["linear", "linear"])
    with pytest.raises(RenderPlanError):
        interpolation([], [], [])
