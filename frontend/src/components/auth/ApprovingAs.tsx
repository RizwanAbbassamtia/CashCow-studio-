import { UserRound } from "lucide-react";

import { useCurrentUser } from "../../api/auth";
import { cn } from "../../lib/cn";
import { can, DENIED_REASONS } from "../../lib/roles";

// Before sign-in existed, the approver typed a name that was kept in this key; forget it.
try {
  localStorage.removeItem("cfs-reviewer-name");
} catch {
  /* storage unavailable: nothing to clear */
}

/** The signed-in person's name as people read it (display name, else username). */
export function useSignedInName(): string {
  const user = useCurrentUser();
  return user ? user.display_name?.trim() || user.username : "";
}

/**
 * Who an approval is recorded under: the signed-in person. The server stamps approvals,
 * redos and skips from the session (docs/M5A-CONTRACT.md rule 4), so nothing is typed.
 * Viewers read why the approve buttons are switched off instead.
 */
export function ApprovingAs({ className }: { className?: string }) {
  const user = useCurrentUser();
  const name = useSignedInName();
  if (!user) return null;
  if (!can(user, "approve")) {
    return <p className={cn("text-[13px] text-warn", className)}>{DENIED_REASONS.approve}</p>;
  }
  return (
    <p className={cn("flex items-center gap-1.5 text-[13px] text-ink-muted", className)}>
      <UserRound className="size-3.5 shrink-0 text-ink-faint" aria-hidden />
      <span>
        Approving as <span className="font-medium text-ink">{name}</span>
      </span>
    </p>
  );
}
