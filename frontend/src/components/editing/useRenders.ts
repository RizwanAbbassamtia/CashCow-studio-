/**
 * Every finished render across the projects, for the Renders page.
 *
 * There is no separate endpoint: the rows come from the project list plus the edit stage's
 * review payload of each video at the edit or export stage (`GET /api/projects/{id}/stage/edit`),
 * which lists every size with its file, length, frame size and file size. The payloads share
 * their cache with the Edit room, and the live updates refresh them when a render finishes.
 */
import { useQueries, type UseQueryResult } from "@tanstack/react-query";
import { useMemo } from "react";

import { finalFile, projectMediaUrl } from "../../api/edit";
import { queryKeys } from "../../api/keys";
import { getStagePayload, useProjects } from "../../api/projects";
import type { ProjectSummary } from "../../types/project";
import { parseEditPayload } from "../../types/timeline";
import { isInEditRoom } from "./editingProjects";

export interface RenderRow {
  key: string;
  project: ProjectSummary;
  /** "720p", "1080p", "2160p" */
  preset: string;
  /** relative to the project folder */
  path: string | null;
  /** where the browser plays it from */
  url: string | null;
  duration_s: number | null;
  size_bytes: number | null;
  width: number | null;
  height: number | null;
  rendered_at: string | null;
}

const PREVIEW_PRESET = "proxy";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** `rendered_at` per preset; the shared payload parser does not keep it. */
function renderTimes(raw: unknown): Map<string, string> {
  const times = new Map<string, string>();
  if (!isRecord(raw)) return times;
  const renders = raw.renders;
  if (Array.isArray(renders)) {
    for (const item of renders) {
      if (isRecord(item) && typeof item.preset === "string" && typeof item.rendered_at === "string") times.set(item.preset, item.rendered_at);
    }
  } else if (isRecord(renders)) {
    for (const [preset, value] of Object.entries(renders)) {
      if (isRecord(value) && typeof value.rendered_at === "string") times.set(preset, value.rendered_at);
    }
  }
  return times;
}

/** The finished final renders of one project (the low-resolution preview is left out). */
export function rendersOf(project: ProjectSummary, raw: unknown): RenderRow[] {
  const payload = parseEditPayload(raw);
  if (!payload) return [];
  const times = renderTimes(raw);
  return payload.renders
    .filter((render) => render.status === "done" && render.preset !== PREVIEW_PRESET)
    .map((render) => ({
      key: `${project.id}:${render.preset}`,
      project,
      preset: render.preset,
      path: render.path,
      url: projectMediaUrl(project.id, render.path ?? finalFile(render.preset), render.play_url),
      duration_s: render.duration_s,
      size_bytes: render.size_bytes,
      width: render.width,
      height: render.height,
      rendered_at: times.get(render.preset) ?? null,
    }));
}

/** Kept outside the hook so react-query only re-runs it when a payload changes. */
function combinePayloads(results: Array<UseQueryResult<unknown, Error>>) {
  return {
    data: results.map((result) => result.data),
    pending: results.filter((result) => result.isPending).length,
    failed: results.filter((result) => result.isError).length,
  };
}

/** Newest render first; renders without a time go last. */
function compareRenders(a: RenderRow, b: RenderRow): number {
  const left = a.rendered_at ?? "";
  const right = b.rendered_at ?? "";
  if (left !== right) return right.localeCompare(left);
  return b.project.updated_at.localeCompare(a.project.updated_at);
}

export function useRenders() {
  const projects = useProjects();
  const candidates = useMemo(() => (projects.data ?? []).filter(isInEditRoom), [projects.data]);

  const payloads = useQueries({
    queries: candidates.map((project) => ({
      queryKey: queryKeys.stagePayload(project.id, "edit"),
      queryFn: () => getStagePayload<unknown>(project.id, "edit"),
      staleTime: 30_000,
    })),
    combine: combinePayloads,
  });

  const rows = useMemo(() => {
    const list: RenderRow[] = [];
    candidates.forEach((project, index) => {
      list.push(...rendersOf(project, payloads.data[index]));
    });
    return list.sort(compareRenders);
  }, [candidates, payloads.data]);

  return {
    rows,
    projects,
    /** videos whose render list is still loading */
    pending: payloads.pending,
    /** videos whose render list could not be read */
    failed: payloads.failed,
    /** videos at the edit or export stage */
    videoCount: candidates.length,
  };
}
