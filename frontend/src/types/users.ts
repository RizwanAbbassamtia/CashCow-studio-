/**
 * Team accounts and the activity log as the admin screens use them (docs/M5A-CONTRACT.md
 * sections 1 and 3). Builds on the shared shapes in `types/auth.ts` (roles, `PublicUser`,
 * `AuditRow`), which mirror the Pydantic models behind `/api/auth/*`, `/api/users` and
 * `/api/audit`.
 *
 * Passwords never live here. The only password the browser ever holds is the one-time
 * password the server returns when an admin adds a person or resets a password; the Users
 * page shows it once and forgets it when the dialog closes.
 *
 * The parsers at the bottom accept the contract shapes and fill safe defaults for anything
 * missing, so a screen never breaks on a field the server left out.
 */
import { AUDIT_ACTIONS, ROLES, USERNAME_PATTERN, type AuditAction, type AuditRow, type PublicUser, type Role } from "./auth";

export const DEFAULT_ROLE: Role = "editor";

export function isRole(value: unknown): value is Role {
  return typeof value === "string" && (ROLES as readonly string[]).includes(value);
}

// ---- accounts -------------------------------------------------------------------------

export const USERNAME_RULE = "3 to 32 characters: small letters, numbers, dots, dashes or underscores. No spaces.";

/** Why a username is not acceptable, or null when it is fine. */
export function usernameProblem(value: string): string | null {
  const text = value.trim();
  if (!text) return "Type a username.";
  if (text.length < 3) return "A username needs at least 3 characters.";
  if (text.length > 32) return "A username can have at most 32 characters.";
  if (/\s/.test(text)) return "A username cannot contain spaces.";
  if (text !== text.toLowerCase()) return "Use small letters only.";
  if (!USERNAME_PATTERN.test(text)) return "Only letters, numbers, dots, dashes and underscores are allowed.";
  return null;
}

/** A row of `GET /api/users` (admins only), with every field filled in. */
export interface TeamUser extends PublicUser {
  /** the Windows computer name the account is locked to, or null for any laptop */
  machine: string | null;
  disabled: boolean;
  last_login_at: string | null;
  created_at: string | null;
  created_by: string | null;
}

/** Body of `POST /api/users`. The server makes up the password and returns it once. */
export interface UserCreate {
  username: string;
  display_name: string;
  role: Role;
  machine?: string | null;
}

/** Response of `POST /api/users`: the account and its one-time password. */
export interface UserCreated {
  user: TeamUser;
  password: string;
}

/** Body of `PUT /api/users/{username}`: only the fields present are changed. */
export interface UserUpdate {
  display_name?: string;
  role?: Role;
  /** a computer name locks the account to that laptop; null removes the lock */
  machine?: string | null;
  disabled?: boolean;
}

/** Response of `POST /api/users/{username}/reset-password`. */
export interface PasswordReset {
  password: string;
}

// ---- activity log ---------------------------------------------------------------------

/** Query of `GET /api/audit`. Editors only ever get their own rows back. */
export interface AuditFilters {
  limit?: number;
  user?: string;
  action?: string;
  project_id?: string;
}

export const AUDIT_DEFAULT_LIMIT = 200;
export const AUDIT_LIMIT_CHOICES = [100, 200, 500, 1000] as const;

/** Short names for the filter list. */
export const AUDIT_ACTION_LABELS: Record<AuditAction, string> = {
  login: "Signed in",
  logout: "Signed out",
  "stage.approve": "Approved a stage",
  "stage.redo": "Sent a stage back",
  "stage.skip": "Skipped a stage",
  "stage.mode": "Changed a stage mode",
  "project.create": "Started a video",
  "project.archive": "Archived a video",
  "export.done": "Exported a video",
  "settings.update": "Changed settings",
  "keys.update": "Changed API keys",
  "channel.create": "Added a channel",
  "channel.update": "Changed a channel",
  "channel.archive": "Archived a channel",
  "user.create": "Added an account",
  "user.update": "Changed an account",
  "user.reset": "Reset a password",
  "user.disable": "Turned off an account",
};

