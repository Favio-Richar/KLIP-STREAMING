"use client";

import { effectiveMs } from "@klick/video-project-schema";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useRef } from "react";
import { isMediaItem, type Doc, type Track } from "@/lib/editor/ops";
import type { SourceMedia } from "@/lib/editor/sources";
import { useEditor } from "@/lib/editor/store";

type MediaItem = Extract<Track, { kind: "video" | "audio" }>["items"][number];
type TextItem = Extract<Track, { kind: "text" }>["items"][number];

/** Elemento de medios activo en `t` para cada pista de video/audio. */
function activeMedia(doc: Doc, t: number): Array<{ kind: "video" | "audio"; item: MediaItem }> {
  const out: Array<{ kind: "video" | "audio"; item: MediaItem }> = [];
  for (const track of doc.timeline.tracks) {
    if (!isMediaItem(track)) continue;
    for (const item of track.items as MediaItem[]) {
      if (t >= item.start_ms && t < item.start_ms + effectiveMs(item)) {
        out.push({ kind: track.kind, item });
      }
    }
  }
  return out;
}

/** Ganancia del elemento en `t` aplicando volumen y fundidos (igual que el render). */
function gainAt(item: MediaItem, t: number): number {
  const local = t - item.start_ms;
  const length = effectiveMs(item);
  const fadeIn = item.fade_in_ms ?? 0;
  const fadeOut = item.fade_out_ms ?? 0;
  let g = item.volume ?? 1;
  if (fadeIn > 0 && local < fadeIn) g *= Math.max(0, local / fadeIn);
  if (fadeOut > 0 && length - local < fadeOut) g *= Math.max(0, (length - local) / fadeOut);
  return Math.min(1, Math.max(0, g));
}

const SYNC_TOLERANCE_S = 0.3;

