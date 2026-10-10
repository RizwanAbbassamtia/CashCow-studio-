import { Lock } from "lucide-react";
import type { ReactNode } from "react";

import { useCan } from "../../api/auth";
import { cn } from "../../lib/cn";
import { DENIED_REASONS, type RoleAction } from "../../lib/roles";
import { Button, LinkButton, type ButtonProps, type LinkButtonProps } from "../ui/Button";
import { Card } from "../ui/Card";
import { EmptyState } from "../ui/States";

export interface GatedProps {
  /** false: the control inside is shown disabled and the reason appears on hover */
  allowed: boolean;
  reason: string;
  className?: string;
  /** the control; render it with `disabled` when `allowed` is false */
  children: ReactNode;
}

/**
 * Wraps a button this account may not use. A disabled button gets no mouse events, so the
 * reason sits on a wrapper instead, where the hover tooltip still works.
 */
export function Gated({ allowed, reason, className, children }: GatedProps) {
  if (allowed) return <>{children}</>;
  return (
    <span title={reason} className={cn("inline-flex cursor-not-allowed [&>*]:pointer-events-none", className)}>
      {children}
      <span className="sr-only">{reason}</span>
    </span>
  );
}

export interface GatedButtonProps extends ButtonProps {
  allowed: boolean;
  reason: string;
  /** classes for the hover wrapper shown when not allowed (e.g. "flex" for a full-width button) */
  gateClassName?: string;
}

/** A Button this account may not use: shown disabled, with the reason on hover. */
export function GatedButton({ allowed, reason, gateClassName, disabled, ...props }: GatedButtonProps) {
  return (
    <Gated allowed={allowed} reason={reason} className={gateClassName}>
      <Button {...props} disabled={disabled || !allowed} />
    </Gated>
  );
}

/**
 * A button that approves, redoes, skips, regenerates or re-records part of a stage. Viewers
 * see it disabled with the reason; the server refuses the call for them anyway.
 */
export function ReviewButton(props: ButtonProps & { gateClassName?: string }) {
  const allowed = useCan("approve");
  return <GatedButton allowed={allowed} reason={DENIED_REASONS.approve} {...props} />;
}

/** A button that starts, resumes or archives a video: disabled for viewers. */
export function RunButton(props: ButtonProps & { gateClassName?: string }) {
  const allowed = useCan("run");
  return <GatedButton allowed={allowed} reason={DENIED_REASONS.run} {...props} />;
}

/**
 * A button-styled link to something that needs a permission (e.g. "New video"): the link
 * when allowed, otherwise a disabled button with the reason on hover.
 */
export function GatedLinkButton({ allowed, reason, ...props }: LinkButtonProps & { allowed: boolean; reason: string }) {
  if (allowed) return <LinkButton {...props} />;
  return (
    <Gated allowed={false} reason={reason}>
      <Button variant={props.variant} size={props.size} icon={props.icon} className={props.className} disabled>
        {props.children as ReactNode}
      </Button>
    </Gated>
  );
}

/** A whole page that only some roles may open, e.g. Settings > Users & roles for admins. */
export function RequireRole({ action, children }: { action: RoleAction; children: ReactNode }) {
  const allowed = useCan(action);
  if (allowed) return <>{children}</>;
  return (
    <Card className="mx-auto max-w-xl">
      <EmptyState
        icon={Lock}
        title="Your account cannot open this page"
        description={DENIED_REASONS[action]}
        action={
          <LinkButton to="/automation" variant="secondary">
            Go to the Dashboard
          </LinkButton>
        }
      />
    </Card>
  );
}
