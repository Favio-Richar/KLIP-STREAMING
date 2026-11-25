"use client";

import { Button, Input, Select } from "@klick/ui";
import { Scissors, Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { formatDuration } from "@/lib/format";
import {
  deleteItem,
  find,
  isMediaItem,
  patchItem,
  span,
  splitItem,
  type Doc,
} from "@/lib/editor/ops";
import { uniqueId, type SourceMedia } from "@/lib/editor/sources";
import { useEditor } from "@/lib/editor/store";

const SPEEDS = [0.25, 0.5, 0.75, 1, 1.25, 1.5, 2, 3, 4];

export function Inspector({
  doc,
  media,
  canWrite,
}: {
  doc: Doc;
  media: Map<string, SourceMedia>;
  canWrite: boolean;
}) {
  const t = useTranslations("editor.inspector");
  const selectedId = useEditor((s) => s.selectedId);
  const playhead = useEditor((s) => s.playheadMs);
  const loc = selectedId ? find(doc, selectedId) : null;

  if (!loc) {
    return (
      <aside className="flex w-72 shrink-0 flex-col gap-3 border-l border-border-1 bg-bg-1 p-3 text-sm">
        <h2 className="text-base font-medium">{t("clip")}</h2>
        <dl className="grid grid-cols-2 gap-y-1 text-text-2">
          <dt>{t("duration")}</dt>
          <dd className="text-right text-text-1">{formatDuration(doc.timeline.duration_ms)}</dd>
          <dt>{t("canvas")}</dt>
          <dd className="text-right text-text-1">
            {doc.canvas.width}×{doc.canvas.height} · {doc.canvas.fps} fps
          </dd>
          <dt>{t("layout")}</dt>
          <dd className="text-right text-text-1">{t(`layoutMode.${doc.layout.mode}`)}</dd>
        </dl>
        <p className="text-text-3">{t("selectHint")}</p>
      </aside>
    );
  }

  const { item, track } = loc;
  const id = (item as { id: string }).id;
  const s = span(track, item);
  const patch = (values: Record<string, unknown>, key: string | null = null) =>
    useEditor.getState().apply((d) => patchItem(d, id, values), key);
  const media0 = isMediaItem(track)
    ? (item as {
        speed?: number;
        volume?: number;
        fade_in_ms?: number;
        fade_out_ms?: number;
        source_id: string;
      })
    : null;
  const inside = playhead > s.start && playhead < s.end;

  return (
    <aside className="flex w-72 shrink-0 flex-col gap-3 overflow-y-auto border-l border-border-1 bg-bg-1 p-3 text-sm">
      <h2 className="text-base font-medium">{t(`kind.${track.kind}`)}</h2>
      <dl className="grid grid-cols-2 gap-y-1 text-text-2">
        <dt>{t("start")}</dt>
        <dd className="text-right text-text-1">{formatDuration(s.start)}</dd>
        <dt>{t("length")}</dt>
        <dd className="text-right text-text-1">{formatDuration(s.end - s.start)}</dd>
        {media0 && (
          <>
            <dt>{t("source")}</dt>
            <dd className="truncate text-right text-text-1">
              {media.get(media0.source_id)?.title}
            </dd>
          </>
        )}
      </dl>

      {media0 && (
        <>
          <Row label={t("volume", { value: Math.round((media0.volume ?? 1) * 100) })}>
            <input
              type="range"
              min={0}
              max={200}
              step={5}
              disabled={!canWrite}
              value={Math.round((media0.volume ?? 1) * 100)}
              aria-label={t("volumeLabel")}
              onChange={(e) => patch({ volume: Number(e.target.value) / 100 }, `volume:${id}`)}
              onPointerUp={() => useEditor.getState().sealHistory()}
              className="w-full accent-[var(--color-accent)]"
            />
          </Row>
          <Row label={t("speed")}>
            <Select
              disabled={!canWrite}
              value={String(media0.speed ?? 1)}
              onChange={(e) => patch({ speed: Number(e.target.value) })}
            >
              {SPEEDS.map((v) => (
                <option key={v} value={v}>
                  {v}×
                </option>
              ))}
            </Select>
          </Row>
          <div className="grid grid-cols-2 gap-2">
            <Row label={t("fadeIn")}>
              <Input
                type="number"
                min={0}
                step={100}
                disabled={!canWrite}
                value={media0.fade_in_ms ?? 0}
                onChange={(e) =>
                  patch({ fade_in_ms: Math.max(0, Number(e.target.value) || 0) }, `fi:${id}`)
                }
              />
            </Row>
            <Row label={t("fadeOut")}>
              <Input
                type="number"
                min={0}
                step={100}
                disabled={!canWrite}
                value={media0.fade_out_ms ?? 0}
                onChange={(e) =>
                  patch({ fade_out_ms: Math.max(0, Number(e.target.value) || 0) }, `fo:${id}`)
                }
              />
            </Row>
          </div>
        </>
      )}

      {track.kind === "text" && (
        <>
          <Row label={t("text")}>
            <textarea
              disabled={!canWrite}
              maxLength={500}
              rows={3}
              value={(item as { text: string }).text}
              onChange={(e) => e.target.value && patch({ text: e.target.value }, `text:${id}`)}
              onBlur={() => useEditor.getState().sealHistory()}
              className="w-full rounded-control border border-control bg-bg-2 p-2 text-text-1"
            />
          </Row>
          <Row label={t("align")}>
            <Select
              disabled={!canWrite}
              value={(item as { align?: string }).align ?? "center"}
              onChange={(e) => patch({ align: e.target.value })}
            >
              {(["left", "center", "right"] as const).map((a) => (
                <option key={a} value={a}>
                  {t(`alignment.${a}`)}
                </option>
              ))}
            </Select>
          </Row>
        </>
      )}

      <div className="mt-auto flex gap-2">
        <Button
          size="dense"
          icon={<Scissors className="size-3.5" />}
          disabled={!canWrite || !inside}
          onClick={() => {
            useEditor.getState().apply((d) => splitItem(d, id, playhead, () => uniqueId(d)).doc);
            useEditor.getState().sealHistory();
          }}
        >
          {t("split")}
        </Button>
        <Button
          size="dense"
          variant="danger"
          icon={<Trash2 className="size-3.5" />}
          disabled={!canWrite}
          onClick={() => {
            useEditor.getState().apply((d) => deleteItem(d, id));
            useEditor.getState().sealHistory();
            useEditor.getState().select(null);
          }}
        >
          {t("delete")}
        </Button>
      </div>
    </aside>
  );
}

/** Etiqueta + control: la etiqueta envuelve al control, así queda asociada sin ids. */
function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5 text-sm font-medium text-text-1">
      {label}
      {children}
    </label>
  );
}
