"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import {
  Badge,
  Button,
  EmptyState,
  ErrorState,
  IconButton,
  Notice,
  ProgressBar,
  Skeleton,
  toast,
  type Tone,
} from "@klick/ui";
import {
  ArrowLeft,
  Download,
  FileQuestion,
  Magnet,
  Pause,
  Play,
  Redo2,
  Scissors,
  Undo2,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ApiProblem, api, unwrap } from "@/lib/api";
import { useClip, useEditProject, type RenderJob } from "@/lib/clips";
import { canRedo, canUndo } from "@/lib/editor/history";
import { splitItem, type Doc } from "@/lib/editor/ops";
import { uniqueId, useSourceMedia } from "@/lib/editor/sources";
import { useEditor, type SaveState } from "@/lib/editor/store";
import { useAutosave } from "@/lib/editor/use-autosave";
import { useShortcuts } from "@/lib/editor/use-shortcuts";
import { can, useActiveOrg } from "@/lib/queries";
import { useErrorMessage } from "@/lib/use-error-message";
import { Inspector } from "./inspector";
import { Preview } from "./preview";
import { Timeline } from "./timeline";

const SAVE_TONE: Record<SaveState, Tone> = {
  saved: "success",
  dirty: "neutral",
  saving: "info",
  error: "danger",
  conflict: "danger",
};

export function ClipEditor({ id }: { id: string }) {
  const t = useTranslations("editor");
  const errorMessage = useErrorMessage();
  const { active, isPending: orgPending } = useActiveOrg();
  const orgId = active?.id ?? null;
  const clip = useClip(orgId, id);
  const project = useEditProject(orgId, id);

  if (orgPending || !active || clip.isPending || project.isPending) {
    return (
      <div className="flex flex-col gap-4" aria-busy="true">
        <Skeleton className="h-8 w-72" />
        <Skeleton className="h-96 w-full" />
      </div>
    );
  }
  const failure = clip.error ?? project.error;
  if (failure || !clip.data || !project.data) {
    const notFound = failure instanceof ApiProblem && failure.status === 404;
    return notFound ? (
      <EmptyState
        icon={FileQuestion}
        title={t("notFoundTitle")}
        description={t("notFoundBody")}
        action={
          <Link href="/clips" className="text-accent-text underline underline-offset-2">
            {t("back")}
          </Link>
        }
      />
    ) : (
      <ErrorState
        title={t("loadFailed")}
        description={errorMessage(failure)}
        retryLabel={t("retry")}
        onRetry={() => {
          void clip.refetch();
          void project.refetch();
        }}
      />
    );
  }
  return (
    <Loaded
      id={id}
      orgId={active.id}
      title={clip.data.title}
      initial={project.data}
      canWrite={can(active, "clips:write")}
    />
  );
}

