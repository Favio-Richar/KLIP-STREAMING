"use client";

import {
  Badge,
  Button,
  EmptyState,
  ErrorState,
  IconButton,
  Input,
  Select,
  Skeleton,
  type Tone,
} from "@klick/ui";
import {
  Film,
  FileAudio,
  Image as ImageIcon,
  LayoutGrid,
  List,
  Search,
  SearchX,
} from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import Link from "next/link";
import { useMemo, useState } from "react";
import { formatDuration } from "@/lib/format";
import {
  EMPTY_FILTERS,
  hasFilters,
  useAssetList,
  useDebounced,
  useTags,
  type Asset,
  type AssetFilters,
} from "@/lib/library";
import { can, useActiveOrg } from "@/lib/queries";
import { useLibraryView } from "@/lib/stores";
import { useErrorMessage } from "@/lib/use-error-message";
import { UploadPanel } from "./upload-panel";

const STATUS_TONE: Record<string, Tone> = {
  uploading: "info",
  processing: "info",
  ready: "success",
  failed: "danger",
};

const KIND_ICON = { video: Film, audio: FileAudio, image: ImageIcon } as const;

export function Library() {
  const t = useTranslations("library");
  const errorMessage = useErrorMessage();
  const { active, isPending: orgPending } = useActiveOrg();
  const orgId = active?.id ?? null;
  const view = useLibraryView((s) => s.view);
  const setView = useLibraryView((s) => s.setView);

  const [draft, setDraft] = useState<AssetFilters>(EMPTY_FILTERS);
  const debouncedQ = useDebounced(draft.q);
  const filters = useMemo<AssetFilters>(() => ({ ...draft, q: debouncedQ }), [draft, debouncedQ]);
  const tags = useTags(orgId);
  const list = useAssetList(orgId, filters);
  const items = useMemo(() => list.data?.pages.flatMap((p) => p.items) ?? [], [list.data]);

  if (orgPending || !active) return <Skeleton className="h-44 w-full" />;

  const filtered = hasFilters(filters);
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl">{t("title")}</h1>
        <p className="m-0 text-text-2">{t("subtitle")}</p>
      </div>
      <UploadPanel orgId={active.id} canUpload={can(active, "media:write")} />

      <section aria-labelledby="files-title" className="flex flex-col gap-3">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <h2 id="files-title" className="text-lg">
            {t("list.title")}
          </h2>
          <div className="flex items-center gap-1" role="group" aria-label={t("view.label")}>
            <IconButton
              label={t("view.grid")}
              aria-pressed={view === "grid"}
              variant={view === "grid" ? "primary" : "secondary"}
              onClick={() => setView("grid")}
            >
              <LayoutGrid size={16} aria-hidden />
            </IconButton>
            <IconButton
              label={t("view.list")}
              aria-pressed={view === "list"}
              variant={view === "list" ? "primary" : "secondary"}
              onClick={() => setView("list")}
            >
              <List size={16} aria-hidden />
            </IconButton>
          </div>
        </div>

        <form
          role="search"
          aria-label={t("filters.label")}
          onSubmit={(e) => e.preventDefault()}
          className="flex flex-wrap items-center gap-2"
        >
          <div className="relative min-w-[14rem] flex-1">
            <Search
              size={16}
              aria-hidden
              className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-text-2"
            />
            <Input
              type="search"
              aria-label={t("filters.search")}
              placeholder={t("filters.searchPlaceholder")}
              className="w-full pl-9"
              value={draft.q}
              onChange={(e) => setDraft({ ...draft, q: e.target.value })}
            />
          </div>
          <Select
            aria-label={t("filters.kind")}
            value={draft.kind}
            onChange={(e) => setDraft({ ...draft, kind: e.target.value as AssetFilters["kind"] })}
          >
            <option value="">{t("filters.allKinds")}</option>
            <option value="video">{t("kind.video")}</option>
            <option value="audio">{t("kind.audio")}</option>
            <option value="image">{t("kind.image")}</option>
          </Select>
          <Select
            aria-label={t("filters.status")}
            value={draft.status}
            onChange={(e) =>
              setDraft({ ...draft, status: e.target.value as AssetFilters["status"] })
            }
          >
            <option value="">{t("filters.allStatuses")}</option>
            <option value="ready">{t("status.ready")}</option>
            <option value="processing">{t("status.processing")}</option>
            <option value="uploading">{t("status.uploading")}</option>
            <option value="failed">{t("status.failed")}</option>
          </Select>
          <Select
            aria-label={t("filters.tag")}
            value={draft.tagIds[0] ?? ""}
            disabled={(tags.data ?? []).length === 0}
            onChange={(e) => setDraft({ ...draft, tagIds: e.target.value ? [e.target.value] : [] })}
          >
            <option value="">{t("filters.allTags")}</option>
            {(tags.data ?? []).map((tag) => (
              <option key={tag.id} value={tag.id}>
                {tag.name}
              </option>
            ))}
          </Select>
          {hasFilters(draft) ? (
            <Button type="button" onClick={() => setDraft(EMPTY_FILTERS)}>
              {t("filters.clear")}
            </Button>
          ) : null}
        </form>

        {list.isPending ? (
          <div className="flex flex-col gap-2" aria-busy="true">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : list.isError ? (
          <ErrorState
            title={t("list.loadFailed")}
            description={errorMessage(list.error)}
            retryLabel={t("list.retry")}
            onRetry={() => void list.refetch()}
          />
        ) : items.length === 0 ? (
          filtered ? (
            <EmptyState
              icon={SearchX}
              title={t("list.noResultsTitle")}
              description={t("list.noResultsBody")}
              action={<Button onClick={() => setDraft(EMPTY_FILTERS)}>{t("filters.clear")}</Button>}
            />
          ) : (
            <EmptyState
              icon={Film}
              title={t("list.emptyTitle")}
              description={t("list.emptyBody")}
            />
          )
        ) : (
          <>
            {view === "grid" ? <AssetGrid items={items} /> : <AssetTable items={items} />}
            {list.hasNextPage ? (
              <div className="flex justify-center">
                <Button loading={list.isFetchingNextPage} onClick={() => void list.fetchNextPage()}>
                  {t("list.loadMore")}
                </Button>
              </div>
            ) : null}
          </>
        )}
      </section>
    </div>
  );
}

