"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Badge, Button, DialogRoot, Field, Input, Notice, toast } from "@klick/ui";
import { Copy, Download, ShieldCheck } from "lucide-react";
import { useTranslations } from "next-intl";
import QRCode from "qrcode";
import { useEffect, useState, type FormEvent } from "react";
import { api, qk, unwrap } from "@/lib/api";
import { useErrorMessage } from "@/lib/use-error-message";

type Step = "idle" | "scan" | "codes";

export function MfaSection({ enabled }: { enabled: boolean }) {
  const t = useTranslations("profile.mfa");
  const errorMessage = useErrorMessage();
  const qc = useQueryClient();
  const [step, setStep] = useState<Step>("idle");
  const [code, setCode] = useState("");
  const [recovery, setRecovery] = useState<string[]>([]);
  const [disableOpen, setDisableOpen] = useState(false);
  const [disableCode, setDisableCode] = useState("");
  const [disablePassword, setDisablePassword] = useState("");

  const enroll = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/auth/mfa/totp/enroll")),
    onSuccess: () => {
      setCode("");
      setStep("scan");
    },
  });

  const confirm = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/auth/mfa/totp/confirm", { body: { code: code.replace(/\s/g, "") } }),
      ),
    onSuccess: (res) => {
      setRecovery(res.recovery_codes);
      setStep("codes");
      void qc.invalidateQueries({ queryKey: qk.me });
    },
  });

  const disable = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/auth/mfa/totp/disable", {
          body: {
            code: disableCode.replace(/\s/g, ""),
            ...(disablePassword ? { password: disablePassword } : {}),
          },
        }),
      ),
    onSuccess: () => {
      toast.success(t("disabledToast"));
      setDisableOpen(false);
      setDisableCode("");
      setDisablePassword("");
      void qc.invalidateQueries({ queryKey: qk.me });
    },
  });

  const closeCodes = () => {
    setRecovery([]);
    setStep("idle");
  };

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(recovery.join("\n"));
      toast.success(t("copied"));
    } catch {
      toast.error(t("copyFailed"));
    }
  };

  const download = () => {
    const url = URL.createObjectURL(new Blob([recovery.join("\n") + "\n"], { type: "text/plain" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "klick-codigos-de-recuperacion.txt";
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <section
      aria-labelledby="mfa-h"
      className="flex flex-col gap-4 rounded-card border border-border-1 bg-bg-1 p-5"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <ShieldCheck size={20} aria-hidden className="text-text-2" />
          <h2 id="mfa-h" className="text-lg">
            {t("title")}
          </h2>
          <Badge tone={enabled ? "success" : "neutral"}>{enabled ? t("on") : t("off")}</Badge>
        </div>
        {enabled ? (
          <Button variant="danger" onClick={() => setDisableOpen(true)}>
            {t("disable")}
          </Button>
        ) : step === "idle" ? (
          <Button variant="primary" loading={enroll.isPending} onClick={() => enroll.mutate()}>
            {t("enable")}
          </Button>
        ) : null}
      </div>
      <p className="m-0 text-text-2">{enabled ? t("onBody") : t("offBody")}</p>
      {enroll.isError ? <Notice tone="danger">{errorMessage(enroll.error)}</Notice> : null}

      {step === "scan" && enroll.data ? (
        <Enrollment
          uri={enroll.data.otpauth_uri}
          secret={enroll.data.secret}
          code={code}
          setCode={setCode}
          pending={confirm.isPending}
          error={confirm.isError ? errorMessage(confirm.error) : null}
          onCancel={() => {
            setStep("idle");
            confirm.reset();
          }}
          onSubmit={() => confirm.mutate()}
        />
      ) : null}

      <DialogRoot
        open={step === "codes"}
        onOpenChange={(o) => !o && closeCodes()}
        title={t("codesTitle")}
        description={t("codesBody")}
        closeLabel={t("close")}
      >
        <ul className="m-0 mb-4 grid list-none grid-cols-2 gap-2 rounded-card border border-border-1 bg-bg-2 p-3 font-mono text-sm tabular-nums">
          {recovery.map((c) => (
            <li key={c}>{c}</li>
          ))}
        </ul>
        <div className="flex flex-wrap justify-end gap-2">
          <Button icon={<Copy size={16} aria-hidden />} onClick={() => void copy()}>
            {t("copy")}
          </Button>
          <Button icon={<Download size={16} aria-hidden />} onClick={download}>
            {t("download")}
          </Button>
          <Button variant="primary" onClick={closeCodes}>
            {t("savedThem")}
          </Button>
        </div>
      </DialogRoot>

      <DialogRoot
        open={disableOpen}
        onOpenChange={setDisableOpen}
        title={t("disableTitle")}
        description={t("disableBody")}
        closeLabel={t("close")}
      >
        <form
          className="flex flex-col gap-4"
          noValidate
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            disable.mutate();
          }}
        >
          <Field label={t("codeLabel")}>
            {(a) => (
              <Input
                {...a}
                autoComplete="one-time-code"
                inputMode="numeric"
                className="font-mono tabular-nums"
                value={disableCode}
                onChange={(e) => setDisableCode(e.target.value)}
              />
            )}
          </Field>
          <Field label={t("passwordLabel")} hint={t("passwordHint")}>
            {(a) => (
              <Input
                {...a}
                type="password"
                autoComplete="current-password"
                value={disablePassword}
                onChange={(e) => setDisablePassword(e.target.value)}
              />
            )}
          </Field>
          {disable.isError ? <Notice tone="danger">{errorMessage(disable.error)}</Notice> : null}
          <div className="flex justify-end gap-2">
            <Button onClick={() => setDisableOpen(false)}>{t("cancel")}</Button>
            <Button
              type="submit"
              variant="danger"
              loading={disable.isPending}
              disabled={disableCode.trim().length < 6}
            >
              {t("disableConfirm")}
            </Button>
          </div>
        </form>
      </DialogRoot>
    </section>
  );
}

