"use client";

import { Toaster as Sonner, toast } from "sonner";

export { toast };

/** Avisos con superficie sólida y borde; el tema lo decide la aplicación. */
export function Toaster({ theme }: { theme: "dark" | "light" | "system" }) {
  return (
    <Sonner
      theme={theme}
      position="bottom-right"
      toastOptions={{
        style: {
          background: "var(--bg-3)",
          color: "var(--text-1)",
          border: "1px solid var(--border-2)",
          boxShadow: "none",
          borderRadius: "var(--radius-card)",
        },
      }}
    />
  );
}
