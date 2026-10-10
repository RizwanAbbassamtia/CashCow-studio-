/** Small text helpers shared by the Master Sheet, Editing and team pages. */
import { formatBytes } from "../edit/editUtils";
import { formatDuration } from "../research/numbers";

/** "12.4 MB", or "-" when unknown. */
export function bytesText(bytes: number | null | undefined): string {
  return formatBytes(bytes) || "-";
}

/** "12:34", or "-" when unknown. */
export function durationText(seconds: number | null | undefined): string {
  return formatDuration(seconds) || "-";
}

/** A total running time: "45 s", "12 min", "2 h 05 min". */
export function totalDurationText(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "0 min";
  if (seconds < 60) return `${Math.round(seconds)} s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return `${hours} h ${String(minutes % 60).padStart(2, "0")} min`;
}

/** Sum of the known numbers; nulls are skipped. */
export function sumKnown(values: ReadonlyArray<number | null | undefined>): number {
  let total = 0;
  for (const value of values) if (typeof value === "number" && Number.isFinite(value)) total += value;
  return total;
}

/** True when the time lies within the last `days` days. */
export function isWithinDays(iso: string | null | undefined, days: number, now: number = Date.now()): boolean {
  if (!iso) return false;
  const time = new Date(iso).getTime();
  if (Number.isNaN(time)) return false;
  return now - time <= days * 86_400_000 && time <= now + 60_000;
}

/** Local date as "YYYY-MM-DD" (the value of a date input). */
export function dateInputValue(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

/** "YYYY-MM-DD" of `days` days ago. */
export function daysAgo(days: number, now: Date = new Date()): string {
  const date = new Date(now);
  date.setDate(date.getDate() - days);
  return dateInputValue(date);
}

/** https://docs.google.com/... and similar: a reference, not a file on disk. */
export function isWebLink(path: string): boolean {
  return /^[a-z][a-z0-9+.-]*:\/\//i.test(path.trim());
}

/** The last part of a path: "C:\\music\\calm.mp3" -> "calm.mp3". */
export function lastSegment(path: string): string {
  return path.split(/[\\/]+/).filter(Boolean).at(-1) ?? path;
}

/** Case-insensitive "contains" over several fields; an empty needle matches everything. */
export function matchesText(needle: string, ...values: ReadonlyArray<string | null | undefined>): boolean {
  const text = needle.trim().toLowerCase();
  if (!text) return true;
  return values.some((value) => typeof value === "string" && value.toLowerCase().includes(text));
}
