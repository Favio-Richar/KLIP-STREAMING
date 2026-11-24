"use client";

import { useMutation } from "@tanstack/react-query";
import { Button, Field, Input, Notice } from "@klick/ui";
import { MailCheck } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useState, type FormEvent } from "react";
import { AuthShell } from "@/components/auth-shell";
import { api, unwrap } from "@/lib/api";
import { useErrorMessage } from "@/lib/use-error-message";

export function ForgotForm() {
  const t = useTranslations("auth.forgot");
  const tc = useTranslations("auth");
  const errorMessage = useErrorMessage();
  const [email, setEmail] = useState("");
  const forgot = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/auth/password/forgot", { body: { email: email.trim() } })),
  });
  const back = (
    <Link href="/login" className="font-medium text-accent-text underline underline-offset-2">
      {t("back")}
    </Link>
  );

  const submit = (e: FormEvent) => {
    e.preventDefault();
    forgot.mutate();
  };

  // La API responde igual exista o no la cuenta; la pantalla tampoco lo revela.
  if (forgot.isSuccess) {
    return (
      <AuthShell title={t("doneTitle")} footer={back}>
        <div className="flex flex-col items-start gap-3 rounded-card border border-border-1 bg-bg-1 p-5">
          <MailCheck size={24} aria-hidden className="text-accent-text" />
          <p className="m-0">{t("doneBody")}</p>
        </div>
      </AuthShell>
    );
  }

  return (
    <AuthShell title={t("title")} subtitle={t("subtitle")} footer={back}>
      <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
        <Field label={tc("fields.email")}>
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
        {forgot.isError ? <Notice tone="danger">{errorMessage(forgot.error)}</Notice> : null}
        <Button
          type="submit"
          variant="primary"
          size="touch"
          loading={forgot.isPending}
          disabled={!email}
        >
          {t("submit")}
        </Button>
      </form>
    </AuthShell>
  );
}
