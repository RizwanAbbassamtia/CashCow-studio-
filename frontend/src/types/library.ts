/**
 * The Master Sheet libraries and the production log (docs/M5A-CONTRACT.md section 2).
 * Mirrors the shapes of `GET /api/library/frameworks`, `/api/library/voices`,
 * `/api/library/music` and `/api/production-log` (backend/cashcow_studio/library/ and
 * api/library.py are the source of truth).
 *
 * Every list is gathered by the server from all channels in the shared folder. The parsers
 * at the bottom fill safe defaults for anything missing, so one odd channel file never
 * breaks a whole page.
 */
import { FRAMEWORK_TYPES, VOICE_TOOLS, type FrameworkType, type VoiceTool } from "./channel";

// ---- frameworks -----------------------------------------------------------------------

/** One framework file of one channel. */
export interface LibraryFramework {
  channel_slug: string;
  channel_name: string;
  /** a FrameworkType; kept as text so a type the server adds later still shows */
  type: string;
  name: string;
  /** relative to the shared folder, or a web link */
  path: string;
  version: string;
  /** "long", "shorts" or "both" */
  formats: string;
  /** null when the file is missing or the path is a link */
  size_bytes: number | null;
  updated_at: string | null;
  /** the text is ready for the writing model (`<name>.extracted.txt` or the app's cache) */
  extracted: boolean;
  /** the file is in the shared folder (always false for a link) */
  exists: boolean;
  /** the path is a web link (a Google Doc, say): shown, never opened by the app */
  is_link: boolean;
  /** plain English, when the stages cannot use this framework */
  problem: string | null;
}

export function isFrameworkType(value: unknown): value is FrameworkType {
  return typeof value === "string" && (FRAMEWORK_TYPES as readonly string[]).includes(value);
}

// ---- voices ---------------------------------------------------------------------------

export interface VoiceConsentSummary {
  /** a complete consent record was found (the channel's Consent fields or a consent.json) */
  present: boolean;
  owner_name: string | null;
  consented_at: string | null;
  /** the voice is a clone, so the voice stage needs the record; null when the server did not say */
  required: boolean | null;
  /** where the record was found */
  ref: string | null;
  /** plain English, when something is off */
  problem: string | null;
}

/** The voice one channel narrates with. */
export interface LibraryVoice {
  channel_slug: string;
  channel_name: string;
  /** a VoiceTool; kept as text so a tool the server adds later still shows */
  tool: string;
  /** the clone or voice id in the voice tool */
  clone_ref: string;
  name: string;
  language: string;
  consent: VoiceConsentSummary;
  sample_path: string;
  sample_exists: boolean;
}

export const VOICE_TOOL_NAMES: Record<VoiceTool, string> = {
  ai33: "ai33 (OpenSpeaker)",
  minimax: "MiniMax",
  cartesia: "Cartesia",
  inworld: "Inworld",
  fish_audio: "Fish Audio",
  azure: "Microsoft Azure",
  google: "Google",
  local: "Local (on this computer)",
  other: "Other",
};

export function voiceToolName(tool: string): string {
  return (VOICE_TOOLS as readonly string[]).includes(tool) ? VOICE_TOOL_NAMES[tool as VoiceTool] : tool || "Not chosen";
}

/**
 * A voice needs a consent record when the channel names a clone (`clone_ref`) or a voice
 * sample (`sample_path`); the voice stage refuses to run without one. A voice with neither
 * is a stock voice of the tool and needs none.
 */
export function voiceNeedsConsent(voice: Pick<LibraryVoice, "clone_ref" | "sample_path" | "consent">): boolean {
  if (voice.consent.required !== null) return voice.consent.required;
  return Boolean(voice.clone_ref.trim() || voice.sample_path.trim());
}

// ---- music ----------------------------------------------------------------------------

/** One track in a channel music folder (folders shared by channels are listed once). */
export interface LibraryMusicTrack {
  folder: string;
  file: string;
  duration_s: number | null;
  size_bytes: number | null;
  /** a `<track>.license.txt` next to it or a LICENSE file in the folder */
  license_ok: boolean;
  license_file: string | null;
  /** slugs of the channels whose music folder holds this track */
  used_by_channels: string[];
}

/** One channel music folder (`GET /api/library/music/folders`): says why a channel shows no tracks. */
export interface LibraryMusicFolder {
  folder: string;
  exists: boolean;
  tracks: number;
  licensed_tracks: number;
  /** a LICENSE file that covers the whole folder */
  license_file: string | null;
  used_by_channels: string[];
  /** plain English, when the folder cannot be used */
  problem: string | null;
}

// ---- production log -------------------------------------------------------------------

/** One exported video. Newest first. */
export interface ProductionLogRow {
  at: string;
  user: string;
  machine: string;
  channel_slug: string;
  title: string;
  project_id: string;
  /** "long" or "shorts" */
  format: string;
  language: string;
  /** the render sizes exported, e.g. ["1080p"] */
  presets: string[];
  duration_s: number | null;
  size_bytes: number | null;
  export_folder: string;
  /** the project folder was archived since */
  archived: boolean;
}

/** Query of `GET /api/production-log` and its CSV. */
export interface ProductionLogFilters {
  channel_slug?: string;
  /** a date (YYYY-MM-DD) or a full ISO time; rows older than it are left out */
  since?: string;
}

