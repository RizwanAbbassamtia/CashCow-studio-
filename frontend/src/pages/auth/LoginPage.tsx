import { useQueryClient } from "@tanstack/react-query";
import { CloudOff, Info, Laptop, LogOut } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router";

import { setSignOutReason, useAuthStatus, useMe, useSignIn } from "../../api/auth";
import { ApiError, errorMessage } from "../../api/client";
import { AuthLayout } from "../../components/auth/AuthLayout";
import { readLoginState, safeReturnPath } from "../../components/auth/loginRedirect";
import { PasswordInput } from "../../components/auth/PasswordInput";
import { readKeepSignedIn, writeKeepSignedIn } from "../../components/auth/rememberedLogin";
import { Button } from "../../components/ui/Button";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Spinner } from "../../components/ui/Spinner";
import { cn } from "../../lib/cn";

/** The server's own words when it sent any, else a plain-English line for the status. */
function signInProblem(error: unknown): string {
  if (!(error instanceof ApiError)) return errorMessage(error);
  const said = typeof error.detail === "string" ? error.detail.trim() : "";
  switch (error.status) {
    case 0:
      return "Cannot reach the CashCow Studio server. Close the app, start it again and retry.";
    case 401:
      return said && said !== "Please sign in." ? said : "Wrong username or password.";
    case 403:
      return said || "This account is locked to another laptop. Sign in there, or ask an admin to change the lock.";
    case 423:
      return said || "This account has been turned off. Ask an admin to turn it back on.";
    case 429:
      return said || "Too many wrong passwords for this account. Wait ten minutes, then try again.";
    case 503:
      return said || "The team's account list cannot be read right now. Check that the shared folder is available.";
    case 422:
      return "Check the username and password, then try again.";
    default:
      return said || error.message;
  }
}

function Checkbox({ checked, onChange, label, hint, id }: { checked: boolean; onChange: (value: boolean) => void; label: string; hint: string; id: string }) {
  return (
    <label htmlFor={id} className="flex cursor-pointer items-start gap-3 rounded-md py-1">
      <input
        id={id}
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-0.5 size-4 shrink-0 cursor-pointer rounded border-line-strong accent-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60"
      />
      <span className="min-w-0">
        <span className="block text-[13px] font-medium text-ink">{label}</span>
        <span className="block text-xs text-ink-faint">{hint}</span>
      </span>
    </label>
  );
}

