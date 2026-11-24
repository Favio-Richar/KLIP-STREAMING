"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, Field, Input, Notice } from "@klick/ui";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";
import { AuthShell } from "@/components/auth-shell";
import { GoogleButton } from "@/components/google-button";
import { api, qk, unwrap } from "@/lib/api";
import { safeNext } from "@/lib/nav";
import { mfaChallenge } from "@/lib/stores";
import { useErrorMessage } from "@/lib/use-error-message";

export function LoginForm() {
  const t = useTranslations("auth.login");
  const tc = useTranslations("auth");
  const errorMessage = useErrorMessage();
  const router = useRouter();
  const qc = useQueryClient();
  const params = useSearchParams();
  const next = safeNext(params.get("next"));
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const login = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/auth/login", { body: { email: email.trim(), password } })),
    onSuccess: (data) => {
      if (data.mfa_required) {
        mfaChallenge.set(data.mfa_token);
        router.push(`/login/mfa?next=${encodeURIComponent(next)}`);
        return;
      }
      qc.setQueryData(qk.me, data.user);
      router.replace(next);
    },
  });

  const banner = params.get("expired")
    ? t("expired")
    : params.get("error") === "oidc_failed"
      ? t("oidcFailed")
      : params.get("reset")
        ? t("resetDone")
        : null;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    login.mutate();
  };

  return (
    <AuthShell
      title={t("title")}
      subtitle={t("subtitle")}
      footer={
        <p className="m-0">
          {t("noAccount")}{" "}
          <Link
            href="/register"
            className="font-medium text-accent-text underline underline-offset-2"
          >
            {t("register")}
          </Link>
        </p>
      }
    >
      {banner ? <Notice tone={params.get("reset") ? "success" : "info"}>{banner}</Notice> : null}
      <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
        <Field label={tc("fields.email")}>
          {(a) => (
            <Input
              {...a}
              type="email"
              name="email"
              autoComplete="username"
              inputMode="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          )}
        </Field>
        <Field label={tc("fields.password")}>
          {(a) => (
            <Input
              {...a}
              type="password"
              name="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          )}
        </Field>
        {login.isError ? <Notice tone="danger">{errorMessage(login.error)}</Notice> : null}
        <Button
          type="submit"
          variant="primary"
          size="touch"
          loading={login.isPending}
          disabled={!email || !password}
        >
          {t("submit")}
        </Button>
        <Link
          href="/forgot-password"
          className="self-start text-sm text-text-2 underline underline-offset-2"
        >
          {t("forgot")}
        </Link>
      </form>
      <div className="flex items-center gap-3 text-xs text-text-3">
        <span className="h-px flex-1 bg-border-1" />
        {tc("or")}
        <span className="h-px flex-1 bg-border-1" />
      </div>
      <GoogleButton next={next} />
    </AuthShell>
  );
}
