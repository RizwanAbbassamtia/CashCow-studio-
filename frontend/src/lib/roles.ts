/**
 * Who may do what in the screens (docs/M5A-CONTRACT.md section 0, rule 5).
 *
 * - admin: everything, including team accounts, channels, settings and keys
 * - editor: run the pipeline, review, edit and export; reads channels
 * - viewer: reads everything, changes nothing
 *
 * The server enforces the same rules; these only decide which buttons are enabled, so a
 * person never meets a button that is sure to be refused. An unknown role gets viewer rights.
 */
import type { PublicUser, Role } from "../types/auth";

export type RoleAction =
  /** approve, redo or skip a stage; choose titles, edit scripts and storyboards */
  | "approve"
  /** start a new video, scan competitors, run or resume the pipeline */
  | "run"
  /** create, change or archive channels */
  | "edit_channels"
  /** Users & roles: add accounts, reset passwords, change roles */
  | "manage_users"
  /** change folders, API keys, models and render settings */
  | "settings"
  /** open the Activity log (editors see their own rows, admins see everyone's) */
  | "view_activity";

const ALLOWED: Record<RoleAction, readonly Role[]> = {
  approve: ["admin", "editor"],
  run: ["admin", "editor"],
  edit_channels: ["admin"],
  manage_users: ["admin"],
  settings: ["admin"],
  view_activity: ["admin", "editor"],
};

/** True when this signed-in person may do the action. Nobody signed in may do nothing. */
export function can(user: Pick<PublicUser, "role"> | null | undefined, action: RoleAction): boolean {
  if (!user) return false;
  return (ALLOWED[action] as readonly string[]).includes(user.role);
}

export const ROLE_LABELS: Record<Role, string> = {
  admin: "Admin",
  editor: "Editor",
  viewer: "Viewer",
};

export const ROLE_DESCRIPTIONS: Record<Role, string> = {
  admin: "Everything, including team accounts, channels, settings and API keys.",
  editor: "Runs the pipeline, reviews, edits and exports videos. Reads channels.",
  viewer: "Looks at everything, changes nothing.",
};

/** Plain-English label for any role string, including ones a newer server might add. */
export function roleLabel(role: string): string {
  return (ROLE_LABELS as Record<string, string>)[role] ?? (role ? role.charAt(0).toUpperCase() + role.slice(1) : "");
}

/** The tooltip on a button this account may not use. */
export const DENIED_REASONS: Record<RoleAction, string> = {
  approve: "Your account can look at reviews but not approve, redo or skip. Ask an admin for editor access.",
  run: "Your account cannot start or run videos. Ask an admin for editor access.",
  edit_channels: "Only an admin can add or change channels.",
  manage_users: "Only an admin can manage team accounts.",
  settings: "Only an admin can change the settings.",
  view_activity: "Your account cannot open the activity log.",
};
