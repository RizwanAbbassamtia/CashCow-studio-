/**
 * Storyboard stage shapes. Mirrors backend/cashcow_studio/models/storyboard.py (Pydantic v2,
 * the source of truth); see docs/M1-M2-CONTRACT.md section 6.
 *
 * `04_storyboard/storyboard.json` is the file on disk; the review payload of
 * `GET /api/projects/{id}/stage/storyboard` carries the whole document plus the pickers'
 * option lists. Rectangles are fractions of the frame `[x, y, w, h]` in 0-1.
 */
import { DEFAULT_TRANSITION_PACK, MOTION_STYLES, TRANSITION_PACKS, type MotionStyle, type TransitionPack } from "./channel";
import type { GateResult, ProjectFormat } from "./project";
import { parseGateResults } from "./script";

export const MOTION_PRESETS = [
  "zoom_in",
  "zoom_out",
  "pan_left",
  "pan_right",
  "pan_up",
  "pan_down",
  "hold",
  "slow_push",
] as const;
export type MotionPreset = (typeof MOTION_PRESETS)[number];

export const MOTION_LABELS: Record<MotionPreset, string> = {
  zoom_in: "Zoom in",
  zoom_out: "Zoom out",
  pan_left: "Pan left",
  pan_right: "Pan right",
  pan_up: "Pan up",
  pan_down: "Pan down",
  hold: "Hold still",
  slow_push: "Slow push in",
};

export const POPUP_POSITIONS = ["top-left", "top-right", "bottom-left", "bottom-right", "center"] as const;
export type PopupPosition = (typeof POPUP_POSITIONS)[number];

export const POPUP_POSITION_LABELS: Record<PopupPosition, string> = {
  "top-left": "Top left",
  "top-right": "Top right",
  "bottom-left": "Bottom left",
  "bottom-right": "Bottom right",
  center: "Centre",
};

export const IMAGE_STATUSES = ["pending", "generated", "approved", "rejected"] as const;
export type ImageStatus = (typeof IMAGE_STATUSES)[number];

export const STORYBOARD_ASPECTS = ["16:9", "9:16"] as const;
export type StoryboardAspect = (typeof STORYBOARD_ASPECTS)[number];

/** [x, y, w, h] as shares of the frame, 0-1 */
export type Rect = [number, number, number, number];

export interface ScenePopup {
  /** at most 6 words; null = no popup */
  text: string | null;
  style: string;
  position: PopupPosition;
  in_offset_s: number;
  out_offset_s: number;
}

export interface SceneMotion {
  preset: MotionPreset;
  start_rect: Rect;
  end_rect: Rect;
}

export interface SceneTransition {
  /** a transition id from config/transitions.yaml (old xfade names such as "fade" still map) */
  type: string;
  duration_s: number;
  /** the story moment at this cut: calm, reveal, topic_jump or section (set by the AI) */
  beat?: string | null;
}

export interface SceneImage {
  path: string | null;
  status: ImageStatus;
  qa: Record<string, unknown> | null;
  /** optional, when the backend serves the picture over HTTP (M3) */
  url?: string | null;
}

export interface SceneLocks {
  image_prompt: boolean;
  popup: boolean;
  motion: boolean;
  transition_out: boolean;
  narration: boolean;
}
export type SceneLockField = keyof SceneLocks;

export interface StoryboardScene {
  index: number;
  /** never empty: a scene covers at least one sentence */
  sentence_ids: string[];
  narration: string;
  est_start_s: number;
  est_end_s: number;
  est_duration_s: number;
  /** text-free; the stage prefixes the style guide */
  image_prompt: string;
  negative_prompt: string;
  popup: ScenePopup;
  motion: SceneMotion;
  transition_out: SceneTransition;
  on_screen_text: string | null;
  image: SceneImage;
  locked: SceneLocks;
  notes: string[];
}

export interface StoryboardVariety {
  scenes: number;
  avg_scene_s: number;
  /** share of scenes with a popup or on-screen text, 0-1 */
  popup_share: number;
  distinct_transitions: number;
  distinct_motions: number;
  warnings: string[];
  /** uses per transition id, most used first */
  transition_histogram: Record<string, number>;
}

