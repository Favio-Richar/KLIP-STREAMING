"use client";

import { useMutation } from "@tanstack/react-query";
import { Button, Field, Input, Notice } from "@klick/ui";
import { MailCheck } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";
import { AuthShell } from "@/components/auth-shell";
import { GoogleButton } from "@/components/google-button";
import { api, unwrap } from "@/lib/api";
import { safeNext } from "@/lib/nav";
import { fieldErrors, useErrorMessage } from "@/lib/use-error-message";

const PASSWORD_MIN = 10;

export function RegisterForm() {
  const t = useTranslations("auth.register");
  const tc = useTranslations("auth");
  const errorMessage = useErrorMessage();
  const next = safeNext(useSearchParams().get("next"));
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [touched, setTouched] = useState(false);

  const register = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/auth/register", {
          body: { display_name: name.trim(), email: email.trim(), password },
        }),
      ),
  });

  const tooShort = password.length < PASSWORD_MIN;
  const server = fieldErrors(register.error);
  const passwordError =
    touched && tooShort ? t("passwordShort", { min: PASSWORD_MIN }) : server.password;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setTouched(true);
    if (tooShort) return;
    register.mutate();
  };

  if (register.isSuccess) {
    return (
      <AuthShell
        title={t("doneTitle")}
        footer={
          <Link href="/login" className="font-medium text-accent-text underline underline-offset-2">
            {t("backToLogin")}
          </Link>
        }
      >
        <div className="flex flex-col items-start gap-3 rounded-card border border-border-1 bg-bg-1 p-5">
          <MailCheck size={24} aria-hidden className="text-accent-text" />
          <p className="m-0">{t("doneBody", { email: email.trim() })}</p>
        </div>
      </AuthShell>
    );
  }

  return (
    <AuthShell
      title={t("title")}
      subtitle={t("subtitle")}
      footer={
        <p className="m-0">
          {t("haveAccount")}{" "}
          <Link href="/login" className="font-medium text-accent-text underline underline-offset-2">
            {t("login")}
          </Link>
        </p>
      }
    >
      <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
        <Field label={tc("fields.name")} error={server.display_name}>
          {(a) => (
            <Input
              {...a}
              name="name"
              autoComplete="name"
              required
              maxLength={80}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          )}
        </Field>
        <Field label={tc("fields.email")} error={server.email}>
          {(a) => (
            <Input
              {...a}
              type="email"
              name="email"
              autoComplete="email"
              inputMode="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          )}
        </Field>
        <Field
          label={tc("fields.password")}
          hint={t("passwordHint", { min: PASSWORD_MIN })}
          error={passwordError}
        >
          {(a) => (
            <Input
              {...a}
              type="password"
              name="password"
              autoComplete="new-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onBlur={() => setTouched(true)}
            />
          )}
        </Field>
        {register.isError && Object.keys(server).length === 0 ? (
          <Notice tone="danger">{errorMessage(register.error)}</Notice>
        ) : null}
        <Button
          type="submit"
          variant="primary"
          size="touch"
          loading={register.isPending}
          disabled={!name.trim() || !email || !password}
        >
          {t("submit")}
        </Button>
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
