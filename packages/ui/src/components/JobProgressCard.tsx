import type { ReactNode } from "react";
import { Badge, ProgressBar, type Tone } from "./Feedback";

export interface JobProgressCardProps {
  title: string;
  statusLabel: string;
  tone: Tone;
  /** 0–100; `null` si el trabajo aún no informa progreso. */
  progress: number | null;
  message?: string | undefined;
  footer?: ReactNode;
}

export function JobProgressCard({
  title,
  statusLabel,
  tone,
  progress,
  message,
  footer,
}: JobProgressCardProps) {
  return (
    <article className="flex flex-col gap-3 rounded-card border border-border-1 bg-bg-2 p-4">
      <header className="flex items-center justify-between gap-3">
        <h3 className="font-mono text-base tabular-nums">{title}</h3>
        <Badge tone={tone}>{statusLabel}</Badge>
      </header>
      {progress !== null ? <ProgressBar value={progress} label={title} /> : null}
      {message ? <p className="m-0 text-sm text-text-2">{message}</p> : null}
      {footer}
    </article>
  );
}
