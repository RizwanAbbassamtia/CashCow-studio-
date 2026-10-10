/**
 * The transition catalogue for the pickers: `frontend/public/transitions/catalogue.json`, a
 * static file written from config/transitions.yaml by tools/make_transition_previews.py
 * (docs/TRANSITIONS-V2-CONTRACT.md section 1). It never changes while the app runs, so it is
 * loaded once and kept.
 */
import { useQuery } from "@tanstack/react-query";

import {
  findTransition,
  parseTransitionCatalogue,
  type TransitionCatalogue,
  type TransitionOption,
} from "../../types";

export const TRANSITION_CATALOGUE_URL = "/transitions/catalogue.json";

async function fetchTransitionCatalogue(): Promise<TransitionCatalogue> {
  const response = await fetch(TRANSITION_CATALOGUE_URL, { credentials: "same-origin" });
  if (!response.ok) throw new Error(`The list of transitions could not be loaded (${response.status}).`);
  const catalogue = parseTransitionCatalogue(await response.json());
  if (!catalogue) throw new Error("The list of transitions is empty or damaged.");
  return catalogue;
}

/** The transition catalogue; `data` stays undefined while it loads or when it cannot be read. */
export function useTransitionCatalogue() {
  return useQuery({
    queryKey: ["transition-catalogue"],
    queryFn: fetchTransitionCatalogue,
    staleTime: Number.POSITIVE_INFINITY,
    gcTime: Number.POSITIVE_INFINITY,
    retry: 1,
  });
}

/** "Film dissolve" for "film_dissolve" (and for the old name "fade"); a readable guess otherwise. */
export function transitionName(type: string, catalogue?: TransitionCatalogue | null, options?: ReadonlyArray<TransitionOption>): string {
  const entry = findTransition(catalogue ?? null, type);
  if (entry) return entry.label;
  const option = options?.find((item) => item.type === type);
  if (option) return option.label;
  const words = type.replace(/[_-]+/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : type;
}
