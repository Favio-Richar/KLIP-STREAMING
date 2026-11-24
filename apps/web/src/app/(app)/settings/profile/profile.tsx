"use client";

import { Badge, Field, Select, Skeleton } from "@klick/ui";
import { useLocale, useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useMe } from "@/lib/queries";
import { useThemeStore, type ThemePreference } from "@/lib/stores";
import { ApiTokens } from "./api-tokens";
import { MfaSection } from "./mfa";
import { Sessions } from "./sessions";

const LOCALE_COOKIE = "klick_locale";

export function ProfileScreen() {
  const t = useTranslations("profile");
  const locale = useLocale();
  const router = useRouter();
  const me = useMe();
  const theme = useThemeStore((s) => s.theme);
  const setTheme = useThemeStore((s) => s.setTheme);

  if (!me.data) return <Skeleton className="h-64 w-full" />;
  const user = me.data;

  const changeLocale = (value: string) => {
    document.cookie = `${LOCALE_COOKIE}=${value}; path=/; max-age=31536000; samesite=lax`;
    router.refresh();
  };

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-8">
      <h1 className="text-2xl">{t("title")}</h1>

      <section
        aria-labelledby="acc-h"
        className="flex flex-col gap-4 rounded-card border border-border-1 bg-bg-1 p-5"
      >
        <h2 id="acc-h" className="text-lg">
          {t("account.title")}
        </h2>
        <dl className="m-0 grid gap-x-6 gap-y-3 sm:grid-cols-[auto_1fr]">
          <dt className="text-text-2">{t("account.name")}</dt>
          <dd className="m-0">{user.display_name}</dd>
          <dt className="text-text-2">{t("account.email")}</dt>
          <dd className="m-0 flex flex-wrap items-center gap-2">
            {user.email}
            {user.email_verified ? (
              <Badge tone="success">{t("account.verified")}</Badge>
            ) : (
              <Badge tone="warning">{t("account.unverified")}</Badge>
            )}
          </dd>
        </dl>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t("account.language")}>
            {(a) => (
              <Select {...a} value={locale} onChange={(e) => changeLocale(e.target.value)}>
                <option value="es-CL">Español (Chile)</option>
                <option value="en">English</option>
              </Select>
            )}
          </Field>
          <Field label={t("account.theme")}>
            {(a) => (
              <Select
                {...a}
                value={theme}
                onChange={(e) => setTheme(e.target.value as ThemePreference)}
              >
                <option value="system">{t("account.themeSystem")}</option>
                <option value="dark">{t("account.themeDark")}</option>
                <option value="light">{t("account.themeLight")}</option>
              </Select>
            )}
          </Field>
        </div>
      </section>

      <MfaSection enabled={user.mfa_enabled} />
      <Sessions />
      <ApiTokens />
    </div>
  );
}
