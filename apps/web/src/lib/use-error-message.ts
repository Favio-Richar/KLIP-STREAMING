"use client";

import { useTranslations } from "next-intl";
import { ApiProblem } from "@klick/client-sdk";

/** Traduce un error cualquiera a un texto claro: catálogo por código, o mensaje genérico. */
export function useErrorMessage(): (err: unknown) => string {
  const t = useTranslations("errors");
  return (err) => {
    if (err instanceof ApiProblem) {
      if (t.has(err.code)) return t(err.code);
      if (err.status === 429) return t("rate_limited");
      if (err.status === 403) return t("forbidden");
      if (err.status >= 500) return t("server");
      return t("generic");
    }
    if (err instanceof TypeError) return t("network");
    return t("generic");
  };
}

/** Errores de validación por campo (`errors[].field`) traducidos a mensajes de formulario. */
export function fieldErrors(err: unknown): Record<string, string> {
  return err instanceof ApiProblem ? err.fields : {};
}