// ---------------------------------------------------------------------------------------
// Defensive parsing
// ---------------------------------------------------------------------------------------

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function str(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function nullableStr(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function nullableNum(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function bool(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function strList(value: unknown): string[] {
  if (typeof value === "string") {
    return value
      .split(/[,;]/)
      .map((item) => item.trim())
      .filter(Boolean);
  }
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && item.trim() !== "") : [];
}

function listOf(raw: unknown, key: string): unknown[] {
  if (Array.isArray(raw)) return raw;
  if (isRecord(raw) && Array.isArray(raw[key])) return raw[key] as unknown[];
  if (isRecord(raw) && Array.isArray(raw.items)) return raw.items;
  return [];
}

function parseFramework(raw: unknown): LibraryFramework | null {
  if (!isRecord(raw)) return null;
  const name = str(raw.name).trim();
  const path = str(raw.path).trim();
  if (!name && !path) return null;
  const slug = str(raw.channel_slug);
  return {
    channel_slug: slug,
    channel_name: str(raw.channel_name).trim() || slug,
    type: str(raw.type, "other") || "other",
    name: name || path.split(/[\\/]/).filter(Boolean).at(-1) || path,
    path,
    version: str(raw.version),
    formats: str(raw.formats, "both") || "both",
    size_bytes: nullableNum(raw.size_bytes),
    updated_at: nullableStr(raw.updated_at),
    extracted: bool(raw.extracted),
    exists: bool(raw.exists, raw.size_bytes !== null && raw.size_bytes !== undefined),
    is_link: bool(raw.is_link, /^[a-z][a-z0-9+.-]*:\/\//i.test(path)),
    problem: nullableStr(raw.problem),
  };
}

export function parseFrameworks(raw: unknown): LibraryFramework[] {
  return listOf(raw, "frameworks")
    .map(parseFramework)
    .filter((item): item is LibraryFramework => item !== null);
}

function parseConsent(raw: unknown): VoiceConsentSummary {
  const none: VoiceConsentSummary = { present: false, owner_name: null, consented_at: null, required: null, ref: null, problem: null };
  if (typeof raw === "boolean") return { ...none, present: raw };
  if (!isRecord(raw)) return none;
  return {
    present: bool(raw.present),
    owner_name: nullableStr(raw.owner_name),
    consented_at: nullableStr(raw.consented_at),
    required: typeof raw.required === "boolean" ? raw.required : null,
    ref: nullableStr(raw.ref),
    problem: nullableStr(raw.problem),
  };
}

function parseVoice(raw: unknown): LibraryVoice | null {
  if (!isRecord(raw)) return null;
  const slug = str(raw.channel_slug);
  if (!slug && !str(raw.channel_name)) return null;
  return {
    channel_slug: slug,
    channel_name: str(raw.channel_name).trim() || slug,
    tool: str(raw.tool),
    clone_ref: str(raw.clone_ref),
    name: str(raw.name),
    language: str(raw.language),
    consent: parseConsent(raw.consent),
    sample_path: str(raw.sample_path),
    sample_exists: bool(raw.sample_exists),
  };
}

export function parseVoices(raw: unknown): LibraryVoice[] {
  return listOf(raw, "voices")
    .map(parseVoice)
    .filter((item): item is LibraryVoice => item !== null);
}

function parseTrack(raw: unknown): LibraryMusicTrack | null {
  if (!isRecord(raw)) return null;
  const file = str(raw.file).trim() || str(raw.name).trim();
  if (!file) return null;
  return {
    folder: str(raw.folder),
    file,
    duration_s: nullableNum(raw.duration_s),
    size_bytes: nullableNum(raw.size_bytes),
    license_ok: bool(raw.license_ok),
    license_file: nullableStr(raw.license_file),
    used_by_channels: strList(raw.used_by_channels),
  };
}

export function parseMusic(raw: unknown): LibraryMusicTrack[] {
  return listOf(raw, "tracks")
    .map(parseTrack)
    .filter((item): item is LibraryMusicTrack => item !== null);
}

function count(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? Math.round(value) : 0;
}

function parseMusicFolder(raw: unknown): LibraryMusicFolder | null {
  if (!isRecord(raw)) return null;
  const folder = str(raw.folder).trim();
  if (!folder) return null;
  return {
    folder,
    exists: bool(raw.exists),
    tracks: count(raw.tracks),
    licensed_tracks: count(raw.licensed_tracks),
    license_file: nullableStr(raw.license_file),
    used_by_channels: strList(raw.used_by_channels),
    problem: nullableStr(raw.problem),
  };
}

export function parseMusicFolders(raw: unknown): LibraryMusicFolder[] {
  return listOf(raw, "folders")
    .map(parseMusicFolder)
    .filter((item): item is LibraryMusicFolder => item !== null);
}

function parseLogRow(raw: unknown): ProductionLogRow | null {
  if (!isRecord(raw)) return null;
  const projectId = str(raw.project_id);
  const title = str(raw.title).trim();
  if (!projectId && !title) return null;
  return {
    at: str(raw.at),
    user: str(raw.user),
    machine: str(raw.machine),
    channel_slug: str(raw.channel_slug),
    title,
    project_id: projectId,
    format: str(raw.format),
    language: str(raw.language),
    presets: strList(raw.presets),
    duration_s: nullableNum(raw.duration_s),
    size_bytes: nullableNum(raw.size_bytes),
    export_folder: str(raw.export_folder),
    archived: bool(raw.archived),
  };
}

/** `GET /api/production-log`, newest first. */
export function parseProductionLog(raw: unknown): ProductionLogRow[] {
  return listOf(raw, "rows")
    .map(parseLogRow)
    .filter((item): item is ProductionLogRow => item !== null)
    .sort((a, b) => b.at.localeCompare(a.at));
}
