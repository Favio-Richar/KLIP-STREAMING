import { getTranslations } from "next-intl/server";
import { Suspense } from "react";
import { AcceptInvite } from "./accept";

export async function generateMetadata() {
  const t = await getTranslations("auth.invite");
  return { title: t("title") };
}

export default function AcceptInvitationPage() {
  return (
    <Suspense>
      <AcceptInvite />
    </Suspense>
  );
}