export function Preview({ doc, media }: { doc: Doc; media: Map<string, SourceMedia> }) {
  const t = useTranslations("editor.preview");
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const elements = useRef(new Map<string, HTMLMediaElement>());
  const docRef = useRef(doc);
  useEffect(() => {
    docRef.current = doc;
  }, [doc]);
  const playing = useEditor((s) => s.playing);
  const playhead = useEditor((s) => s.playheadMs);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    const d = docRef.current;
    const now = useEditor.getState().playheadMs;
    const W = canvas.width;
    const H = canvas.height;
    const bg =
      d.layout.mode === "fit"
        ? (d.layout.fit?.background as { color?: string } | undefined)
        : undefined;
    ctx.fillStyle = bg?.color ?? d.canvas.background ?? "#000000";
    ctx.fillRect(0, 0, W, H);

    const video = activeMedia(d, now).find((a) => a.kind === "video");
    if (video) {
      const el = elements.current.get(video.item.source_id);
      if (el instanceof HTMLVideoElement && el.readyState >= 2 && el.videoWidth > 0) {
        const fit = d.layout.mode === "fit";
        const scale = fit
          ? Math.min(W / el.videoWidth, H / el.videoHeight)
          : Math.max(W / el.videoWidth, H / el.videoHeight);
        const w = el.videoWidth * scale;
        const h = el.videoHeight * scale;
        ctx.drawImage(el, (W - w) / 2, (H - h) / 2, w, h);
      }
    }

    // texto: capa simple (los estilos de plantilla llegan con F1-11/F1-12)
    for (const track of d.timeline.tracks) {
      if (track.kind !== "text") continue;
      for (const item of track.items as TextItem[]) {
        if (now < item.start_ms || now >= item.end_ms) continue;
        const size = Math.round(H * 0.045);
        ctx.font = `700 ${size}px system-ui, sans-serif`;
        ctx.textAlign = item.align ?? "center";
        ctx.textBaseline = "middle";
        ctx.lineWidth = Math.max(2, size / 8);
        ctx.strokeStyle = "#000000";
        ctx.fillStyle = "#ffffff";
        const x = item.position.x * W;
        const y = item.position.y * H;
        ctx.strokeText(item.text, x, y, W * 0.9);
        ctx.fillText(item.text, x, y, W * 0.9);
      }
    }
  }, []);

  // Sincroniza los elementos de medios con el cabezal
  const sync = useCallback((now: number, isPlaying: boolean) => {
    const d = docRef.current;
    const active = activeMedia(d, now);
    const used = new Set<string>();
    for (const { kind, item } of active) {
      const el = elements.current.get(item.source_id);
      if (!el) continue;
      // si el mismo archivo suena por dos pistas, manda la de video
      if (used.has(item.source_id) && kind === "audio") continue;
      used.add(item.source_id);
      const speed = item.speed ?? 1;
      const want = (item.source_in_ms + (now - item.start_ms) * speed) / 1000;
      if (el.playbackRate !== speed) el.playbackRate = speed;
      if (!isPlaying || Math.abs(el.currentTime - want) > SYNC_TOLERANCE_S) el.currentTime = want;
      el.volume = gainAt(item, now);
      if (isPlaying && el.paused) void el.play().catch(() => undefined);
      if (!isPlaying && !el.paused) el.pause();
    }
    for (const [id, el] of elements.current) {
      if (!used.has(id) && !el.paused) el.pause();
    }
  }, []);

  // Cabezal movido a mano / pausado: posiciona y redibuja
  useEffect(() => {
    if (playing) return;
    sync(playhead, false);
    draw();
  }, [playhead, playing, doc, sync, draw, media]);

  // Reproducción: reloj de pared; los medios se corrigen si se desvían
  useEffect(() => {
    if (!playing) return;
    const st = useEditor.getState();
    const end = docRef.current.timeline.duration_ms;
    let from = st.playheadMs >= end ? 0 : st.playheadMs;
    const wallStart = performance.now();
    let raf = 0;
    const tick = () => {
      const now = Math.min(end, from + (performance.now() - wallStart));
      useEditor.getState().seek(now);
      sync(now, true);
      draw();
      if (now >= end) {
        useEditor.getState().setPlaying(false);
        return;
      }
      raf = requestAnimationFrame(tick);
    };
    if (from !== st.playheadMs) {
      st.seek(from);
      from = 0;
    }
    raf = requestAnimationFrame(tick);
    const els = elements.current;
    return () => {
      cancelAnimationFrame(raf);
      for (const el of els.values()) el.pause();
    };
  }, [playing, sync, draw]);

  const onSeeked = useCallback(() => draw(), [draw]);

  const aspect = `${doc.canvas.width} / ${doc.canvas.height}`;
  const scale = 0.5;
  const sources = [...media.values()].filter((m) => m.url && m.kind !== "image");

  return (
    <div className="flex min-h-0 flex-1 items-center justify-center bg-bg-0 p-3">
      <canvas
        ref={canvasRef}
        width={Math.round(doc.canvas.width * scale)}
        height={Math.round(doc.canvas.height * scale)}
        role="img"
        aria-label={t("canvas")}
        className="max-h-full max-w-full rounded-control border border-border-1 bg-black"
        style={{ aspectRatio: aspect, height: "100%" }}
      />
      {sources.map((m) =>
        m.kind === "video" ? (
          <video
            key={m.id}
            ref={(el) => {
              if (el) elements.current.set(m.id, el);
              else elements.current.delete(m.id);
            }}
            src={m.url ?? undefined}
            preload="auto"
            playsInline
            className="hidden"
            onSeeked={onSeeked}
            onLoadedData={onSeeked}
          />
        ) : (
          <audio
            key={m.id}
            ref={(el) => {
              if (el) elements.current.set(m.id, el);
              else elements.current.delete(m.id);
            }}
            src={m.url ?? undefined}
            preload="auto"
            className="hidden"
          />
        ),
      )}
    </div>
  );
}
