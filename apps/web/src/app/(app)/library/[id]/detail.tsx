"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Badge,
  Button,
  ConfirmDialog,
  EmptyState,
  ErrorState,
  Field,
  Input,
  Notice,
  Skeleton,
  toast,
  type Tone,
} from "@klick/ui";
import { ArrowLeft, Download, FileQuestion, RotateCcw, Scissors, Trash2 } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { ApiProblem, api, unwrap } from "@/lib/api";
import { formatBytes, formatDuration } from "@/lib/format";
import { PENDING, useAsset, usePlaybackUrl, useTags, type Asset } from "@/lib/library";
import { can, useActiveOrg } from "@/lib/queries";
import { useErrorMessage } from "@/lib/use-error-message";

const STATUS_TONE: Record<string, Tone> = {
  uploading: "info",
  processing: "info",
  ready: "success",
  failed: "danger",
};

export function AssetDetail({ id }: { id: string }) {
  const t = useTranslations("library");
  const errorMessage = useErrorMessage();
  const { active, isPending: orgPending } = useActiveOrg();
  const orgId = active?.id ?? null;
  const asset = useAsset(orgId, id);

  if (orgPending || !active || asset.isPending) {
    return (
      <div className="flex flex-col gap-4" aria-busy="true">
        <Skeleton className="h-8 w-72" />
        <Skeleton className="aspect-video w-full max-w-3xl" />
      </div>
    );
  }
  if (asset.isError) {
    const notFound = asset.error instanceof ApiProblem && asset.error.status === 404;
    return notFound ? (
      <EmptyState
        icon={FileQuestion}
        title={t("detail.notFoundTitle")}
        description={t("detail.notFoundBody")}
        action={
          <Link href="/library" className="text-accent-text underline underline-offset-2">
            {t("detail.back")}
          </Link>
        }
      />
    ) : (
      <ErrorState
        title={t("detail.loadFailed")}
        description={errorMessage(asset.error)}
        retryLabel={t("list.retry")}
        onRetry={() => void asset.refetch()}
      />
    );
  }
  return <Loaded asset={asset.data} orgId={active.id} canWrite={can(active, "media:write")} />;
}

function Loaded({ asset, orgId, canWrite }: { asset: Asset; orgId: string; canWrite: boolean }) {
  const t = useTranslations("library");
  const errorMessage = useErrorMessage();
  const router = useRouter();
  const qc = useQueryClient();
  const [confirmDelete, setConfirmDelete] = useState(false);

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["asset", orgId, asset.id] });
    void qc.invalidateQueries({ queryKey: ["assets"] });
  };

  const remove = useMutation({
    mutationFn: () =>
      unwrap(api.DELETE("/api/v1/assets/{asset_id}", { params: { path: { asset_id: asset.id } } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["assets"] });
      toast.success(t("detail.deleted"));
      router.replace("/library");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const reprocess = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/assets/{asset_id}/reprocess", {
          params: { path: { asset_id: asset.id } },
        }),
      ),
    onSuccess: () => {
      toast.success(t("detail.reprocessStarted"));
      refresh();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const createClip = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/clips", {
          body: {
            source_asset_id: asset.id,
            start_ms: 0,
            end_ms: Math.max(1000, Math.min(asset.duration_ms ?? 60_000, 60_000)),
            aspect: "9x16",
            title: asset.title,
          },
        }),
      ),
    onSuccess: (clip) => {
      void qc.invalidateQueries({ queryKey: ["clips"] });
      router.push(`/clips/${clip.id}`);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const download = useMutation({
    mutationFn: () =>
      unwrap(
        api.GET("/api/v1/assets/{asset_id}/download-url", {
          params: { path: { asset_id: asset.id } },
        }),
      ),
    onSuccess: (res) => window.open(res.url, "_blank", "noopener,noreferrer"),
    onError: (e) => toast.error(errorMessage(e)),
  });

  return (
    <div className="flex flex-col gap-6">
      <Link
        href="/library"
        className="inline-flex items-center gap-1 self-start text-accent-text underline underline-offset-2"
      >
        <ArrowLeft size={16} aria-hidden />
        {t("detail.back")}
      </Link>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-4">
          <Title asset={asset} canWrite={canWrite} onSaved={refresh} />
          <Player asset={asset} orgId={orgId} />
        </div>
        <aside className="flex min-w-0 flex-col gap-5" aria-label={t("detail.info")}>
          <Info asset={asset} />
          <TagEditor asset={asset} orgId={orgId} canWrite={canWrite} onSaved={refresh} />
          <div className="flex flex-wrap gap-2">
            {asset.kind === "video" && asset.status === "ready" ? (
              <Button
                variant="primary"
                loading={createClip.isPending}
                onClick={() => createClip.mutate()}
              >
                <Scissors size={16} aria-hidden />
                {t("detail.createClip")}
              </Button>
            ) : null}
            <Button
              loading={download.isPending}
              disabled={asset.status === "uploading"}
              onClick={() => download.mutate()}
            >
              <Download size={16} aria-hidden />
              {t("detail.download")}
            </Button>
            {canWrite && (asset.status === "failed" || asset.status === "ready") ? (
              <Button loading={reprocess.isPending} onClick={() => reprocess.mutate()}>
                <RotateCcw size={16} aria-hidden />
                {t("detail.reprocess")}
              </Button>
            ) : null}
            {canWrite ? (
              <Button variant="danger" onClick={() => setConfirmDelete(true)}>
                <Trash2 size={16} aria-hidden />
                {t("detail.delete")}
              </Button>
            ) : null}
          </div>
        </aside>
      </div>

      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title={t("detail.deleteTitle", { name: asset.title })}
        description={t("detail.deleteBody")}
        confirmLabel={t("detail.delete")}
        cancelLabel={t("detail.cancel")}
        closeLabel={t("detail.close")}
        loading={remove.isPending}
        onConfirm={() => remove.mutate()}
      />
    </div>
  );
}

