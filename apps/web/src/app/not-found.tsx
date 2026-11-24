import { buttonClasses } from "@klick/ui";
import { getTranslations } from "next-intl/server";
import Link from "next/link";
import { Logo } from "@/components/logo";

export async function generateMetadata() {
  const t = await getTranslations("pages");
  return { title: t("notFoundTitle") };
}

export default async function NotFound() {
  const t = await getTranslations("pages");
  return (
    <main id="main" className="mx-auto flex min-h-dvh max-w-md flex-col justify-center gap-6 px-5">
      <Logo />
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl">{t("notFoundTitle")}</h1>
        <p className="m-0 text-text-2">{t("notFoundBody")}</p>
      </div>
      <Link
        href="/"
        className={buttonClasses({ variant: "primary", size: "touch", className: "self-start" })}
      >
        {t("home")}
      </Link>
    </main>
  );
}
