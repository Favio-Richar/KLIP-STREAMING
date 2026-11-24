"""Pruebas de humo de la imagen `worker-media` (F1-03): FFmpeg fijado por versión, libass y
los filtros que usan el render, el análisis y el audio. Construyen la imagen real con Docker.

Se omiten si Docker no está disponible (en CI siempre lo está y no se omiten).
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "klick-media:smoke"
FFMPEG_VERSION = "7.1.1"

pytestmark = pytest.mark.docker

ASS = """[Script Info]
ScriptType: v4.00+
PlayResX: 320
PlayResY: 180

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, Bold, Outline, Alignment, MarginV
Style: Default,DejaVu Sans,36,&H00FFFFFF,&H00000000,1,2,2,10

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,Hola Klick
"""


def _docker_ok() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


@pytest.fixture(scope="module")
def image() -> str:
    if not _docker_ok():
        if os.environ.get("CI"):
            pytest.fail("Docker es obligatorio en CI")
        pytest.skip("Docker no disponible")
    build = subprocess.run(
        [
            "docker",
            "build",
            "-f",
            "infra/docker/api.Dockerfile",
            "--target",
            "media",
            "-t",
            IMAGE,
            ".",
        ],  # fmt: skip
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stderr[-2000:]
    return IMAGE


def run(image: str, *cmd: str, stdin: str | None = None, user: str | None = None) -> str:
    args = ["docker", "run", "--rm", "-i", "--entrypoint", "sh"]
    if user:
        args += ["--user", user]
    out = subprocess.run(
        [*args, image, "-c", " ".join(cmd)],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=300,
        env={**os.environ, "MSYS_NO_PATHCONV": "1"},
    )
    assert out.returncode == 0, f"{cmd}\n{out.stderr[-1500:]}"
    return out.stdout


def test_ffmpeg_and_ffprobe_are_pinned_to_the_expected_version(image: str) -> None:
    for tool in ("ffmpeg", "ffprobe"):
        first = run(image, f"{tool} -version").splitlines()[0]
        assert f"{tool} version {FFMPEG_VERSION}" in first, first
    assert run(image, "echo $KLICK_FFMPEG_VERSION").strip() == FFMPEG_VERSION


def test_required_encoders_filters_and_libraries_are_available(image: str) -> None:
    conf = run(image, "ffmpeg -hide_banner -buildconf")
    for flag in (
        "--enable-libass",
        "--enable-libx264",
        "--enable-libfreetype",
        "--enable-fontconfig",
    ):
        assert flag in conf, flag
    filters = run(image, "ffmpeg -hide_banner -filters")
    for name in ("subtitles", "ass", "loudnorm", "crop", "scale", "zoompan", "xfade", "amix",
                 "atempo", "setpts", "drawtext", "overlay", "fade", "afade"):  # fmt: skip
        assert f" {name} " in filters, name
    encoders = run(image, "ffmpeg -hide_banner -encoders")
    assert " libx264 " in encoders and " aac " in encoders


def test_runs_as_an_unprivileged_user_with_a_writable_tmp(image: str) -> None:
    assert run(image, "id -u").strip() == "10001"
    assert run(image, "echo ok > /tmp/x && cat /tmp/x").strip() == "ok"


def test_renders_h264_aac_and_ffprobe_reads_it_back(image: str) -> None:
    out = run(
        image,
        "ffmpeg -v error -f lavfi -i testsrc2=size=320x180:rate=30:duration=2 "
        "-f lavfi -i sine=frequency=440:duration=2 -c:v libx264 -pix_fmt yuv420p -c:a aac "
        "-shortest /tmp/t.mp4 && "
        "ffprobe -v error -print_format json -show_streams -show_format /tmp/t.mp4",
    )
    info = json.loads(out)
    kinds = {s["codec_type"]: s for s in info["streams"]}
    assert kinds["video"]["codec_name"] == "h264"
    assert (kinds["video"]["width"], kinds["video"]["height"]) == (320, 180)
    assert kinds["audio"]["codec_name"] == "aac"
    assert 1.8 <= float(info["format"]["duration"]) <= 2.3


def test_burns_ass_subtitles_with_a_system_font(image: str) -> None:
    base = "ffmpeg -v error -f lavfi -i color=c=0x203040:s=320x180:d=1 -frames:v 1"
    plain = run(image, base, "-f md5 -")
    burned = run(
        image,
        "cat > /tmp/s.ass && ",
        base,
        "-vf ass=/tmp/s.ass -f md5 -",
        stdin=ASS,
    )
    assert plain.startswith("MD5=") and burned.startswith("MD5=")
    assert plain != burned  # el texto realmente se dibujó sobre el fondo liso


def test_loudnorm_first_pass_reports_measurements(image: str) -> None:
    err = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "ffmpeg",
            image,
            "-hide_banner",
            "-nostats",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-af",
            "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
            "-f",
            "null",
            "-",
        ],  # fmt: skip
        capture_output=True,
        text=True,
        timeout=120,
    ).stderr
    block = err[err.rindex("{") : err.rindex("}") + 1]
    data = json.loads(block)
    assert {"input_i", "input_tp", "input_lra", "input_thresh", "target_offset"} <= data.keys()
