/**
 * Per-cut transitions on the Edit review's scene strip (docs/TRANSITIONS-V2-CONTRACT.md
 * section 5). A changed cut is sent as a full `timeline` replacement (the edit stage's
 * documented way to change the timeline), with the reviewer's music choice baked in because
 * the stage ignores `music_path` when a timeline comes with the edits.
 */
import type { EditApproveEdits, Timeline } from "../../types/timeline";

/** A cut: the transition out of one scene (type and length in seconds). */
export interface CutTransition {
  type: string;
  duration_s: number;
}

/** Same rule as the timeline builder: a transition takes at most a quarter of the shorter scene. */
export const MAX_SHARE_OF_SHORTER_SCENE = 0.25;

export function cutsFromTimeline(timeline: Timeline | null): CutTransition[] {
  return (timeline?.scenes ?? []).map((scene) => ({ type: scene.transition_out.type, duration_s: scene.transition_out.duration_s }));
}

export function sameCuts(a: ReadonlyArray<CutTransition>, b: ReadonlyArray<CutTransition>): boolean {
  return a.length === b.length && a.every((cut, index) => cut.type === b[index]!.type && Math.abs(cut.duration_s - b[index]!.duration_s) < 1e-6);
}

/** The longest the cut out of scene `index` may run (the last scene: a quarter of itself). */
export function maxCutSeconds(timeline: Timeline | null, index: number): number | undefined {
  const scenes = timeline?.scenes ?? [];
  const scene = scenes[index];
  if (!scene) return undefined;
  const length = scene.end_s - scene.start_s;
  const next = scenes[index + 1];
  const shorter = next ? Math.min(length, next.end_s - next.start_s) : length;
  return Math.max(0, Math.floor(shorter * MAX_SHARE_OF_SHORTER_SCENE * 100) / 100);
}

/** The timeline with the reviewer's cuts (and music choice, when it changed). */
export function timelineWithCuts(timeline: Timeline, cuts: ReadonlyArray<CutTransition>, music?: { path: string | null; licenseOk: boolean }): Timeline {
  return {
    ...timeline,
    scenes: timeline.scenes.map((scene, index) => {
      const cut = cuts[index];
      return cut ? { ...scene, transition_out: { ...scene.transition_out, type: cut.type, duration_s: cut.duration_s } } : scene;
    }),
    music: music ? { ...timeline.music, path: music.path, license_ok: music.path ? music.licenseOk : false } : timeline.music,
  };
}

/**
 * `edits` plus a `timeline` replacement when a cut changed. Returns the same object when the
 * cuts are as saved, so an approve without changes still sends no edits.
 */
export function withCutEdits(
  edits: EditApproveEdits,
  timeline: Timeline | null,
  cuts: ReadonlyArray<CutTransition>,
  savedCuts: ReadonlyArray<CutTransition>,
  music: { path: string | null; licenseOk: boolean },
): EditApproveEdits {
  if (!timeline || sameCuts(cuts, savedCuts)) return edits;
  const musicChanged = "music_path" in edits;
  return { ...edits, timeline: timelineWithCuts(timeline, cuts, musicChanged ? music : undefined) };
}
