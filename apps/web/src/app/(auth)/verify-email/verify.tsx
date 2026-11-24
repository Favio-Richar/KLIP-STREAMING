"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ErrorState, Spinner, buttonClasses } from "@klick/ui";
import { CheckCircle2 } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useRef } from "react";
import { AuthShell } from "@/components/auth-shell";
import { api, qk, unwrap } from "@/lib/api";
import { useErrorMessage } from "@/lib/use-error-message";

export function VerifyEmail() {
  const t = useTranslations("auth.verify");
  const errorMessage = useErrorMessage();
  const qc = useQueryClient();
  const token = useSearchParams().get("token");
  const started = useRef(false);

  const verify = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/auth/email/verify", { body: { token: token ?? "" } })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.me }),
  });

  // El enlace del correo se usa una sola vez: se evita el doble disparo del modo estricto.
  useEffect(() => {
    if (token && !started.current) {
      started.current = true;
      verify.mutate();
    }
  }, [token, verify]);

  return (
    <AuthShell title={t("title")}>
      {!token ? (
        <ErrorState
          title={t("missingTitle")}
          description={t("missingBody")}
          retryLabel={t("retry")}
        />
      ) : verify.isSuccess ? (
        <div className="flex flex-col items-start gap-4 rounded-card border border-border-1 bg-bg-1 p-5">
          <CheckCircle2 size={24} aria-hidden className="text-success" />
          <p className="m-0">{t("done")}</p>
          <Link href="/" className={buttonClasses({ variant: "primary" })}>
            {t("continue")}
          </Link>
        </div>
      ) : verify.isError ? (
        <ErrorState
          title={t("failedTitle")}
          description={errorMessage(verify.error)}
          retryLabel={t("retry")}
          onRetry={() => verify.mutate()}
        />
      ) : (
        <Spinner label={t("working")} />
      )}
    </AuthShell>
  );
}
