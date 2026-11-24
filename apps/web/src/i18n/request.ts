import { cookies, headers } from "next/headers";
import { getRequestConfig } from "next-intl/server";

export const LOCALES = ["es-CL", "en"] as const;
export type Locale = (typeof LOCALES)[number];
export const DEFAULT_LOCALE: Locale = "es-CL";
export const LOCALE_COOKIE = "klick_locale";

export const isLocale = (v: string | undefined | null): v is Locale =>
  !!v && (LOCALES as readonly string[]).includes(v);

/** Cookie del usuario → Accept-Language → es-CL. Sin prefijo de idioma en la URL. */
export async function resolveLocale(): Promise<Locale> {
  const fromCookie = (await cookies()).get(LOCALE_COOKIE)?.value;
  if (isLocale(fromCookie)) return fromCookie;
  const accept = (await headers()).get("accept-language") ?? "";
  for (const part of accept.split(",")) {
    const tag = part.split(";")[0]?.trim().toLowerCase() ?? "";
    if (tag.startsWith("es")) return "es-CL";
    if (tag.startsWith("en")) return "en";
  }
  return DEFAULT_LOCALE;
}

export default getRequestConfig(async () => {
  const locale = await resolveLocale();
  return {
    locale,
    timeZone: "America/Santiago",
    messages: (await import(`../../messages/${locale}.json`)).default,
  };
});
