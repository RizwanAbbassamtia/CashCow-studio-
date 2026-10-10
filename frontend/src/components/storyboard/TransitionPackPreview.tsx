import { useState } from "react";

import { cn } from "../../lib/cn";
import { transitionsInPack, type TransitionPack } from "../../types";
import { TransitionPreview } from "./TransitionPicker";
import { useTransitionCatalogue } from "./transitionCatalogue";

/**
 * The transitions of one pack as small previews (Channel Setup > Images), so the team can
 * see what a pack looks like before choosing it. Pointing at a preview plays it.
 */
export function TransitionPackPreview({ pack, className }: { pack: TransitionPack; className?: string }) {
  const catalogue = useTransitionCatalogue().data;
  const [playing, setPlaying] = useState<string | null>(null);
  if (!catalogue) return null;
  const entries = transitionsInPack(catalogue, pack);
  return (
    <div className={cn("flex flex-col gap-2", className)}>
      <p className="text-xs text-ink-faint">
        {entries.length} transitions in the {catalogue.packs[pack].label} pack. Point at one to see it move.
      </p>
      <ul className="grid grid-cols-3 gap-2 sm:grid-cols-5 lg:grid-cols-7">
        {entries.map((entry) => (
          <li key={entry.id} title={entry.feel} className="flex min-w-0 flex-col gap-1" onMouseEnter={() => setPlaying(entry.id)} onMouseLeave={() => setPlaying(null)}>
            <TransitionPreview entry={entry} playing={playing === entry.id} className="aspect-video w-full rounded-sm border border-line" />
            <span className="truncate text-[11px] text-ink-muted">{entry.label}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
