import { getTranslations } from "next-intl/server";
import { Suspense } from "react";
import { VerifyEmail } from "./verify";

export async function generateMetadata() {
  const t = await getTranslations("auth.verify");
  return { title: t("title") };
}

export default function VerifyEmailPage() {
  return (
    <Suspense>
      <VerifyEmail />
    </Suspense>
  );
}
