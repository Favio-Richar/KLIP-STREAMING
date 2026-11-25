import { getTranslations } from "next-intl/server";
import { ClipList } from "./clip-list";

export async function generateMetadata() {
  const t = await getTranslations("clips");
  return { title: t("title") };
}

export default function ClipsPage() {
  return <ClipList />;
}
