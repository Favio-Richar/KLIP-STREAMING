"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge, Button, ErrorState, Skeleton, toast } from "@klick/ui";
import { Laptop } from "lucide-react";
import { useFormatter, useTranslations } from "next-intl";
import { api, qk, unwrap } from "@/lib/api";
import { useErrorMessage } from "@/lib/use-error-message";

/** Resumen legible del user-agent: navegador y sistema, sin exponer la cadena completa. */
export function describeAgent(ua: string | null): string | null {
  if (!ua) return null;
  const browser = /Edg\//.test(ua)
    ? "Edge"
    : /OPR\//.test(ua)
      ? "Opera"
      : /Chrome\//.test(ua)
        ? "Chrome"
        : /Firefox\//.test(ua)
          ? "Firefox"
          : /Safari\//.test(ua)
            ? "Safari"
            : null;
  const os = /Windows/.test(ua)
    ? "Windows"
    : /Android/.test(ua)
      ? "Android"
      : /iPhone|iPad/.test(ua)
        ? "iOS"
        : /Mac OS X/.test(ua)
          ? "macOS"
          : /Linux/.test(ua)
            ? "Linux"
            : null;
  return [browser, os].filter(Boolean).join(" · ") || null;
}

export function Sessions() {
  const t = useTranslations("profile.sessions");
  const errorMessage = useErrorMessage();
  const format = useFormatter();
  const qc = useQueryClient();

  const sessions = useQuery({
    queryKey: qk.sessions,
    queryFn: () => unwrap(api.GET("/api/v1/me/sessions")),
  });
  const revoke = useMutation({
    mutationFn: (id: string) =>
      unwrap(
        api.DELETE("/api/v1/me/sessions/{session_id}", { params: { path: { session_id: id } } }),
      ),
    onSuccess: () => {
      toast.success(t("revoked"));
      return qc.invalidateQueries({ queryKey: qk.sessions });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  return (
    <section
      aria-labelledby="ses-h"
      className="flex flex-col gap-3 rounded-card border border-border-1 bg-bg-1 p-5"
    >
      <h2 id="ses-h" className="text-lg">
        {t("title")}
      </h2>
      <p className="m-0 text-text-2">{t("body")}</p>
      {sessions.isPending ? (
        <Skeleton className="h-14 w-full" />
      ) : sessions.isError ? (
        <ErrorState
          title={t("loadFailed")}
          description={errorMessage(sessions.error)}
          retryLabel={t("retry")}
          onRetry={() => void sessions.refetch()}
        />
      ) : (
        <ul className="m-0 flex list-none flex-col divide-y divide-border-1 p-0">
          {sessions.data.map((s) => (
            <li key={s.id} className="flex flex-wrap items-center gap-3 py-3">
              <Laptop size={20} aria-hidden className="text-text-2" />
              <div className="min-w-0 flex-1">
                <p className="m-0 font-medium">
                  {describeAgent(s.user_agent) ?? t("unknownDevice")}
                </p>
                <p className="m-0 text-sm text-text-2">
                  {[
                    s.ip,
                    t("since", {
                      date: format.dateTime(new Date(s.created_at), {
                        dateStyle: "medium",
                        timeStyle: "short",
                      }),
                    }),
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </p>
              </div>
              {s.current ? (
                <Badge tone="accent">{t("current")}</Badge>
              ) : (
                <Button
                  variant="ghost"
                  size="dense"
                  loading={revoke.isPending && revoke.variables === s.id}
                  onClick={() => revoke.mutate(s.id)}
                >
                  {t("revoke")}
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
