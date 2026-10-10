import { useState, type KeyboardEvent } from "react";

import { cn } from "../../lib/cn";
import { Input, type InputProps } from "../ui/Input";

export type PasswordInputProps = Omit<InputProps, "type">;

/**
 * Password box with Show / Hide (typing blind is the commonest cause of a wrong password)
 * and a Caps Lock warning while the box has the focus. Takes the same props as `Input`,
 * including `ref` and react-hook-form's `register(...)`.
 */
export function PasswordInput({ className, onKeyDown, onKeyUp, onBlur, id, ...props }: PasswordInputProps) {
  const [shown, setShown] = useState(false);
  const [capsLock, setCapsLock] = useState(false);

  const readCaps = (event: KeyboardEvent<HTMLInputElement>) => {
    if (typeof event.getModifierState === "function") setCapsLock(event.getModifierState("CapsLock"));
  };

  return (
    <div className="flex flex-col gap-1">
      <div className="relative">
        <Input
          id={id}
          type={shown ? "text" : "password"}
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
          className={cn("h-10 pr-16", className)}
          onKeyDown={(event) => {
            readCaps(event);
            onKeyDown?.(event);
          }}
          onKeyUp={(event) => {
            readCaps(event);
            onKeyUp?.(event);
          }}
          onBlur={(event) => {
            setCapsLock(false);
            onBlur?.(event);
          }}
          {...props}
        />
        <button
          type="button"
          onClick={() => setShown((value) => !value)}
          aria-pressed={shown}
          aria-label={shown ? "Hide password" : "Show password"}
          aria-controls={id}
          className="absolute right-1.5 top-1/2 h-7 -translate-y-1/2 rounded px-2 text-xs font-medium text-ink-muted transition-colors hover:bg-surface-2 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60"
        >
          {shown ? "Hide" : "Show"}
        </button>
      </div>
      {capsLock ? <p className="text-xs text-warn">Caps Lock is on.</p> : null}
    </div>
  );
}
