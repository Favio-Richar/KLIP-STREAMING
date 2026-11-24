import { getTranslations } from "next-intl/server";
import { Dashboard } from "./dashboard";

export async function generateMetadata() {
  const t = await getTranslations("panel");
  return { title: t("title") };
}

export default function PanelPage() {
  return <Dashboard />;
}
