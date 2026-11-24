import { getTranslations } from "next-intl/server";
import { Suspense } from "react";
import { MfaForm } from "./form";

export async function generateMetadata() {
  const t = await getTranslations("auth.mfa");
  return { title: t("title") };
}

export default function MfaPage() {
  return (
    <Suspense>
      <MfaForm />
    </Suspense>
  );
}
