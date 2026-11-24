"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";

/** Organización activa. Es solo una preferencia de interfaz: la autoridad es siempre la API. */
interface OrgState {
  orgId: string | null;
  setOrgId: (id: string | null) => void;
}

export const useOrgStore = create<OrgState>()(
  persist((set) => ({ orgId: null, setOrgId: (orgId) => set({ orgId }) }), { name: "klick.org" }),
);

export type ThemePreference = "system" | "dark" | "light";

interface ThemeState {
  theme: ThemePreference;
  setTheme: (t: ThemePreference) => void;
}

export const THEME_KEY = "klick.theme";

/** Aplica la preferencia al documento: `system` deja que decida `prefers-color-scheme`. */
export function applyTheme(theme: ThemePreference): void {
  const root = document.documentElement;
  if (theme === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
}

function readTheme(): ThemePreference {
  if (typeof localStorage === "undefined") return "system";
  const v = localStorage.getItem(THEME_KEY);
  return v === "dark" || v === "light" ? v : "system";
}

export const useThemeStore = create<ThemeState>()((set) => ({
  theme: readTheme(),
  setTheme: (theme) => {
    if (theme === "system") localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, theme);
    applyTheme(theme);
    set({ theme });
  },
}));

/** Datos del desafío MFA entre el login y la pantalla de código (viven solo en la pestaña). */
const MFA_KEY = "klick.mfa";
export const mfaChallenge = {
  set: (token: string) => sessionStorage.setItem(MFA_KEY, token),
  get: () => sessionStorage.getItem(MFA_KEY),
  clear: () => sessionStorage.removeItem(MFA_KEY),
};
