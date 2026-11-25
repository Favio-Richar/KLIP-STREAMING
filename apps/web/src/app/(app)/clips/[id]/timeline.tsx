"use client";

import { Film, Image as ImageIcon, Music, Type } from "lucide-react";
import { useTranslations } from "next-intl";
import { useCallback, useRef, type PointerEvent as ReactPointerEvent } from "react";
import { formatDuration } from "@/lib/format";
import {
  find,
  isMediaItem,
  moveItem,
  snapBlock,
  snapPoints,
  snap,
  span,
  trimEnd,
  trimStart,
  type Doc,
  type Track,
} from "@/lib/editor/ops";
import type { SourceMedia } from "@/lib/editor/sources";
import { useEditor } from "@/lib/editor/store";

const LANE_H = 44;
const GUTTER = 96;
const HANDLE = 7;
const SNAP_PX = 8;
const PAD_MS = 4000;

type Drag =
  | { mode: "move"; id: string; originX: number; originStart: number; length: number }
  | { mode: "trimL" | "trimR"; id: string };

const ICON = { video: Film, audio: Music, text: Type, image: ImageIcon } as const;

/** Intervalo de marcas de la regla según el zoom (≥ 70 px entre marcas con etiqueta). */
export function tickInterval(zoom: number): number {
  const steps = [
    100, 250, 500, 1000, 2000, 5000, 10000, 15000, 30000, 60000, 120000, 300000, 600000,
  ];
  return steps.find((s) => s * zoom >= 70) ?? 600000;
}

