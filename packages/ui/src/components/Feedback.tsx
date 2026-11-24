import { AlertTriangle, CheckCircle2, Info, XCircle, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../cn";
import { Button } from "./Button";

export type Tone = "neutral" | "success" | "warning" | "danger" | "info" | "accent";
type Semantic = Exclude<Tone, "neutral" | "accent">;

const toneText: Record<Tone, string> = {
  neutral: "text-text-2",
  success: "text-success",
  warning: "text-warning",
  danger: "text-danger",
  info: "text-info",
  accent: "text-accent-text",
};
const toneIcon: Record<Semantic, LucideIcon> = {
  success: CheckCircle2,
  warning: AlertTriangle,
  danger: XCircle,
  info: Info,
};

/** Etiqueta de estado: siempre con texto (y con icono cuando el tono es semántico). */
export function Badge({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  const Icon = tone === "neutral" || tone === "accent" ? null : toneIcon[tone];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-tag border border-border-2 bg-bg-2 px-1.5 py-0.5",
        "text-xs font-medium",
        toneText[tone],
      )}
    >
      {Icon ? <Icon size={14} aria-hidden /> : null}
      {children}
    </span>
  );
}

export function StatusDot({ tone = "neutral" }: { tone?: Tone }) {
  const fill: Record<Tone, string> = {
    neutral: "bg-text-3",
    success: "bg-success",
    warning: "bg-warning",
    danger: "bg-danger",
    info: "bg-info",
    accent: "bg-accent-text",
  };
  return <span aria-hidden className={cn("inline-block size-2 rounded-full", fill[tone])} />;
}

/** Esqueleto sólido: solo cambia la opacidad, sin barridos de brillo. */
export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-hidden
      className={cn(
        "rounded-control bg-bg-3 animate-[klick-pulse_1.6s_ease-in-out_infinite]",
        className,
      )}
    />
  );
}

export function Spinner({ label }: { label: string }) {
  return (
    <span role="status" className="inline-flex items-center gap-2 text-text-2">
      <span
        aria-hidden
        className="size-4 rounded-full border-2 border-border-2 border-t-accent-text animate-[klick-spin_0.8s_linear_infinite]"
      />
      <span className="text-sm">{label}</span>
    </span>
  );
}

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
}: {
  icon?: LucideIcon;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-card border border-dashed border-border-2 bg-bg-1 px-6 py-12 text-center">
      {Icon ? <Icon size={24} aria-hidden className="text-text-3" /> : null}
      <h3 className="text-lg">{title}</h3>
      {description ? <p className="m-0 max-w-[48ch] text-text-2">{description}</p> : null}
      {action}
    </div>
  );
}

export function ErrorState({
  title,
  description,
  onRetry,
  retryLabel,
}: {
  title: string;
  description?: string | undefined;
  onRetry?: () => void;
  retryLabel: string;
}) {
  return (
    <div
      role="alert"
      className="flex flex-col items-start gap-3 rounded-card border border-danger bg-bg-1 p-5"
    >
      <div className="flex items-center gap-2 text-danger">
        <XCircle size={20} aria-hidden />
        <h3 className="text-lg text-text-1">{title}</h3>
      </div>
      {description ? <p className="m-0 text-text-2">{description}</p> : null}
      {onRetry ? <Button onClick={onRetry}>{retryLabel}</Button> : null}
    </div>
  );
}

export function Notice({ tone = "info", children }: { tone?: Semantic; children: ReactNode }) {
  const Icon = toneIcon[tone];
  return (
    <div
      role={tone === "danger" ? "alert" : "status"}
      className="flex items-start gap-2 rounded-card border border-border-2 bg-bg-2 p-3 text-sm text-text-1"
    >
      <Icon size={16} aria-hidden className={cn("mt-0.5 shrink-0", toneText[tone])} />
      <div>{children}</div>
    </div>
  );
}

export function ProgressBar({ value, label }: { value: number; label: string }) {
  const pct = Math.max(0, Math.min(100, Math.round(value)));
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={pct}
      className="h-2 w-full overflow-hidden rounded-tag border border-border-1 bg-bg-3"
    >
      <div
        className="h-full w-full origin-left bg-accent transition-transform duration-[var(--dur-base)] ease-[var(--ease-out)]"
        style={{ transform: `scaleX(${pct / 100})` }}
      />
    </div>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="rounded-tag border border-border-2 bg-bg-2 px-1.5 font-mono text-xs text-text-2">
      {children}
    </kbd>
  );
}
