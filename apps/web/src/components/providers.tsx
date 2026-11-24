"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiProblem } from "@klick/client-sdk";
import { Toaster } from "@klick/ui";
import { useEffect, useState, type ReactNode } from "react";
import { applyTheme, useThemeStore } from "@/lib/stores";
import { registerServiceWorker } from "@/lib/pwa";

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 15_000,
            refetchOnWindowFocus: false,
            // Los errores 4xx no se arreglan reintentando.
            retry: (count, err) => !(err instanceof ApiProblem && err.status < 500) && count < 2,
          },
        },
      }),
  );
  const theme = useThemeStore((s) => s.theme);

  useEffect(() => applyTheme(theme), [theme]);
  useEffect(() => registerServiceWorker(), []);

  return (
    <QueryClientProvider client={client}>
      {children}
      <Toaster theme={theme} />
    </QueryClientProvider>
  );
}
