import { cn } from "../../lib/cn";
import type { StageStatus } from "../../types/project";
import { Badge } from "../ui/Badge";
import { Spinner } from "../ui/Spinner";
import { STAGE_STATUS_LABELS, STAGE_STATUS_TONES } from "./stageMeta";

/** Status pill for a stage or a whole project (the status of its current stage). */
export function StageStatusBadge({ status, className }: { status: StageStatus; className?: string }) {
  if (status === "running") {
    return (
      <Badge tone="info" className={cn("whitespace-nowrap", className)}>
        <Spinner className="size-3 text-current" />
        {STAGE_STATUS_LABELS[status]}
      </Badge>
    );
  }
  return (
    <Badge tone={STAGE_STATUS_TONES[status]} dot className={cn("whitespace-nowrap", className)}>
      {STAGE_STATUS_LABELS[status]}
    </Badge>
  );
}
