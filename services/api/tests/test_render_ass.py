import re

import pytest
from klick.render.ass import build_ass, escape_text

CUES = [
    {"start_ms": 0, "end_ms": 1500, "text": "Hola a todos"},
    {"start_ms": 3_723_450, "end_ms": 3_725_000, "text": "Dos\nlíneas"},
]


def body(line: str) -> str:
    """Texto de un Dialogue (el décimo campo; el texto puede contener comas)."""
    return line.split(",", 9)[9]


def dialogues(ass: str) -> list[str]:
    return [line for line in ass.splitlines() if line.startswith("Dialogue:")]


def test_structure_timestamps_and_resolution() -> None:
    ass = build_ass(CUES, width=1080, height=1920)
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    first, second = dialogues(ass)
    assert first == "Dialogue: 0,0:00:00.00,0:00:01.50,Default,,0,0,0,,Hola a todos"
    assert second.startswith("Dialogue: 0,1:02:03.45,1:02:05.00,Default,")  # h:mm:ss.cs
    assert second.endswith("Dos\\Nlíneas")  # salto de línea de ASS
    assert ass.endswith("\n") and "\r" not in ass


def test_it_is_deterministic() -> None:
    assert build_ass(CUES, width=1080, height=1920) == build_ass(CUES, width=1080, height=1920)


def test_safe_area_margins_for_vertical_video() -> None:
    style = next(
        line
        for line in build_ass(CUES, width=1080, height=1920).splitlines()
        if line.startswith("Style:")
    )
    fields = style.split(",")
    # MarginL, MarginR, MarginV = 6 % lateral y 20 % inferior (docs/07 §3)
    assert fields[-4:-1] == ["65", "65", "384"]
    plain = next(
        line
        for line in build_ass(CUES, width=1080, height=1920, safe_area="none").splitlines()
        if line.startswith("Style:")
    )
    assert plain.split(",")[-4:-1] == ["54", "54", "96"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("{\\pos(0,0)}hola", "(/pos(0,0))hola"),
        ("{\\fs500}grande", "(/fs500)grande"),
        ("a\\Nb", "a/Nb"),
        ("sin cambios", "sin cambios"),
        ("uno\ndos", "uno\\Ndos"),
        ("c\r\nd", "c\\Nd"),
    ],
)
def test_user_text_cannot_inject_ass_commands(raw: str, expected: str) -> None:
    assert escape_text(raw) == expected


def test_injected_override_blocks_never_reach_the_file() -> None:
    ass = build_ass(
        [{"start_ms": 0, "end_ms": 1000, "text": "{\\1c&H0000FF&}rojo{\\p1}m 0 0 l 99999 0"}],
        width=1080,
        height=1920,
    )
    (line,) = dialogues(ass)
    text = body(line)
    assert text == "(/1c&H0000FF&)rojo(/p1)m 0 0 l 99999 0"


def test_highlight_style_uses_karaoke_timings_per_word() -> None:
    cue = {
        "start_ms": 1000,
        "end_ms": 3000,
        "text": "hola mundo",
        "words": [
            {"start_ms": 1000, "end_ms": 1800, "text": "hola"},
            {"start_ms": 2000, "end_ms": 2900, "text": "mundo"},
        ],
    }
    (line,) = dialogues(build_ass([cue], width=1080, height=1920, style="highlight"))
    text = body(line)
    assert text == "{\\k80}hola {\\k20}{\\k90}mundo{\\k10}"
    # la suma de los tiempos cubre exactamente el subtítulo (2000 ms = 200 cs)
    assert sum(int(n) for n in re.findall(r"\\k(\d+)", text)) == 200


def test_highlight_without_words_falls_back_to_plain_text() -> None:
    (line,) = dialogues(build_ass(CUES[:1], width=1080, height=1920, style="highlight"))
    assert body(line) == "Hola a todos"


def test_styles_differ_and_unknown_falls_back_to_clean() -> None:
    clean = build_ass(CUES, width=1080, height=1920, style="clean")
    outline = build_ass(CUES, width=1080, height=1920, style="outline")
    assert clean != outline
    assert build_ass(CUES, width=1080, height=1920, style="no-existe") == clean


def test_colors_are_converted_to_ass_bgr() -> None:
    ass = build_ass(CUES, width=1080, height=1920, style="highlight")
    assert "&H0000E6FF" in ass  # amarillo #FFE600 → BGR


def test_cues_beyond_the_clip_end_are_dropped_or_clamped() -> None:
    cues = [
        {"start_ms": 0, "end_ms": 1000, "text": "dentro"},
        {"start_ms": 4000, "end_ms": 9000, "text": "recortado"},
        {"start_ms": 6000, "end_ms": 7000, "text": "fuera"},
    ]
    lines = dialogues(build_ass(cues, width=1080, height=1920, duration_ms=5000))
    assert [body(x) for x in lines] == ["dentro", "recortado"]
    assert lines[1].startswith("Dialogue: 0,0:00:04.00,0:00:05.00,")
    assert len(dialogues(build_ass(cues, width=1080, height=1920))) == 3  # sin límite: todos
