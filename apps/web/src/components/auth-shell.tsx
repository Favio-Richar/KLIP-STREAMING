import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { Logo } from "./logo";

/** Motivo de marca: una línea de tiempo con bloques sólidos de los colores de pista del editor. */
function TimelineMotif() {
  const rows: Array<{ color: string; blocks: Array<[number, number]> }> = [
    {
      color: "var(--track-video)",
      blocks: [
        [0, 38],
        [40, 22],
        [64, 36],
      ],
    },
    {
      color: "var(--track-captions)",
      blocks: [
        [6, 14],
        [24, 20],
        [48, 10],
        [62, 24],
      ],
    },
    {
      color: "var(--track-text)",
      blocks: [
        [14, 26],
        [58, 18],
      ],
    },
    {
      color: "var(--track-audio)",
      blocks: [
        [0, 26],
        [28, 34],
        [64, 36],
      ],
    },
  ];
  return (
    <svg viewBox="0 0 100 44" role="presentation" aria-hidden className="w-full max-w-md">
      <rect
        x="0"
        y="0"
        width="100"
        height="44"
        fill="var(--bg-1)"
        stroke="var(--border-2)"
        strokeWidth="0.4"
      />
      {rows.map((row, i) =>
        row.blocks.map(([x, w]) => (
          <rect
            key={`${i}-${x}`}
            x={x}
            y={5 + i * 9.5}
            width={w}
            height={7}
            rx="1"
            fill={row.color}
          />
        )),
      )}
      <rect x="47.6" y="0" width="0.8" height="44" fill="var(--text-1)" />
    </svg>
  );
}

export function AuthShell({
  title,
  subtitle,
  children,
  footer,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const t = useTranslations("auth.shell");
  return (
    <div className="grid min-h-dvh lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
      <aside className="hidden flex-col justify-between border-r border-border-1 bg-bg-1 p-10 lg:flex">
        <Logo />
        <div className="flex flex-col gap-6">
          <TimelineMotif />
          <p className="m-0 max-w-[30ch] text-3xl font-semibold leading-tight">{t("tagline")}</p>
          <p className="m-0 max-w-[46ch] text-text-2">{t("body")}</p>
        </div>
        <p className="m-0 text-xs text-text-3">{t("legal")}</p>
      </aside>
      <main id="main" className="flex items-center justify-center px-5 py-10">
        <div className="flex w-full max-w-[400px] flex-col gap-6">
          <div className="lg:hidden">
            <Logo />
          </div>
          <div className="flex flex-col gap-1.5">
            <h1 className="text-2xl">{title}</h1>
            {subtitle ? <p className="m-0 text-text-2">{subtitle}</p> : null}
          </div>
          {children}
          {footer ? <div className="text-sm text-text-2">{footer}</div> : null}
        </div>
      </main>
    </div>
  );
}
