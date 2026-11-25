"use client";

import { Button, EmptyState, Skeleton, toast } from "@klick/ui";
import { ImageIcon, Plus, Subtitles, Trash2, Type } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { createCaptionTrack } from "@/lib/clips";
import {
  cueAt,
  insertCue,
  removeCue,
  updateCue,
  validateCues,
  type Cue,
  type CueIssue,
} from "@/lib/editor/captions";
import { useCaptions } from "@/lib/editor/captions-store";
import { addImageLayer, addTextLayer, setCaptionsTrack, type Doc } from "@/lib/editor/ops";
import { uniqueId } from "@/lib/editor/sources";
import { useEditor } from "@/lib/editor/store";
import { formatDuration } from "@/lib/format";
import { useAssetList } from "@/lib/library";

type Tab = "text" | "images" | "captions";

export function SidePanel({
  clipId,
  orgId,
  doc,
  canWrite,
}: {
  clipId: string;
  orgId: string;
  doc: Doc;
  canWrite: boolean;
}) {
  const t = useTranslations("editor.side");
  const [tab, setTab] = useState<Tab>("text");
  const tabs: Array<{ id: Tab; label: string; icon: typeof Type }> = [
    { id: "text", label: t("text"), icon: Type },
    { id: "images", label: t("images"), icon: ImageIcon },
    { id: "captions", label: t("captions"), icon: Subtitles },
  ];
  return (
    <aside
      aria-label={t("tabs")}
      className="flex w-72 shrink-0 flex-col border-r border-border-1 bg-bg-1"
    >
      <div role="tablist" aria-label={t("tabs")} className="flex border-b border-border-1">
        {tabs.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            role="tab"
            id={`tab-${id}`}
            aria-selected={tab === id}
            aria-controls={`panel-${id}`}
            type="button"
            onClick={() => setTab(id)}
            className={[
              "flex h-9 flex-1 items-center justify-center gap-1.5 border-b-2 text-sm",
              tab === id
                ? "border-accent text-text-1"
                : "border-transparent text-text-2 hover:bg-bg-3",
            ].join(" ")}
          >
            <Icon className="size-3.5" aria-hidden="true" />
            {label}
          </button>
        ))}
      </div>
      <div
        role="tabpanel"
        id={`panel-${tab}`}
        aria-labelledby={`tab-${tab}`}
        className="min-h-0 flex-1 overflow-y-auto p-3"
      >
        {tab === "text" && <TextTab doc={doc} canWrite={canWrite} />}
        {tab === "images" && <ImagesTab orgId={orgId} canWrite={canWrite} />}
        {tab === "captions" && <CaptionsTab clipId={clipId} doc={doc} canWrite={canWrite} />}
      </div>
    </aside>
  );
}

// ------------------------------------------------------------------------------------ texto

