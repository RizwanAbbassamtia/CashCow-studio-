/**
 * Plain-English sentences for audit rows ("Imran approved the Title stage"), shared by the
 * dashboard's Recent activity and anything else that lists the audit log.
 */
import {
  Archive,
  Check,
  Film,
  KeyRound,
  LogIn,
  LogOut,
  PackageCheck,
  RotateCcw,
  Settings,
  SkipForward,
  SlidersHorizontal,
  Tv,
  UserCog,
  type LucideIcon,
} from "lucide-react";

import { humanize } from "../../lib/format";
import type { AuditRow } from "../../types/auth";
import { STAGE_LABELS } from "../projects/stageMeta";

function text(detail: AuditRow["detail"], key: string): string {
  const value = detail?.[key];
  return typeof value === "string" ? value.trim() : "";
}

/** "the Title stage", or "a stage" when the row does not say which. */
function stagePhrase(detail: AuditRow["detail"]): string {
  const stage = text(detail, "stage");
  if (!stage) return "a stage";
  return `the ${(STAGE_LABELS as Record<string, string>)[stage] ?? humanize(stage)} stage`;
}

function account(detail: AuditRow["detail"]): string {
  const name = text(detail, "username") || text(detail, "target") || text(detail, "user");
  return name ? `the account ${name}` : "an account";
}

function channel(row: AuditRow): string {
  return row.channel_slug ? `the channel ${row.channel_slug}` : "a channel";
}

/** What the person did, without their name: "approved the Title stage". */
export function describeAction(row: AuditRow): string {
  const detail = row.detail ?? null;
  switch (row.action) {
    case "login":
      return "signed in";
    case "logout":
      return "signed out";
    case "stage.approve":
      return `approved ${stagePhrase(detail)}`;
    case "stage.redo":
      return `sent ${stagePhrase(detail)} back with notes`;
    case "stage.skip":
      return `skipped ${stagePhrase(detail)}`;
    case "stage.mode": {
      const mode = text(detail, "mode");
      return mode ? `set ${stagePhrase(detail)} to ${humanize(mode)}` : `changed how ${stagePhrase(detail)} runs`;
    }
    case "project.create":
      return "started a new video";
    case "project.archive":
      return "archived a video";
    case "export.done":
      return "exported a video";
    case "settings.update":
      return "changed the settings";
    case "keys.update":
      return "changed the API keys";
    case "channel.create":
      return `added ${channel(row)}`;
    case "channel.update":
      return `changed ${channel(row)}`;
    case "channel.archive":
      return `archived ${channel(row)}`;
    case "user.create":
      return detail?.first_run === true ? "set up CashCow Studio as the first admin" : `added ${account(detail)}`;
    case "user.update": {
      const changed = Array.isArray(detail?.changed) ? detail.changed : [];
      if (detail?.by_self === true && changed.includes("password")) return "changed their password";
      return `changed ${account(detail)}`;
    }
    case "user.reset":
      return `reset the password of ${account(detail)}`;
    case "user.disable":
      return `turned off ${account(detail)}`;
    default:
      return humanize(String(row.action).replace(/\./g, " "));
  }
}

/** The video the row is about, when the server named it. */
export function auditTitle(row: AuditRow): string {
  return text(row.detail ?? null, "title");
}

export function auditIcon(action: string): LucideIcon {
  if (action === "login") return LogIn;
  if (action === "logout") return LogOut;
  if (action === "stage.approve") return Check;
  if (action === "stage.redo") return RotateCcw;
  if (action === "stage.skip") return SkipForward;
  if (action === "stage.mode") return SlidersHorizontal;
  if (action === "project.archive" || action === "channel.archive") return Archive;
  if (action.startsWith("project.")) return Film;
  if (action === "export.done") return PackageCheck;
  if (action === "keys.update") return KeyRound;
  if (action.startsWith("settings.")) return Settings;
  if (action.startsWith("channel.")) return Tv;
  if (action.startsWith("user.")) return UserCog;
  return Settings;
}
