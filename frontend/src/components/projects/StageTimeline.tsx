import { Check, CircleAlert, Clock, Hand, Minus, type LucideIcon } from "lucide-react";

import { useStageProgress } from "../../api/ws";
import { cn } from "../../lib/cn";
import { formatRelative } from "../../lib/format";
import type { StageMode, StageName } from "../../types/channel";
import { STAGE_ORDER, type Project, type StageStatus } from "../../types/project";
import { SegmentedControl } from "../form/SegmentedControl";
import { Spinner } from "../ui/Spinner";
import { ProgressBar } from "./ProgressBar";
import { STAGE_DESCRIPTIONS, STAGE_LABELS, STAGE_MODE_OPTIONS, STAGE_STATUS_LABELS } from "./stageMeta";
import { StageStatusBadge } from "./StageStatusBadge";

export interface StageTimelineProps {
  project: Project;
  selected: StageName;
  onSelect: (stage: StageName) => void;
  onModeChange?: (stage: StageName, mode: StageMode) => void;
  /** stage whose mode change is in flight */
  modeChanging?: StageName | null;
}

const markerStyles: Record<StageStatus, { icon: LucideIcon | null; className: string }> = {
  pending: { icon: null, className: "border-line bg-surface-2 text-ink-faint" },
  running: { icon: null, className: "border-info/50 bg-info/15 text-info" },
  awaiting_review: { icon: Clock, className: "border-accent/60 bg-accent/15 text-accent-text" },
  awaiting_manual: { icon: Hand, className: "border-warn/50 bg-warn/15 text-warn" },
  approved: { icon: Check, className: "border-ok/40 bg-ok/15 text-ok" },
  done: { icon: Check, className: "border-ok/40 bg-ok/15 text-ok" },
  failed: { icon: CircleAlert, className: "border-fail/50 bg-fail/15 text-fail" },
  skipped: { icon: Minus, className: "border-line bg-surface-2 text-ink-faint" },
};

function Marker({ status, index }: { status: StageStatus; index: number }) {
  const style = markerStyles[status];
  const Icon = style.icon;
  return (
    <span className={cn("flex size-7 shrink-0 items-center justify-center rounded-full border text-xs font-semibold", style.className)} aria-hidden>
      {status === "running" ? <Spinner className="size-3.5 text-current" /> : Icon ? <Icon className="size-3.5" /> : index + 1}
    </span>
  );
}

/** The eight steps of a project with their status, mode and timing. Click a step to review it. */
export function StageTimeline({ project, selected, onSelect, onModeChange, modeChanging }: StageTimelineProps) {
  const progress = useStageProgress(project.id);
  const currentIndex = STAGE_ORDER.indexOf(project.current_stage);

  return (
    <ol className="divide-y divide-line">
      {STAGE_ORDER.map((stage, index) => {
        const state = project.stages[stage];
        const status = state?.status ?? "pending";
        const isCurrent = stage === project.current_stage;
        const isSelected = stage === selected;
        const isPast = index < currentIndex;
        const liveProgress = status === "running" && progress && progress.stage === stage ? progress : null;
        const mode = project.stage_modes[stage];

        return (
          <li key={stage} className={cn("relative transition-colors", isSelected ? "bg-surface-2/60" : "hover:bg-surface-2/30")}>
            {isSelected ? <span className="absolute left-0 top-0 h-full w-0.5 bg-accent" aria-hidden /> : null}
            <div className="flex items-start gap-4 px-5 py-3.5">
              <button
                type="button"
                onClick={() => onSelect(stage)}
                aria-current={isCurrent ? "step" : undefined}
                aria-pressed={isSelected}
                className="flex min-w-0 flex-1 items-start gap-4 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 rounded-md"
              >
                <Marker status={status} index={index} />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={cn("text-sm font-medium", isPast || isCurrent || isSelected ? "text-ink" : "text-ink-muted")}>{STAGE_LABELS[stage]}</span>
                    <StageStatusBadge status={status} />
                    {isCurrent ? <span className="text-[11px] font-medium uppercase tracking-wide text-accent-text">Current</span> : null}
                    {state?.attempts > 1 ? <span className="text-[11px] text-ink-faint">attempt {state.attempts}</span> : null}
                  </div>
                  <p className="mt-0.5 text-xs text-ink-muted">{state?.summary || STAGE_DESCRIPTIONS[stage]}</p>
                  {liveProgress ? (
                    <ProgressBar className="mt-2 max-w-md" value={liveProgress.pct ?? null} label={liveProgress.message} />
                  ) : null}
                  {status === "failed" && state?.error ? <p className="mt-1 text-xs text-fail">{state.error}</p> : null}
                  <p className="mt-1 text-[11px] text-ink-faint">
                    {state?.finished_at
                      ? `${STAGE_STATUS_LABELS[status]} ${formatRelative(state.finished_at)}`
                      : state?.started_at
                        ? `Started ${formatRelative(state.started_at)}`
                        : ""}
                    {state?.approved_by ? ` - approved by ${state.approved_by}` : ""}
                  </p>
                </div>
              </button>
              {onModeChange ? (
                <div className="flex shrink-0 items-center gap-2 pt-0.5">
                  {modeChanging === stage ? <Spinner className="size-3.5" /> : null}
                  <SegmentedControl
                    options={STAGE_MODE_OPTIONS}
                    value={mode}
                    onChange={(next) => onModeChange(stage, next)}
                    aria-label={`${STAGE_LABELS[stage]} mode`}
                  />
                </div>
              ) : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
