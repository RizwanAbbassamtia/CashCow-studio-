import { Link, useLocation } from "react-router";

import { useCurrentUser } from "../api/auth";
import { useProjects } from "../api/projects";
import { useSystemInfo } from "../api/system";
import { ProductMark } from "../components/auth/ProductMark";
import { cn } from "../lib/cn";
import { can } from "../lib/roles";
import { areaFor, NAV_AREAS, type NavItem } from "./nav";

function NavEntry({ item, active, badge }: { item: NavItem; active: boolean; badge: number }) {
  return (
    <li>
      <Link
        to={item.to}
        aria-current={active ? "page" : undefined}
        className={cn(
          "group relative flex h-8 items-center gap-3 rounded-md px-3 text-[13px] font-medium transition-colors",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60",
          active ? "bg-surface-2 text-ink" : "text-ink-muted hover:bg-surface-2/70 hover:text-ink",
        )}
      >
        {active ? <span className="absolute left-0 top-1/2 h-5 w-0.5 -translate-y-1/2 rounded-r bg-accent" aria-hidden /> : null}
        <item.icon className={cn("size-4 shrink-0", active ? "text-accent-text" : "text-ink-faint group-hover:text-ink-muted")} aria-hidden />
        <span className="min-w-0 flex-1 truncate">{item.label}</span>
        {badge > 0 ? (
          <span
            className="rounded-full bg-accent/15 px-1.5 text-[11px] font-semibold leading-5 text-accent-text tabular-nums"
            title={`${badge} waiting for review`}
          >
            {badge}
          </span>
        ) : null}
      </Link>
    </li>
  );
}

/** Left navigation: the four areas with their pages; the area you are in is highlighted. */
export function Sidebar() {
  const system = useSystemInfo();
  const user = useCurrentUser();
  const projects = useProjects();
  const { pathname } = useLocation();
  const current = areaFor(pathname);
  const reviewCount = (projects.data ?? []).filter((project) => project.status === "awaiting_review").length;

  return (
    <aside className="flex h-screen w-60 shrink-0 flex-col border-r border-line bg-surface">
      <Link
        to="/automation"
        className="flex h-16 shrink-0 items-center gap-3 border-b border-line px-5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent/60"
      >
        <ProductMark size={32} />
        <div className="leading-tight">
          <div className="text-sm font-semibold text-ink">CashCow Studio</div>
          <div className="text-[11px] text-ink-faint">Faceless video pipeline</div>
        </div>
      </Link>

      <nav className="flex-1 overflow-y-auto px-3 py-3" aria-label="Main">
        <div className="flex flex-col gap-1.5">
          {NAV_AREAS.map((area) => {
            const items = area.items.filter((item) => !item.requires || can(user, item.requires));
            const active = current?.id === area.id;
            const headingId = `nav-area-${area.id}`;
            return (
              <section
                key={area.id}
                aria-labelledby={headingId}
                className={cn("rounded-lg px-1 pb-1.5 pt-1 transition-colors", active ? "bg-surface-2/45 ring-1 ring-line" : "")}
              >
                <Link
                  id={headingId}
                  to={items[0]?.to ?? area.base}
                  className={cn(
                    "flex h-7 items-center gap-2 rounded px-2 text-[11px] font-semibold uppercase tracking-[0.08em] transition-colors",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60",
                    active ? "text-accent-text" : "text-ink-faint hover:text-ink-muted",
                  )}
                >
                  <area.icon className="size-3.5 shrink-0" aria-hidden />
                  {area.label}
                </Link>
                <ul className="mt-0.5 flex flex-col gap-0.5">
                  {items.map((item) => (
                    <NavEntry
                      key={item.to}
                      item={item}
                      active={item.isActive(pathname)}
                      badge={item.badge === "review" ? reviewCount : 0}
                    />
                  ))}
                </ul>
              </section>
            );
          })}
        </div>
      </nav>

      <div className="border-t border-line px-5 py-3 text-[11px] leading-5 text-ink-faint">
        <div>Version {system.data?.version ?? "..."}</div>
        <div>Runs on this computer only (127.0.0.1)</div>
      </div>
    </aside>
  );
}