/** `04_storyboard/storyboard.json` */
export interface StoryboardDoc {
  project_id: string;
  format: ProjectFormat;
  aspect: StoryboardAspect;
  style_guide: string;
  negative_rules: string;
  popup_style: string;
  /** the channel's transition pack when the storyboard was planned */
  transition_pack: TransitionPack;
  /** this video's camera motion; null = the channel's default */
  motion_style: MotionStyle | null;
  generated_at: string;
  model: string;
  speaking_rate_wpm: number;
  scenes: StoryboardScene[];
  variety: StoryboardVariety;
  gate_results: GateResult[];
  notes: string[];
  schema_version: number;
}

/** One entry of config/transitions.yaml as the backend exposes it. */
export interface TransitionOption {
  type: string;
  label: string;
  /** default length for this video (pack range and Shorts factor applied) */
  duration_s: number;
  energy?: TransitionEnergy;
  /** the packs it belongs to */
  packs?: TransitionPack[];
  /** in the channel's pack (the AI only picks those) */
  in_pack?: boolean;
  feel?: string;
  preview_url?: string;
  poster_url?: string;
}

/** What `GET /api/projects/{id}/stage/storyboard` returns (`StoryboardReviewPayload`). */
export interface StoryboardReviewPayload {
  storyboard: StoryboardDoc;
  /** every transition, the channel's pack first; FALLBACK_TRANSITIONS when the backend sends none */
  transitions: TransitionOption[];
  motion_presets: MotionPreset[];
  popup_positions: PopupPosition[];
  /** [min, max] seconds per scene for this format */
  scene_band_s: [number, number];
  /** popup style names to suggest (optional in the payload) */
  popup_styles: string[];
  /** the channel's transition pack */
  transition_pack: TransitionPack;
  /** the channel's default motion style, when the server knows it */
  channel_motion_style: MotionStyle | null;
}

// ---------------------------------------------------------------------------
// The transition catalogue: frontend/public/transitions/catalogue.json, written from
// config/transitions.yaml by tools/make_transition_previews.py (docs/TRANSITIONS-V2-CONTRACT.md
// section 1). Read as a static file; mirrors backend/cashcow_studio/models/transitions.py.
// ---------------------------------------------------------------------------

export const TRANSITION_ENERGIES = ["calm", "medium", "high"] as const;
export type TransitionEnergy = (typeof TRANSITION_ENERGIES)[number];

export const TRANSITION_ENERGY_LABELS: Record<TransitionEnergy, string> = {
  calm: "Calm",
  medium: "Medium",
  high: "High",
};

/** The story moments a transition suits, in the words the picker shows. */
export const TRANSITION_BEAT_LABELS: Record<string, string> = {
  calm: "calm moments",
  reveal: "reveals",
  topic_jump: "jumps to a new idea",
  section: "the end of a section",
};

export interface TransitionPackInfo {
  label: string;
  description: string;
  /** [shortest, longest] default length of a transition in this pack */
  duration_s: [number, number];
  /** the transition out of the last scene */
  closer: string;
}

export interface TransitionCatalogueEntry {
  id: string;
  label: string;
  pack: TransitionPack[];
  energy: TransitionEnergy;
  duration_s: number;
  easing: string;
  params: Record<string, unknown>;
  feel: string;
  beats: string[];
  /** signature effects: none of them again within this many seconds */
  min_gap_s: number;
  /** animated preview, e.g. /transitions/film_dissolve.webp */
  preview_url: string;
  /** a still from the middle of the transition */
  poster_url: string;
  /** "engine" or "stand-in" (a placeholder dissolve until the previews are made again) */
  preview_engine: string;
}

export interface TransitionCatalogue {
  version: number;
  default: string;
  default_pack: TransitionPack;
  last_scene: string;
  shorts_duration_factor: number;
  packs: Record<TransitionPack, TransitionPackInfo>;
  /** old xfade names -> v2 ids */
  legacy: Record<string, string>;
  transitions: TransitionCatalogueEntry[];
}