function Thumb({ asset, className }: { asset: Asset; className: string }) {
  const [broken, setBroken] = useState(false);
  const Icon = KIND_ICON[asset.kind as keyof typeof KIND_ICON] ?? Film;
  if (asset.thumbnail_url && !broken) {
    return (
      // eslint-disable-next-line @next/next/no-img-element -- URL firmada de corta vida: next/image no aplica
      <img
        src={asset.thumbnail_url}
        alt=""
        loading="lazy"
        className={`${className} object-cover`}
        onError={() => setBroken(true)}
      />
    );
  }
  return (
    <div className={`${className} grid place-items-center bg-bg-3 text-text-2`} aria-hidden>
      <Icon size={28} />
    </div>
  );
}

function AssetGrid({ items }: { items: Asset[] }) {
  const t = useTranslations("library");
  return (
    <ul className="m-0 grid list-none grid-cols-1 gap-3 p-0 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
      {items.map((a) => (
        <li key={a.id} data-testid="asset-row">
          <Link
            href={`/library/${a.id}`}
            className="flex h-full flex-col overflow-hidden rounded-card border border-border-1 bg-bg-1 hover:border-border-2"
          >
            <div className="relative">
              <Thumb asset={a} className="aspect-video w-full" />
              {a.duration_ms !== null ? (
                <span className="absolute bottom-1.5 right-1.5 rounded-tag bg-bg-1 px-1.5 font-mono text-xs tabular-nums">
                  {formatDuration(a.duration_ms)}
                </span>
              ) : null}
            </div>
            <div className="flex flex-col gap-1.5 p-3">
              <p className="m-0 truncate font-medium" title={a.title}>
                {a.title}
              </p>
              <div className="flex flex-wrap items-center gap-1.5">
                <Badge tone={STATUS_TONE[a.status] ?? "neutral"}>
                  {t(`status.${a.status as "uploading" | "processing" | "ready" | "failed"}`)}
                </Badge>
                <span className="text-sm text-text-2">
                  {t(`kind.${a.kind as "video" | "audio" | "image"}`)}
                </span>
              </div>
            </div>
          </Link>
        </li>
      ))}
    </ul>
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
                <Link
                  href={`/library/${a.id}`}
                  className="text-accent-text underline underline-offset-2"
                >
                  {a.title}
                </Link>
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