/** The actions in groups, for the filter list. */
export const AUDIT_ACTION_GROUPS: ReadonlyArray<{ label: string; actions: readonly AuditAction[] }> = [
  { label: "Videos", actions: ["stage.approve", "stage.redo", "stage.skip", "stage.mode", "project.create", "project.archive", "export.done"] },
  { label: "Channels", actions: ["channel.create", "channel.update", "channel.archive"] },
  { label: "Settings", actions: ["settings.update", "keys.update"] },
  { label: "Team", actions: ["user.create", "user.update", "user.reset", "user.disable"] },
  { label: "Signing in", actions: ["login", "logout"] },
];

export function isAuditAction(value: unknown): value is AuditAction {
  return typeof value === "string" && (AUDIT_ACTIONS as readonly string[]).includes(value);
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

function bool(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}

/** A person as the server sent it; null when the entry has no username. */
export function parseTeamUser(raw: unknown): TeamUser | null {
  if (!isRecord(raw)) return null;
  const username = str(raw.username).trim();
  if (!username) return null;
  return {
    username,
    display_name: str(raw.display_name).trim() || username,
    // An unknown role is shown with the least rights; the server decides what it can do.
    role: isRole(raw.role) ? raw.role : "viewer",
    machine: nullableStr(raw.machine),
    must_change_password: bool(raw.must_change_password),
    disabled: bool(raw.disabled),
    last_login_at: nullableStr(raw.last_login_at),
    created_at: nullableStr(raw.created_at),
    created_by: nullableStr(raw.created_by),
  };
}

/** `GET /api/users`, sorted by display name. */
export function parseTeamUsers(raw: unknown): TeamUser[] {
  const list = Array.isArray(raw) ? raw : isRecord(raw) && Array.isArray(raw.users) ? raw.users : [];
  return list
    .map(parseTeamUser)
    .filter((user): user is TeamUser => user !== null)
    .sort((a, b) => a.display_name.localeCompare(b.display_name, undefined, { sensitivity: "base" }));
}

/** `POST /api/users`: `{user, password}`; null when the answer is not usable. */
export function parseUserCreated(raw: unknown): UserCreated | null {
  if (!isRecord(raw)) return null;
  const user = parseTeamUser(raw.user);
  const password = str(raw.password);
  if (!user || !password) return null;
  return { user, password };
}

function parseDetail(raw: unknown): Record<string, unknown> {
  if (isRecord(raw)) return raw;
  if (typeof raw === "string" && raw.trim()) {
    try {
      const parsed = JSON.parse(raw) as unknown;
      return isRecord(parsed) ? parsed : { note: raw };
    } catch {
      return { note: raw };
    }
  }
  return {};
}

function parseAuditRow(raw: unknown, position: number): AuditRow | null {
  if (!isRecord(raw)) return null;
  const action = str(raw.action).trim();
  if (!action) return null;
  const numericId = typeof raw.id === "number" ? raw.id : typeof raw.id === "string" && raw.id.trim() ? Number(raw.id) : Number.NaN;
  return {
    // Rows without a usable id get a negative one, so they never clash with real ids.
    id: Number.isFinite(numericId) ? numericId : -(position + 1),
    at: str(raw.at),
    user: str(raw.user),
    machine: str(raw.machine),
    action,
    project_id: nullableStr(raw.project_id),
    channel_slug: nullableStr(raw.channel_slug),
    detail: parseDetail(raw.detail),
  };
}

/** `GET /api/audit`, newest first, with `detail` always an object. */
export function parseAuditRows(raw: unknown): AuditRow[] {
  const list = Array.isArray(raw) ? raw : isRecord(raw) && Array.isArray(raw.rows) ? raw.rows : [];
  return list
    .map((item, index) => parseAuditRow(item, index))
    .filter((row): row is AuditRow => row !== null)
    .sort((a, b) => b.at.localeCompare(a.at));
}