/** `edits` on `POST .../stage/storyboard/approve`: the whole edited document. */
export interface StoryboardApproveEdits {
  storyboard: StoryboardDoc;
  /** approve even though a blocking check fails (the server refuses otherwise) */
  override_gates?: boolean;
}

/** Scene length targets in seconds (contract section 6), used when the payload has no band. */
export const SCENE_LENGTH: Record<ProjectFormat, [number, number]> = {
  long: [8, 12],
  shorts: [3, 4],
};

export const POPUP_MAX_WORDS = 6;
/** at least this share of scenes should carry a popup or on-screen text */
export const MIN_TEXT_SHARE = 0.35;

/**
 * The cinematic pack of config/transitions.yaml (v2), used only when the payload carries no
 * list. The backend's file (and catalogue.json) is the real source; keep this in step with it.
 */
export const FALLBACK_TRANSITIONS: TransitionOption[] = [
  { type: "film_dissolve", label: "Film dissolve", duration_s: 0.7, energy: "calm" },
  { type: "dip_black", label: "Dip to black", duration_s: 0.8, energy: "calm" },
  { type: "dip_white", label: "Dip to white", duration_s: 0.8, energy: "calm" },
  { type: "blur_dissolve", label: "Blur dissolve", duration_s: 0.7, energy: "calm" },
  { type: "luma_wipe_left", label: "Wipe left", duration_s: 0.6, energy: "medium" },
  { type: "luma_wipe_right", label: "Wipe right", duration_s: 0.6, energy: "medium" },
  { type: "luma_wipe_up", label: "Wipe up", duration_s: 0.6, energy: "medium" },
  { type: "luma_wipe_down", label: "Wipe down", duration_s: 0.6, energy: "medium" },
  { type: "luma_wipe_diag", label: "Diagonal wipe", duration_s: 0.6, energy: "medium" },
  { type: "light_leak_warm", label: "Warm light leak", duration_s: 0.8, energy: "medium" },
  { type: "film_burn", label: "Film burn", duration_s: 0.8, energy: "high" },
  { type: "morph_blend", label: "Morph blend", duration_s: 0.8, energy: "calm" },
  { type: "zoom_through", label: "Zoom through", duration_s: 0.7, energy: "medium" },
  { type: "crosswarp", label: "Cross warp", duration_s: 0.7, energy: "medium" },
];

/**
 * Old FFmpeg xfade names (storyboards from before transitions v2) and their v2 ids, as in the
 * `legacy` table of config/transitions.yaml; used when catalogue.json has not loaded.
 */
export const LEGACY_TRANSITIONS: Record<string, string> = {
  fade: "film_dissolve",
  dissolve: "film_dissolve",
  fadegrays: "film_dissolve",
  fadeblack: "dip_black",
  fadewhite: "dip_white",
  wipeleft: "luma_wipe_left",
  wiperight: "luma_wipe_right",
  wipeup: "luma_wipe_up",
  wipedown: "luma_wipe_down",
  smoothleft: "luma_wipe_left",
  smoothright: "luma_wipe_right",
  smoothup: "luma_wipe_up",
  smoothdown: "luma_wipe_down",
  diagtl: "luma_wipe_diag",
  diagtr: "luma_wipe_diag",
  diagbl: "luma_wipe_diag",
  diagbr: "luma_wipe_diag",
  slideleft: "push_left",
  slideright: "push_right",
  slideup: "push_up",
  slidedown: "push_down",
  zoomin: "zoom_through",
  circleopen: "film_dissolve",
  circleclose: "film_dissolve",
  radial: "film_dissolve",
  pixelize: "film_dissolve",
  hlslice: "film_dissolve",
  vuslice: "film_dissolve",
  distance: "film_dissolve",
};

export const DEFAULT_POPUP_STYLES = ["bold", "caption", "highlight", "sticker", "lower-third"];

// ---------------------------------------------------------------------------
// Defaults, identical to the Pydantic defaults.
// ---------------------------------------------------------------------------

export function fullRect(): Rect {
  return [0, 0, 1, 1];
}

export function defaultPopup(): ScenePopup {
  return { text: null, style: "", position: "bottom-left", in_offset_s: 0.4, out_offset_s: 3.5 };
}

