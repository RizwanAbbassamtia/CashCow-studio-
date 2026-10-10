/**
 * Activity-log rows as the Activity page shows them. The sentence itself comes from
 * `components/auth/activity.ts` (shared with the Dashboard's Recent activity, so an event
 * reads the same everywhere); this file adds the area of each action and the small facts a
 * row recorded (note, sizes, length, what changed from what to what...), in plain English
 * and never anything that looks like a secret.
 */
import { humanize } from "../../lib/format";
import { FRAMEWORK_TYPE_LABELS } from "../../lib/labels";
import { ROLE_LABELS } from "../../lib/roles";
import type { AuditRow } from "../../types/auth";
import type { StageMode } from "../../types/channel";
import { isFrameworkType } from "../../types/library";
import { renderPresetLabel } from "../../types/timeline";
import { AUDIT_ACTION_LABELS, isAuditAction, isRole } from "../../types/users";
import { describeAction } from "../auth/activity";
import { FORMAT_SHORT_LABELS, SOURCE_KIND_LABELS, STAGE_MODE_LABELS } from "../projects/stageMeta";
import type { BadgeTone } from "../ui/Badge";
import { bytesText, durationText } from "./format";

/** The area an action belongs to, for its colour. */
export type AuditArea = "video" | "channel" | "settings" | "team" | "session" | "other";

export function auditArea(action: string): AuditArea {
  if (action === "login" || action === "logout") return "session";
  if (action.startsWith("stage.") || action.startsWith("project.") || action.startsWith("export.")) return "video";
  if (action.startsWith("channel.")) return "channel";
  if (action.startsWith("settings.") || action.startsWith("keys.")) return "settings";
  if (action.startsWith("user.")) return "team";
  return "other";
}

export const AUDIT_AREA_TONES: Record<AuditArea, BadgeTone> = {
  video: "accent",
  channel: "info",
  settings: "warn",
  team: "ok",
  session: "neutral",
  other: "neutral",
};

/** The short label of an action ("Approved a stage"); for unknown actions a tidied name. */
export function auditActionLabel(action: string): string {
  return isAuditAction(action) ? AUDIT_ACTION_LABELS[action] : humanize(action.replace(/\./g, " "));
}

/**
 * One sentence for the "What" column: "Approved the Title stage". A channel is named by its
 * name rather than its folder name when `channelName` knows it.
 */
export function describeAuditRow(row: AuditRow, channelName?: (slug: string) => string): string {
  const named = row.channel_slug && channelName ? { ...row, channel_slug: channelName(row.channel_slug) } : row;
  const sentence = describeAction(named);
  return sentence ? sentence.charAt(0).toUpperCase() + sentence.slice(1) : auditActionLabel(row.action);
}

// ---- details --------------------------------------------------------------------------

const DETAIL_LABELS: Record<string, string> = {
  notes: "Note",
  note: "Note",
  reason: "Reason",
  mode: "Mode",
  presets: "Sizes",
  duration_s: "Length",
  size_bytes: "File size",
  files: "Files",
  role: "Role",
  display_name: "Name",
  machine: "Laptop",
  disabled: "Status",
  must_change_password: "New password needed",
  changed: "Changed",
  fields: "Changed",
  edits: "Changed in the review",
  sections: "Settings changed",
  set: "Keys saved",
  cleared: "Keys removed",
  export_folder: "Folder",
  archived_to: "Moved to",
  framework_uploaded: "Framework file",
  type: "Type",
  format: "Format",
  source: "Started from",
  language: "Language",
  channel: "Channel",
  name: "Name",
  remember: "Keep me signed in",
  first_run: "First admin account",
  by_self: "Changed by themselves",
  override_gates: "Checks overridden",
};

const FOLDER_LABELS: Record<string, string> = {
  shared_dir: "Shared folder",
  projects_dir: "Projects folder",
  exports_dir: "Exports folder",
};

const SECTION_LABELS: Record<string, string> = {
  llm: "Models",
  research: "Research",
  pipeline: "Pipeline",
  voice: "Voice",
  render: "Render",
  captions: "Captions",
};

/** Keys shown elsewhere in the row (the sentence, or the video title), never repeated in the details. */
const HIDDEN_KEYS = new Set(["stage", "user", "at", "action", "project_id", "channel_slug", "id", "by", "target", "username", "account", "title"]);

