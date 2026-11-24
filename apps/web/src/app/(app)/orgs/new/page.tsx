import { getTranslations } from "next-intl/server";
import { NewOrgForm } from "./form";

export async function generateMetadata() {
  const t = await getTranslations("org.create");
  return { title: t("title") };
}

export default function NewOrgPage() {
  return <NewOrgForm />;
}
