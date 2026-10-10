/**
 * The Master Sheet libraries and the production log (docs/M5A-CONTRACT.md section 2).
 *
 * Each list is gathered by the server from every channel in the shared folder:
 * frameworks, voices, music tracks (with their length and licence) and every exported
 * video. The production log also comes as CSV for the team sheet.
 */
import { useQuery } from "@tanstack/react-query";

import {
  parseFrameworks,
  parseMusic,
  parseMusicFolders,
  parseProductionLog,
  parseVoices,
  type LibraryFramework,
  type LibraryMusicFolder,
  type LibraryMusicTrack,
  type LibraryVoice,
  type ProductionLogFilters,
  type ProductionLogRow,
} from "../types/library";
import { api } from "./client";
import { retryUnlessDenied } from "./users";

export const libraryKeys = {
  all: ["library"] as const,
  frameworks: ["library", "frameworks"] as const,
  voices: ["library", "voices"] as const,
  music: ["library", "music"] as const,
  musicFolders: ["library", "music", "folders"] as const,
  productionLog: (filters: ProductionLogFilters) => ["library", "production-log", filters.channel_slug ?? "", filters.since ?? ""] as const,
};

// ---- plain functions ------------------------------------------------------------------

export function listLibraryFrameworks(): Promise<LibraryFramework[]> {
  return api.get<unknown>("/api/library/frameworks").then(parseFrameworks);
}

export function listLibraryVoices(): Promise<LibraryVoice[]> {
  return api.get<unknown>("/api/library/voices").then(parseVoices);
}

/** Can take a few seconds the first time: the server measures every track once. */
export function listLibraryMusic(): Promise<LibraryMusicTrack[]> {
  return api.get<unknown>("/api/library/music").then(parseMusic);
}

/** Each channel music folder with its track and licence counts, or why it cannot be used. */
export function listLibraryMusicFolders(): Promise<LibraryMusicFolder[]> {
  return api.get<unknown>("/api/library/music/folders").then(parseMusicFolders);
}

/** `channel_slug=...&since=...`, or an empty string without filters. */
export function productionLogQuery(filters: ProductionLogFilters = {}): string {
  const query = new URLSearchParams();
  if (filters.channel_slug) query.set("channel_slug", filters.channel_slug);
  if (filters.since) query.set("since", filters.since);
  return query.toString();
}

export function listProductionLog(filters: ProductionLogFilters = {}): Promise<ProductionLogRow[]> {
  const query = productionLogQuery(filters);
  return api.get<unknown>(`/api/production-log${query ? `?${query}` : ""}`).then(parseProductionLog);
}

/** The CSV download of the same rows, for a plain link (the session cookie goes along). */
export function productionLogCsvUrl(filters: ProductionLogFilters = {}): string {
  const query = productionLogQuery(filters);
  return `/api/production-log.csv${query ? `?${query}` : ""}`;
}

// ---- hooks ----------------------------------------------------------------------------

export function useLibraryFrameworks() {
  return useQuery({ queryKey: libraryKeys.frameworks, queryFn: listLibraryFrameworks, retry: retryUnlessDenied });
}

export function useLibraryVoices() {
  return useQuery({ queryKey: libraryKeys.voices, queryFn: listLibraryVoices, retry: retryUnlessDenied });
}

export function useLibraryMusic() {
  return useQuery({ queryKey: libraryKeys.music, queryFn: listLibraryMusic, staleTime: 60_000, retry: retryUnlessDenied });
}

export function useLibraryMusicFolders() {
  return useQuery({ queryKey: libraryKeys.musicFolders, queryFn: listLibraryMusicFolders, staleTime: 60_000, retry: retryUnlessDenied });
}

export function useProductionLog(filters: ProductionLogFilters = {}) {
  return useQuery({
    queryKey: libraryKeys.productionLog(filters),
    queryFn: () => listProductionLog(filters),
    retry: retryUnlessDenied,
  });
}
