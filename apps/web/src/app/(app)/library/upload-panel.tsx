"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Badge, Button, IconButton, Notice, ProgressBar, type Tone } from "@klick/ui";
import { Pause, Play, RotateCcw, Upload, X } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useEffect, useId, useRef, useState, useSyncExternalStore } from "react";
import { formatBytes } from "@/lib/format";
import { useUploads } from "@/lib/upload-manager";
import type { UploadTask } from "@/lib/uploader";

const TONE: Record<string, Tone> = {
  queued: "neutral",
  hashing: "info",
  starting: "info",
  uploading: "info",
  paused: "warning",
  completing: "info",
  done: "success",
  error: "danger",
  canceled: "neutral",
};

export function UploadPanel({ orgId, canUpload }: { orgId: string; canUpload: boolean }) {
  const t = useTranslations("library.upload");
  const tasks = useUploads((s) => s.tasks);
  const add = useUploads((s) => s.add);
  const clearFinished = useUploads((s) => s.clearFinished);
  const [rights, setRights] = useState(false);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const rightsId = useId();

  const take = (list: FileList | File[] | null) => {
    const files = Array.from(list ?? []);
    if (files.length === 0 || !rights) return;
    add(files, { orgId, rightsConfirmed: rights });
    if (input.current) input.current.value = "";
  };

  if (!canUpload) {
    return <Notice tone="info">{t("readOnly")}</Notice>;
  }

  return (
    <section
      aria-labelledby="upload-title"
      className="flex flex-col gap-4 rounded-card border border-border-1 bg-bg-1 p-5"
    >
      <div className="flex flex-col gap-1">
        <h2 id="upload-title" className="text-lg">
          {t("title")}
        </h2>
        <p className="m-0 text-text-2">{t("hint")}</p>
      </div>

      <div className="flex flex-col gap-2">
        <label htmlFor={rightsId} className="flex min-h-8 items-start gap-2">
          <input
            id={rightsId}
            type="checkbox"
            className="mt-1 size-4 accent-[var(--accent-text)]"
            checked={rights}
            onChange={(e) => setRights(e.target.checked)}
          />
          <span>{t("rights")}</span>
        </label>
        <p className="m-0 pl-6 text-sm text-text-2">{t("rightsHelp")}</p>
      </div>

      <div
        data-testid="dropzone"
        onDragOver={(e) => {
          e.preventDefault();
          if (rights) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          take(e.dataTransfer.files);
        }}
        className={
          "flex flex-col items-center gap-3 rounded-card border border-dashed px-6 py-8 text-center " +
          (dragging ? "border-accent bg-bg-2" : "border-border-2 bg-bg-1")
        }
      >
        <Upload size={24} aria-hidden className="text-text-2" />
        <p className="m-0 text-text-2">{dragging ? t("dropping") : t("hint")}</p>
        <input
          ref={input}
          type="file"
          multiple
          hidden
          data-testid="file-input"
          aria-label={t("choose")}
          onChange={(e) => take(e.target.files)}
        />
        <Button variant="primary" disabled={!rights} onClick={() => input.current?.click()}>
          {t("choose")}
        </Button>
        {!rights ? <p className="m-0 text-sm text-text-2">{t("needRights")}</p> : null}
      </div>

      {tasks.length > 0 ? (
        <div className="flex flex-col gap-3">
          <div className="flex items-center justify-between gap-3">
            <h3 className="text-base">{t("queue")}</h3>
            <Button size="dense" onClick={clearFinished}>
              {t("clearFinished")}
            </Button>
          </div>
          <ul className="m-0 flex list-none flex-col gap-3 p-0">
            {tasks.map((task) => (
              <UploadRow key={task.id} task={task} />
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

function UploadRow({ task }: { task: UploadTask }) {
  const t = useTranslations("library.upload");
  const te = useTranslations("errors");
  const locale = useLocale();
  const qc = useQueryClient();
  const remove = useUploads((s) => s.remove);
  const s = useSyncExternalStore(task.subscribe, task.getSnapshot, task.getSnapshot);

  // al terminar, la biblioteca muestra el archivo nuevo sin esperar al sondeo
  const done = s.status === "done";
  useEffect(() => {
    if (done) void qc.invalidateQueries({ queryKey: ["assets"] });
  }, [done, qc]);

  const fraction = s.status === "hashing" ? s.hashProgress : s.size > 0 ? s.loaded / s.size : 0;
  const label = s.status === "done" && s.deduplicated ? t("status.dedup") : t(`status.${s.status}`);
  const running = ["hashing", "starting", "uploading"].includes(s.status);

  return (
    <li
      data-testid="upload-row"
      data-status={s.status}
      className="flex flex-col gap-2 rounded-card border border-border-1 bg-bg-2 p-3"
    >
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="m-0 truncate font-medium" title={s.fileName}>
            {s.fileName}
          </p>
          <p className="m-0 text-sm text-text-2">
            {formatBytes(s.size, locale)}
            {s.partsTotal > 0 && s.status !== "done"
              ? ` · ${t("parts", { done: s.partsDone, total: s.partsTotal })}`
              : ""}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Badge tone={TONE[s.status] ?? "neutral"}>{label}</Badge>
          {running ? (
            <IconButton label={t("pause")} size="dense" onClick={() => task.pause()}>
              <Pause size={16} aria-hidden />
            </IconButton>
          ) : null}
          {s.status === "paused" ? (
            <IconButton label={t("resume")} size="dense" onClick={() => void task.resume()}>
              <Play size={16} aria-hidden />
            </IconButton>
          ) : null}
          {s.status === "error" ? (
            <IconButton label={t("retry")} size="dense" onClick={() => void task.resume()}>
              <RotateCcw size={16} aria-hidden />
            </IconButton>
          ) : null}
          <IconButton
            label={["done", "error", "canceled"].includes(s.status) ? t("dismiss") : t("cancel")}
            size="dense"
            onClick={() => remove(task.id)}
          >
            <X size={16} aria-hidden />
          </IconButton>
        </div>
      </div>
      {s.status !== "done" && s.status !== "canceled" ? (
        <ProgressBar value={fraction * 100} label={t("progress", { name: s.fileName })} />
      ) : null}
      {s.status === "error" && s.errorCode ? (
        <p role="alert" className="m-0 text-sm text-danger">
          {te.has(s.errorCode) ? te(s.errorCode) : te("generic")}
        </p>
      ) : null}
    </li>
  );
}