function Loaded({
  id,
  orgId,
  title,
  initial,
  canWrite,
}: {
  id: string;
  orgId: string;
  title: string;
  initial: { doc: Doc; revision: number };
  canWrite: boolean;
}) {
  const t = useTranslations("editor");
  const loadedId = useEditor((s) => s.clipId);
  const ready = loadedId === id;
  useEffect(() => {
    useEditor.getState().load(id, initial.doc, initial.revision);
    return () => useEditor.getState().setPlaying(false);
    // se carga una sola vez por apertura del clip
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const doc = useEditor((s) => s.history.present);
  const save = useEditor((s) => s.save);
  const history = useEditor((s) => s.history);
  const playing = useEditor((s) => s.playing);
  const playhead = useEditor((s) => s.playheadMs);
  const snapping = useEditor((s) => s.snapping);
  const media = useSourceMedia(orgId, ready ? doc : null);
  const { flush } = useAutosave(id, orgId);
  const [render, setRender] = useState<RenderJob | null>(null);

  const saveVersion = useCallback(async () => {
    await flush();
    try {
      await unwrap(
        api.POST("/api/v1/clips/{clip_id}/versions", {
          params: { path: { clip_id: id } },
          body: {},
        }),
      );
      toast.success(t("versionSaved"));
    } catch {
      toast.error(t("versionFailed"));
    }
  }, [flush, id, t]);

  const startRender = useMutation({
    mutationFn: async () => {
      await flush();
      return unwrap(
        api.POST("/api/v1/clips/{clip_id}/render", {
          params: { path: { clip_id: id } },
          body: { preset_key: "9x16_1080_30" },
        }),
      );
    },
    onSuccess: (job) => setRender(job),
    onError: () => toast.error(t("export.failed")),
  });
  const exportClip = useCallback(() => {
    if (canWrite && !startRender.isPending) startRender.mutate();
  }, [canWrite, startRender]);

  useShortcuts({ save: () => void saveVersion(), exportClip });

  if (!ready || !doc) return <Skeleton className="h-96 w-full" aria-busy="true" />;

  const splitAtPlayhead = () => {
    const st = useEditor.getState();
    st.apply((d) => {
      let next = d;
      for (const track of d.timeline.tracks) {
        for (const item of track.items) {
          const loc = (item as { id: string }).id;
          const cur = next;
          next = splitItem(cur, loc, st.playheadMs, () => uniqueId(cur)).doc;
        }
      }
      return next;
    });
    st.sealHistory();
  };

  return (
    <div className="-m-4 flex h-[calc(100dvh-3.5rem)] flex-col bg-bg-0" data-testid="clip-editor">
      <header className="flex h-11 shrink-0 items-center gap-2 border-b border-border-1 bg-bg-1 px-3">
        <Link
          href="/clips"
          aria-label={t("back")}
          className="inline-flex size-8 items-center justify-center rounded-control text-text-2 hover:bg-bg-3"
        >
          <ArrowLeft className="size-4" aria-hidden="true" />
        </Link>
        <h1 className="min-w-0 flex-1 truncate text-base font-medium">{title}</h1>
        <span data-testid="save-state">
          <Badge tone={SAVE_TONE[save]}>{t(`save.${save}`)}</Badge>
        </span>
        <IconButton
          label={t("undo")}
          size="dense"
          variant="ghost"
          disabled={!canWrite || !canUndo(history)}
          onClick={() => useEditor.getState().undo()}
        >
          <Undo2 className="size-4" />
        </IconButton>
        <IconButton
          label={t("redo")}
          size="dense"
          variant="ghost"
          disabled={!canWrite || !canRedo(history)}
          onClick={() => useEditor.getState().redo()}
        >
          <Redo2 className="size-4" />
        </IconButton>
        <Button
          size="dense"
          variant="secondary"
          disabled={!canWrite || save === "conflict"}
          onClick={() => void saveVersion()}
        >
          {t("saveVersion")}
        </Button>
        <Button
          size="dense"
          variant="primary"
          icon={<Download className="size-3.5" />}
          disabled={!canWrite || save === "conflict"}
          loading={startRender.isPending}
          onClick={exportClip}
        >
          {t("export.action")}
        </Button>
      </header>

      {save === "conflict" && (
        <div className="m-2">
          <Notice tone="danger">
            {t("conflict")}{" "}
            <button className="underline" onClick={() => window.location.reload()}>
              {t("reload")}
            </button>
          </Notice>
        </div>
      )}
      {render && <ExportStatus job={render} onChange={setRender} />}

      <div className="flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col">
          <Preview doc={doc} media={media} />
          <div className="flex h-10 shrink-0 items-center gap-2 border-t border-border-1 bg-bg-1 px-3">
            <IconButton
              label={playing ? t("pause") : t("play")}
              size="dense"
              variant="ghost"
              onClick={() => useEditor.getState().setPlaying(!playing)}
            >
              {playing ? <Pause className="size-4" /> : <Play className="size-4" />}
            </IconButton>
            <span className="font-mono text-sm tabular-nums" data-testid="timecode">
              {formatTimecode(playhead)} / {formatTimecode(doc.timeline.duration_ms)}
            </span>
            <span className="flex-1" />
            <IconButton
              label={t("split")}
              size="dense"
              variant="ghost"
              disabled={!canWrite}
              onClick={splitAtPlayhead}
            >
              <Scissors className="size-4" />
            </IconButton>
            <IconButton
              label={t("snap")}
              size="dense"
              variant={snapping ? "secondary" : "ghost"}
              aria-pressed={snapping}
              onClick={() => useEditor.getState().toggleSnapping()}
            >
              <Magnet className="size-4" />
            </IconButton>
            <IconButton
              label={t("zoomOut")}
              size="dense"
              variant="ghost"
              onClick={() => useEditor.getState().setZoom(useEditor.getState().zoom / 1.25)}
            >
              <ZoomOut className="size-4" />
            </IconButton>
            <IconButton
              label={t("zoomIn")}
              size="dense"
              variant="ghost"
              onClick={() => useEditor.getState().setZoom(useEditor.getState().zoom * 1.25)}
            >
              <ZoomIn className="size-4" />
            </IconButton>
          </div>
          <div className="h-56 shrink-0">
            <Timeline doc={doc} media={media} />
          </div>
        </div>
        <Inspector doc={doc} media={media} canWrite={canWrite} />
      </div>
    </div>
  );
}

function formatTimecode(ms: number): string {
  const total = Math.floor(ms / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  const cs = Math.floor((ms % 1000) / 10);
  const pad = (n: number, w = 2) => String(n).padStart(w, "0");
  return `${pad(m)}:${pad(s)}.${pad(cs)}`;
}

/** Progreso del render: sondea el trabajo y ofrece la descarga al terminar (F1-13 lo amplía). */
function ExportStatus({ job, onChange }: { job: RenderJob; onChange: (j: RenderJob) => void }) {
  const t = useTranslations("editor.export");
  const done = job.status === "succeeded" || job.status === "failed" || job.status === "canceled";
  const live = useQuery({
    queryKey: ["render-job", job.id],
    enabled: !done,
    refetchInterval: 1500,
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/render-jobs/{render_job_id}", {
          params: { path: { render_job_id: job.id } },
        }),
      ),
  });
  useEffect(() => {
    if (live.data && live.data.status !== job.status) onChange(live.data);
    else if (live.data && live.data.progress !== job.progress) onChange(live.data);
  }, [live.data, job.status, job.progress, onChange]);

  const download = useMutation({
    mutationFn: async () => {
      const res = await unwrap(
        api.GET("/api/v1/assets/{asset_id}/download-url", {
          params: { path: { asset_id: job.output_asset_id as string } },
        }),
      );
      window.open(res.url, "_blank", "noopener");
    },
  });

  return (
    <div
      className="flex items-center gap-3 border-b border-border-1 bg-bg-1 px-3 py-1.5 text-sm"
      role="status"
    >
      <span>{t(`status.${job.status}`)}</span>
      {(job.status === "queued" || job.status === "running") && (
        <div className="max-w-xs flex-1">
          <ProgressBar value={job.progress} label={t("progress")} />
        </div>
      )}
      {job.status === "succeeded" && job.output_asset_id && (
        <Button size="dense" loading={download.isPending} onClick={() => download.mutate()}>
          {t("download")}
        </Button>
      )}
    </div>
  );
}
