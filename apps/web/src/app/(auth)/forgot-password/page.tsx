import { getTranslations } from "next-intl/server";
import { ForgotForm } from "./form";

export async function generateMetadata() {
  const t = await getTranslations("auth.forgot");
  return { title: t("title") };
}

export default function ForgotPasswordPage() {
  return <ForgotForm />;
}