function TextTab({ doc, canWrite }: { doc: Doc; canWrite: boolean }) {
  const t = useTranslations("editor.side");
  const items = doc.timeline.tracks.flatMap((track) => (track.kind === "text" ? track.items : []));
  const selectedId = useEditor((s) => s.selectedId);
  return (
    <div className="flex flex-col gap-3">
      <Button
        icon={<Plus className="size-3.5" />}
        disabled={!canWrite}
        onClick={() => {
          const st = useEditor.getState();
          let created = "";
          st.apply((d) => {
            const r = addTextLayer(d, st.playheadMs, t("newText"), (p) => uniqueId(d, p));
            created = r.id;
            return r.doc;
          });
          st.sealHistory();
          st.select(created);
        }}
      >
        {t("addText")}
      </Button>
      {items.length === 0 ? (
        <p className="text-sm text-text-3">{t("noText")}</p>
      ) : (
        <ul className="flex flex-col gap-1">
          {items.map((item) => (
            <li key={item.id}>
              <button
                type="button"
                aria-pressed={item.id === selectedId}
                onClick={() => {
                  useEditor.getState().select(item.id);
                  useEditor.getState().seek(item.start_ms);
                }}
                className={[
                  "flex w-full items-center justify-between gap-2 rounded-control border px-2 py-1.5 text-left text-sm",
                  item.id === selectedId
                    ? "border-accent bg-bg-3"
                    : "border-border-1 hover:bg-bg-3",
                ].join(" ")}
              >
                <span className="truncate">{item.text}</span>
                <span className="shrink-0 text-xs text-text-3">
                  {formatDuration(item.start_ms)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------------- imágenes

function ImagesTab({ orgId, canWrite }: { orgId: string; canWrite: boolean }) {
  const t = useTranslations("editor.side");
  const list = useAssetList(orgId, { q: "", kind: "image", status: "ready", tagIds: [] });
  const items = list.data?.pages.flatMap((p) => p.items) ?? [];
  if (list.isPending) return <Skeleton className="h-20" aria-busy="true" />;
  if (items.length === 0) {
    return <EmptyState icon={ImageIcon} title={t("noImages")} description={t("noImagesBody")} />;
  }
  return (
    <ul className="grid grid-cols-2 gap-2">
      {items.map((asset) => (
        <li key={asset.id}>
          <button
            type="button"
            disabled={!canWrite}
            title={t("addImage", { name: asset.title })}
            aria-label={t("addImage", { name: asset.title })}
            onClick={() => {
              const st = useEditor.getState();
              let created = "";
              st.apply((d) => {
                const r = addImageLayer(d, asset.id, st.playheadMs, (p) => uniqueId(d, p));
                created = r.id;
                return r.doc;
              });
              st.sealHistory();
              st.select(created);
            }}
            className="flex w-full flex-col gap-1 rounded-control border border-border-1 p-1 text-left hover:bg-bg-3 disabled:opacity-50"
          >
            {asset.thumbnail_url ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={asset.thumbnail_url} alt="" className="aspect-video w-full object-cover" />
            ) : (
              <div className="aspect-video w-full bg-bg-3" />
            )}
            <span className="truncate text-xs">{asset.title}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

// ------------------------------------------------------------------------------- subtítulos

const ISSUE_KEY: Record<CueIssue["code"], string> = {
  empty_text: "issue.emptyText",
  text_too_long: "issue.textTooLong",
  invalid_range: "issue.invalidRange",
  cues_overlap: "issue.overlap",
  too_many_lines: "issue.tooManyLines",
  line_too_long: "issue.lineTooLong",
};

function CaptionsTab({ clipId, doc, canWrite }: { clipId: string; doc: Doc; canWrite: boolean }) {
  const t = useTranslations("editor.side");
  const trackId = useCaptions((s) => s.trackId);
  const cues = useCaptions((s) => s.cues);
  const save = useCaptions((s) => s.save);
  const playhead = useEditor((s) => s.playheadMs);
  const [creating, setCreating] = useState(false);
  const issues = validateCues(cues);
  const active = cueAt(cues, playhead);

  const create = async () => {
    setCreating(true);
    try {
      const track = await createCaptionTrack(clipId);
      useCaptions.getState().attach({ id: track.id, revision: track.revision, cues: [] });
      useEditor.getState().apply((d) => setCaptionsTrack(d, track.id));
      useEditor.getState().sealHistory();
    } catch {
      toast.error(t("createFailed"));
    } finally {
      setCreating(false);
    }
  };

  if (!trackId) {
    return (
      <EmptyState
        icon={Subtitles}
        title={t("noCaptions")}
        description={t("noCaptionsBody")}
        action={
          <Button
            variant="primary"
            loading={creating}
            disabled={!canWrite}
            onClick={() => void create()}
          >
            {t("createCaptions")}
          </Button>
        }
      />
    );
  }

  const add = () => {
    const r = insertCue(cues, playhead, doc.timeline.duration_ms);
    if (!r) {
      toast.error(t("noRoom"));
      return;
    }
    useCaptions.getState().edit(() => r.cues);
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-2">
        <Button icon={<Plus className="size-3.5" />} disabled={!canWrite} onClick={add}>
          {t("addCue")}
        </Button>
        <span data-testid="captions-state" className="text-xs text-text-2">
          {t(`state.${save}`)}
        </span>
      </div>
      {save === "conflict" && <p className="text-sm text-danger">{t("conflict")}</p>}
      {cues.length === 0 ? (
        <p className="text-sm text-text-3">{t("noCues")}</p>
      ) : (
        <ol className="flex flex-col gap-2" aria-label={t("cueList")}>
          {cues.map((cue, i) => (
            <CueRow
              key={i}
              index={i}
              cue={cue}
              active={active === cue}
              problems={issues.filter((x) => x.index === i)}
              beyondEnd={cue.end_ms > doc.timeline.duration_ms}
              canWrite={canWrite}
            />
          ))}
        </ol>
      )}
    </div>
  );
}

function CueRow({
  index,
  cue,
  active,
  problems,
  beyondEnd,
  canWrite,
}: {
  index: number;
  cue: Cue;
  active: boolean;
  problems: CueIssue[];
  beyondEnd: boolean;
  canWrite: boolean;
}) {
  const t = useTranslations("editor.side");
  const edit = (patch: Partial<Cue>) =>
    useCaptions.getState().edit((all) => updateCue(all, index, patch));
  return (
    <li
      data-testid={`cue-${index}`}
      className={[
        "flex flex-col gap-1.5 rounded-control border p-2",
        active ? "border-accent" : "border-border-1",
      ].join(" ")}
    >
      <div className="flex items-center gap-1.5">
        <SecondsInput
          label={t("cueStart", { n: index + 1 })}
          ms={cue.start_ms}
          disabled={!canWrite}
          onCommit={(ms) => edit({ start_ms: ms })}
        />
        <span aria-hidden="true">–</span>
        <SecondsInput
          label={t("cueEnd", { n: index + 1 })}
          ms={cue.end_ms}
          disabled={!canWrite}
          onCommit={(ms) => edit({ end_ms: ms })}
        />
        <button
          type="button"
          aria-label={t("seekCue", { n: index + 1 })}
          title={t("seekCue", { n: index + 1 })}
          className="ml-auto rounded-control px-1.5 text-xs text-accent-text underline"
          onClick={() => useEditor.getState().seek(cue.start_ms)}
        >
          {formatDuration(cue.start_ms)}
        </button>
        <button
          type="button"
          aria-label={t("removeCue", { n: index + 1 })}
          title={t("removeCue", { n: index + 1 })}
          disabled={!canWrite}
          className="rounded-control p-1 text-text-2 hover:bg-bg-3 disabled:opacity-50"
          onClick={() => useCaptions.getState().edit((all) => removeCue(all, index))}
        >
          <Trash2 className="size-3.5" aria-hidden="true" />
        </button>
      </div>
      <textarea
        aria-label={t("cueText", { n: index + 1 })}
        rows={2}
        maxLength={400}
        disabled={!canWrite}
        value={cue.text}
        onChange={(e) => edit({ text: e.target.value })}
        className="w-full rounded-control border border-control bg-bg-2 p-1.5 text-sm text-text-1"
      />
      {beyondEnd && <p className="text-xs text-warning">{t("beyondEnd")}</p>}
      {problems.map((p) => (
        <p key={p.code} role="alert" className="text-xs text-danger">
          {t(ISSUE_KEY[p.code])}
        </p>
      ))}
    </li>
  );
}

/** Segundos con decimales; confirma al salir del campo o con Enter (no corta lo que se escribe). */
function SecondsInput({
  label,
  ms,
  disabled,
  onCommit,
}: {
  label: string;
  ms: number;
  disabled: boolean;
  onCommit: (ms: number) => void;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const shown = draft ?? (ms / 1000).toFixed(2);
  const commit = () => {
    if (draft === null) return;
    const value = Number(draft.replace(",", "."));
    setDraft(null);
    if (Number.isFinite(value) && value >= 0) onCommit(Math.round(value * 1000));
  };
  return (
    <input
      aria-label={label}
      inputMode="decimal"
      disabled={disabled}
      value={shown}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") commit();
      }}
      className="h-7 w-16 rounded-control border border-control bg-bg-2 px-1.5 text-sm text-text-1"
    />
  );
}
