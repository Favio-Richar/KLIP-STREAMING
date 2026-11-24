"""Presets de exportación (docs/04 `render_presets`, docs/07 §3). Los del sistema son constantes;
las tablas por organización llegan con F1-13."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Preset:
    key: str
    width: int
    height: int
    fps: int
    crf: int = 20
    max_bitrate_kbps: int | None = None
    audio_bitrate_kbps: int = 192
    loudness_target_lufs: float = -14.0
    true_peak_db: float = -1.5
    container: str = "mp4"
    video_codec: str = "libx264"
    audio_codec: str = "aac"


SYSTEM_PRESETS: dict[str, Preset] = {
    "9x16_1080_30": Preset("9x16_1080_30", 1080, 1920, 30),
    "16x9_1080_30": Preset("16x9_1080_30", 1920, 1080, 30),
    "1x1_1080_30": Preset("1x1_1080_30", 1080, 1080, 30),
    "4x5_1080_30": Preset("4x5_1080_30", 1080, 1350, 30),
}
