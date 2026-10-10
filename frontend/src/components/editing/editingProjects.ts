/** Which videos belong in the Edit room, how they are grouped and in what order. */
import { isProjectFinished, WAITING_STATUSES, type ProjectSummary, type StageStatus } from "../../types/project";

/** Editing: at the edit stage. Exporting: at the export stage. Done: exported. */
export type EditingGroup = "edit" | "export" | "done";

export const EDITING_GROUPS: readonly EditingGroup[] = ["edit", "export", "done"];

export const EDITING_GROUP_LABELS: Record<EditingGroup, string> = {
  edit: "Editing",
  export: "Exporting",
  done: "Done",
};

export const EDITING_GROUP_HINTS: Record<EditingGroup, string> = {
  edit: "At the edit stage: preview, music, captions and render sizes",
  export: "At the export stage: thumbnail, title, description and export folder",
  done: "Exported to the export folder",
};

type Summary = Pick<ProjectSummary, "current_stage" | "status">;

/** The Edit room group of a project, or null when it is at an earlier stage. */
export function editingGroup(project: Summary): EditingGroup | null {
  if (project.current_stage === "edit") return "edit";
  if (project.current_stage === "export") return isProjectFinished(project) ? "done" : "export";
  return null;
}

export function isInEditRoom(project: Summary): boolean {
  return editingGroup(project) !== null;
}

function urgency(status: StageStatus): number {
  if (WAITING_STATUSES.includes(status)) return 0;
  if (status === "running") return 1;
  if (status === "pending") return 2;
  return 3;
}

/** Waiting for a person first, then running, then the rest; newest first inside each. */
export function compareEditing(a: ProjectSummary, b: ProjectSummary): number {
  const byUrgency = urgency(a.status) - urgency(b.status);
  if (byUrgency !== 0) return byUrgency;
  return b.updated_at.localeCompare(a.updated_at);
}

/** The picture that stands for a video: the chosen thumbnail once exported, else its first scene. */
export function thumbnailSources(project: Summary): string[] {
  const firstScene = "06_images/scene_01.png";
  return project.current_stage === "export" ? ["08_export/thumbnail.png", firstScene] : [firstScene];
}
