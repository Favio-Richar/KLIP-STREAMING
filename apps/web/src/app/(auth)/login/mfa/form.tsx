"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, Field, Input, Notice } from "@klick/ui";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";
import { AuthShell } from "@/components/auth-shell";
import { api, qk, unwrap } from "@/lib/api";
import { safeNext } from "@/lib/nav";
import { mfaChallenge } from "@/lib/stores";
import { useErrorMessage } from "@/lib/use-error-message";

export function MfaForm() {
  const t = useTranslations("auth.mfa");
  const errorMessage = useErrorMessage();
  const router = useRouter();
  const qc = useQueryClient();
  const next = safeNext(useSearchParams().get("next"));
  const [useRecovery, setUseRecovery] = useState(false);
  const [code, setCode] = useState("");

  const verify = useMutation({
    // Sin token en la pestaña, la API usa la cookie del flujo con Google.
    mutationFn: () => {
      const token = mfaChallenge.get() ?? undefined;
      const factor = useRecovery
        ? { recovery_code: code.trim() }
        : { code: code.replace(/\s/g, "") };
      return unwrap(api.POST("/api/v1/auth/mfa/verify", { body: { ...factor, mfa_token: token } }));
    },
    onSuccess: (data) => {
      mfaChallenge.clear();
      qc.setQueryData(qk.me, data.user);
      router.replace(next);
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    verify.mutate();
  };

  return (
    <AuthShell
      title={t("title")}
      subtitle={useRecovery ? t("recoverySubtitle") : t("subtitle")}
      footer={
        <Link href="/login" className="font-medium text-accent-text underline underline-offset-2">
          {t("back")}
        </Link>
      }
    >
      <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
        <Field
          label={useRecovery ? t("recoveryLabel") : t("codeLabel")}
          hint={useRecovery ? t("recoveryHint") : undefined}
        >
          {(a) => (
            <Input
              {...a}
              key={useRecovery ? "recovery" : "code"}
              name="code"
              autoFocus
              autoComplete="one-time-code"
              inputMode={useRecovery ? "text" : "numeric"}
              className="font-mono tabular-nums tracking-widest"
              maxLength={useRecovery ? 32 : 7}
              value={code}
              onChange={(e) => setCode(e.target.value)}
            />
          )}
        </Field>
        {verify.isError ? <Notice tone="danger">{errorMessage(verify.error)}</Notice> : null}
        <Button
          type="submit"
          variant="primary"
          size="touch"
          loading={verify.isPending}
          disabled={code.trim().length < 6}
        >
          {t("submit")}
        </Button>
        <Button
          variant="ghost"
          onClick={() => {
            setUseRecovery((v) => !v);
            setCode("");
            verify.reset();
          }}
        >
          {useRecovery ? t("useApp") : t("useRecovery")}
        </Button>
      </form>
    </AuthShell>
  );
}
