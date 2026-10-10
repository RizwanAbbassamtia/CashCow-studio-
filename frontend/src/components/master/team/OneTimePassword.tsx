import { KeyRound } from "lucide-react";

import { Notice } from "../../ui/States";
import { CopyButton } from "../CopyButton";

/**
 * A password the server made up, shown to the admin exactly once. It lives only in the
 * dialog's memory: closing the dialog forgets it, and the only way to get another is a reset.
 */
export function OneTimePassword({ password, name }: { password: string; name: string }) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-3 rounded-md border border-accent/40 bg-accent/10 px-3 py-3">
        <KeyRound className="size-4 shrink-0 text-accent-text" aria-hidden />
        <code
          className="min-w-0 flex-1 select-all break-all font-mono text-base font-semibold tracking-wider text-ink"
          aria-label={`One-time password for ${name}`}
          translate="no"
          spellCheck={false}
        >
          {password}
        </code>
        <CopyButton text={password} what="Password" variant="primary" />
      </div>
      <Notice tone="warn" title="Shown only this once">
        Give it to {name} in person or in a private message. They choose their own password the first time they sign in. Once this
        window closes the password cannot be shown again; use Reset password to make a new one.
      </Notice>
    </div>
  );
}
