/**
 * Sign-in, session and audit calls (docs/M5A-CONTRACT.md sections 1 and 3).
 *
 * `/api/auth/me` is the one source of truth for "who is signed in": the app shell polls it,
 * re-checks it after any 401 or a dropped live connection, and goes back to the sign-in page
 * when it answers `null`. The sign-in calls never report their 401s (a wrong password is not
 * a sign-out).
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSyncExternalStore } from "react";

import { can, type RoleAction } from "../lib/roles";
import type { AuditRow, AuthStatus, ChangePasswordBody, FirstRunBody, LoginBody, PublicUser, TeamFolderBody } from "../types/auth";
import { api, ApiError, isUnauthorized, type RequestOptions } from "./client";
import { queryKeys } from "./keys";

/** The auth calls handle a 401 themselves. */
const QUIET: RequestOptions = { reportUnauthorized: false };

export const authKeys = {
  all: ["auth"] as const,
  status: ["auth", "status"] as const,
  me: ["auth", "me"] as const,
  activity: (limit: number) => ["auth", "activity", limit] as const,
};

// ---- plain functions ------------------------------------------------------

/** Public: whether this install still needs its first admin, this laptop's name, the roster source. */
export function getAuthStatus(): Promise<AuthStatus> {
  return api.get<AuthStatus>("/api/auth/status", QUIET);
}

/** The signed-in person, or `null` when nobody is (a 401). Other failures still throw. */
export async function getMe(): Promise<PublicUser | null> {
  try {
    return await api.get<PublicUser>("/api/auth/me", QUIET);
  } catch (error) {
    if (isUnauthorized(error)) return null;
    throw error;
  }
}

/** 401 wrong username or password, 423 account turned off, 403 locked to another laptop, 429 too many tries. */
export function login(body: LoginBody): Promise<PublicUser> {
  return api.post<PublicUser>("/api/auth/login", body, QUIET);
}

/** Only works while no account exists; signs the new admin in. */
export function firstRun(body: FirstRunBody): Promise<PublicUser> {
  return api.post<PublicUser>("/api/auth/first-run", body, QUIET);
}

/**
 * Before the first account exists on this laptop: use the team's shared folder, so the team's
 * accounts apply here. 422 when the folder is not there, cannot be read or is still syncing.
 */
export function chooseTeamFolder(body: TeamFolderBody): Promise<AuthStatus> {
  return api.post<AuthStatus>("/api/auth/first-run/shared-folder", body, QUIET);
}

/** Ends the session on this laptop (a copy of the cookie stops working too). Already signed out counts as done. */
export async function logout(): Promise<void> {
  try {
    await api.post<void>("/api/auth/logout", {}, QUIET);
  } catch (error) {
    if (isUnauthorized(error)) return;
    throw error;
  }
}

export function changePassword(body: ChangePasswordBody): Promise<void> {
  return api.post<void>("/api/auth/change-password", body, QUIET);
}

export interface AuditQuery {
  limit?: number;
  user?: string;
  action?: string;
  project_id?: string;
}

/** Newest first. Admins see everyone's rows, editors their own. */
export function listAudit(params: AuditQuery = {}): Promise<AuditRow[]> {
  const query = new URLSearchParams();
  if (params.limit) query.set("limit", String(params.limit));
  if (params.user) query.set("user", params.user);
  if (params.action) query.set("action", params.action);
  if (params.project_id) query.set("project_id", params.project_id);
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return api.get<AuditRow[]>(`/api/audit${suffix}`);
}

// ---- why the last session ended -------------------------------------------

/** "signed_out": the person pressed Sign out. "expired": the session ended on its own. */
export type SignOutReason = "signed_out" | "expired";

let signOutReason: SignOutReason | null = null;
const reasonListeners = new Set<() => void>();

export function setSignOutReason(reason: SignOutReason | null): void {
  if (signOutReason === reason) return;
  signOutReason = reason;
  for (const listener of reasonListeners) listener();
}

function subscribeReason(listener: () => void): () => void {
  reasonListeners.add(listener);
  return () => {
    reasonListeners.delete(listener);
  };
}

export function useSignOutReason(): SignOutReason | null {
  return useSyncExternalStore(
    subscribeReason,
    () => signOutReason,
    () => signOutReason,
  );
}

// ---- hooks ----------------------------------------------------------------

export function useAuthStatus() {
  return useQuery({ queryKey: authKeys.status, queryFn: getAuthStatus, staleTime: 30_000 });
}

/**
 * The signed-in person (`null` = nobody). Pass `poll` from exactly one always-mounted place
 * (the app shell) so a session that ends while the app sits idle is noticed.
 */
export function useMe(options: { poll?: boolean } = {}) {
  return useQuery({
    queryKey: authKeys.me,
    queryFn: getMe,
    staleTime: 60_000,
    refetchInterval: options.poll ? 120_000 : false,
  });
}

/** The signed-in person, or null. Inside the app shell this is always a person. */
export function useCurrentUser(): PublicUser | null {
  return useMe().data ?? null;
}

/** Whether the signed-in person may do this (see lib/roles.ts). */
export function useCan(action: RoleAction): boolean {
  return can(useCurrentUser(), action);
}

export function useSignIn() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: login,
    onSuccess: (user) => {
      setSignOutReason(null);
      queryClient.setQueryData(authKeys.me, user);
      void queryClient.invalidateQueries({ queryKey: authKeys.status });
    },
  });
}

export function useFirstRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: firstRun,
    onSuccess: (user) => {
      setSignOutReason(null);
      queryClient.setQueryData(authKeys.me, user);
      void queryClient.invalidateQueries({ queryKey: authKeys.status });
    },
  });
}

/** Points a fresh install at the team's shared folder; the sign-in status is read again. */
export function useChooseTeamFolder() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: chooseTeamFolder,
    onSuccess: (status) => {
      queryClient.setQueryData(authKeys.status, status);
      void queryClient.invalidateQueries({ queryKey: authKeys.status });
      // The folders shown on the screens changed with it.
      void queryClient.invalidateQueries({ queryKey: queryKeys.system });
    },
  });
}

/** Signs out; the app shell then shows the sign-in page with "You have signed out". */
export function useSignOut() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: logout,
    onSuccess: () => {
      setSignOutReason("signed_out");
      queryClient.setQueryData(authKeys.me, null);
      void queryClient.invalidateQueries({ queryKey: authKeys.status });
    },
  });
}

export function useChangePassword() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: changePassword,
    onSuccess: () => {
      queryClient.setQueryData<PublicUser | null>(authKeys.me, (old) => (old ? { ...old, must_change_password: false } : old));
      void queryClient.invalidateQueries({ queryKey: authKeys.me });
    },
    onError: (error) => {
      // A 401 here is either a wrong current password or a session that just ended; asking
      // /me again tells them apart (the shell signs out only in the second case).
      if (isUnauthorized(error)) void queryClient.invalidateQueries({ queryKey: authKeys.me });
    },
  });
}

/** The last audit rows this person may see (dashboard "Recent activity"). */
export function useRecentActivity(limit = 10, enabled = true) {
  return useQuery({
    queryKey: authKeys.activity(limit),
    queryFn: () => listAudit({ limit }),
    enabled,
    staleTime: 15_000,
    refetchInterval: 60_000,
    // A 403 (not allowed) or 404 (older server) will not change by asking again.
    retry: (count, error) => !(error instanceof ApiError && (error.status === 403 || error.status === 404)) && count < 1,
  });
}
