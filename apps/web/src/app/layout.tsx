import type { Metadata, Viewport } from "next";
import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import { NextIntlClientProvider } from "next-intl";
import { getLocale, getTranslations } from "next-intl/server";
import type { ReactNode } from "react";
import { Providers } from "@/components/providers";
import "./globals.css";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("meta");
  return {
    title: { default: "Klick", template: "%s · Klick" },
    description: t("description"),
    applicationName: "Klick",
    manifest: "/manifest.webmanifest",
    icons: { icon: [{ url: "/icon.svg", type: "image/svg+xml" }], apple: "/apple-touch-icon.png" },
    appleWebApp: { capable: true, title: "Klick", statusBarStyle: "black-translucent" },
  };
}

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#0B0D10" },
    { media: "(prefers-color-scheme: light)", color: "#F6F7F9" },
  ],
  width: "device-width",
  initialScale: 1,
};

// Fija el tema antes del primer pintado para evitar el parpadeo entre oscuro y claro.
const THEME_SCRIPT = `try{var t=localStorage.getItem("klick.theme");if(t==="dark"||t==="light")document.documentElement.setAttribute("data-theme",t)}catch(e){}`;

export default async function RootLayout({ children }: { children: ReactNode }) {
  const locale = await getLocale();
  const t = await getTranslations("nav");
  return (
    <html
      lang={locale}
      className={`${GeistSans.variable} ${GeistMono.variable}`}
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body>
        <a href="#main" className="skip-link">
          {t("skip")}
        </a>
        <NextIntlClientProvider>
          <Providers>{children}</Providers>
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
