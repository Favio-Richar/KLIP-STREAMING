import { getTranslations } from "next-intl/server";
import { Suspense } from "react";
import { ResetForm } from "./form";

export async function generateMetadata() {
  const t = await getTranslations("auth.reset");
  return { title: t("title") };
}

export default function ResetPasswordPage() {
  return (
    <Suspense>
      <ResetForm />
    </Suspense>
  );
}
