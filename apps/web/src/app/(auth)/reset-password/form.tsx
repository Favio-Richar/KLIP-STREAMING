"use client";

import { useMutation } from "@tanstack/react-query";
import { Button, ErrorState, Field, Input, Notice } from "@klick/ui";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";
import { AuthShell } from "@/components/auth-shell";
import { api, unwrap } from "@/lib/api";
import { fieldErrors, useErrorMessage } from "@/lib/use-error-message";

const PASSWORD_MIN = 10;

export function ResetForm() {
  const t = useTranslations("auth.reset");
  const tr = useTranslations("auth.register");
  const tc = useTranslations("auth");
  const errorMessage = useErrorMessage();
  const router = useRouter();
  const token = useSearchParams().get("token");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [touched, setTouched] = useState(false);

  const reset = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/auth/password/reset", {
          body: { token: token ?? "", new_password: password },
        }),
      ),
    onSuccess: () => router.replace("/login?reset=1"),
  });

  if (!token) {
    return (
      <AuthShell title={t("title")}>
        <ErrorState
          title={t("missingTitle")}
          description={t("missingBody")}
          retryLabel={t("requestNew")}
          onRetry={() => router.push("/forgot-password")}
        />
      </AuthShell>
    );
  }

  const short = password.length < PASSWORD_MIN;
  const mismatch = confirm.length > 0 && confirm !== password;
  const server = fieldErrors(reset.error);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    setTouched(true);
    if (short || confirm !== password) return;
    reset.mutate();
  };

  return (
    <AuthShell
      title={t("title")}
      subtitle={t("subtitle")}
      footer={
        <Link href="/login" className="font-medium text-accent-text underline underline-offset-2">
          {t("back")}
        </Link>
      }
    >
      <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
        <Field
          label={t("newPassword")}
          hint={tr("passwordHint", { min: PASSWORD_MIN })}
          error={
            touched && short ? tr("passwordShort", { min: PASSWORD_MIN }) : server.new_password
          }
        >
          {(a) => (
            <Input
              {...a}
              type="password"
              name="new-password"
              autoComplete="new-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          )}
        </Field>
        <Field label={t("confirm")} error={mismatch ? t("mismatch") : undefined}>
          {(a) => (
            <Input
              {...a}
              type="password"
              name="confirm-password"
              autoComplete="new-password"
              required
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
            />
          )}
        </Field>
        {reset.isError && !server.new_password ? (
          <Notice tone="danger">{errorMessage(reset.error)}</Notice>
        ) : null}
        <Button
          type="submit"
          variant="primary"
          size="touch"
          loading={reset.isPending}
          disabled={!password || !confirm}
        >
          {t("submit")}
        </Button>
      </form>
      <p className="m-0 text-xs text-text-3">{tc("sessionsNote")}</p>
    </AuthShell>
  );
}
