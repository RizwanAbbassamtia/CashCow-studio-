/**
 * The four areas of the app and their pages (docs/M5A-CONTRACT.md section 3), in sidebar order.
 * The router in App.tsx uses the same paths; each route's `handle` carries `{area, title}` so
 * the top bar can show "Area · Page".
 */
import {
  BookOpen,
  CirclePlus,
  Clapperboard,
  Film,
  History,
  LayoutDashboard,
  Layers,
  ListChecks,
  Mic,
  MonitorPlay,
  Music,
  Scissors,
  ScrollText,
  Settings,
  SlidersHorizontal,
  Table2,
  Tv,
  Users,
  Workflow,
  type LucideIcon,
} from "lucide-react";

import type { RoleAction } from "../lib/roles";

export type AreaId = "automation" | "editing" | "master" | "settings";

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  /** true when this item is the page being shown */
  isActive: (pathname: string) => boolean;
  /** hidden for accounts that may not do this */
  requires?: RoleAction;
  /** shows the number of videos waiting for review */
  badge?: "review";
}

export interface NavArea {
  id: AreaId;
  label: string;
  icon: LucideIcon;
  /** every page of the area lives under this path */
  base: string;
  items: NavItem[];
}

/** Route `handle` used by the top bar title. */
export interface RouteHandle {
  area?: string;
  title?: string;
}

function trimmed(pathname: string): string {
  return pathname.length > 1 ? pathname.replace(/\/+$/, "") : pathname;
}

/** Exactly this path. */
function exact(path: string) {
  return (pathname: string) => trimmed(pathname) === path;
}

/** This path or anything below it. */
function under(path: string) {
  return (pathname: string) => {
    const current = trimmed(pathname);
    return current === path || current.startsWith(`${path}/`);
  };
}

export const NAV_AREAS: NavArea[] = [
  {
    id: "automation",
    label: "Automation",
    icon: Workflow,
    base: "/automation",
    items: [
      { to: "/automation", label: "Dashboard", icon: LayoutDashboard, isActive: exact("/automation") },
      { to: "/automation/research", label: "New video", icon: CirclePlus, isActive: under("/automation/research") },
      { to: "/automation/projects", label: "Projects", icon: Film, isActive: under("/automation/projects") },
      { to: "/automation/review", label: "Review queue", icon: ListChecks, isActive: under("/automation/review"), badge: "review" },
      { to: "/automation/batch", label: "Batch", icon: Layers, isActive: under("/automation/batch") },
    ],
  },
  {
    id: "editing",
    label: "Editing",
    icon: Clapperboard,
    base: "/editing",
    items: [
      {
        to: "/editing",
        label: "Edit room",
        icon: Scissors,
        isActive: (pathname) => under("/editing")(pathname) && !under("/editing/renders")(pathname),
      },
      { to: "/editing/renders", label: "Renders", icon: MonitorPlay, isActive: under("/editing/renders") },
    ],
  },
  {
    id: "master",
    label: "Master Sheet",
    icon: Table2,
    base: "/master",
    items: [
      { to: "/master/channels", label: "Channels", icon: Tv, isActive: (pathname) => under("/master/channels")(pathname) || exact("/master")(pathname) },
      { to: "/master/frameworks", label: "Frameworks", icon: BookOpen, isActive: under("/master/frameworks") },
      { to: "/master/voices", label: "Voices", icon: Mic, isActive: under("/master/voices") },
      { to: "/master/music", label: "Music", icon: Music, isActive: under("/master/music") },
      { to: "/master/production-log", label: "Production log", icon: ScrollText, isActive: under("/master/production-log") },
    ],
  },
  {
    id: "settings",
    label: "Settings",
    icon: Settings,
    base: "/settings",
    items: [
      { to: "/settings", label: "General", icon: SlidersHorizontal, isActive: exact("/settings") },
      { to: "/settings/users", label: "Users & roles", icon: Users, isActive: under("/settings/users"), requires: "manage_users" },
      { to: "/settings/activity", label: "Activity", icon: History, isActive: under("/settings/activity"), requires: "view_activity" },
    ],
  },
];

/** The area the path belongs to, if any. */
export function areaFor(pathname: string): NavArea | null {
  return NAV_AREAS.find((area) => under(area.base)(pathname)) ?? null;
}
