/**
 * Links into the four areas (docs/M5A-CONTRACT.md section 3). The old paths (/channels,
 * /projects, ...) still redirect, but the pages here link to the new ones directly.
 */

export function channelPath(slug: string): string {
  return `/master/channels/${encodeURIComponent(slug)}`;
}

/** New video (the Research page), opened on one channel. */
export function researchPath(slug: string): string {
  return `/automation/research/${encodeURIComponent(slug)}`;
}

export function projectPath(id: string): string {
  return `/automation/projects/${encodeURIComponent(id)}`;
}

/** The Edit room, opened on one video (and on its export panel when asked). */
export function editRoomPath(projectId?: string, stage?: "edit" | "export"): string {
  if (!projectId) return "/editing";
  return `/editing/${encodeURIComponent(projectId)}${stage === "export" ? "?stage=export" : ""}`;
}

export const RENDERS_PATH = "/editing/renders";
export const USERS_PATH = "/settings/users";
export const ACTIVITY_PATH = "/settings/activity";
export const PROJECTS_PATH = "/automation/projects";
export const REVIEW_PATH = "/automation/review";
