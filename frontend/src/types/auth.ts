/**
 * Team accounts, sessions and the audit log (docs/M5A-CONTRACT.md sections 1 and 3).
 *
 * Mirrors the JSON of /api/auth/*, /api/users and /api/audit. Passwords never appear in any
 * of these shapes except the bodies the user types into, and a one-time password the server
 * hands to an admin once.
 */

export const ROLES = ["admin", "editor", "viewer"] as const;
export type Role = (typeof ROLES)[number];

/** Where the team list was read from: the shared Google Drive folder, this laptop's saved copy, or nowhere yet. */
export type RosterSource = "shared" | "cache" | "none";

/** An account as the server shows it. Never carries the password hash or salt. */
export interface PublicUser {
  /** lowercase, 3-32 characters of a-z 0-9 . _ - */
  username: string;
  display_name: string;
  role: Role;
  /** the Windows computer name this account is locked to, or null when it may sign in anywhere */
  machine?: string | null;
  /** true after an admin reset the password: the app asks for a new one before anything else */
  must_change_password: boolean;
  // ---- filled by the server; the admin list (GET /api/users) relies on them ----
  disabled?: boolean;
  last_login_at?: string | null;
  created_at?: string | null;
  /** username of the admin who added the account ("" or null for the first admin) */
  created_by?: string | null;
}

/** GET /api/auth/status (public): what the sign-in screens need before anyone signs in. */
export interface AuthStatus {
  /** no account exists yet: the app shows "Create the first admin account" */
  needs_first_run: boolean;
  signed_in: boolean;
  user?: PublicUser | null;
  /** this laptop's Windows computer name */
  machine: string;
  roster_source: RosterSource;
  /**
   * plain-English notice when the team list cannot be used as it is: read from this laptop's
   * saved copy, still being downloaded by Google Drive, saved by a newer version, partly unreadable
   */
  roster_notice?: string | null;
  /** set when the first admin needs a confirmation: the shared folder already holds channels but no accounts */
  first_run_warning?: string | null;
  /** the username kept by "Remember my login details" on this laptop (never a password) */
  saved_username?: string | null;
  /** "Use at least 10 characters." */
  password_rule?: string;
  min_password_length?: number;
}

/** POST /api/auth/login */
export interface LoginBody {
  username: string;
  password: string;
  /** "Keep me signed in on this laptop": 30 days instead of 12 hours */
  remember: boolean;
  /** "Remember my login details": the username (never the password) is filled in next time */
  remember_username: boolean;
}

/** POST /api/auth/first-run (only while no account exists) */
export interface FirstRunBody {
  username: string;
  display_name: string;
  password: string;
  /** the person read `first_run_warning` and goes ahead anyway (otherwise a 409) */
  confirm_team_folder?: boolean;
}

/**
 * POST /api/auth/first-run/shared-folder (only while no account exists on this laptop): use the
 * team's shared folder, so the team's accounts apply here. Answers the new AuthStatus.
 */
export interface TeamFolderBody {
  /** full path of the team's synced Google Drive folder */
  shared_dir: string;
}

/** POST /api/auth/change-password */
export interface ChangePasswordBody {
  current_password: string;
  new_password: string;
}

export const AUDIT_ACTIONS = [
  "login",
  "logout",
  "stage.approve",
  "stage.redo",
  "stage.skip",
  "stage.mode",
  "project.create",
  "project.archive",
  "export.done",
  "settings.update",
  "keys.update",
  "channel.create",
  "channel.update",
  "channel.archive",
  "user.create",
  "user.update",
  "user.reset",
  "user.disable",
] as const;
export type AuditAction = (typeof AUDIT_ACTIONS)[number];

/** One row of GET /api/audit: who did what, on which laptop, when. */
export interface AuditRow {
  id: number;
  /** ISO 8601 date-time */
  at: string;
  /** username */
  user: string;
  /** Windows computer name */
  machine: string;
  /** one of AUDIT_ACTIONS; newer servers may add more */
  action: AuditAction | (string & {});
  project_id?: string | null;
  channel_slug?: string | null;
  detail?: Record<string, unknown> | null;
}

/** Rules shared by the first-run, change-password and sign-in screens (the server checks them too). */
export const PASSWORD_MIN_LENGTH = 10;
export const USERNAME_PATTERN = /^[a-z0-9._-]{3,32}$/;
