import { Wifi, WifiOff } from "lucide-react";

import type { ConnectionState } from "../../api/ws";
import { cn } from "../../lib/cn";

const text: Record<ConnectionState, string> = {
  open: "Live updates on",
  connecting: "Reconnecting...",
  closed: "Live updates off",
};

/** Small pill showing whether the page receives live events from the backend. */
export function LiveIndicator({ state, className }: { state: ConnectionState; className?: string }) {
  const Icon = state === "open" ? Wifi : WifiOff;
  return (
    <span
      className={cn(
        "inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-[11px] font-medium",
        state === "open" && "border-ok/30 bg-ok/10 text-ok",
        state === "connecting" && "border-warn/30 bg-warn/10 text-warn",
        state === "closed" && "border-line bg-surface-2 text-ink-faint",
        className,
      )}
      title={state === "open" ? "Changes made by the pipeline show up here as they happen." : "The page still refreshes on its own while a stage runs."}
    >
      <Icon className={cn("size-3", state === "connecting" && "animate-pulse")} aria-hidden />
      {text[state]}
    </span>
  );
}
