"use client";

import { useMutation } from "@tanstack/react-query";
import {
  Badge,
  Button,
  EmptyState,
  buttonClasses,
  ErrorState,
  JobProgressCard,
  Skeleton,
  StatusDot,
  type Tone,
} from "@klick/ui";
import { Building2, Activity } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useState } from "react";
import { api, unwrap } from "@/lib/api";
import { can, useActiveOrg, useMe } from "@/lib/queries";
import { TERMINAL, useJob } from "@/lib/use-job";
import { useErrorMessage } from "@/lib/use-error-message";

const STATUS_TONE: Record<string, Tone> = {
  queued: "neutral",
  running: "info",
  retrying: "warning",
  succeeded: "success",
  failed: "danger",
  canceled: "neutral",
  expired: "warning",
};

export function Dashboard() {
  const t = useTranslations("panel");
  const errorMessage = useErrorMessage();
  const me = useMe();
  const { active, orgs, isPending, isError, error, refetch } = useActiveOrg();

  if (isPending) {
    return (
      <div className="flex flex-col gap-4" aria-busy="true">
        <Skeleton className="h-8 w-72" />
        <Skeleton className="h-44 w-full" />
      </div>
    );
  }
  if (isError) {
    return (
      <ErrorState
        title={t("loadFailed")}
        description={errorMessage(error)}
        retryLabel={t("retry")}
        onRetry={() => void refetch()}
      />
    );
  }
  if (orgs.length === 0 || !active) {
    return (
      <EmptyState
        icon={Building2}
        title={t("noOrgTitle")}
        description={t("noOrgBody")}
        action={
          <Link href="/orgs/new" className={buttonClasses({ variant: "primary" })}>
            {t("noOrgAction")}
          </Link>
        }
      />
    );
  }

  const name = me.data?.display_name.split(/\s+/)[0] ?? "";
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl">{t("greeting", { name })}</h1>
        <p className="m-0 text-text-2">{t("workingIn", { org: active.name })}</p>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <section
          aria-labelledby="sys-title"
          className="flex flex-col gap-4 rounded-card border border-border-1 bg-bg-1 p-5"
        >
          <div className="flex items-start gap-3">
            <Activity size={20} aria-hidden className="mt-0.5 text-text-2" />
            <div>
              <h2 id="sys-title" className="text-lg">
                {t("system.title")}
              </h2>
              <p className="m-0 text-text-2">{t("system.body")}</p>
            </div>
          </div>
          <PingJob canRun={can(active, "org:manage")} />
        </section>
        <section
          aria-labelledby="role-title"
          className="flex flex-col gap-3 rounded-card border border-border-1 bg-bg-1 p-5"
        >
          <h2 id="role-title" className="text-lg">
            {t("access.title")}
          </h2>
          <p className="m-0 text-text-2">{t("access.body", { role: t(`roles.${active.role}`) })}</p>
          <div className="flex flex-wrap gap-2">
            <Badge tone="accent">{t(`roles.${active.role}`)}</Badge>
          </div>
          <Link
            href="/settings/members"
            className="self-start text-accent-text underline underline-offset-2"
          >
            {t("access.members")}
          </Link>
        </section>
      </div>
    </div>
  );
}

function PingJob({ canRun }: { canRun: boolean }) {
  const t = useTranslations("panel.system");
  const errorMessage = useErrorMessage();
  const [jobId, setJobId] = useState<string | null>(null);
  const { job, live, stream } = useJob(jobId);

  const run = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/jobs", {
          body: { kind: "system.ping", payload: { steps: 10, delay_ms: 700 }, delay_s: 0 },
        }),
      ),
    onSuccess: (accepted) => setJobId(accepted.job_id),
  });

  const polled = job.data?.status ?? "queued";
  const done = TERMINAL.has(polled);
  // Si ya llegó progreso por SSE, el trabajo está en curso aunque la consulta HTTP aún diga "en cola".
  const status = !done && live ? "running" : polled;
  const progress = done
    ? status === "succeeded"
      ? 100
      : job.data?.progress ?? 0
    : live?.progress ?? job.data?.progress ?? 0;
  const busy = run.isPending || (!!jobId && !done);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="primary" loading={busy} disabled={!canRun} onClick={() => run.mutate()}>
          {jobId && done ? t("runAgain") : t("run")}
        </Button>
        {!canRun ? <span className="text-sm text-text-2">{t("noPermission")}</span> : null}
        {jobId ? (
          <span className="inline-flex items-center gap-2 text-sm text-text-2" role="status">
            <StatusDot
              tone={stream === "open" ? "success" : stream === "connecting" ? "warning" : "danger"}
            />
            {t(`stream.${stream}`)}
          </span>
        ) : null}
      </div>
      {run.isError ? (
        <ErrorState
          title={t("startFailed")}
          description={errorMessage(run.error)}
          retryLabel={t("retry")}
          onRetry={() => run.mutate()}
        />
      ) : null}
      {jobId && job.isPending ? <Skeleton className="h-24 w-full" /> : null}
      {jobId && job.isError ? (
        <ErrorState
          title={t("trackFailed")}
          description={errorMessage(job.error)}
          retryLabel={t("retry")}
          onRetry={() => void job.refetch()}
        />
      ) : null}
      {job.data ? (
        <JobProgressCard
          title={job.data.kind}
          statusLabel={t(`status.${status}`)}
          tone={STATUS_TONE[status] ?? "neutral"}
          progress={progress}
          message={
            status === "succeeded"
              ? t("doneMessage")
              : status === "failed"
                ? t("failedMessage")
                : live?.message ?? undefined
          }
        />
      ) : null}
    </div>
  );
}
