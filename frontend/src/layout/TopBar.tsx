import { Moon, Sun } from "lucide-react";
import { useMatches } from "react-router";

import { UserMenu } from "../components/auth/UserMenu";
import { useTheme } from "../lib/theme";
import type { PublicUser } from "../types/auth";
import { DoctorPill } from "./DoctorPill";
import type { RouteHandle } from "./nav";

function asHandle(handle: unknown): RouteHandle {
  return typeof handle === "object" && handle !== null ? (handle as RouteHandle) : {};
}

/** The area and page of the current route, from the deepest route handles that name them. */
export function usePageHeading(): { area: string | null; title: string } {
  const matches = useMatches();
  let area: string | null = null;
  let title: string | null = null;
  for (let i = matches.length - 1; i >= 0; i -= 1) {
    const handle = asHandle(matches[i]?.handle);
    if (title === null && typeof handle.title === "string") title = handle.title;
    if (area === null && typeof handle.area === "string") area = handle.area;
  }
  return { area, title: title ?? "CashCow Studio" };
}

/** "Area · Page", e.g. "Automation · Dashboard". */
export function usePageTitle(): string {
  const { area, title } = usePageHeading();
  return area ? `${area} · ${title}` : title;
}

export function TopBar({ user }: { user: PublicUser }) {
  const { area, title } = usePageHeading();
  const { theme, toggle } = useTheme();

  return (
    // z-30: the blur makes the bar its own layer; without a z-index, positioned cards further
    // down the page would paint over the account menu that drops out of it.
    <header className="relative z-30 flex h-16 shrink-0 items-center justify-between gap-4 border-b border-line bg-surface/80 px-8 backdrop-blur">
      <h1 className="flex min-w-0 items-baseline gap-2 text-lg font-semibold tracking-tight text-ink">
        {area ? (
          <>
            <span className="shrink-0 font-medium text-ink-muted">{area}</span>
            <span className="shrink-0 text-ink-faint" aria-hidden>
              ·
            </span>
          </>
        ) : null}
        <span className="truncate">{title}</span>
      </h1>
      <div className="flex shrink-0 items-center gap-3">
        <DoctorPill />
        <button
          type="button"
          onClick={toggle}
          aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
          title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
          className="flex size-8 items-center justify-center rounded-md border border-line bg-surface-2 text-ink-muted transition-colors hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60"
        >
          {theme === "dark" ? <Sun className="size-4" aria-hidden /> : <Moon className="size-4" aria-hidden />}
        </button>
        <UserMenu user={user} />
      </div>
    </header>
  );
}
