/**
 * The sign-in page's "Keep me signed in on this laptop" box remembers its last setting here.
 * (The remembered username for "Remember my login details" is kept by the server, in
 * <app_data_dir>/saved-login.json, and comes back in GET /api/auth/status as `saved_username`.)
 * Storage can be missing or blocked; every call falls back to the default.
 */
const KEEP_SIGNED_IN_KEY = "ccs-keep-signed-in";

/** Ticked unless it was unticked the last time someone signed in on this laptop. */
export function readKeepSignedIn(): boolean {
  try {
    return localStorage.getItem(KEEP_SIGNED_IN_KEY) !== "0";
  } catch {
    return true;
  }
}

export function writeKeepSignedIn(keep: boolean): void {
  try {
    localStorage.setItem(KEEP_SIGNED_IN_KEY, keep ? "1" : "0");
  } catch {
    /* storage unavailable: the box starts ticked next time */
  }
}