function Title({
  asset,
  canWrite,
  onSaved,
}: {
  asset: Asset;
  canWrite: boolean;
  onSaved: () => void;
}) {
  const t = useTranslations("library.detail");
  const errorMessage = useErrorMessage();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(asset.title);

  const save = useMutation({
    mutationFn: () =>
      unwrap(
        api.PATCH("/api/v1/assets/{asset_id}", {
          params: { path: { asset_id: asset.id } },
          body: { title: value.trim() },
        }),
      ),
    onSuccess: () => {
      setEditing(false);
      onSaved();
    },
  });

  if (!editing) {
    return (
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="min-w-0 break-words text-2xl">{asset.title}</h1>
        {canWrite ? (
          <Button
            size="dense"
            onClick={() => {
              setValue(asset.title);
              setEditing(true);
            }}
          >
            {t("rename")}
          </Button>
        ) : null}
      </div>
    );
  }
  return (
    <form
      className="flex flex-col gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (value.trim()) save.mutate();
      }}
    >
      <Field label={t("titleField")} error={save.isError ? errorMessage(save.error) : undefined}>
        {(props) => (
          <Input
            {...props}
            autoFocus
            maxLength={300}
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
        )}
      </Field>
      <div className="flex gap-2">
        <Button type="submit" variant="primary" loading={save.isPending} disabled={!value.trim()}>
          {t("save")}
        </Button>
        <Button
          type="button"
          onClick={() => {
            setValue(asset.title);
            setEditing(false);
          }}
        >
          {t("cancel")}
        </Button>
      </div>
    </form>
  );
}

function Player({ asset, orgId }: { asset: Asset; orgId: string }) {
  const t = useTranslations("library.detail");
  const play = usePlaybackUrl(orgId, asset);
  const errorMessage = useErrorMessage();

  if (PENDING.has(asset.status)) {
    return (
      <Notice tone="info">
        <span data-testid="processing-note">{t("processing")}</span>
      </Notice>
    );
  }
  if (asset.status === "failed") {
    const code = asset.error?.code;
    return (
      <Notice tone="danger">
        <span data-testid="failed-note">
          {typeof code === "string" && t.has(`errors.${code}`)
            ? t(`errors.${code}`)
            : t("errors.unknown")}
        </span>
      </Notice>
    );
  }
  if (play.isPending) return <Skeleton className="aspect-video w-full" />;
  if (play.isError) return <Notice tone="danger">{errorMessage(play.error)}</Notice>;

  const url = play.data;
  if (asset.kind === "video") {
    return (
      <video
        data-testid="player"
        controls
        preload="metadata"
        poster={asset.thumbnail_url ?? undefined}
        src={url}
        className="aspect-video w-full rounded-card border border-border-1 bg-bg-3"
      />
    );
  }
  if (asset.kind === "audio") {
    return <audio data-testid="player" controls preload="metadata" src={url} className="w-full" />;
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element -- URL firmada de corta vida
    <img
      data-testid="player"
      src={url}
      alt={asset.title}
      className="max-h-[70vh] w-full rounded-card border border-border-1 bg-bg-3 object-contain"
    />
  );
}

