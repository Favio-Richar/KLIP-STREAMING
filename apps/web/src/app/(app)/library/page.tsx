import { getTranslations } from "next-intl/server";
import { Library } from "./library";

export async function generateMetadata() {
  const t = await getTranslations("library");
  return { title: t("title") };
}

export default function LibraryPage() {
  return <Library />;
}
