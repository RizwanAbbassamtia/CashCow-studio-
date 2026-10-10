/** Plain-English names, descriptions and colours for the eight stages and their statuses. */
import type { StageMode, StageName } from "../../types/channel";
import type { StageStatus } from "../../types/project";
import type { SegmentedOption } from "../form/SegmentedControl";
import type { BadgeTone } from "../ui/Badge";

export const STAGE_LABELS: Record<StageName, string> = {
  research: "Research",
  title: "Title",
  script: "Script",
  storyboard: "Storyboard",
  voice: "Voice",
  images: "Images",
  edit: "Edit",
  export: "Export",
};

export const STAGE_DESCRIPTIONS: Record<StageName, string> = {
  research: "Scan the competitors and pick one video to learn from",
  title: "Write seven title ideas and recommend one",
  script: "Write the full script and check it is original",
  storyboard: "Split the script into scenes with image prompts",
  voice: "Record the narration",
  images: "Generate and check the scene images",
  edit: "Build the timeline: motion, transitions, captions, music",
  export: "Render the video, thumbnail and description",
};

export const STAGE_STATUS_LABELS: Record<StageStatus, string> = {
  pending: "Not started",
  running: "Running",
  awaiting_review: "Waiting for review",
  awaiting_manual: "Waiting for your file",
  approved: "Approved",
  done: "Done",
  failed: "Failed",
  skipped: "Skipped",
};

export const STAGE_STATUS_TONES: Record<StageStatus, BadgeTone> = {
  pending: "neutral",
  running: "info",
  awaiting_review: "accent",
  awaiting_manual: "warn",
  approved: "ok",
  done: "ok",
  failed: "fail",
  skipped: "neutral",
};

export const STAGE_MODE_LABELS: Record<StageMode, string> = {
  auto: "Auto",
  review: "Review",
  manual: "Manual",
};

export const STAGE_MODE_OPTIONS: ReadonlyArray<SegmentedOption<StageMode>> = [
  { value: "auto", label: "Auto", description: "Runs and continues on its own" },
  { value: "review", label: "Review", description: "Runs, then waits for your approval" },
  { value: "manual", label: "Manual", description: "Skips the AI; you supply the result" },
];

export const STAGE_FOLDERS: Record<StageName, string> = {
  research: "01_research",
  title: "02_title",
  script: "03_script",
  storyboard: "04_storyboard",
  voice: "05_voice",
  images: "06_images",
  edit: "07_edit",
  export: "08_export",
};

/** The files a stage writes (docs/M1-M2-CONTRACT.md); what a person supplies for a manual stage. */
export const STAGE_FILES: Record<StageName, string[]> = {
  research: ["candidates.json", "pick.json", "video.json", "transcript.json", "competitor_thumbnail.jpg"],
  title: ["title.json"],
  script: ["script.md", "script.json", "speech.json", "originality.json"],
  storyboard: ["storyboard.json"],
  voice: ["voice.wav (the app cuts it into sentences and writes timing.json when you continue)"],
  images: ["scene_01.png, scene_02.png, ... (one picture per storyboard scene)"],
  edit: ["final_1080p.mp4 (or final_720p.mp4 / final_2160p.mp4 for the sizes you chose)"],
  export: ["metadata.json, the thumbnails and the exported video"],
};

export const FORMAT_SHORT_LABELS = { long: "Long video", shorts: "Short" } as const;

export const SOURCE_KIND_LABELS = {
  ai_pick: "AI pick",
  manual_pick: "Chosen by hand",
  own_topic: "Own topic",
} as const;

/** "$0.0123" or "$1.20" */
export function formatUsd(value: number): string {
  if (!Number.isFinite(value)) return "-";
  if (value === 0) return "$0.00";
  if (value < 0.01) return `$${value.toFixed(4)}`;
  return `$${value.toFixed(2)}`;
}