function Enrollment(props: {
  uri: string;
  secret: string;
  code: string;
  setCode: (v: string) => void;
  pending: boolean;
  error: string | null;
  onCancel: () => void;
  onSubmit: () => void;
}) {
  const t = useTranslations("profile.mfa");
  const [qr, setQr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    // Se dibuja en el navegador: el secreto no sale hacia ningún servicio externo.
    void QRCode.toDataURL(props.uri, { width: 176, margin: 2, errorCorrectionLevel: "M" }).then(
      (url) => alive && setQr(url),
    );
    return () => {
      alive = false;
    };
  }, [props.uri]);

  return (
    <form
      className="flex flex-col gap-4 border-t border-border-1 pt-4 sm:flex-row"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        props.onSubmit();
      }}
    >
      <div className="flex shrink-0 flex-col items-center gap-2">
        {qr ? (
          // Imagen generada localmente (data URL): next/image no aporta nada aquí.
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={qr}
            width={176}
            height={176}
            alt={t("qrAlt")}
            className="rounded-card border border-border-2"
          />
        ) : (
          <div className="size-44 rounded-card bg-bg-3" aria-hidden />
        )}
      </div>
      <div className="flex flex-1 flex-col gap-4">
        <p className="m-0 text-text-2">{t("scan")}</p>
        <Field label={t("manualKey")}>
          {(a) => (
            <Input
              {...a}
              readOnly
              className="font-mono"
              value={props.secret}
              onFocus={(e) => e.currentTarget.select()}
            />
          )}
        </Field>
        <Field label={t("codeLabel")}>
          {(a) => (
            <Input
              {...a}
              autoComplete="one-time-code"
              inputMode="numeric"
              maxLength={7}
              className="font-mono tabular-nums tracking-widest"
              value={props.code}
              onChange={(e) => props.setCode(e.target.value)}
            />
          )}
        </Field>
        {props.error ? <Notice tone="danger">{props.error}</Notice> : null}
        <div className="flex gap-2">
          <Button onClick={props.onCancel}>{t("cancel")}</Button>
          <Button
            type="submit"
            variant="primary"
            loading={props.pending}
            disabled={props.code.trim().length < 6}
          >
            {t("confirm")}
          </Button>
        </div>
      </div>
    </form>
  );
}
