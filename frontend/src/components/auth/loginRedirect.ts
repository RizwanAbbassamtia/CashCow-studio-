import type { SignOutReason } from "../../api/auth";

/** What the app shell hands the sign-in page when it sends someone there. */
export interface LoginLocationState {
  /** the page the person wanted, to return to after signing in */
  from?: string;
  /** why the last session ended; shown as a notice above the form */
  notice?: SignOutReason | null;
}

export const HOME_PATH = "/automation";

export function readLoginState(state: unknown): LoginLocationState {
  if (typeof state !== "object" || state === null) return {};
  const raw = state as Record<string, unknown>;
  const notice = raw.notice === "expired" || raw.notice === "signed_out" ? raw.notice : null;
  return { from: typeof raw.from === "string" ? raw.from : undefined, notice };
}

/** Only a page inside this app, never the sign-in screens themselves or another site. */
export function safeReturnPath(from: string | undefined | null): string {
  if (!from || !from.startsWith("/") || from.startsWith("//") || from.startsWith("/\\")) return HOME_PATH;
  const path = from.split(/[?#]/, 1)[0] ?? "";
  if (path === "/" || path === "/login" || path === "/first-run") return HOME_PATH;
  return from;
}
