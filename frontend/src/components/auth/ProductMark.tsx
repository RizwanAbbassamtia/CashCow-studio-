import { useId } from "react";

import { cn } from "../../lib/cn";

/** The CashCow Studio mark (same drawing as the window icon): a play button on deep navy. */
export function ProductMark({ size = 48, className }: { size?: number; className?: string }) {
  // Each mark needs its own gradient id: the sidebar and a dialog may show two at once.
  const fillId = `ccs-mark-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  return (
    <svg
      viewBox="0 0 64 64"
      width={size}
      height={size}
      className={cn("shrink-0", className)}
      role="img"
      aria-label="CashCow Studio"
    >
      <defs>
        <linearGradient id={fillId} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#16223d" />
          <stop offset="1" stopColor="#0B1220" />
        </linearGradient>
      </defs>
      <rect x="2" y="2" width="60" height="60" rx="14" fill={`url(#${fillId})`} />
      <rect x="2" y="2" width="60" height="60" rx="14" fill="none" stroke="#FFC000" strokeWidth="3" />
      <path d="M25 19 L45 32 L25 45 Z" fill="#FFC000" strokeLinejoin="round" />
    </svg>
  );
}
