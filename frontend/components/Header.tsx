import Link from "next/link";

export function Header({ active, demo = true }: { active?: "discover" | "trips"; demo?: boolean }) {
  const nav = (href: string, label: string, key: "discover" | "trips") => (
    <Link
      href={href}
      className={`text-sm ${active === key ? "font-semibold text-ink" : "text-muted hover:text-ink"}`}
      aria-current={active === key ? "page" : undefined}
    >
      {label}
    </Link>
  );
  return (
    <header className="border-b border-line bg-cream/90 backdrop-blur sticky top-0 z-20">
      <div className="mx-auto flex h-14 max-w-7xl items-center gap-6 px-4 sm:px-6">
        <Link href="/" className="font-serif text-xl font-semibold tracking-tight text-ink">
          TotalTrip
        </Link>
        <nav className="flex items-center gap-5">
          {nav("/", "Discover", "discover")}
          {nav("/trips", "My trips", "trips")}
        </nav>
        {demo && (
          <span className="ml-auto hidden rounded-full border border-line px-3 py-1 text-xs text-muted sm:inline">
            Concept prototype · illustrative prices
          </span>
        )}
      </div>
    </header>
  );
}
