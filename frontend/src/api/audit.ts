/**
 * The activity log for the Activity page (docs/M5A-CONTRACT.md section 1): who approved,
 * sent back, skipped, exported or changed what, on which laptop, and when.
 *
 * `GET /api/audit?limit=&user=&action=&project_id=`. Admins get everyone's rows; editors
 * get only their own. The rows are parsed so `detail` is always an object. (The Dashboard's
 * short "Recent activity" list uses `useRecentActivity` in `api/auth.ts`.)
 */
import { useQuery } from "@tanstack/react-query";

import type { AuditRow } from "../types/auth";
import { AUDIT_DEFAULT_LIMIT, parseAuditRows, type AuditFilters } from "../types/users";
import { api } from "./client";
import { retryUnlessDenied } from "./users";

export const auditKeys = {
  all: ["audit"] as const,
  list: (filters: AuditFilters) =>
    ["audit", "list", filters.limit ?? AUDIT_DEFAULT_LIMIT, filters.user ?? "", filters.action ?? "", filters.project_id ?? ""] as const,
};

export function auditQuery(filters: AuditFilters = {}): string {
  const query = new URLSearchParams();
  query.set("limit", String(filters.limit ?? AUDIT_DEFAULT_LIMIT));
  if (filters.user) query.set("user", filters.user);
  if (filters.action) query.set("action", filters.action);
  if (filters.project_id) query.set("project_id", filters.project_id);
  return query.toString();
}

/** Audit rows, newest first. */
export function listAuditRows(filters: AuditFilters = {}): Promise<AuditRow[]> {
  return api.get<unknown>(`/api/audit?${auditQuery(filters)}`).then(parseAuditRows);
}

export function useAudit(filters: AuditFilters = {}, options: { enabled?: boolean; refetchInterval?: number | false } = {}) {
  return useQuery({
    queryKey: auditKeys.list(filters),
    queryFn: () => listAuditRows(filters),
    enabled: options.enabled ?? true,
    refetchInterval: options.refetchInterval ?? false,
    retry: retryUnlessDenied,
  });
}
