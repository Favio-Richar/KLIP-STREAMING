import { getTranslations } from "next-intl/server";
import { ClipEditor } from "./editor";

export async function generateMetadata() {
  const t = await getTranslations("editor");
  return { title: t("title") };
}

export default async function ClipPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ClipEditor id={id} />;
}
