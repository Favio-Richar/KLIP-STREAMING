"use client";

import { Button, buttonClasses } from "@klick/ui";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useEffect } from "react";

/** Última red de seguridad de una pantalla: nunca muestra detalles técnicos al usuario. */
export default function ErrorPage({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const t = useTranslations("pages");
  useEffect(() => {
    console.error("ui_error", error.digest ?? "", error.message);
  }, [error]);
  return (
    <main
      id="main"
      role="alert"
      className="mx-auto flex min-h-dvh max-w-md flex-col justify-center gap-6 px-5"
    >
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl">{t("errorTitle")}</h1>
        <p className="m-0 text-text-2">{t("errorBody")}</p>
        {error.digest ? <p className="m-0 font-mono text-xs text-text-3">{error.digest}</p> : null}
      </div>
      <div className="flex gap-2">
        <Button variant="primary" size="touch" onClick={reset}>
          {t("retry")}
        </Button>
        <Link href="/" className={buttonClasses({ size: "touch" })}>
          {t("home")}
        </Link>
      </div>
    </main>
  );
}
