import { getTranslations } from "next-intl/server";
import { Suspense } from "react";
import { RegisterForm } from "./form";

export async function generateMetadata() {
  const t = await getTranslations("auth.register");
  return { title: t("title") };
}

export default function RegisterPage() {
  return (
    <Suspense>
      <RegisterForm />
    </Suspense>
  );
}