function Banner({ tone, icon, children }: { tone: "info" | "ok"; icon: ReactNode; children: ReactNode }) {
  return (
    <div
      role="status"
      className={cn(
        "mb-5 flex items-start gap-2.5 rounded-md border px-3 py-2.5 text-[13px] text-ink",
        tone === "info" && "border-info/30 bg-info/10",
        tone === "ok" && "border-ok/30 bg-ok/10",
      )}
    >
      <span className={cn("mt-0.5 shrink-0 [&_svg]:size-4", tone === "info" ? "text-info" : "text-ok")}>{icon}</span>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

type Problem = { text: string; field: "username" | "password" | null };

/** Cached answers that are the same whoever signs in: who is signed in, this laptop's sign-in status and system info. */
function belongsToNobody(key: readonly unknown[]): boolean {
  if (key[0] === "system") return true;
  return key[0] === "auth" && (key[1] === "me" || key[1] === "status");
}

/**
 * The form itself, mounted once the laptop's sign-in status is known so it can start from
 * the username "Remember my login details" kept (the password is never kept).
 */
function LoginForm({ savedUsername, onNoAccounts }: { savedUsername: string; onNoAccounts: () => void }) {
  const signIn = useSignIn();
  const [username, setUsername] = useState(savedUsername);
  const [password, setPassword] = useState("");
  const [keepSignedIn, setKeepSignedIn] = useState(() => readKeepSignedIn());
  const [rememberDetails, setRememberDetails] = useState(savedUsername !== "");
  const [problem, setProblem] = useState<Problem | null>(null);
  const usernameRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const name = username.trim().toLowerCase();
    if (!name || !password) {
      setProblem(!name ? { text: "Type your username.", field: "username" } : { text: "Type your password.", field: "password" });
      (!name ? usernameRef.current : passwordRef.current)?.focus();
      return;
    }
    setProblem(null);
    signIn.mutate(
      { username: name, password, remember: keepSignedIn, remember_username: rememberDetails },
      {
        // On success `me` holds the person and the page redirects; only the choice is kept here.
        onSuccess: () => writeKeepSignedIn(keepSignedIn),
        onError: (error) => {
          setPassword("");
          if (error instanceof ApiError && error.status === 409) onNoAccounts();
          setProblem({ text: signInProblem(error), field: error instanceof ApiError && error.status === 401 ? "password" : null });
          passwordRef.current?.focus();
        },
      },
    );
  };

  return (
    <form noValidate onSubmit={onSubmit} className="flex flex-col gap-4">
      <Field label="Username" htmlFor="login-username">
        <Input
          ref={usernameRef}
          id="login-username"
          name="username"
          autoComplete="username"
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
          autoFocus={!savedUsername}
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          className="h-10"
          invalid={problem?.field === "username"}
        />
      </Field>
      <Field label="Password" htmlFor="login-password">
        <PasswordInput
          ref={passwordRef}
          id="login-password"
          name="password"
          autoComplete="current-password"
          autoFocus={Boolean(savedUsername)}
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          invalid={problem?.field === "password"}
        />
      </Field>

      <div className="flex flex-col gap-1">
        <Checkbox
          id="login-keep"
          checked={keepSignedIn}
          onChange={setKeepSignedIn}
          label="Keep me signed in on this laptop"
          hint="Stay signed in for 30 days, even after you close the app. Sign out to stop."
        />
        <Checkbox
          id="login-remember"
          checked={rememberDetails}
          onChange={setRememberDetails}
          label="Remember my login details"
          hint="Fill in your username next time. Your password is never saved."
        />
      </div>

      {problem ? (
        <p role="alert" className="rounded-md border border-fail/30 bg-fail/10 px-3 py-2 text-[13px] text-ink">
          {problem.text}
        </p>
      ) : null}

      <Button type="submit" variant="primary" loading={signIn.isPending} className="h-10 w-full text-sm">
        {signIn.isPending ? "Checking..." : "Sign in"}
      </Button>

      <p className="text-center text-xs text-ink-faint">Forgot your password? Ask an admin to reset it.</p>
    </form>
  );
}

/** /login: product mark, username, password, the two "remember" choices, Sign in. */
export function LoginPage() {
  const location = useLocation();
  const queryClient = useQueryClient();
  const status = useAuthStatus();
  const me = useMe();
  const { from, notice } = readLoginState(location.state);

  // Whatever the last session had cached belongs to that person: drop it before anyone signs in.
  useEffect(() => {
    queryClient.removeQueries({ predicate: (query) => !belongsToNobody(query.queryKey) });
    setSignOutReason(null);
  }, [queryClient]);

  if (me.data) return <Navigate to={safeReturnPath(from)} replace />;
  if (status.data?.needs_first_run) return <Navigate to="/first-run" replace />;

  const machine = status.data?.machine;
  // Any reason the team's account list cannot be used as it is: this laptop's saved copy,
  // Google Drive still downloading it, saved by a newer version, or not readable at all.
  const rosterNotice =
    status.data?.roster_notice?.trim() ||
    (status.data?.roster_source === "cache"
      ? "The shared team list could not be read, so this laptop uses its saved copy. Accounts added or changed on another laptop may not be here yet."
      : "");

  return (
    <AuthLayout
      pageTitle="Sign in"
      below={
        machine ? (
          <span className="inline-flex items-center gap-1.5">
            <Laptop className="size-3.5" aria-hidden />
            This laptop: <span className="font-mono text-ink-muted">{machine}</span>
          </span>
        ) : null
      }
    >
      <h1 className="text-lg font-semibold text-ink">Sign in</h1>
      <p className="mb-5 mt-1 text-sm text-ink-muted">Use the account your admin set up for you.</p>

      {notice === "expired" ? (
        <Banner tone="info" icon={<Info />}>
          <p className="font-medium">You were signed out</p>
          <p className="text-ink-muted">Your session ended. Sign in again to carry on where you were.</p>
        </Banner>
      ) : notice === "signed_out" ? (
        <Banner tone="ok" icon={<LogOut />}>
          <p className="font-medium">You have signed out</p>
          <p className="text-ink-muted">Sign in again whenever you are ready.</p>
        </Banner>
      ) : null}

      {status.isPending ? (
        <div className="flex items-center justify-center gap-2 py-10 text-sm text-ink-muted" role="status">
          <Spinner className="size-4" />
          Checking this laptop...
        </div>
      ) : (
        <LoginForm savedUsername={status.data?.saved_username?.trim() ?? ""} onNoAccounts={() => void status.refetch()} />
      )}

      {rosterNotice ? (
        <div className="mt-5 flex items-start gap-2 border-t border-line pt-4 text-xs text-ink-muted" role="status">
          <CloudOff className="mt-0.5 size-3.5 shrink-0 text-warn" aria-hidden />
          <p>{rosterNotice}</p>
        </div>
      ) : null}
    </AuthLayout>
  );
}
