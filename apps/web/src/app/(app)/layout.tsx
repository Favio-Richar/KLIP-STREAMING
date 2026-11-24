"use client";

import { ApiProblem } from "@klick/client-sdk";
import { ErrorState, Notice, Skeleton } from "@klick/ui";
import { useTranslations } from "next-intl";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import { TopBar } from "@/components/topbar";
import { SESSION_EXPIRED_EVENT, SESSION_EXPIRED_FLAG } from "@/lib/api";
import { useMe } from "@/lib/queries";
import { useErrorMessage } from "@/lib/use-error-message";

/** Guardia de sesión: la autoridad es la API; aquí solo se decide qué mostrar. */
export default function AppLayout({ children }: { children: ReactNode }) {
  const t = useTranslations("app");
  const errorMessage = useErrorMessage();
  const router = useRouter();
  const pathname = usePathname();
  const me = useMe();
  const unauthenticated = me.error instanceof ApiProblem && me.error.status === 401;

  useEffect(() => {
    if (!unauthenticated) return;
    const expired = sessionStorage.getItem(SESSION_EXPIRED_FLAG) === "1";
    sessionStorage.removeItem(SESSION_EXPIRED_FLAG);
    router.replace(`/login?next=${encodeURIComponent(pathname)}${expired ? "&expired=1" : ""}`);
  }, [unauthenticated, router, pathname]);

  // Una petición cualquiera descubrió que la sesión murió: se vuelve a preguntar por el usuario.
  const refetchMe = me.refetch;
  useEffect(() => {
    const onExpired = () => void refetchMe();
    window.addEventListener(SESSION_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onExpired);
  }, [refetchMe]);

  if (me.isPending || unauthenticated) {
    return (
      <div className="min-h-dvh" aria-busy="true">
        <div className="h-12 border-b border-border-1 bg-bg-1" />
        <div className="mx-auto flex max-w-[1440px] flex-col gap-4 p-6">
          <Skeleton className="h-8 w-64" />
          <Skeleton className="h-40 w-full" />
        </div>
      </div>
    );
  }

  if (me.isError) {
    return (
      <main id="main" className="mx-auto max-w-xl p-6">
        <ErrorState
          title={t("loadFailed")}
          description={errorMessage(me.error)}
          retryLabel={t("retry")}
          onRetry={() => void me.refetch()}
        />
      </main>
    );
  }

  return (
    <div className="min-h-dvh">
      <TopBar user={me.data} />
      {!me.data.email_verified ? (
        <div className="mx-auto max-w-[1440px] px-4 pt-4 md:px-6">
          <Notice tone="warning">{t("verifyEmail")}</Notice>
        </div>
      ) : null}
      <main id="main" className="mx-auto w-full max-w-[1440px] px-4 py-6 md:px-6">
        {children}
      </main>
    </div>
  );
}
