import { Scissors } from "lucide-react";

import { cn } from "../../lib/cn";
import { Badge } from "../ui/Badge";

/** The "Timeline editor arrives in M5" strip above the edit and export panels. */
export function TimelineEditorStrip({ className }: { className?: string }) {
  return (
    <div className={cn("flex items-start gap-3 rounded-md border border-dashed border-line-strong bg-surface-2/40 px-4 py-3", className)} role="note">
      <div className="flex size-8 shrink-0 items-center justify-center rounded-md bg-navy ring-1 ring-accent/60">
        <Scissors className="size-4 text-accent" aria-hidden />
      </div>
      <div className="min-w-0 text-[13px]">
        <p className="flex flex-wrap items-center gap-2 font-medium text-ink">
          Timeline editor arrives in M5
          <Badge tone="accent">Coming next</Badge>
        </p>
        <p className="mt-0.5 text-ink-muted">
          Trimming scenes, swapping a picture and moving popups on a timeline come with the next update. Until then, choose the music,
          captions, popups and render sizes below. To change a picture, redo that scene at the Images stage on the project page.
        </p>
      </div>
    </div>
  );
}
