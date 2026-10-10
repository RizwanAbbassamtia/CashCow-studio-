import { Clapperboard, PackageCheck } from "lucide-react";

import { cn } from "../../lib/cn";
import type { Project } from "../../types/project";
import { StageStatusBadge } from "../projects/StageStatusBadge";

export type RoomStage = "edit" | "export";

const OPTIONS: ReadonlyArray<{ id: RoomStage; label: string; hint: string; icon: typeof Clapperboard }> = [
  { id: "edit", label: "Edit", hint: "Preview, music, captions and sizes", icon: Clapperboard },
  { id: "export", label: "Export", hint: "Thumbnail, title and description", icon: PackageCheck },
];

/** The two panels of the Edit room, each with the status of its stage. */
export function StageSwitch({ value, onChange, project, panelId }: { value: RoomStage; onChange: (stage: RoomStage) => void; project: Project; panelId: string }) {
  return (
    <div role="tablist" aria-label="Stage" className="grid gap-2 sm:grid-cols-2">
      {OPTIONS.map((option) => {
        const selected = option.id === value;
        const status = project.stages[option.id]?.status ?? "pending";
        return (
          <button
            key={option.id}
            type="button"
            role="tab"
            id={`${panelId}-${option.id}`}
            aria-selected={selected}
            aria-controls={panelId}
            onClick={() => onChange(option.id)}
            className={cn(
              "flex items-center justify-between gap-3 rounded-card border px-4 py-3 text-left transition-colors",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60",
              selected ? "border-accent/60 bg-accent/10 shadow-card" : "border-line bg-surface hover:border-line-strong",
            )}
          >
            <span className="flex min-w-0 items-start gap-3">
              <option.icon className={cn("mt-0.5 size-4 shrink-0", selected ? "text-accent-text" : "text-ink-faint")} aria-hidden />
              <span className="min-w-0">
                <span className="block text-sm font-semibold text-ink">{option.label}</span>
                <span className="block truncate text-xs text-ink-muted">{option.hint}</span>
              </span>
            </span>
            <StageStatusBadge status={status} />
          </button>
        );
      })}
    </div>
  );
}
