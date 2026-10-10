import { ChevronRight, History, Users, type LucideIcon } from "lucide-react";
import { Link } from "react-router";

import { useCan } from "../../../api/auth";
import { Card } from "../../ui/Card";
import { ACTIVITY_PATH, USERS_PATH } from "../routes";

interface TeamLink {
  to: string;
  title: string;
  detail: string;
  icon: LucideIcon;
}

/** Settings > Team: links to Users & roles (admins) and Activity (admins and editors). */
export function TeamLinksCard() {
  const canManageUsers = useCan("manage_users");
  const canViewActivity = useCan("view_activity");

  const links: TeamLink[] = [];
  if (canManageUsers) {
    links.push({
      to: USERS_PATH,
      title: "Users & roles",
      detail: "Add people, choose what each person can do, reset passwords and lock an account to one laptop.",
      icon: Users,
    });
  }
  if (canViewActivity) {
    links.push({
      to: ACTIVITY_PATH,
      title: "Activity",
      detail: canManageUsers
        ? "Who approved, sent back, exported or changed what, on which laptop, and when."
        : "What you approved, sent back, exported or changed, and when.",
      icon: History,
    });
  }
  if (links.length === 0) return null;

  return (
    <Card id="team" title="Team" description="Accounts are shared by every laptop through the shared folder; sign-ins stay on each laptop." flush>
      <ul className="divide-y divide-line">
        {links.map((link) => (
          <li key={link.to}>
            <Link to={link.to} className="group flex items-center gap-4 px-5 py-4 transition-colors hover:bg-surface-2/60 focus-visible:bg-surface-2/60 focus-visible:outline-none">
              <span className="flex size-9 shrink-0 items-center justify-center rounded-md bg-surface-2 text-ink-muted group-hover:text-accent-text">
                <link.icon className="size-4" aria-hidden />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-semibold text-ink">{link.title}</span>
                <span className="block text-[13px] text-ink-muted">{link.detail}</span>
              </span>
              <ChevronRight className="size-4 shrink-0 text-ink-faint" aria-hidden />
            </Link>
          </li>
        ))}
      </ul>
    </Card>
  );
}
