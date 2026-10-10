import { cn } from "../../lib/cn";

export interface ProgressBarProps {
  /** 0-100; omit or pass null for an indeterminate bar */
  value?: number | null;
  label?: string;
  className?: string;
  tone?: "accent" | "ok" | "fail";
}

const toneClasses = {
  accent: "bg-accent",
  ok: "bg-ok",
  fail: "bg-fail",
} as const;

/** Thin progress bar with an optional message under it. Never blocks the page. */
export function ProgressBar({ value, label, className, tone = "accent" }: ProgressBarProps) {
  const determinate = typeof value === "number" && Number.isFinite(value);
  const pct = determinate ? Math.max(0, Math.min(100, value)) : 0;
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <div
        className="h-1.5 w-full overflow-hidden rounded-full bg-surface-2"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={determinate ? Math.round(pct) : undefined}
        aria-label={label}
      >
        {determinate ? (
          <div className={cn("h-full rounded-full transition-all duration-500", toneClasses[tone])} style={{ width: `${pct}%` }} />
        ) : (
          <div className={cn("h-full w-1/3 animate-pulse rounded-full", toneClasses[tone])} />
        )}
      </div>
      {label ? (
        <p className="flex items-center justify-between gap-3 text-xs text-ink-muted">
          <span className="truncate">{label}</span>
          {determinate ? <span className="shrink-0 tabular-nums">{Math.round(pct)}%</span> : null}
        </p>
      ) : null}
    </div>
  );
}
