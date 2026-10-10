/**
 * Team accounts for the Users & roles page (docs/M5A-CONTRACT.md section 1).
 *
 * `/api/users` is for admins only; the server answers 403 to anyone else. The one-time
 * password of a new account or a reset comes back in the response and is handed straight
 * to the dialog that shows it: it is never put in the query cache, logged or stored, and
 * the mutations drop it from memory (`gcTime: 0`) once the dialog lets go.
 *
 * Who is signed in, this laptop's name and where the team list comes from are in
 * `api/auth.ts` (`useCurrentUser`, `useAuthStatus`).
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  parseTeamUser,
  parseTeamUsers,
  parseUserCreated,
  type PasswordReset,
  type TeamUser,
  type UserCreate,
  type UserCreated,
  type UserUpdate,
} from "../types/users";
import { authKeys } from "./auth";
import { ApiError, api } from "./client";

export const userKeys = {
  all: ["users"] as const,
  list: ["users", "list"] as const,
};

/** A 401, 403 or 404 does not get better by asking again. */
export function retryUnlessDenied(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && (error.status === 401 || error.status === 403 || error.status === 404)) return false;
  return failureCount < 1;
}

/** True when the server refused because the signed-in person may not do this. */
export function isForbidden(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function userPath(username: string): string {
  return `/api/users/${encodeURIComponent(username)}`;
}

// ---- plain functions ------------------------------------------------------------------

export function listUsers(): Promise<TeamUser[]> {
  return api.get<unknown>("/api/users").then(parseTeamUsers);
}

/** Adds a person. The response carries the generated password, shown once. */
export async function createUser(body: UserCreate): Promise<UserCreated> {
  const raw = await api.post<unknown>("/api/users", body);
  const created = parseUserCreated(raw);
  if (!created) {
    throw new ApiError(
      500,
      "The account was made, but the server did not send its password back. Use Reset password on the new row to get one.",
    );
  }
  return created;
}

/**
 * Changes a person's name, role, laptop lock or disabled flag. Returns the fields to show at
 * once: what was asked for, overridden by whatever the server answered.
 */
export async function updateUser(username: string, body: UserUpdate): Promise<Partial<TeamUser>> {
  const raw = await api.put<unknown>(userPath(username), body);
  const patch: Partial<TeamUser> = {};
  if (body.display_name !== undefined) patch.display_name = body.display_name;
  if (body.role !== undefined) patch.role = body.role;
  if (body.machine !== undefined) patch.machine = body.machine ? body.machine : null;
  if (body.disabled !== undefined) patch.disabled = body.disabled;
  const answered = parseTeamUser(raw);
  if (answered && isRecord(raw)) {
    if (typeof raw.display_name === "string") patch.display_name = answered.display_name;
    if (typeof raw.role === "string") patch.role = answered.role;
    if ("machine" in raw) patch.machine = answered.machine;
    if (typeof raw.must_change_password === "boolean") patch.must_change_password = raw.must_change_password;
    if (typeof raw.disabled === "boolean") patch.disabled = raw.disabled;
  }
  return patch;
}

/** Makes a new password for a person (shown once); they must change it when they sign in. */
export async function resetUserPassword(username: string): Promise<PasswordReset> {
  const raw = await api.post<unknown>(`${userPath(username)}/reset-password`, {});
  const password = isRecord(raw) && typeof raw.password === "string" ? raw.password : "";
  if (!password) {
    throw new ApiError(500, "The server did not send the new password back. Try Reset password again.");
  }
  return { password };
}

// ---- hooks ----------------------------------------------------------------------------

/** Everyone on the team (admins only; a 403 means the signed-in person is not an admin). */
export function useUsers(options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: userKeys.list,
    queryFn: listUsers,
    enabled: options.enabled ?? true,
    retry: retryUnlessDenied,
  });
}

function patchRow(rows: TeamUser[] | undefined, username: string, patch: Partial<TeamUser>): TeamUser[] | undefined {
  return rows?.map((row) => (row.username === username ? { ...row, ...patch } : row));
}

/**
 * Adds a person. The one-time password lives only in this mutation's result; `gcTime: 0`
 * drops it from memory as soon as the dialog that shows it lets go (call `reset()` on close).
 */
export function useCreateUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: createUser,
    gcTime: 0,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: userKeys.list }),
  });
}

export function useUpdateUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ username, body }: { username: string; body: UserUpdate }) => updateUser(username, body),
    onSuccess: (patch, { username }) => {
      queryClient.setQueryData<TeamUser[]>(userKeys.list, (rows) => patchRow(rows, username, patch));
      void queryClient.invalidateQueries({ queryKey: userKeys.list });
      // An admin may have changed their own name or role: the top bar asks again.
      void queryClient.invalidateQueries({ queryKey: authKeys.me });
    },
  });
}

/** Resets a password; like `useCreateUser`, the new password is dropped once the dialog closes. */
export function useResetUserPassword() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (username: string) => resetUserPassword(username),
    gcTime: 0,
    onSuccess: (_result, username) => {
      queryClient.setQueryData<TeamUser[]>(userKeys.list, (rows) => patchRow(rows, username, { must_change_password: true }));
      void queryClient.invalidateQueries({ queryKey: userKeys.list });
    },
  });
}
