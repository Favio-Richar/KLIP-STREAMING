"use client";

import { useQuery } from "@tanstack/react-query";
import { Badge, EmptyState, ErrorState, Skeleton, type Tone } from "@klick/ui";
import { Film } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { api, unwrap, type Schemas } from "@/lib/api";
import { formatDuration } from "@/lib/format";
import { can, useActiveOrg } from "@/lib/queries";
import { useErrorMessage } from "@/lib/use-error-message";
import { UploadPanel } from "./upload-panel";

type Asset = Schemas["AssetOut"];

const STATUS_TONE: Record<string, Tone> = {
  uploading: "info",
  processing: "info",
  ready: "success",
  failed: "danger",
};

/** Mientras haya archivos en proceso la lista se refresca sola. */
const PENDING = new Set(["uploading", "processing"]);

export function Library() {
  const t = useTranslations("library");
  const errorMessage = useErrorMessage();
  const { active, isPending: orgPending } = useActiveOrg();
  const orgId = active?.id ?? null;

  const assets = useQuery({
    queryKey: ["assets", orgId],
    enabled: !!orgId,
    queryFn: () => unwrap(api.GET("/api/v1/assets", { params: { query: { limit: 50 } } })),
    refetchInterval: (q) => (q.state.data?.items.some((a) => PENDING.has(a.status)) ? 3000 : false),
  });

  if (orgPending || !active) {
    return <Skeleton className="h-44 w-full" />;
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl">{t("title")}</h1>
        <p className="m-0 text-text-2">{t("subtitle")}</p>
      </div>
      <UploadPanel orgId={active.id} canUpload={can(active, "media:write")} />
      <section aria-labelledby="files-title" className="flex flex-col gap-3">
        <h2 id="files-title" className="text-lg">
          {t("list.title")}
        </h2>
        {assets.isPending ? (
          <div className="flex flex-col gap-2" aria-busy="true">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : assets.isError ? (
          <ErrorState
            title={t("list.loadFailed")}
            description={errorMessage(assets.error)}
            retryLabel={t("list.retry")}
            onRetry={() => void assets.refetch()}
          />
        ) : assets.data.items.length === 0 ? (
          <EmptyState icon={Film} title={t("list.emptyTitle")} description={t("list.emptyBody")} />
        ) : (
          <AssetTable items={assets.data.items} />
        )}
      </section>
    </div>
  );
}

function AssetTable({ items }: { items: Asset[] }) {
  const t = useTranslations("library");
  const locale = useLocale();
  const when = new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" });
  return (
    <div className="overflow-x-auto rounded-card border border-border-1">
      <table className="w-full min-w-[40rem] border-collapse text-left">
        <caption className="sr-only">{t("list.title")}</caption>
        <thead className="bg-bg-2 text-sm text-text-2">
          <tr>
            <th scope="col" className="px-3 py-2 font-medium">
              {t("list.name")}
            </th>
            <th scope="col" className="px-3 py-2 font-medium">
              {t("list.kind")}
            </th>
            <th scope="col" className="px-3 py-2 font-medium">
              {t("list.status")}
            </th>
            <th scope="col" className="px-3 py-2 text-right font-medium">
              {t("list.duration")}
            </th>
            <th scope="col" className="px-3 py-2 font-medium">
              {t("list.added")}
            </th>
          </tr>
        </thead>
        <tbody>
          {items.map((a) => (
            <tr key={a.id} data-testid="asset-row" className="border-t border-border-1">
              <td className="max-w-[24rem] truncate px-3 py-2" title={a.title}>
                {a.title}
              </td>
              <td className="px-3 py-2">{t(`kind.${a.kind as "video" | "audio" | "image"}`)}</td>
              <td className="px-3 py-2">
                <Badge tone={STATUS_TONE[a.status] ?? "neutral"}>
                  {t(`status.${a.status as "uploading" | "processing" | "ready" | "failed"}`)}
                </Badge>
              </td>
              <td className="px-3 py-2 text-right font-mono tabular-nums">
                {formatDuration(a.duration_ms)}
              </td>
              <td className="px-3 py-2 text-text-2">{when.format(new Date(a.created_at))}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
