import { History, Laptop } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";

import { useCurrentUser, useRecentActivity } from "../../api/auth";
import { ApiError } from "../../api/client";
import { formatDateTime, formatRelative } from "../../lib/format";
import { can } from "../../lib/roles";
import type { AuditRow } from "../../types/auth";
import { Card } from "../ui/Card";
import { auditIcon, auditTitle, describeAction } from "./activity";

function rowLink(row: AuditRow): string | null {
  if (row.project_id && row.action !== "project.archive") return `/automation/projects/${encodeURIComponent(row.project_id)}`;
  if (row.channel_slug && row.action !== "channel.archive") return `/master/channels/${encodeURIComponent(row.channel_slug)}`;
  return null;
}

function ActivityRow({ row }: { row: AuditRow }) {
  const Icon = auditIcon(String(row.action));
  const title = auditTitle(row);
  const href = rowLink(row);
  const body: ReactNode = (
    <>
      <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full bg-surface-2 text-ink-muted">
        <Icon className="size-3.5" aria-hidden />
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-[13px] text-ink">
          <span className="font-semibold">{row.user || "Someone"}</span> {describeAction(row)}
          {title ? <span className="text-ink-muted">: &ldquo;{title}&rdquo;</span> : null}
        </p>
        <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-ink-faint">
          <span title={formatDateTime(row.at)}>{formatRelative(row.at)}</span>
          {row.machine ? (
            <span className="inline-flex items-center gap-1">
              <Laptop className="size-3" aria-hidden />
              {row.machine}
            </span>
          ) : null}
          {row.channel_slug && !title ? <span className="font-mono">{row.channel_slug}</span> : null}
        </p>
      </div>
    </>
  );
  return (
    <li>
      {href ? (
        <Link to={href} className="flex items-start gap-3 px-5 py-3 transition-colors hover:bg-surface-2/60">
          {body}
        </Link>
      ) : (
        <div className="flex items-start gap-3 px-5 py-3">{body}</div>
      )}
    </li>
  );
}

/** Dashboard card: the last audit rows this person may see (admins: the whole team). */
export function RecentActivity({ limit = 10 }: { limit?: number }) {
  const user = useCurrentUser();
  const activity = useRecentActivity(limit);
  const rows = activity.data ?? [];
  const status = activity.error instanceof ApiError ? activity.error.status : null;

  let empty: string | null = null;
  if (activity.isPending) empty = "Loading...";
  else if (status === 403) empty = "Your account cannot see the activity log.";
  else if (status === 404) empty = "This server does not keep an activity log yet.";
  else if (activity.isError) empty = "The activity log could not be loaded right now.";
  else if (rows.length === 0) empty = "Nothing has happened yet. Approvals, exports and sign-ins show up here.";

  return (
    <Card
      title="Recent activity"
      description={user?.role === "admin" ? "What the team did last, newest first." : "What you did last, newest first."}
      actions={
        can(user, "view_activity") ? (
          <Link to="/settings/activity" className="text-xs font-medium text-accent-text hover:underline">
            See all
          </Link>
        ) : null
      }
      flush
    >
      {empty ? (
        <div className="flex flex-col items-center gap-2 px-5 py-8 text-center">
          <History className="size-5 text-ink-faint" aria-hidden />
          <p className="text-[13px] text-ink-faint">{empty}</p>
        </div>
      ) : (
        <ul className="divide-y divide-line">
          {rows.slice(0, limit).map((row) => (
            <ActivityRow key={`${row.id}-${row.at}`} row={row} />
          ))}
        </ul>
      )}
    </Card>
  );
}
