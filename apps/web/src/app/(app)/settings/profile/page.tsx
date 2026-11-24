import { getTranslations } from "next-intl/server";
import { ProfileScreen } from "./profile";

export async function generateMetadata() {
  const t = await getTranslations("profile");
  return { title: t("title") };
}

export default function ProfilePage() {
  return <ProfileScreen />;
}
