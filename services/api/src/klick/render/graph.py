"""Generador del grafo de render (docs/07 §3). **Puro y determinista**: la misma entrada produce
exactamente los mismos argumentos de FFmpeg (se prueba con instantáneas y con FFmpeg real).

``build_render_plan(documento, preset, entradas)`` devuelve el plan completo: los argumentos de la
pasada de medición de volumen (si hay normalización), los del render final, los archivos auxiliares
que hay que escribir (los textos del usuario van en archivos: **nunca** se interpolan en el grafo) y
un hash de los argumentos para guardarlo en ``render_jobs``.

Reglas de seguridad:
- Todo texto/color/ruta que llega al grafo pasa por ``exprs`` (alfabeto cerrado).
- ``drawtext`` usa ``textfile`` y ``expansion=none``: el texto no se interpreta como expresión.
- Las fuentes de usuario se abren con el demuxer forzado y ``-protocol_whitelist file,pipe``.
"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field

from klick.edit_project.models import (
    AudioItem,
    AudioTrack,
    EditProject,
    ImageTrack,
    TextTrack,
    VideoItem,
    VideoTrack,
)
from klick.edit_project.validate import effective_ms
from klick.media.ffmpeg import SAFE_ARGS
from klick.render.exprs import (
    RenderPlanError,
    atempo_chain,
    clamp,
    color,
    even,
    interpolation,
    num,
    safe_path,
    secs,
)
from klick.render.presets import Preset

DEFAULT_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
AUDIO_RATE = 48000
DUCK_THRESHOLD = 0.04
DUCK_RATIO = 8
DUCK_ATTACK_MS = 20
DUCK_RELEASE_MS = 400


@dataclass(frozen=True)
class SourceInfo:
    """Un archivo de entrada ya descargado y analizado (los medidores vienen de ``media_assets``)."""

    path: str
    width: int | None = None
    height: int | None = None
    has_audio: bool = False
    demuxer: str | None = None  # el mismo que validó FFprobe (docs/07 §1)


@dataclass(frozen=True)
class TextStyle:
    """Estilo ya resuelto de una plantilla (F1-12). Los valores por defecto sirven sin plantilla."""

    font_size_frac: float = 0.05
    color: str = "#FFFFFF"
    border_color: str = "#000000"
    border_px: int = 3


@dataclass(frozen=True)
class LoudnormMeasure:
    """Resultado de la primera pasada de ``loudnorm`` (``print_format=json``)."""

    input_i: float
    input_tp: float
    input_lra: float
    input_thresh: float
    target_offset: float


@dataclass(frozen=True)
class RenderInputs:
    sources: Mapping[str, SourceInfo]
    output: str
    aux_dir: str
    ffmpeg: str = "ffmpeg"
    font_file: str = DEFAULT_FONT
    ass_path: str | None = None
    styles: Mapping[str, TextStyle] = field(default_factory=dict)
    loudnorm: LoudnormMeasure | None = None
    threads: int = 2


@dataclass(frozen=True)
class RenderPlan:
    measure_args: list[str] | None
    render_args: list[str]
    aux_files: dict[str, str]
    args_hash: str
    duration_ms: int
    has_audio: bool


# ------------------------------------------------------------------------------ utilidades


class _Builder:
    def __init__(self, doc: EditProject, preset: Preset, inputs: RenderInputs) -> None:
        self.doc, self.preset, self.inp = doc, preset, inputs
        self.W, self.H = doc.canvas.width, doc.canvas.height
        self.fps = doc.canvas.fps
        self.duration = doc.timeline.duration_ms
        self.index: dict[str, int] = {}
        self.kinds: dict[str, str] = {}
        self.aux: dict[str, str] = {}
        self._labels = 0
        for source in doc.sources:
            self.kinds[source.id] = source.kind
        # índice de entrada por orden de primera aparición (determinista)
        for track in doc.timeline.tracks:
            for item in track.items:
                source_id = getattr(item, "source_id", None)
                if source_id is not None and source_id not in self.index:
                    self._require(source_id)
                    self.index[source_id] = len(self.index)
        layout = doc.layout
        if layout.mode == "split" and layout.split is not None:
            for source_id in (layout.split.top_source, layout.split.bottom_source):
                if source_id not in self.index:
                    self._require(source_id)
                    self.index[source_id] = len(self.index)

    def _require(self, source_id: str) -> SourceInfo:
        info = self.inp.sources.get(source_id)
        if info is None:
            raise RenderPlanError(f"falta el archivo de la fuente {source_id}")
        return info

    def label(self, prefix: str) -> str:
        self._labels += 1
        return f"{prefix}{self._labels}"

    # ------------------------------------------------------------------------- entradas

    def input_args(self) -> list[str]:
        args: list[str] = []
        for source_id, _ in sorted(self.index.items(), key=lambda kv: kv[1]):
            info = self._require(source_id)
            if self.kinds[source_id] == "image":
                args += ["-f", "image2", "-loop", "1", "-framerate", str(self.fps)]
                args += ["-t", secs(self.duration)]
            elif info.demuxer:
                args += ["-f", info.demuxer]
            args += ["-i", info.path]
        return args

    # ------------------------------------------------------------------------- video

    def _scale_to(self, mode: str, width: int, height: int) -> str:
        if mode == "fit":
            fit = self.doc.layout.fit
            bg = (
                color(fit.background.color)
                if fit
                else color(self.doc.canvas.background or "#000000")
            )
            return (
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color={bg}"
            )
        # follow / manual / carriles del split: cubrir el cuadro y recortar el sobrante (centrado)
        return f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"

    def _item_chain(self, item: VideoItem, out: str, mode: str, width: int, height: int) -> str:
        """Cadena de un ítem de video: corte, velocidad, recorte, zoom animado y encuadre."""
        idx = self.index[item.source_id]
        speed = item.speed if item.speed is not None else 1.0
        steps = [
            f"trim=start={secs(item.source_in_ms)}:end={secs(item.source_out_ms)}",
            f"setpts=(PTS-STARTPTS)/{num(speed, 5)}",
            f"fps={self.fps}",
        ]
        source = self._require(item.source_id)
        crop_w = crop_h = None
        if item.crop is not None:
            c = item.crop
            steps.append(
                f"crop=iw*{num(c.w, 5)}:ih*{num(c.h, 5)}:iw*{num(c.x, 5)}:ih*{num(c.y, 5)}"
            )
            if source.width and source.height:
                crop_w, crop_h = even(source.width * c.w), even(source.height * c.h)
        elif source.width and source.height:
            crop_w, crop_h = even(source.width), even(source.height)
        if item.keyframes:
            if crop_w is None or crop_h is None:
                raise RenderPlanError(f"el zoom de {item.id} necesita las dimensiones de la fuente")
            steps.append(self._zoompan(item, crop_w, crop_h))
        steps.append(self._scale_to(mode, width, height))
        steps.append(f"setpts=PTS-STARTPTS+{secs(item.start_ms)}/TB")
        return f"[{idx}:v]" + ",".join(steps) + f"[{out}]"

    def _zoompan(self, item: VideoItem, width: int, height: int) -> str:
        frames = sorted(item.keyframes or [], key=lambda k: k.t_ms)
        times = [k.t_ms / 1000 for k in frames]
        eases = [k.ease or "linear" for k in frames]
        zoom = interpolation(
            times, [clamp(k.scale if k.scale is not None else 1.0, 1, 8) for k in frames], eases
        )
        cx = interpolation(
            times, [clamp(k.x if k.x is not None else 0.5, 0, 1) for k in frames], eases
        )
        cy = interpolation(
            times, [clamp(k.y if k.y is not None else 0.5, 0, 1) for k in frames], eases
        )
        x = f"max(0,min(iw-iw/zoom,({cx})*iw-iw/zoom/2))"
        y = f"max(0,min(ih-ih/zoom,({cy})*ih-ih/zoom/2))"
        return f"zoompan=z='{zoom}':x='{x}':y='{y}':d=1:s={width}x{height}:fps={self.fps}"

    def _canvas_base(self, label: str, width: int, height: int) -> str:
        bg = color(self.doc.canvas.background or "#000000")
        return f"color=c={bg}:s={width}x{height}:r={self.fps}:d={secs(self.duration)}[{label}]"

    def _span(self, start_ms: int, end_ms: int) -> str:
        return f"enable='between(t,{secs(start_ms)},{secs(end_ms)})'"

    def video_graph(self) -> tuple[list[str], str]:
        parts: list[str] = []
        layout = self.doc.layout
        mode = layout.mode
        video_tracks = [t for t in self.doc.timeline.tracks if isinstance(t, VideoTrack)]
        lanes: set[str] = set()
        if mode == "split" and layout.split is not None:
            current = self._split_base(parts, video_tracks, lanes)  # el split ya es el lienzo
        else:
            current = self.label("b")
            parts.append(self._canvas_base(current, self.W, self.H))

        for track in self.doc.timeline.tracks:
            if isinstance(track, VideoTrack):
                for item in track.items:
                    if item.source_id in lanes:
                        continue  # ya está en uno de los carriles del split
                    label = self.label("v")
                    item_mode = "fit" if mode == "split" else mode
                    if item_mode == "fit" and layout.fit is None:
                        item_mode = "follow"
                    parts.append(self._item_chain(item, label, item_mode, self.W, self.H))
                    nxt = self.label("b")
                    end = item.start_ms + effective_ms(item)
                    parts.append(
                        f"[{current}][{label}]overlay=eof_action=pass:"
                        f"{self._span(item.start_ms, end)}[{nxt}]"
                    )
                    current = nxt
            elif isinstance(track, ImageTrack):
                current = self._image_items(parts, track, current)
            elif isinstance(track, TextTrack):
                current = self._text_items(parts, track, current)

        if self.inp.ass_path is not None and self.doc.captions is not None:
            nxt = self.label("c")
            parts.append(f"[{current}]ass=filename={safe_path(self.inp.ass_path)}[{nxt}]")
            current = nxt

        final: list[str] = []
        if (self.W, self.H) != (self.preset.width, self.preset.height):
            final.append(f"scale={self.preset.width}:{self.preset.height}:flags=lanczos")
        if self.fps != self.preset.fps:
            final.append(f"fps={self.preset.fps}")
        final.append("format=yuv420p")
        parts.append(f"[{current}]" + ",".join(final) + "[vout]")
        return parts, "vout"

    def _split_base(self, parts: list[str], tracks: list[VideoTrack], lanes: set[str]) -> str:
        split = self.doc.layout.split
        assert split is not None  # noqa: S101 - verificado por el llamador
        top_h = even(self.H * split.ratio)
        bottom_h = self.H - top_h
        outputs: list[str] = []
        for source_id, height in ((split.top_source, top_h), (split.bottom_source, bottom_h)):
            lanes.add(source_id)
            lane = self.label("l")
            parts.append(self._canvas_base(lane, self.W, height))
            current = lane
            for track in tracks:
                for item in track.items:
                    if item.source_id != source_id:
                        continue
                    label = self.label("v")
                    parts.append(self._item_chain(item, label, "follow", self.W, height))
                    nxt = self.label("l")
                    end = item.start_ms + effective_ms(item)
                    parts.append(
                        f"[{current}][{label}]overlay=eof_action=pass:"
                        f"{self._span(item.start_ms, end)}[{nxt}]"
                    )
                    current = nxt
            outputs.append(current)
        stacked = self.label("b")
        parts.append(f"[{outputs[0]}][{outputs[1]}]vstack=inputs=2[{stacked}]")
        return stacked

    def _image_items(self, parts: list[str], track: ImageTrack, current: str) -> str:
        for item in track.items:
            idx = self.index[item.source_id]
            scale = item.scale if item.scale is not None else 1.0
            img = self.label("g")
            parts.append(f"[{idx}:v]scale=iw*{num(scale, 4)}:ih*{num(scale, 4)},format=rgba[{img}]")
            nxt = self.label("b")
            parts.append(
                f"[{current}][{img}]overlay=x=main_w*{num(item.position.x, 4)}-overlay_w/2:"
                f"y=main_h*{num(item.position.y, 4)}-overlay_h/2:"
                f"{self._span(item.start_ms, item.end_ms)}[{nxt}]"
            )
            current = nxt
        return current

    def _text_items(self, parts: list[str], track: TextTrack, current: str) -> str:
        for item in track.items:
            style = self.inp.styles.get(item.style_ref or "", TextStyle())
            path = safe_path(f"{self.inp.aux_dir.rstrip('/')}/text_{item.id}.txt")
            self.aux[path] = item.text  # el texto va en un archivo: nunca dentro del grafo
            size = max(8, round(self.H * clamp(style.font_size_frac, 0.01, 0.3)))
            align = item.align or "center"
            x = {
                "left": f"w*{num(item.position.x, 4)}",
                "right": f"w*{num(item.position.x, 4)}-text_w",
            }.get(align, f"w*{num(item.position.x, 4)}-text_w/2")
            y = f"h*{num(item.position.y, 4)}-text_h/2"
            nxt = self.label("t")
            parts.append(
                f"[{current}]drawtext=fontfile={safe_path(self.inp.font_file)}:textfile={path}:"
                f"expansion=none:fontsize={size}:fontcolor={color(style.color)}:"
                f"borderw={int(clamp(style.border_px, 0, 12))}:bordercolor={color(style.border_color)}:"
                f"x={x}:y={y}:{self._span(item.start_ms, item.end_ms)}[{nxt}]"
            )
            current = nxt
        return current

    # ------------------------------------------------------------------------- audio

    def _audio_item(self, item: VideoItem | AudioItem, out: str) -> str:
        idx = self.index[item.source_id]
        speed = item.speed if item.speed is not None else 1.0
        length_ms = effective_ms(item)
        steps = [
            f"atrim=start={secs(item.source_in_ms)}:end={secs(item.source_out_ms)}",
            "asetpts=PTS-STARTPTS",
            *atempo_chain(speed),
        ]
        volume = item.volume if item.volume is not None else 1.0
        if abs(volume - 1.0) > 1e-9:
            steps.append(f"volume={num(volume, 4)}")
        fade_in = item.fade_in_ms or 0
        fade_out = item.fade_out_ms or 0
        if fade_in > 0:
            steps.append(f"afade=t=in:st=0:d={secs(fade_in)}")
        if fade_out > 0:
            steps.append(f"afade=t=out:st={secs(length_ms - fade_out)}:d={secs(fade_out)}")
        steps.append(f"aresample={AUDIO_RATE}")
        steps.append("aformat=sample_fmts=fltp:channel_layouts=stereo")
        if item.start_ms > 0:
            steps.append(f"adelay={item.start_ms}:all=1")
        return f"[{idx}:a]" + ",".join(steps) + f"[{out}]"

    def _mix(self, parts: list[str], labels: list[str]) -> str | None:
        if not labels:
            return None
        if len(labels) == 1:
            return labels[0]
        out = self.label("m")
        joined = "".join(f"[{x}]" for x in labels)
        parts.append(f"{joined}amix=inputs={len(labels)}:duration=longest:normalize=0[{out}]")
        return out

    def audio_graph(self, *, loudnorm_json: bool) -> tuple[list[str], str | None]:
        """Grafo de audio completo (mezcla, ducking, normalización). ``None`` si no hay audio."""
        parts: list[str] = []
        voices: list[str] = []
        music: list[str] = []
        for track in self.doc.timeline.tracks:
            if isinstance(track, VideoTrack):
                for vitem in track.items:
                    if self._require(vitem.source_id).has_audio:
                        label = self.label("a")
                        parts.append(self._audio_item(vitem, label))
                        voices.append(label)
            elif isinstance(track, AudioTrack):
                for aitem in track.items:
                    has_audio = self._require(aitem.source_id).has_audio
                    if has_audio or self.kinds[aitem.source_id] == "audio":
                        label = self.label("a")
                        parts.append(self._audio_item(aitem, label))
                        music.append(label)
        voice_mix = self._mix(parts, voices)
        music_mix = self._mix(parts, music)
        if voice_mix is None and music_mix is None:
            return [], None

        ducking = self.doc.audio.ducking
        if ducking is not None and ducking.enabled and voice_mix and music_mix:
            keep, side = self.label("s"), self.label("s")
            parts.append(f"[{voice_mix}]asplit=2[{keep}][{side}]")
            ducked = self.label("d")
            parts.append(
                f"[{music_mix}][{side}]sidechaincompress=threshold={DUCK_THRESHOLD}:ratio={DUCK_RATIO}:"
                f"attack={DUCK_ATTACK_MS}:release={DUCK_RELEASE_MS}[{ducked}]"
            )
            mixed = self._mix(parts, [keep, ducked])
        else:
            mixed = self._mix(parts, [x for x in (voice_mix, music_mix) if x])
        assert mixed is not None  # noqa: S101

        tail = [
            f"apad=whole_dur={secs(self.duration)}",
            f"atrim=0:{secs(self.duration)}",
            "asetpts=PTS-STARTPTS",
        ]
        norm = self.doc.audio.normalize
        if norm.enabled:
            tail.append(self._loudnorm(loudnorm_json))
        parts.append(f"[{mixed}]" + ",".join(tail) + "[aout]")
        return parts, "aout"

    def _loudnorm(self, as_json: bool) -> str:
        norm = self.doc.audio.normalize
        target = (
            norm.target_lufs if norm.target_lufs is not None else self.preset.loudness_target_lufs
        )
        peak = norm.true_peak_db if norm.true_peak_db is not None else self.preset.true_peak_db
        base = f"loudnorm=I={num(target, 2)}:TP={num(peak, 2)}:LRA=11"
        if as_json:
            return base + ":print_format=json"
        m = self.inp.loudnorm
        if m is None:  # sin primera pasada: normalización dinámica de una sola pasada
            return base
        return (
            f"{base}:measured_I={num(m.input_i, 2)}:measured_TP={num(m.input_tp, 2)}:"
            f"measured_LRA={num(m.input_lra, 2)}:measured_thresh={num(m.input_thresh, 2)}:"
            f"offset={num(m.target_offset, 2)}:linear=true"
        )


def _common(builder: _Builder, *, loglevel: str = "error") -> list[str]:
    # `loudnorm` imprime su medición con nivel `info`: solo la pasada de medición lo necesita
    safe = [
        "info" if a == "error" and i and SAFE_ARGS[i - 1] == "-loglevel" else a
        for i, a in enumerate(SAFE_ARGS)
    ]
    return [
        builder.inp.ffmpeg,
        "-y",
        "-nostdin",
        *(safe if loglevel == "info" else SAFE_ARGS),
        "-threads",
        str(builder.inp.threads),
        *builder.input_args(),
    ]


def parse_loudnorm(output: str) -> LoudnormMeasure:
    """Lee el bloque JSON que imprime la pasada de medición de ``loudnorm``."""
    start, end = output.rfind("{"), output.rfind("}")
    if start < 0 or end < start:
        raise RenderPlanError("la medición de volumen no devolvió datos")
    try:
        data = json.loads(output[start : end + 1])
        values = {
            k: float(data[k])
            for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
        }
    except (KeyError, ValueError, TypeError):
        raise RenderPlanError("la medición de volumen es inválida") from None
    if not all(-120 < v < 120 for v in values.values()):  # -inf (silencio) u otros valores absurdos
        raise RenderPlanError("la medición de volumen no es finita (audio silencioso)")
    return LoudnormMeasure(**values)


def build_render_plan(doc: EditProject, preset: Preset, inputs: RenderInputs) -> RenderPlan:
    """Convierte un documento de edición válido en el plan de render. No ejecuta nada."""
    b = _Builder(doc, preset, inputs)
    video, vlabel = b.video_graph()
    audio, alabel = b.audio_graph(loudnorm_json=False)
    if not audio:  # sin audio en ningún elemento: pista silenciosa para que el MP4 sea estándar
        audio = [f"anullsrc=r={AUDIO_RATE}:cl=stereo:d={secs(b.duration)}[aout]"]
        alabel = "aout"
    graph = ";".join([*video, *audio])
    maxrate: list[str] = []
    if preset.max_bitrate_kbps:
        maxrate = [
            "-maxrate",
            f"{preset.max_bitrate_kbps}k",
            "-bufsize",
            f"{preset.max_bitrate_kbps * 2}k",
        ]
    render_args = [
        *_common(b),
        "-filter_complex",
        graph,
        "-map",
        f"[{vlabel}]",
        "-map",
        f"[{alabel}]",
        "-t",
        secs(b.duration),
        "-r",
        str(preset.fps),
        "-c:v",
        preset.video_codec,
        "-preset",
        "fast",
        "-crf",
        str(preset.crf),
        *maxrate,
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-c:a",
        preset.audio_codec,
        "-b:a",
        f"{preset.audio_bitrate_kbps}k",
        "-ar",
        str(AUDIO_RATE),
        "-ac",
        "2",
        "-progress",
        "pipe:1",
        "-nostats",
        safe_path(inputs.output),
    ]

    measure_args: list[str] | None = None
    has_audio = alabel == "aout" and any("anullsrc" not in p for p in audio)
    if has_audio and doc.audio.normalize.enabled:
        m_parts, m_label = b.audio_graph(loudnorm_json=True)
        if m_label is not None:
            measure_args = [
                *_common(b, loglevel="info"),
                "-filter_complex",
                ";".join(m_parts),
                "-map",
                f"[{m_label}]",
                "-vn",
                "-f",
                "null",
                "-",
            ]
    digest = hashlib.sha256(
        json.dumps(
            {"measure": measure_args, "render": render_args, "aux": b.aux},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return RenderPlan(measure_args, render_args, dict(b.aux), digest, b.duration, has_audio)
