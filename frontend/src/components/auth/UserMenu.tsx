import { ChevronDown, KeyRound, Laptop, LogOut } from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { createPortal } from "react-dom";

import { useAuthStatus, useSignOut } from "../../api/auth";
import { errorMessage } from "../../api/client";
import { cn } from "../../lib/cn";
import { roleLabel } from "../../lib/roles";
import type { PublicUser } from "../../types/auth";
import { useToast } from "../ui/Toast";
import { ChangePasswordDialog } from "./ChangePasswordDialog";

/** "Imran Azmeer" -> "IA", "imran" -> "IM". */
export function initials(name: string): string {
  const words = name.trim().split(/[\s._-]+/).filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) return words[0]!.slice(0, 2).toUpperCase();
  return (words[0]!.charAt(0) + words[words.length - 1]!.charAt(0)).toUpperCase();
}

const roleTone: Record<string, string> = {
  admin: "text-accent-text",
  editor: "text-info",
  viewer: "text-ink-faint",
};

/** Top-bar chip with the signed-in person; opens Change password and Sign out. */
export function UserMenu({ user }: { user: PublicUser }) {
  const status = useAuthStatus();
  const signOut = useSignOut();
  const { toast } = useToast();
  const [open, setOpen] = useState(false);
  const [changing, setChanging] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const name = user.display_name?.trim() || user.username;
  // This laptop's name (user.machine is the laptop the account is locked to, which may differ).
  const machine = status.data?.machine || "";
  const tooltip = `Signed in as ${user.username} (${roleLabel(user.role)})${machine ? ` on ${machine}` : ""}`;

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      }
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    menuRef.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const onMenuKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    const items = Array.from(menuRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]:not(:disabled)') ?? []);
    if (items.length === 0) return;
    const index = items.indexOf(document.activeElement as HTMLElement);
    let next: number | null = null;
    if (event.key === "ArrowDown") next = (index + 1) % items.length;
    else if (event.key === "ArrowUp") next = (index - 1 + items.length) % items.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = items.length - 1;
    else if (event.key === "Tab") setOpen(false);
    if (next !== null) {
      event.preventDefault();
      items[next]?.focus();
    }
  };

  const onSignOut = () => {
    signOut.mutate(undefined, {
      onError: (error) => toast({ tone: "error", title: "Could not sign out", description: errorMessage(error) }),
    });
  };

  return (
    <div ref={rootRef} className="relative">
      <button
        ref={buttonRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Your account: ${name}, ${roleLabel(user.role)}`}
        title={tooltip}
        onClick={() => setOpen((value) => !value)}
        className={cn(
          "flex h-9 items-center gap-2.5 rounded-full border border-line bg-surface-2 pl-1 pr-2.5 transition-colors",
          "hover:border-line-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60",
          open && "border-line-strong",
        )}
      >
        <span className="flex size-7 items-center justify-center rounded-full bg-accent text-[11px] font-bold text-accent-ink" aria-hidden>
          {initials(name)}
        </span>
        <span className="flex min-w-0 flex-col items-start text-left leading-tight">
          <span className="max-w-[160px] truncate text-[13px] font-medium text-ink">{name}</span>
          <span className={cn("text-[10px] font-semibold uppercase tracking-wider", roleTone[user.role] ?? "text-ink-faint")}>{roleLabel(user.role)}</span>
        </span>
        <ChevronDown className={cn("size-3.5 text-ink-faint transition-transform", open && "rotate-180")} aria-hidden />
      </button>

      {open ? (
        <div
          ref={menuRef}
          role="menu"
          aria-label="Your account"
          onKeyDown={onMenuKeyDown}
          className="absolute right-0 top-11 z-40 w-72 rounded-card border border-line bg-surface p-1.5 shadow-card"
        >
          <div className="mb-1 border-b border-line px-3 pb-3 pt-2">
            <div className="truncate text-sm font-semibold text-ink">{name}</div>
            <div className="mt-0.5 text-xs text-ink-muted">
              {user.username} - {roleLabel(user.role)}
            </div>
            {machine ? (
              <div className="mt-2 flex items-center gap-1.5 text-xs text-ink-faint">
                <Laptop className="size-3.5" aria-hidden />
                This laptop: <span className="font-mono text-ink-muted">{machine}</span>
              </div>
            ) : null}
          </div>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              setChanging(true);
            }}
            className="flex h-9 w-full items-center gap-2.5 rounded-md px-3 text-left text-sm text-ink transition-colors hover:bg-surface-2 focus-visible:bg-surface-2 focus-visible:outline-none"
          >
            <KeyRound className="size-4 text-ink-faint" aria-hidden />
            Change password
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={onSignOut}
            disabled={signOut.isPending}
            className="flex h-9 w-full items-center gap-2.5 rounded-md px-3 text-left text-sm text-ink transition-colors hover:bg-surface-2 focus-visible:bg-surface-2 focus-visible:outline-none disabled:opacity-60"
          >
            <LogOut className="size-4 text-ink-faint" aria-hidden />
            {signOut.isPending ? "Signing out..." : "Sign out"}
          </button>
        </div>
      ) : null}

      {/* Portalled: the top bar's backdrop blur would otherwise trap the fixed-position dialog inside the bar. */}
      {createPortal(<ChangePasswordDialog open={changing} onClose={() => setChanging(false)} />, document.body)}
    </div>
  );
}