export function Timeline({ doc, media }: { doc: Doc; media: Map<string, SourceMedia> }) {
  const t = useTranslations("editor.timeline");
  const zoom = useEditor((s) => s.zoom);
  const playhead = useEditor((s) => s.playheadMs);
  const selectedId = useEditor((s) => s.selectedId);
  const markers = useEditor((s) => s.markers);
  const inMs = useEditor((s) => s.inMs);
  const outMs = useEditor((s) => s.outMs);
  const scroller = useRef<HTMLDivElement | null>(null);
  const drag = useRef<Drag | null>(null);

  const total = doc.timeline.duration_ms + PAD_MS;
  const width = Math.max(total * zoom, 600);
  const msAt = (clientX: number) => {
    const box = scroller.current?.getBoundingClientRect();
    if (!box || !scroller.current) return 0;
    return Math.max(0, (clientX - box.left + scroller.current.scrollLeft - GUTTER) / zoom);
  };

  const seekFromPointer = (e: ReactPointerEvent<HTMLElement>) => {
    useEditor.getState().setPlaying(false);
    useEditor.getState().seek(msAt(e.clientX));
  };

  const startDrag = (e: ReactPointerEvent<HTMLElement>, next: Drag, id: string) => {
    e.stopPropagation();
    e.preventDefault();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    drag.current = next;
    useEditor.getState().select(id);
    useEditor.getState().sealHistory();
  };

  const onDragMove = useCallback(
    (e: ReactPointerEvent<HTMLElement>) => {
      const d = drag.current;
      if (!d) return;
      const st = useEditor.getState();
      const threshold = st.snapping ? SNAP_PX / st.zoom : 0;
      const ms = msAt(e.clientX);
      const key = `drag:${d.id}`;
      if (d.mode === "move") {
        const delta = (e.clientX - d.originX) / st.zoom;
        st.apply((doc0) => {
          const points = snapPoints(doc0, d.id, st.playheadMs);
          const wanted = d.originStart + delta;
          const start = st.snapping ? snapBlock(wanted, d.length, points, threshold) : wanted;
          return moveItem(doc0, d.id, start);
        }, key);
      } else {
        st.apply((doc0) => {
          const points = snapPoints(doc0, d.id, st.playheadMs);
          const at = st.snapping ? snap(ms, points, threshold) : ms;
          const loc = find(doc0, d.id);
          const limits =
            loc && isMediaItem(loc.track)
              ? {
                  sourceDurationMs:
                    media.get((loc.item as { source_id: string }).source_id)?.durationMs ??
                    undefined,
                }
              : {};
          return d.mode === "trimL"
            ? trimStart(doc0, d.id, at, limits)
            : trimEnd(doc0, d.id, at, limits);
        }, key);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [media],
  );

  const endDrag = () => {
    if (!drag.current) return;
    drag.current = null;
    useEditor.getState().sealHistory();
  };

  const interval = tickInterval(zoom);
  const ticks: number[] = [];
  for (let ms = 0; ms <= total; ms += interval) ticks.push(ms);

  return (
    <div
      className="flex min-h-0 flex-col border-t border-border-1 bg-bg-1"
      role="region"
      aria-label={t("region")}
    >
      <div
        ref={scroller}
        className="relative min-h-0 flex-1 overflow-auto"
        onWheel={(e) => {
          if (e.ctrlKey || e.metaKey) {
            e.preventDefault();
            const st = useEditor.getState();
            st.setZoom(st.zoom * (e.deltaY < 0 ? 1.15 : 1 / 1.15));
          }
        }}
      >
        <div style={{ width: width + GUTTER, minWidth: "100%" }} className="relative">
          {/* regla */}
          <div
            className="sticky top-0 z-20 flex h-7 cursor-pointer border-b border-border-1 bg-bg-2"
            onPointerDown={seekFromPointer}
            onPointerMove={(e) => e.buttons === 1 && seekFromPointer(e)}
            data-testid="timeline-ruler"
          >
            <div
              style={{ width: GUTTER }}
              className="sticky left-0 z-10 shrink-0 border-r border-border-1 bg-bg-2"
            />
            <div className="relative flex-1">
              {ticks.map((ms) => (
                <div
                  key={ms}
                  style={{ left: ms * zoom }}
                  className="absolute bottom-0 top-0 border-l border-border-2 pl-1 text-xs text-text-3"
                >
                  {formatTick(ms)}
                </div>
              ))}
              {inMs !== null && outMs !== null && outMs > inMs && (
                <div
                  style={{ left: inMs * zoom, width: (outMs - inMs) * zoom }}
                  className="absolute bottom-0 h-1 bg-accent"
                  aria-label={t("range")}
                />
              )}
              {markers.map((ms) => (
                <button
                  key={ms}
                  type="button"
                  title={`${t("marker")} ${formatDuration(ms)}`}
                  aria-label={`${t("marker")} ${formatDuration(ms)}`}
                  style={{ left: ms * zoom - 4 }}
                  className="absolute bottom-0 size-2 rotate-45 bg-warning"
                  onPointerDown={(e) => {
                    e.stopPropagation();
                    useEditor.getState().seek(ms);
                  }}
                />
              ))}
            </div>
          </div>

          {/* pistas */}
          {doc.timeline.tracks.map((track) => (
            <Lane
              key={track.id}
              track={track}
              selectedId={selectedId}
              media={media}
              onSeek={seekFromPointer}
              onBlockDown={(e, item) => {
                const s = span(track, item);
                startDrag(
                  e,
                  {
                    mode: "move",
                    id: (item as { id: string }).id,
                    originX: e.clientX,
                    originStart: s.start,
                    length: s.end - s.start,
                  },
                  (item as { id: string }).id,
                );
              }}
              onHandleDown={(e, item, side) =>
                startDrag(
                  e,
                  { mode: side, id: (item as { id: string }).id },
                  (item as { id: string }).id,
                )
              }
              onMove={onDragMove}
              onUp={endDrag}
              zoom={zoom}
            />
          ))}

          {/* cabezal */}
          <div
            className="pointer-events-none absolute bottom-0 top-0 z-30 w-px bg-accent"
            style={{ left: GUTTER + playhead * zoom }}
            data-testid="playhead"
          >
            <div className="absolute -left-1.5 top-0 size-3 rotate-45 bg-accent" />
          </div>
        </div>
      </div>
    </div>
  );
}

function formatTick(ms: number): string {
  if (ms < 1000) return `${ms} ms`;
  return formatDuration(ms);
}

function Lane({
  track,
  selectedId,
  media,
  zoom,
  onSeek,
  onBlockDown,
  onHandleDown,
  onMove,
  onUp,
}: {
  track: Track;
  selectedId: string | null;
  media: Map<string, SourceMedia>;
  zoom: number;
  onSeek: (e: ReactPointerEvent<HTMLElement>) => void;
  onBlockDown: (e: ReactPointerEvent<HTMLElement>, item: Track["items"][number]) => void;
  onHandleDown: (
    e: ReactPointerEvent<HTMLElement>,
    item: Track["items"][number],
    side: "trimL" | "trimR",
  ) => void;
  onMove: (e: ReactPointerEvent<HTMLElement>) => void;
  onUp: () => void;
}) {
  const t = useTranslations("editor.timeline");
  const Icon = ICON[track.kind];
  return (
    <div className="flex border-b border-border-1" style={{ height: LANE_H }}>
      <div
        style={{ width: GUTTER }}
        className="sticky left-0 z-10 flex shrink-0 items-center gap-1.5 border-r border-border-1 bg-bg-2 px-2 text-xs text-text-2"
      >
        <Icon className="size-3.5" aria-hidden="true" />
        {t(`track.${track.kind}`)}
      </div>
      <div className="relative flex-1" onPointerDown={onSeek}>
        {(track.items as Array<Track["items"][number]>).map((item) => {
          const id = (item as { id: string }).id;
          const s = span(track, item);
          const selected = id === selectedId;
          const label = blockLabel(track, item, media);
          return (
            <div
              key={id}
              role="button"
              tabIndex={0}
              aria-label={`${label} ${formatDuration(s.start)}–${formatDuration(s.end)}`}
              aria-pressed={selected}
              data-testid={`block-${id}`}
              data-selected={selected || undefined}
              style={{
                left: s.start * zoom,
                width: Math.max(4, (s.end - s.start) * zoom),
                top: 4,
                height: LANE_H - 8,
              }}
              className={[
                "absolute flex cursor-grab touch-none select-none items-center overflow-hidden rounded-control border px-2 text-xs",
                BLOCK_TONE[track.kind],
                selected ? "border-accent ring-1 ring-accent" : "border-border-2",
              ].join(" ")}
              onPointerDown={(e) => onBlockDown(e, item)}
              onPointerMove={onMove}
              onPointerUp={onUp}
              onPointerCancel={onUp}
              onKeyDown={(e) => {
                if (e.key === "Enter") useEditor.getState().select(id);
              }}
            >
              <span className="truncate">{label}</span>
              {(["trimL", "trimR"] as const).map((side) => (
                <div
                  key={side}
                  data-testid={`${side}-${id}`}
                  aria-hidden="true"
                  style={{ width: HANDLE, [side === "trimL" ? "left" : "right"]: 0 }}
                  className="absolute bottom-0 top-0 cursor-ew-resize bg-border-2 hover:bg-accent"
                  onPointerDown={(e) => onHandleDown(e, item, side)}
                  onPointerMove={onMove}
                  onPointerUp={onUp}
                  onPointerCancel={onUp}
                />
              ))}
            </div>
          );
        })}
      </div>
    </div>
  );
}

const BLOCK_TONE: Record<Track["kind"], string> = {
  video: "bg-bg-3 text-text-1",
  audio: "bg-bg-2 text-text-1",
  text: "bg-bg-3 text-text-1",
  image: "bg-bg-3 text-text-1",
};

function blockLabel(
  track: Track,
  item: Track["items"][number],
  media: Map<string, SourceMedia>,
): string {
  if (track.kind === "text") return (item as { text: string }).text;
  const sourceId = (item as { source_id?: string }).source_id;
  return (sourceId && media.get(sourceId)?.title) || sourceId || track.kind;
}
