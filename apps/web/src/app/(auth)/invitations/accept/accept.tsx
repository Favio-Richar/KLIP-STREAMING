"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ApiProblem } from "@klick/client-sdk";
import { Button, ErrorState, buttonClasses, Notice, Skeleton } from "@klick/ui";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { AuthShell } from "@/components/auth-shell";
import { api, qk, unwrap } from "@/lib/api";
import { useMe } from "@/lib/queries";
import { useOrgStore } from "@/lib/stores";
import { useErrorMessage } from "@/lib/use-error-message";

export function AcceptInvite() {
  const t = useTranslations("auth.invite");
  const errorMessage = useErrorMessage();
  const router = useRouter();
  const qc = useQueryClient();
  const setOrgId = useOrgStore((s) => s.setOrgId);
  const me = useMe();
  const token = useSearchParams().get("token") ?? "";
  const here = `/invitations/accept?token=${encodeURIComponent(token)}`;

  const accept = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/invitations/{token}/accept", { params: { path: { token } } })),
    onSuccess: async (org) => {
      setOrgId(org.id);
      await qc.invalidateQueries({ queryKey: qk.orgs });
      router.replace("/");
    },
  });

  if (!token) {
    return (
      <AuthShell title={t("title")}>
        <ErrorState
          title={t("missingTitle")}
          description={t("missingBody")}
          retryLabel={t("retry")}
        />
      </AuthShell>
    );
  }

  if (me.isPending) {
    return (
      <AuthShell title={t("title")}>
        <Skeleton className="h-24 w-full" />
      </AuthShell>
    );
  }

  const unauthenticated = me.error instanceof ApiProblem && me.error.status === 401;
  if (unauthenticated) {
    return (
      <AuthShell title={t("title")} subtitle={t("needLogin")}>
        <div className="flex flex-col gap-3">
          <Link
            href={`/login?next=${encodeURIComponent(here)}`}
            className={buttonClasses({ variant: "primary", size: "touch", className: "w-full" })}
          >
            {t("login")}
          </Link>
          <Link
            href={`/register?next=${encodeURIComponent(here)}`}
            className={buttonClasses({ size: "touch", className: "w-full" })}
          >
            {t("register")}
          </Link>
        </div>
      </AuthShell>
    );
  }

  if (me.isError) {
    return (
      <AuthShell title={t("title")}>
        <ErrorState
          title={t("failedTitle")}
          description={errorMessage(me.error)}
          retryLabel={t("retry")}
          onRetry={() => void me.refetch()}
        />
      </AuthShell>
    );
  }

  return (
    <AuthShell title={t("title")} subtitle={t("subtitle", { email: me.data.email })}>
      {accept.isError ? <Notice tone="danger">{errorMessage(accept.error)}</Notice> : null}
      <Button
        variant="primary"
        size="touch"
        loading={accept.isPending}
        onClick={() => accept.mutate()}
      >
        {t("accept")}
      </Button>
      <p className="m-0 text-xs text-text-3">{t("emailNote")}</p>
    </AuthShell>
  );
}
