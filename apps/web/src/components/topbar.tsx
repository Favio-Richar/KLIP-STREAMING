"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  Button,
  MenuContent,
  MenuItem,
  MenuLabel,
  MenuRoot,
  MenuSeparator,
  MenuTrigger,
  cn,
} from "@klick/ui";
import { Check, ChevronsUpDown, LogOut, Plus, ShieldCheck, Users } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Logo } from "./logo";
import { api, unwrap } from "@/lib/api";
import { useActiveOrg, type User } from "@/lib/queries";
import { mfaChallenge, useOrgStore } from "@/lib/stores";

/** `href: null` marca módulos de fases futuras: se muestran pero no se pueden activar. */
const NAV: ReadonlyArray<{ key: string; href: string | null }> = [
  { key: "panel", href: "/" },
  { key: "live", href: null },
  { key: "library", href: "/library" },
  { key: "clips", href: null },
  { key: "agent", href: null },
  { key: "publish", href: null },
  { key: "analytics", href: null },
];

/** Preguntas de cuenta que no dependen de la organización: no se invalidan al cambiar de org. */
const GLOBAL_KEYS = new Set<string>(["me", "orgs", "sessions"]);

export function TopBar({ user }: { user: User }) {
  const t = useTranslations("nav");
  const pathname = usePathname();
  const router = useRouter();
  const qc = useQueryClient();
  const { orgs, active } = useActiveOrg();
  const setOrgId = useOrgStore((s) => s.setOrgId);

  const switchOrg = (id: string) => {
    if (id === active?.id) return;
    setOrgId(id);
    qc.removeQueries({ predicate: (q) => !GLOBAL_KEYS.has(String(q.queryKey[0])) });
    router.push("/");
  };

  const logout = async () => {
    try {
      await unwrap(api.POST("/api/v1/auth/logout"));
    } finally {
      // Aunque la API falle, la sesión local se descarta: el usuario no debe quedar atrapado.
      mfaChallenge.clear();
      qc.clear();
      router.replace("/login");
    }
  };

  const initials = user.display_name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase())
    .join("");

  return (
    <header className="sticky top-0 z-30 border-b border-border-1 bg-bg-1">
      <div className="mx-auto flex h-12 max-w-[1440px] items-center gap-4 px-4 md:px-6">
        <Logo />
        <nav aria-label={t("main")} className="hidden min-w-0 flex-1 items-center gap-1 md:flex">
          <NavLinks pathname={pathname} />
        </nav>
        <div className="ml-auto flex items-center gap-2 md:ml-0">
          <MenuRoot>
            <MenuTrigger asChild>
              <Button size="dense" className="max-w-[200px]" aria-label={t("orgMenu")}>
                <span className="truncate">{active?.name ?? t("noOrg")}</span>
                <ChevronsUpDown size={14} aria-hidden className="shrink-0" />
              </Button>
            </MenuTrigger>
            <MenuContent>
              <MenuLabel>{t("organizations")}</MenuLabel>
              {orgs.map((o) => (
                <MenuItem
                  key={o.id}
                  onSelect={() => switchOrg(o.id)}
                  icon={
                    o.id === active?.id ? (
                      <Check size={16} aria-hidden />
                    ) : (
                      <span className="size-4" />
                    )
                  }
                >
                  {o.name}
                </MenuItem>
              ))}
              <MenuSeparator />
              {active ? (
                <MenuItem
                  onSelect={() => router.push("/settings/members")}
                  icon={<Users size={16} aria-hidden />}
                >
                  {t("members")}
                </MenuItem>
              ) : null}
              <MenuItem
                onSelect={() => router.push("/orgs/new")}
                icon={<Plus size={16} aria-hidden />}
              >
                {t("newOrg")}
              </MenuItem>
            </MenuContent>
          </MenuRoot>
          <MenuRoot>
            <MenuTrigger asChild>
              <button
                aria-label={t("userMenu")}
                className="flex size-10 items-center justify-center rounded-full border border-control bg-bg-2 text-sm font-semibold md:size-8"
              >
                {initials || "·"}
              </button>
            </MenuTrigger>
            <MenuContent>
              <MenuLabel>{user.email}</MenuLabel>
              <MenuItem
                onSelect={() => router.push("/settings/profile")}
                icon={<ShieldCheck size={16} aria-hidden />}
              >
                {t("profile")}
              </MenuItem>
              <MenuSeparator />
              <MenuItem
                tone="danger"
                onSelect={() => void logout()}
                icon={<LogOut size={16} aria-hidden />}
              >
                {t("logout")}
              </MenuItem>
            </MenuContent>
          </MenuRoot>
        </div>
      </div>
      <nav
        aria-label={t("main")}
        className="flex gap-1 overflow-x-auto border-t border-border-1 px-4 py-1 md:hidden"
      >
        <NavLinks pathname={pathname} />
      </nav>
    </header>
  );
}

function NavLinks({ pathname }: { pathname: string }) {
  const t = useTranslations("nav");
  return (
    <>
      {NAV.map((item) => {
        const base = "inline-flex h-10 shrink-0 items-center rounded-control px-3 text-base md:h-8";
        if (!item.href) {
          // Módulos de fases futuras: visibles pero no activables, con el motivo disponible.
          return (
            <span
              key={item.key}
              aria-disabled="true"
              title={t("soon")}
              className={cn(base, "cursor-not-allowed text-text-3")}
            >
              {t(item.key)}
            </span>
          );
        }
        const current = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
        return (
          <Link
            key={item.key}
            href={item.href}
            aria-current={current ? "page" : undefined}
            className={cn(
              base,
              "no-underline hover:bg-bg-3",
              current ? "bg-bg-3 font-medium text-text-1" : "text-text-2",
            )}
          >
            {t(item.key)}
          </Link>
        );
      })}
    </>
  );
}
