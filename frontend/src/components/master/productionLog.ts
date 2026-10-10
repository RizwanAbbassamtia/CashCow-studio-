/** Production log helpers: the period filter and the rows as tab-separated text for a sheet. */
import type { ProductionLogRow } from "../../types/library";
import { renderPresetLabel } from "../../types/timeline";
import { daysAgo } from "./format";

export const PERIODS = [
  { id: "all", label: "All time" },
  { id: "7", label: "Last 7 days" },
  { id: "30", label: "Last 30 days" },
  { id: "90", label: "Last 90 days" },
  { id: "year", label: "This year" },
] as const;
export type PeriodId = (typeof PERIODS)[number]["id"];

/** The `since` filter for a period: a local date "YYYY-MM-DD", or undefined for all time. */
export function sinceFor(period: PeriodId, now: Date = new Date()): string | undefined {
  if (period === "all") return undefined;
  if (period === "year") return `${now.getFullYear()}-01-01`;
  return daysAgo(Number(period), now);
}

const FORMULA_START = /^[=+\-@\t\r]/;

/** One cell for a sheet: no tabs or line breaks, and never read as a formula. */
function cell(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return "";
  const text = String(value).replace(/[\t\r\n]+/g, " ").trim();
  return FORMULA_START.test(text) ? `'${text}` : text;
}

/** Seconds as "m:ss" (or "h:mm:ss") for the sheet. */
function clock(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds)) return "";
  const total = Math.max(0, Math.round(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = String(total % 60).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
}

/** The rows as tab-separated text with a header, ready to paste into Google Sheets or Excel. */
export function productionLogTsv(rows: readonly ProductionLogRow[], channelName: (slug: string) => string): string {
  const header = ["Exported", "Title", "Channel", "Format", "Language", "Sizes", "Length", "Size (MB)", "Exported by", "Laptop", "Export folder", "Project id"];
  const lines = rows.map((row) =>
    [
      row.at ? new Date(row.at).toLocaleString() : "",
      row.title,
      channelName(row.channel_slug),
      row.format === "shorts" ? "Short" : row.format === "long" ? "Long video" : row.format,
      row.language,
      row.presets.map(renderPresetLabel).join(", "),
      clock(row.duration_s),
      row.size_bytes !== null ? (row.size_bytes / (1024 * 1024)).toFixed(1) : "",
      row.user,
      row.machine,
      row.export_folder,
      row.project_id,
    ]
      .map(cell)
      .join("\t"),
  );
  return [header.join("\t"), ...lines].join("\n");
}
