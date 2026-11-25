"use client";

import { Badge, Button, EmptyState, ErrorState, Skeleton, type Tone } from "@klick/ui";
import { Scissors } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import Link from "next/link";
import { useClips } from "@/lib/clips";
import { useActiveOrg } from "@/lib/queries";
import { useErrorMessage } from "@/lib/use-error-message";

const STATUS_TONE: Record<string, Tone> = {
  draft: "neutral",
  ready: "success",
  rendering: "info",
  failed: "danger",
};

export function ClipList() {
  const t = useTranslations("clips");
  const locale = useLocale();
  const errorMessage = useErrorMessage();
  const { active, isPending: orgPending } = useActiveOrg();
  const list = useClips(active?.id ?? null);
  const items = list.data?.pages.flatMap((p) => p.items) ?? [];
  const date = new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" });

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-lg font-medium">{t("heading")}</h1>
      {orgPending || list.isPending ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" aria-busy="true">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-20" />
          ))}
        </div>
      ) : list.isError ? (
        <ErrorState
          title={t("loadFailed")}
          description={errorMessage(list.error)}
          retryLabel={t("retry")}
          onRetry={() => void list.refetch()}
        />
      ) : items.length === 0 ? (
        <EmptyState
          icon={Scissors}
          title={t("emptyTitle")}
          description={t("emptyBody")}
          action={
            <Link href="/library" className="text-accent-text underline underline-offset-2">
              {t("goLibrary")}
            </Link>
          }
        />
      ) : (
        <>
          <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {items.map((clip) => (
              <li key={clip.id}>
                <Link
                  href={`/clips/${clip.id}`}
                  className="flex flex-col gap-1.5 rounded-control border border-border-1 bg-bg-1 p-3 hover:bg-bg-2"
                >
                  <span className="truncate font-medium">{clip.title}</span>
                  <span className="flex items-center gap-2 text-sm text-text-2">
                    <Badge tone={STATUS_TONE[clip.status] ?? "neutral"}>
                      {t.has(`status.${clip.status}`) ? t(`status.${clip.status}`) : clip.status}
                    </Badge>
                    {clip.updated_at ? date.format(new Date(clip.updated_at)) : null}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
          {list.hasNextPage ? (
            <div className="flex justify-center">
              <Button loading={list.isFetchingNextPage} onClick={() => void list.fetchNextPage()}>
                {t("loadMore")}
              </Button>
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}
