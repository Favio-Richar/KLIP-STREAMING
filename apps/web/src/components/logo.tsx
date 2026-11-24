import Link from "next/link";

export function Logo({ href = "/" }: { href?: string }) {
  return (
    <Link
      href={href}
      className="inline-flex items-center gap-2 rounded-control font-semibold text-text-1 no-underline"
    >
      <svg width="24" height="24" viewBox="0 0 32 32" aria-hidden>
        <rect width="32" height="32" rx="7" fill="var(--accent)" />
        <path d="M9 7h4v7l7-7h5l-8 8 8 10h-5l-6-8v8H9z" fill="var(--on-accent)" />
      </svg>
      <span className="text-lg tracking-tight">Klick</span>
    </Link>
  );
}