function Info({ asset }: { asset: Asset }) {
  const t = useTranslations("library");
  const locale = useLocale();
  const when = new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" });
  const rows: Array<[string, string]> = [
    [t("detail.kind"), t(`kind.${asset.kind as "video" | "audio" | "image"}`)],
    [t("detail.duration"), formatDuration(asset.duration_ms)],
    [
      t("detail.resolution"),
      asset.width && asset.height ? `${asset.width} × ${asset.height}` : "—",
    ],
    [t("detail.fps"), asset.fps ? String(asset.fps) : "—"],
    [t("detail.codecs"), [asset.video_codec, asset.audio_codec].filter(Boolean).join(" / ") || "—"],
    [t("detail.container"), asset.container ?? "—"],
    [
      t("detail.bitrate"),
      asset.bitrate_kbps ? `${formatBytes(asset.bitrate_kbps * 125, locale)}/s` : "—",
    ],
    [t("detail.added"), when.format(new Date(asset.created_at))],
  ];
  return (
    <section aria-labelledby="info-title" className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <h2 id="info-title" className="text-lg">
          {t("detail.info")}
        </h2>
        <Badge tone={STATUS_TONE[asset.status] ?? "neutral"}>
          {t(`status.${asset.status as "uploading" | "processing" | "ready" | "failed"}`)}
        </Badge>
      </div>
      <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-text-2">{k}</dt>
            <dd className="m-0 min-w-0 break-words font-mono tabular-nums">{v}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function TagEditor({
  asset,
  orgId,
  canWrite,
  onSaved,
}: {
  asset: Asset;
  orgId: string;
  canWrite: boolean;
  onSaved: () => void;
}) {
  const t = useTranslations("library.detail");
  const errorMessage = useErrorMessage();
  const qc = useQueryClient();
  const tags = useTags(orgId);
  const [name, setName] = useState("");
  const current = new Set(asset.tags.map((x) => x.id));

  const setTags = useMutation({
    mutationFn: (ids: string[]) =>
      unwrap(
        api.PATCH("/api/v1/assets/{asset_id}", {
          params: { path: { asset_id: asset.id } },
          body: { tag_ids: ids },
        }),
      ),
    onSuccess: onSaved,
    onError: (e) => toast.error(errorMessage(e)),
  });
  const create = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/tags", { body: { name: name.trim() } })),
    onSuccess: (tag) => {
      setName("");
      void qc.invalidateQueries({ queryKey: ["tags", orgId] });
      setTags.mutate([...current, tag.id]);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const toggle = (id: string) => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setTags.mutate([...next]);
  };

  return (
    <section aria-labelledby="tags-title" className="flex flex-col gap-2">
      <h2 id="tags-title" className="text-lg">
        {t("tags")}
      </h2>
      {(tags.data ?? []).length === 0 ? (
        <p className="m-0 text-sm text-text-2">{t("noTags")}</p>
      ) : null}
      <ul className="m-0 flex list-none flex-wrap gap-1.5 p-0">
        {(tags.data ?? []).map((tag) => (
          <li key={tag.id}>
            <label
              className={
                "inline-flex min-h-8 cursor-pointer items-center gap-1.5 rounded-tag border px-2 text-sm " +
                (current.has(tag.id) ? "border-accent bg-bg-3" : "border-border-2 bg-bg-1")
              }
            >
              <input
                type="checkbox"
                className="size-4 accent-[var(--accent-text)]"
                checked={current.has(tag.id)}
                disabled={!canWrite || setTags.isPending}
                onChange={() => toggle(tag.id)}
              />
              {tag.name}
            </label>
          </li>
        ))}
      </ul>
      {canWrite ? (
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (name.trim()) create.mutate();
          }}
        >
          <Input
            aria-label={t("newTag")}
            placeholder={t("newTag")}
            maxLength={60}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <Button type="submit" loading={create.isPending} disabled={!name.trim()}>
            {t("addTag")}
          </Button>
        </form>
      ) : null}
    </section>
  );
}
