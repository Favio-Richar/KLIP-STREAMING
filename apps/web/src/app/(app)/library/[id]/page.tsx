import { getTranslations } from "next-intl/server";
import { AssetDetail } from "./detail";

export async function generateMetadata() {
  const t = await getTranslations("library.detail");
  return { title: t("title") };
}

export default async function AssetPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <AssetDetail id={id} />;
}
