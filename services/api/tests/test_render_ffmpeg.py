"""Render con FFmpeg real (F1-08): el resultado debe verse y sonar como dice el documento.

Corre en la imagen `worker-media` (`pnpm test:media`) y en CI: FFmpeg solo existe allí. Cada prueba
renderiza de verdad y mide el resultado (ffprobe, fotogramas, volumen).
"""

import copy
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from klick.edit_project import parse_edit_project
from klick.media import ffmpeg as ff
from klick.render.graph import RenderInputs, SourceInfo, build_render_plan, parse_loudnorm
from klick.render.presets import SYSTEM_PRESETS

pytestmark = [
    pytest.mark.ffmpeg,
    pytest.mark.skipif(
        shutil.which("ffmpeg") is None and not os.environ.get("KLICK_REQUIRE_FFMPEG"),
        reason="requiere FFmpeg real: pnpm test:media",
    ),
]

REPO = Path(__file__).resolve().parents[3]
FIXTURES = REPO / "packages" / "video-project-schema" / "fixtures"
VALID = [m["file"] for m in json.loads((FIXTURES / "manifest.json").read_text()) if m["valid"]]
PRESET_BY_CANVAS = {
    (1080, 1920): "9x16_1080_30",
    (1920, 1080): "16x9_1080_30",
    (1080, 1080): "1x1_1080_30",
    (1080, 1350): "4x5_1080_30",
}
ASS = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, Bold, Outline, Alignment, MarginV
Style: Default,DejaVu Sans,80,&H0000FFFF,&H00000000,1,4,2,300

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:03.00,0:00:06.00,Default,,0,0,0,,Subtitulo de prueba
"""


def _ffmpeg(*args: str) -> None:
    out = subprocess.run(  # noqa: S603
        ["ffmpeg", "-y", "-nostdin", "-hide_banner", "-loglevel", "error", *args],  # noqa: S607
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr[-800:]


@pytest.fixture(scope="module")
def media(tmp_path_factory: pytest.TempPathFactory) -> dict[str, SourceInfo]:
    """Fuentes sintéticas largas (los fixtures cortan hasta el segundo 176): video con audio, un
    segundo video distinto, música y una imagen. Todo generado: sin contenido de terceros."""
    d = tmp_path_factory.mktemp("render-src")
    lavfi = ["-f", "lavfi", "-i"]
    h264 = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-pix_fmt", "yuv420p"]
    _ffmpeg(*lavfi, "testsrc2=size=1280x720:rate=25:duration=180", *lavfi, "sine=frequency=440:duration=180",
            *h264, "-c:a", "aac", "-shortest", str(d / "video.mp4"))  # fmt: skip
    _ffmpeg(*lavfi, "smptehdbars=size=1280x720:rate=25:duration=40", *lavfi, "sine=frequency=660:duration=40",
            *h264, "-c:a", "aac", "-shortest", str(d / "video2.mp4"))  # fmt: skip
    _ffmpeg(*lavfi, "sine=frequency=880:duration=40", str(d / "music.wav"))
    _ffmpeg(*lavfi, "color=c=0xFF00FF:s=640x480", "-frames:v", "1", str(d / "logo.png"))
    mp4 = "mov,mp4,m4a,3gp,3g2,mj2"
    return {
        "s1": SourceInfo(str(d / "video.mp4"), 1280, 720, True, mp4),
        "s2": SourceInfo(str(d / "video2.mp4"), 1280, 720, True, mp4),
        "m1": SourceInfo(str(d / "music.wav"), None, None, True, "wav"),
        "logo": SourceInfo(str(d / "logo.png"), 640, 480, False, "image2"),
    }


@pytest.fixture(scope="module")
def out_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("render-out")


def load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


async def render(
    raw: dict[str, Any], media: dict[str, SourceInfo], out_dir: Path, name: str, **over: Any
) -> tuple[Path, Any]:
    """Renderiza como lo hará el worker: medición de volumen (si aplica) y render final."""
    doc = parse_edit_project(raw)
    preset = SYSTEM_PRESETS[PRESET_BY_CANVAS[(doc.canvas.width, doc.canvas.height)]]
    aux = out_dir / f"aux_{name}"
    aux.mkdir(exist_ok=True)
    (aux / "captions.ass").write_text(ASS, encoding="utf-8")
    base = {
        "sources": media,
        "output": str(out_dir / f"{name}.mp4"),
        "aux_dir": str(aux),
        "ass_path": str(aux / "captions.ass") if doc.captions else None,
        "threads": 2,
        **over,
    }
    plan = build_render_plan(doc, preset, RenderInputs(**base))
    if plan.measure_args:
        measured = await ff.run(plan.measure_args, timeout_s=300)
        assert measured.returncode == 0, measured.stderr_tail
        plan = build_render_plan(
            doc, preset, RenderInputs(**base, loudnorm=parse_loudnorm(measured.stderr_tail))
        )
    for path, content in plan.aux_files.items():
        Path(path).write_text(content, encoding="utf-8")
    result = await ff.run(plan.render_args, timeout_s=600)
    assert result.returncode == 0, result.stderr_tail
    return Path(base["output"]), plan


def probe(path: Path) -> dict[str, Any]:
    out = subprocess.run(  # noqa: S603
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(path),
        ],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    data: dict[str, Any] = json.loads(out.stdout)
    return data


def frame(path: Path, t: float, width: int, height: int) -> bytes:
    out = subprocess.run(  # noqa: S603
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            f"{t}",
            "-i",
            str(path),
            "-frames:v",
            "1",  # noqa: S607
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],  # fmt: skip
        capture_output=True,
        check=True,
    )
    assert len(out.stdout) == width * height * 3, "el fotograma no tiene el tamaño esperado"
    return out.stdout


def px(img: bytes, width: int, x: int, y: int) -> tuple[int, int, int]:
    i = (y * width + x) * 3
    return img[i], img[i + 1], img[i + 2]


def region_diff(a: bytes, b: bytes, width: int, x0: int, y0: int, x1: int, y1: int) -> float:
    total = 0
    for y in range(y0, y1, 2):
        row = (y * width + x0) * 3
        total += sum(abs(a[row + k] - b[row + k]) for k in range(0, (x1 - x0) * 3))
    return total / (((y1 - y0) // 2 or 1) * (x1 - x0) * 3)


def loudness(path: Path, extra: list[str] | None = None) -> float:
    out = subprocess.run(  # noqa: S603
        [
            "ffmpeg",
            "-nostats",
            "-hide_banner",
            "-i",
            str(path),
            "-af",
            "ebur128",
            *(extra or []),
            "-f",
            "null",
            "-",
        ],  # noqa: S607
        capture_output=True,
        text=True,
    )
    values = re.findall(r"\n\s+I:\s+(-?\d+\.\d+) LUFS", out.stderr)
    return float(values[-1])


def rms_db(path: Path, start: float, length: float, af: str = "anull") -> float:
    out = subprocess.run(  # noqa: S603
        [
            "ffmpeg",
            "-nostats",
            "-hide_banner",
            "-ss",
            f"{start}",
            "-t",
            f"{length}",
            "-i",
            str(path),  # noqa: S607
            "-af",
            f"{af},astats=metadata=1:reset=0",
            "-f",
            "null",
            "-",
        ],  # fmt: skip
        capture_output=True,
        text=True,
    )
    values = re.findall(r"Overall\s+RMS level dB:\s+(-?[\d.]+|-inf)", out.stderr) or re.findall(
        r"RMS level dB:\s+(-?[\d.]+|-inf)", out.stderr
    )
    return float("-inf") if values[-1] == "-inf" else float(values[-1])


# ---------------------------------------------------------------------- cada documento


@pytest.mark.parametrize("name", VALID)
async def test_every_valid_document_renders_to_spec(
    name: str, media: dict[str, SourceInfo], out_dir: Path
) -> None:
    raw = load(name)
    out, plan = await render(raw, media, out_dir, name.removeprefix("valid/").removesuffix(".json"))
    doc = parse_edit_project(raw)
    info = probe(out)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
    preset = SYSTEM_PRESETS[PRESET_BY_CANVAS[(doc.canvas.width, doc.canvas.height)]]
    assert (video["width"], video["height"]) == (preset.width, preset.height)
    assert (video["codec_name"], video["pix_fmt"], video["avg_frame_rate"]) == (
        "h264",
        "yuv420p",
        "30/1",
    )
    assert (audio["codec_name"], audio["sample_rate"], audio["channels"]) == ("aac", "48000", 2)
    assert abs(float(info["format"]["duration"]) - doc.timeline.duration_ms / 1000) < 0.15
    head = out.read_bytes()[:200_000]
    assert 0 <= head.find(b"moov") < head.find(b"mdat") or head.find(b"mdat") < 0  # faststart
    assert plan.has_audio
    if doc.audio.normalize.enabled:
        target = doc.audio.normalize.target_lufs or -14
        assert abs(loudness(out) - target) < 1.2, (
            "la normalización de dos pasadas debe acertar el objetivo"
        )


# ------------------------------------------------------------------------------ efectos


async def test_fit_pads_with_the_background_and_never_crops(
    media: dict[str, SourceInfo], out_dir: Path
) -> None:
    out, _ = await render(load("valid/minimal.json"), media, out_dir, "fit")
    img = frame(out, 5, 1080, 1920)
    assert px(img, 1080, 540, 20) == pytest.approx((11, 13, 16), abs=6)  # franja superior = 0B0D10
    assert px(img, 1080, 540, 1900) == pytest.approx((11, 13, 16), abs=6)
    centre = px(img, 1080, 540, 960)
    assert centre != (11, 13, 16) and max(centre) > 30  # el video está en el centro


async def test_text_appears_only_during_its_interval_and_only_where_positioned(
    media: dict[str, SourceInfo], out_dir: Path
) -> None:
    with_text = load("valid/shorts-follow.json")
    without = copy.deepcopy(with_text)
    without["timeline"]["tracks"] = without["timeline"]["tracks"][:1]
    a, _ = await render(with_text, media, out_dir, "text_on")
    b, _ = await render(without, media, out_dir, "text_off")
    inside, outside = 1.0, 5.0  # el texto va de 0 a 2,5 s
    on_in, off_in = frame(a, inside, 1080, 1920), frame(b, inside, 1080, 1920)
    assert region_diff(on_in, off_in, 1080, 100, 300, 980, 420) > 3  # y ≈ 18 % de 1920 = 345
    assert region_diff(on_in, off_in, 1080, 0, 1200, 1080, 1800) < 2  # nada fuera de su zona
    on_out, off_out = frame(a, outside, 1080, 1920), frame(b, outside, 1080, 1920)
    assert region_diff(on_out, off_out, 1080, 0, 0, 1080, 1920) < 1  # ya desapareció


async def test_keyframe_zoom_changes_the_framing_over_time(
    media: dict[str, SourceInfo], out_dir: Path
) -> None:
    animated = load("valid/shorts-follow.json")
    still = copy.deepcopy(animated)
    del still["timeline"]["tracks"][0]["items"][0]["keyframes"]
    a, _ = await render(animated, media, out_dir, "zoom_on")
    b, _ = await render(still, media, out_dir, "zoom_off")
    start_a, start_b = frame(a, 0.5, 1080, 1920), frame(b, 0.5, 1080, 1920)
    late_a, late_b = frame(a, 14, 1080, 1920), frame(b, 14, 1080, 1920)
    near_start = region_diff(start_a, start_b, 1080, 100, 500, 980, 1500)
    near_end = region_diff(late_a, late_b, 1080, 100, 500, 980, 1500)
    assert near_end > 4 * max(near_start, 0.5), (
        near_start,
        near_end,
    )  # al inicio sin zoom, después sí


async def test_split_layout_stacks_two_different_sources_at_the_ratio(
    media: dict[str, SourceInfo], out_dir: Path
) -> None:
    out, _ = await render(load("valid/split-layout.json"), media, out_dir, "split")
    img = frame(out, 3, 1080, 1920)
    above, below = px(img, 1080, 540, 764), px(img, 1080, 540, 772)  # el corte está en y = 768
    assert sum(abs(x - y) for x, y in zip(above, below, strict=True)) > 25
    top_area = [px(img, 1080, x, 100) for x in range(100, 1000, 150)]
    assert len(set(top_area)) > 3  # la parte superior son las barras de color del segundo video


async def test_image_overlay_shows_only_between_its_times(
    media: dict[str, SourceInfo], out_dir: Path
) -> None:
    out, _ = await render(load("valid/image-overlay.json"), media, out_dir, "image")
    magenta = (255, 0, 255)

    def at(t: float) -> tuple[int, int, int]:
        return px(frame(out, t, 1080, 1920), 1080, 918, 154)  # x = 85 %, y = 8 %

    assert at(5) == pytest.approx(magenta, abs=40)
    assert at(0.5) != pytest.approx(magenta, abs=40)  # antes de 1 s
    assert at(12) != pytest.approx(magenta, abs=40)  # después de 9 s


async def test_captions_are_burned_in_during_their_cue(
    media: dict[str, SourceInfo], out_dir: Path
) -> None:
    raw = load("valid/shorts-follow.json")
    with_caps = await render(raw, media, out_dir, "caps_on")
    no_caps = copy.deepcopy(raw)
    no_caps.pop("captions")
    without = await render(no_caps, media, out_dir, "caps_off")
    during = (frame(with_caps[0], 4, 1080, 1920), frame(without[0], 4, 1080, 1920))
    after = (frame(with_caps[0], 8, 1080, 1920), frame(without[0], 8, 1080, 1920))
    assert region_diff(*during, 1080, 100, 1400, 980, 1650) > 3  # el subtítulo se ve (y ≈ 1500)
    assert region_diff(*after, 1080, 0, 0, 1080, 1920) < 1  # fuera de su intervalo no hay nada


async def test_audio_fade_out_and_volume(media: dict[str, SourceInfo], out_dir: Path) -> None:
    raw = load("valid/shorts-follow.json")
    raw["audio"]["normalize"]["enabled"] = False  # medir el efecto de la edición, no de loudnorm
    out, _ = await render(raw, media, out_dir, "fade")
    loud = rms_db(out, 20, 1)
    tail = rms_db(out, 28.3, 0.1)  # los últimos 100 ms caen dentro del fundido de 200 ms
    assert loud > -30 and tail < loud - 6

    quiet = copy.deepcopy(raw)
    quiet["timeline"]["tracks"][0]["items"][0]["volume"] = 0.25
    q, _ = await render(quiet, media, out_dir, "quiet")
    assert rms_db(q, 20, 1) == pytest.approx(loud - 12, abs=1.5)  # 0,25 ≈ -12 dB


async def test_ducking_lowers_the_music_while_the_voice_is_present(
    media: dict[str, SourceInfo], out_dir: Path
) -> None:
    on = load("valid/audio-mix.json")
    on["audio"]["normalize"]["enabled"] = False
    off = copy.deepcopy(on)
    off["audio"]["ducking"]["enabled"] = False
    a, _ = await render(on, media, out_dir, "duck_on")
    b, _ = await render(off, media, out_dir, "duck_off")
    band = "bandpass=f=880:width_type=h:w=60"  # solo la música (880 Hz); la voz es de 440 Hz
    assert rms_db(a, 8, 4, band) < rms_db(b, 8, 4, band) - 3


async def test_speed_changes_the_duration_and_keeps_audio_in_sync(
    media: dict[str, SourceInfo], out_dir: Path
) -> None:
    raw = load("valid/speed-up.json")
    out, _ = await render(raw, media, out_dir, "speed")
    info = probe(out)
    durations = {s["codec_type"]: float(s["duration"]) for s in info["streams"]}
    assert abs(durations["video"] - 28.4) < 0.15 and abs(durations["audio"] - 28.4) < 0.15


async def test_video_without_audio_gets_a_silent_track(
    out_dir: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    d = tmp_path_factory.mktemp("mute")
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=40", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", "-preset", "ultrafast", str(d / "mute.mp4"))  # fmt: skip
    sources = {"s1": SourceInfo(str(d / "mute.mp4"), 640, 360, False, "mov,mp4,m4a,3gp,3g2,mj2")}
    raw = load("valid/minimal.json")
    raw["timeline"]["tracks"][0]["items"][0].update(source_in_ms=1000, source_out_ms=6000)
    raw["timeline"]["duration_ms"] = 5000
    out, plan = await render(raw, sources, out_dir, "mute")
    streams = {s["codec_type"] for s in probe(out)["streams"]}
    assert streams == {"video", "audio"} and plan.has_audio is False and plan.measure_args is None


async def test_hostile_text_renders_as_plain_text_and_executes_nothing(
    media: dict[str, SourceInfo], out_dir: Path
) -> None:
    raw = load("valid/shorts-follow.json")
    raw["timeline"]["tracks"][1]["items"][0]["text"] = (
        "'; [0:v]movie=/etc/passwd[x];drawtext=text='%{eval:1}' ,`$(touch /tmp/pwned)`"
    )
    out, plan = await render(raw, media, out_dir, "hostile")
    assert out.exists() and not Path("/tmp/pwned").exists()  # noqa: S108
    assert "passwd" not in " ".join(plan.render_args)
    on = frame(out, 1.0, 1080, 1920)
    assert (
        region_diff(on, frame(out, 5.0, 1080, 1920), 1080, 100, 300, 980, 420) > 1
    )  # se ve como texto
