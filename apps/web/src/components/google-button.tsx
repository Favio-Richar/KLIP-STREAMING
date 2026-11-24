import { buttonClasses } from "@klick/ui";
import { useTranslations } from "next-intl";

/** Inicio con Google: navegación completa (la API responde con una redirección al proveedor). */
export function GoogleButton({ next }: { next: string }) {
  const t = useTranslations("auth");
  return (
    <a
      href={`/api/v1/auth/google/start?next=${encodeURIComponent(next)}`}
      className={buttonClasses({ size: "touch", className: "w-full gap-2" })}
    >
      <GoogleMark />
      {t("google")}
    </a>
  );
}

function GoogleMark() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden>
      <path
        fill="currentColor"
        d="M21.35 11.1H12v2.98h5.35c-.23 1.4-1.64 4.1-5.35 4.1a5.9 5.9 0 0 1 0-11.8c1.9 0 3.17.8 3.9 1.5l2.66-2.56A9.6 9.6 0 0 0 12 2.4a9.6 9.6 0 1 0 0 19.2c5.54 0 9.2-3.9 9.2-9.4 0-.63-.07-1.1-.15-1.6z"
      />
    </svg>
  );
}