/** Words that suggest a value is a secret; such keys are never shown, whatever the server sent. */
const SECRET_HINT = /(password$|^password|hash|salt|secret|token|api_?key|cookie|session)/i;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function words(value: unknown): string[] {
  if (typeof value === "string") return value ? [value] : [];
  return Array.isArray(value) ? value.filter((item): item is string | number => typeof item === "string" || typeof item === "number").map(String) : [];
}

function valueText(key: string, value: unknown): string {
  if (value === null || value === undefined || value === "") return "";
  switch (key) {
    case "duration_s":
      return typeof value === "number" ? durationText(value) : "";
    case "size_bytes":
      return typeof value === "number" ? bytesText(value) : "";
    case "presets":
      return words(value).map(renderPresetLabel).join(", ");
    case "role":
      return isRole(value) ? ROLE_LABELS[value] : String(value);
    case "mode":
      return typeof value === "string" && value in STAGE_MODE_LABELS ? STAGE_MODE_LABELS[value as StageMode] : String(value);
    case "format":
      return value === "long" || value === "shorts" ? FORMAT_SHORT_LABELS[value] : String(value);
    case "source":
      return typeof value === "string" && value in SOURCE_KIND_LABELS ? SOURCE_KIND_LABELS[value as keyof typeof SOURCE_KIND_LABELS] : String(value);
    case "type":
      return isFrameworkType(value) ? FRAMEWORK_TYPE_LABELS[value] : humanize(String(value));
    case "sections":
      return words(value)
        .map((name) => SECTION_LABELS[name] ?? humanize(name))
        .join(", ");
    case "edits":
    case "changed":
    case "fields":
      return words(value).map(humanize).join(", ");
    case "disabled":
      return typeof value === "boolean" ? (value ? "Turned off" : "Active") : "";
    case "machine":
      return typeof value === "string" ? value : "";
    default:
      break;
  }
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(2);
  if (typeof value === "string") return value.length > 160 ? `${value.slice(0, 157)}...` : value;
  if (Array.isArray(value)) return words(value).join(", ");
  if (isRecord(value)) {
    // Only the names of what changed, never values the row did not explain.
    return Object.keys(value)
      .filter((name) => !SECRET_HINT.test(name))
      .map(humanize)
      .join(", ");
  }
  return "";
}

/** One side of a change: "Editor", "Any laptop", "Turned off", or "none". */
function sideText(field: string, value: unknown): string {
  if (value === null || value === undefined || value === "") return field === "machine" ? "Any laptop" : "none";
  return valueText(field, value) || "none";
}

export interface AuditDetailItem {
  key: string;
  label: string;
  value: string;
}

/** The extra facts of a row as label/value pairs, notes first. */
export function auditDetailItems(row: Pick<AuditRow, "detail">): AuditDetailItem[] {
  const items: AuditDetailItem[] = [];
  for (const [key, raw] of Object.entries(row.detail ?? {})) {
    if (HIDDEN_KEYS.has(key) || SECRET_HINT.test(key)) continue;
    // {"changes": {"role": ["editor", "admin"]}} -> "Role: Editor → Admin"
    if (key === "changes" && isRecord(raw)) {
      for (const [field, pair] of Object.entries(raw)) {
        if (SECRET_HINT.test(field) || !Array.isArray(pair) || pair.length !== 2) continue;
        items.push({ key: `changes.${field}`, label: DETAIL_LABELS[field] ?? humanize(field), value: `${sideText(field, pair[0])} → ${sideText(field, pair[1])}` });
      }
      continue;
    }
    // {"folders": {"shared_dir": "D:\\Drive\\Team"}} -> "Shared folder: D:\Drive\Team"
    if (key === "folders" && isRecord(raw)) {
      for (const [name, path] of Object.entries(raw)) {
        const text = typeof path === "string" ? (path === "default" ? "back to the default" : path) : "";
        if (text) items.push({ key: `folders.${name}`, label: FOLDER_LABELS[name] ?? humanize(name), value: text });
      }
      continue;
    }
    const value = valueText(key, raw);
    if (!value) continue;
    items.push({ key, label: DETAIL_LABELS[key] ?? humanize(key), value });
  }
  const isNote = (item: AuditDetailItem) => item.key === "notes" || item.key === "note" || item.key === "reason";
  return [...items.filter(isNote), ...items.filter((item) => !isNote(item))];
}
