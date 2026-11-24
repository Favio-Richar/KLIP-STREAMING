import { getTranslations } from "next-intl/server";
import { MembersScreen } from "./members";

export async function generateMetadata() {
  const t = await getTranslations("members");
  return { title: t("title") };
}

export default function MembersPage() {
  return <MembersScreen />;
}