export function defaultMotion(preset: MotionPreset = "zoom_in"): SceneMotion {
  return { preset, start_rect: fullRect(), end_rect: [0.1, 0.1, 0.8, 0.8] };
}

export function defaultLocks(): SceneLocks {
  return { image_prompt: false, popup: false, motion: false, transition_out: false, narration: false };
}

export function defaultSceneImage(): SceneImage {
  return { path: null, status: "pending", qa: null };
}

export function defaultScene(index: number): StoryboardScene {
  return {
    index,
    sentence_ids: [],
    narration: "",
    est_start_s: 0,
    est_end_s: 0,
    est_duration_s: 0,
    image_prompt: "",
    negative_prompt: "",
    popup: defaultPopup(),
    motion: defaultMotion(),
    transition_out: { type: "fade", duration_s: 0.6 },
    on_screen_text: null,
    image: defaultSceneImage(),
    locked: defaultLocks(),
    notes: [],
  };
}

export function emptyVariety(): StoryboardVariety {
  return { scenes: 0, avg_scene_s: 0, popup_share: 0, distinct_transitions: 0, distinct_motions: 0, warnings: [], transition_histogram: {} };
}

// ---------------------------------------------------------------------------
// Defensive parsing of the review payload. Unknown fields the backend adds later are kept
// (spread first, then the known fields on top) so an approve sends the whole document back.
// ---------------------------------------------------------------------------

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function str(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function nullableStr(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function num(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function bool(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function strList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function oneOf<T extends string>(value: unknown, allowed: ReadonlyArray<T>, fallback: T): T {
  return typeof value === "string" && (allowed as ReadonlyArray<string>).includes(value) ? (value as T) : fallback;
}

function listOf<T extends string>(value: unknown, allowed: ReadonlyArray<T>): T[] {
  return strList(value).filter((item): item is T => (allowed as ReadonlyArray<string>).includes(item));
}

function motionStyleOrNull(value: unknown): MotionStyle | null {
  return typeof value === "string" && (MOTION_STYLES as ReadonlyArray<string>).includes(value) ? (value as MotionStyle) : null;
}

function rect(value: unknown, fallback: Rect): Rect {
  if (!Array.isArray(value) || value.length !== 4) return fallback;
  const parts = value.map((item) => (typeof item === "number" && Number.isFinite(item) ? item : NaN));
  return parts.some(Number.isNaN) ? fallback : (parts as Rect);
}

function parsePopup(raw: unknown): ScenePopup {
  const base = defaultPopup();
  if (!isRecord(raw)) return base;
  return {
    ...raw,
    text: nullableStr(raw.text),
    style: str(raw.style, base.style),
    position: oneOf(raw.position, POPUP_POSITIONS, base.position),
    in_offset_s: num(raw.in_offset_s, base.in_offset_s),
    out_offset_s: num(raw.out_offset_s, base.out_offset_s),
  };
}

function parseMotion(raw: unknown): SceneMotion {
  const preset = oneOf(isRecord(raw) ? raw.preset : undefined, MOTION_PRESETS, "zoom_in");
  const base = defaultMotion(preset);
  if (!isRecord(raw)) return base;
  return { ...raw, preset, start_rect: rect(raw.start_rect, base.start_rect), end_rect: rect(raw.end_rect, base.end_rect) };
}

function parseTransition(raw: unknown): SceneTransition {
  if (!isRecord(raw)) return { type: "fade", duration_s: 0.6 };
  return { ...raw, type: str(raw.type, "fade"), duration_s: num(raw.duration_s, 0.6) };
}

function parseImage(raw: unknown): SceneImage {
  if (!isRecord(raw)) return defaultSceneImage();
  const image: SceneImage = {
    ...raw,
    path: nullableStr(raw.path),
    status: oneOf(raw.status, IMAGE_STATUSES, "pending"),
    qa: isRecord(raw.qa) ? raw.qa : null,
  };
  if (typeof raw.url === "string") image.url = raw.url;
  else delete image.url;
  return image;
}

function parseLocks(raw: unknown): SceneLocks {
  if (!isRecord(raw)) return defaultLocks();
  return {
    ...raw,
    image_prompt: bool(raw.image_prompt),
    popup: bool(raw.popup),
    motion: bool(raw.motion),
    transition_out: bool(raw.transition_out),
    narration: bool(raw.narration),
  };
}

export function parseScene(raw: unknown, position: number): StoryboardScene {
  const base = defaultScene(position);
  if (!isRecord(raw)) return base;
  const start = num(raw.est_start_s);
  const end = num(raw.est_end_s);
  return {
    ...raw,
    index: num(raw.index, position),
    sentence_ids: strList(raw.sentence_ids),
    narration: str(raw.narration),
    est_start_s: start,
    est_end_s: end,
    est_duration_s: num(raw.est_duration_s, Math.max(0, end - start)),
    image_prompt: str(raw.image_prompt),
    negative_prompt: str(raw.negative_prompt),
    popup: parsePopup(raw.popup),
    motion: parseMotion(raw.motion),
    transition_out: parseTransition(raw.transition_out),
    on_screen_text: nullableStr(raw.on_screen_text),
    image: parseImage(raw.image),
    locked: parseLocks(raw.locked),
    notes: strList(raw.notes),
  };
}

function countMap(value: unknown): Record<string, number> {
  const counts: Record<string, number> = {};
  if (!isRecord(value)) return counts;
  for (const [key, count] of Object.entries(value)) {
    if (typeof count === "number" && Number.isFinite(count)) counts[key] = count;
  }
  return counts;
}

export function parseVariety(raw: unknown): StoryboardVariety {
  const base = emptyVariety();
  if (!isRecord(raw)) return base;
  return {
    ...raw,
    scenes: num(raw.scenes),
    avg_scene_s: num(raw.avg_scene_s),
    popup_share: num(raw.popup_share),
    distinct_transitions: num(raw.distinct_transitions),
    distinct_motions: num(raw.distinct_motions),
    warnings: strList(raw.warnings),
    transition_histogram: countMap(raw.transition_histogram),
  };
}

export function parseStoryboardDoc(raw: unknown): StoryboardDoc | null {
  if (!isRecord(raw) || !Array.isArray(raw.scenes)) return null;
  const format: ProjectFormat = raw.format === "shorts" ? "shorts" : "long";
  return {
    ...raw,
    project_id: str(raw.project_id),
    format,
    aspect: oneOf(raw.aspect, STORYBOARD_ASPECTS, format === "shorts" ? "9:16" : "16:9"),
    style_guide: str(raw.style_guide),
    negative_rules: str(raw.negative_rules),
    popup_style: str(raw.popup_style),
    transition_pack: oneOf(raw.transition_pack, TRANSITION_PACKS, DEFAULT_TRANSITION_PACK),
    motion_style: motionStyleOrNull(raw.motion_style),
    generated_at: str(raw.generated_at),
    model: str(raw.model),
    speaking_rate_wpm: num(raw.speaking_rate_wpm, 150),
    scenes: raw.scenes.map((scene, index) => parseScene(scene, index)),
    variety: parseVariety(raw.variety),
    gate_results: parseGateResults(raw.gate_results),
    notes: strList(raw.notes),
    schema_version: num(raw.schema_version, 1),
  };
}

function optionExtras(item: Record<string, unknown>): Partial<TransitionOption> {
  const extras: Partial<TransitionOption> = {};
  if (typeof item.energy === "string" && (TRANSITION_ENERGIES as ReadonlyArray<string>).includes(item.energy)) {
    extras.energy = item.energy as TransitionEnergy;
  }
  const packs = listOf(item.packs ?? item.pack, TRANSITION_PACKS);
  if (packs.length > 0) extras.packs = packs;
  if (typeof item.in_pack === "boolean") extras.in_pack = item.in_pack;
  if (typeof item.feel === "string") extras.feel = item.feel;
  if (typeof item.preview_url === "string") extras.preview_url = item.preview_url;
  if (typeof item.poster_url === "string") extras.poster_url = item.poster_url;
  return extras;
}

/** Accepts `[{type|id,label,duration_s,...}]`, `["fade", ...]` or `{fade: {label, duration_s}}`. */
export function parseTransitions(raw: unknown): TransitionOption[] {
  const options: TransitionOption[] = [];
  if (Array.isArray(raw)) {
    for (const item of raw) {
      if (typeof item === "string") options.push({ type: item, label: item, duration_s: 0.6 });
      else if (isRecord(item) && (typeof item.type === "string" || typeof item.id === "string")) {
        const type = typeof item.type === "string" ? item.type : (item.id as string);
        options.push({
          type,
          label: str(item.label, type),
          duration_s: num(item.duration_s, num(item.default_duration_s, 0.6)),
          ...optionExtras(item),
        });
      }
    }
  } else if (isRecord(raw)) {
    for (const [type, value] of Object.entries(raw)) {
      const entry = isRecord(value) ? value : {};
      options.push({ type, label: str(entry.label, type), duration_s: num(entry.duration_s, num(entry.default_duration_s, 0.6)) });
    }
  }
  return options;
}

/**
 * Turn whatever `GET /api/projects/{id}/stage/storyboard` returns into a review payload.
 * Returns null when no storyboard document can be found in it.
 */
export function parseStoryboardPayload(raw: unknown): StoryboardReviewPayload | null {
  if (!isRecord(raw)) return null;
  const storyboard = parseStoryboardDoc(raw.storyboard) ?? parseStoryboardDoc(raw);
  if (!storyboard) return null;
  const transitions = parseTransitions(raw.transitions);
  const presets = listOf(raw.motion_presets, MOTION_PRESETS);
  const positions = listOf(raw.popup_positions, POPUP_POSITIONS);
  const band = Array.isArray(raw.scene_band_s) && raw.scene_band_s.length === 2 ? raw.scene_band_s.map((v) => num(v)) : [];
  const fallbackBand = SCENE_LENGTH[storyboard.format];
  return {
    storyboard,
    transitions: transitions.length > 0 ? transitions : FALLBACK_TRANSITIONS,
    motion_presets: presets.length > 0 ? presets : [...MOTION_PRESETS],
    popup_positions: positions.length > 0 ? positions : [...POPUP_POSITIONS],
    scene_band_s: band.length === 2 && band[0]! > 0 && band[1]! > band[0]! ? [band[0]!, band[1]!] : fallbackBand,
    popup_styles: strList(raw.popup_styles),
    transition_pack: oneOf(raw.transition_pack, TRANSITION_PACKS, storyboard.transition_pack),
    channel_motion_style: motionStyleOrNull(raw.channel_motion_style),
  };
}

// ---------------------------------------------------------------------------
// The transition catalogue (catalogue.json): parsing and the lookups the pickers need. The
// rules mirror TransitionCatalog in backend/cashcow_studio/models/transitions.py.
// ---------------------------------------------------------------------------

function parsePackInfo(raw: unknown, pack: TransitionPack): TransitionPackInfo {
  const fallback: TransitionPackInfo = { label: pack.charAt(0).toUpperCase() + pack.slice(1), description: "", duration_s: [0.5, 0.8], closer: "dip_black" };
  if (!isRecord(raw)) return fallback;
  const range = Array.isArray(raw.duration_s) && raw.duration_s.length === 2 ? raw.duration_s.map((v) => num(v)) : [];
  return {
    label: str(raw.label, fallback.label),
    description: str(raw.description),
    duration_s: range.length === 2 && range[0]! > 0 && range[1]! >= range[0]! ? [range[0]!, range[1]!] : fallback.duration_s,
    closer: str(raw.closer, fallback.closer),
  };
}

function parseCatalogueEntry(raw: unknown): TransitionCatalogueEntry | null {
  if (!isRecord(raw) || typeof raw.id !== "string" || !raw.id) return null;
  const id = raw.id;
  const packs = listOf(raw.pack ?? raw.packs, TRANSITION_PACKS);
  return {
    id,
    label: str(raw.label, id),
    pack: packs.length > 0 ? packs : [...TRANSITION_PACKS],
    energy: oneOf(raw.energy, TRANSITION_ENERGIES, "medium"),
    duration_s: num(raw.duration_s, 0.6),
    easing: str(raw.easing, "ease_in_out_cubic"),
    params: isRecord(raw.params) ? raw.params : {},
    feel: str(raw.feel),
    beats: strList(raw.beats),
    min_gap_s: num(raw.min_gap_s),
    preview_url: str(raw.preview_url, `/transitions/${id}.webp`),
    poster_url: str(raw.poster_url, `/transitions/${id}.poster.webp`),
    preview_engine: str(raw.preview_engine, "engine"),
  };
}

/** catalogue.json -> a catalogue, or null when it holds no transitions. */
export function parseTransitionCatalogue(raw: unknown): TransitionCatalogue | null {
  if (!isRecord(raw) || !Array.isArray(raw.transitions)) return null;
  const transitions = raw.transitions.map(parseCatalogueEntry).filter((entry): entry is TransitionCatalogueEntry => entry !== null);
  if (transitions.length === 0) return null;
  const packsRaw = isRecord(raw.packs) ? raw.packs : {};
  const packs = Object.fromEntries(TRANSITION_PACKS.map((pack) => [pack, parsePackInfo(packsRaw[pack], pack)])) as Record<TransitionPack, TransitionPackInfo>;
  const ids = new Set(transitions.map((entry) => entry.id));
  const legacy: Record<string, string> = {};
  if (isRecord(raw.legacy)) {
    for (const [old, next] of Object.entries(raw.legacy)) if (typeof next === "string" && ids.has(next)) legacy[old] = next;
  }
  const fallbackDefault = ids.has("film_dissolve") ? "film_dissolve" : transitions[0]!.id;
  const defaultId = typeof raw.default === "string" && ids.has(raw.default) ? raw.default : fallbackDefault;
  return {
    version: num(raw.version, 2),
    default: defaultId,
    default_pack: oneOf(raw.default_pack, TRANSITION_PACKS, DEFAULT_TRANSITION_PACK),
    last_scene: typeof raw.last_scene === "string" && ids.has(raw.last_scene) ? raw.last_scene : defaultId,
    shorts_duration_factor: num(raw.shorts_duration_factor, 0.6),
    packs,
    legacy,
    transitions,
  };
}

/** The v2 id for an id or an old xfade name ("wipeleft" -> "luma_wipe_left"); null when unknown. */
export function resolveTransitionId(catalogue: TransitionCatalogue | null, name: string | null | undefined): string | null {
  if (!name) return null;
  if (catalogue) {
    if (catalogue.transitions.some((entry) => entry.id === name)) return name;
    const clean = name.trim().toLowerCase().replace(/[\s-]+/g, "_");
    if (catalogue.transitions.some((entry) => entry.id === clean)) return clean;
    return catalogue.legacy[name] ?? catalogue.legacy[clean] ?? catalogue.legacy[clean.replace(/_/g, "")] ?? null;
  }
  return LEGACY_TRANSITIONS[name] ?? (FALLBACK_TRANSITIONS.some((option) => option.type === name) ? name : null);
}

export function findTransition(catalogue: TransitionCatalogue | null, name: string | null | undefined): TransitionCatalogueEntry | null {
  const id = resolveTransitionId(catalogue, name);
  return id && catalogue ? (catalogue.transitions.find((entry) => entry.id === id) ?? null) : null;
}

/** The transitions of one pack, in catalogue order. */
export function transitionsInPack(catalogue: TransitionCatalogue, pack: TransitionPack): TransitionCatalogueEntry[] {
  return catalogue.transitions.filter((entry) => entry.pack.includes(pack));
}

/**
 * Default length in seconds, like `duration_for` on the server: the entry's own length brought
 * into the pack's range when it belongs to `pack`, times the Shorts factor for Shorts.
 */
export function transitionDuration(catalogue: TransitionCatalogue, entry: TransitionCatalogueEntry, pack: TransitionPack | null, format: ProjectFormat): number {
  let seconds = entry.duration_s;
  if (pack && entry.pack.includes(pack)) {
    const [low, high] = catalogue.packs[pack].duration_s;
    seconds = Math.min(Math.max(seconds, low), high);
  }
  if (format === "shorts") seconds *= catalogue.shorts_duration_factor;
  return Math.round(seconds * 100) / 100;
}
