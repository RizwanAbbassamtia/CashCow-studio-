import { KeyRound, RefreshCw, ServerOff } from "lucide-react";

import { useSignOut } from "../../api/auth";
import { errorMessage } from "../../api/client";
import type { PublicUser } from "../../types/auth";
import { Button } from "../ui/Button";
import { Spinner } from "../ui/Spinner";
import { useToast } from "../ui/Toast";
import { AuthLayout } from "./AuthLayout";
import { ChangePasswordForm } from "./ChangePasswordForm";
import { ProductMark } from "./ProductMark";

/** While the app checks who is signed in (a fraction of a second on this laptop). */
export function SessionSplash() {
  return (
    <div className="dark flex min-h-screen items-center justify-center bg-canvas text-ink" role="status">
      <div className="flex flex-col items-center gap-4">
        <ProductMark size={52} className="animate-pulse" />
        <div className="flex items-center gap-2 text-sm text-ink-muted">
          <Spinner className="size-4" />
          Opening CashCow Studio...
        </div>
      </div>
    </div>
  );
}

/** The server did not answer the session check (not running, or an unexpected error). */
export function SessionUnavailable({ error, onRetry, retrying }: { error: unknown; onRetry: () => void; retrying: boolean }) {
  return (
    <AuthLayout pageTitle="Not answering">
      <div className="flex flex-col items-center gap-3 text-center" role="alert">
        <div className="flex size-11 items-center justify-center rounded-full bg-fail/10 text-fail">
          <ServerOff className="size-5" aria-hidden />
        </div>
        <h1 className="text-lg font-semibold text-ink">CashCow Studio is not answering</h1>
        <p className="text-sm text-ink-muted">{errorMessage(error)}</p>
        <p className="text-xs text-ink-faint">Close CashCow Studio and open it again. If this keeps happening, tell your admin.</p>
        <Button variant="primary" icon={<RefreshCw />} onClick={onRetry} loading={retrying} className="mt-2 w-full">
          Try again
        </Button>
      </div>
    </AuthLayout>
  );
}

/** An admin set a one-time password: a new one is chosen before anything else opens. */
export function ForcedPasswordChange({ user }: { user: PublicUser }) {
  const signOut = useSignOut();
  const { toast } = useToast();
  return (
    <AuthLayout
      pageTitle="Choose your own password"
      below={
        <button
          type="button"
          onClick={() =>
            signOut.mutate(undefined, {
              onError: (error) => toast({ tone: "error", title: "Could not sign out", description: errorMessage(error) }),
            })
          }
          disabled={signOut.isPending}
          className="font-medium text-ink-muted underline-offset-2 hover:text-ink hover:underline disabled:opacity-60"
        >
          Not {user.display_name || user.username}? Sign out
        </button>
      }
    >
      <div className="mb-5 flex items-start gap-3">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-full bg-accent/15 text-accent-text">
          <KeyRound className="size-4" aria-hidden />
        </div>
        <div>
          <h1 className="text-lg font-semibold text-ink">Choose your own password</h1>
          <p className="mt-1 text-sm text-ink-muted">
            An admin gave you a one-time password. Pick a new one that only you know before you carry on.
          </p>
        </div>
      </div>
      <ChangePasswordForm
        currentLabel="One-time password from your admin"
        submitLabel="Save my new password"
        onDone={() => toast({ tone: "success", title: "Password saved", description: `Welcome, ${user.display_name || user.username}.` })}
      />
    </AuthLayout>
  );
}
