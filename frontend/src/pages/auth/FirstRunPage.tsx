import { zodResolver } from "@hookform/resolvers/zod";
import { Check, Circle, FolderOpen, Laptop, ShieldCheck, Users } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Controller, useForm, useWatch } from "react-hook-form";
import { Link, Navigate } from "react-router";
import { z } from "zod";

import { useAuthStatus, useChooseTeamFolder, useFirstRun, useMe } from "../../api/auth";
import { ApiError, errorMessage } from "../../api/client";
import { useSystemInfo } from "../../api/system";
import { AuthLayout } from "../../components/auth/AuthLayout";
import { HOME_PATH } from "../../components/auth/loginRedirect";
import { PasswordInput } from "../../components/auth/PasswordInput";
import { Button } from "../../components/ui/Button";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Spinner } from "../../components/ui/Spinner";
import { useToast } from "../../components/ui/Toast";
import { cn } from "../../lib/cn";
import { PASSWORD_MIN_LENGTH, USERNAME_PATTERN } from "../../types/auth";

const USERNAME_RULE = "3 to 32 characters: lowercase letters, numbers, dots, dashes or underscores.";
/** The server's limit for a person's name. */
const MAX_NAME = 64;

const schema = z
  .object({
    display_name: z.string().trim().min(1, "Type the name the team knows you by.").max(MAX_NAME, `Keep the name under ${MAX_NAME + 1} characters.`),
    username: z.string().regex(USERNAME_PATTERN, `Use ${USERNAME_RULE}`),
    password: z.string().min(PASSWORD_MIN_LENGTH, `Use at least ${PASSWORD_MIN_LENGTH} characters.`),
    confirm: z.string().min(1, "Type the password again."),
  })
  .refine((values) => values.password === values.confirm, { path: ["confirm"], message: "The two passwords are not the same." });

type Values = z.infer<typeof schema>;

/** "Imran Azmeer" -> "imran": a starting suggestion the person can change. */
function suggestUsername(displayName: string): string {
  const first = displayName.trim().split(/\s+/)[0] ?? "";
  return first
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9._-]/g, "")
    .slice(0, 32);
}

/**
 * "Joining a team?": a laptop added to a team that already uses the app points at the team's
 * shared folder instead of making an admin of its own. Only offered while this laptop has no
 * account at all (the server checks that too).
 */
function JoinTeamFolder({ currentFolder }: { currentFolder: string | undefined }) {
  const choose = useChooseTeamFolder();
  const { toast } = useToast();
  const [open, setOpen] = useState(false);
  const [folder, setFolder] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [usedEmpty, setUsedEmpty] = useState<string | null>(null);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const path = folder.trim();
    if (!path) {
      setProblem("Type the full path of the team's shared folder.");
      return;
    }
    setProblem(null);
    setUsedEmpty(null);
    choose.mutate(
      { shared_dir: path },
      {
        onSuccess: (status) => {
          if (!status.needs_first_run) {
            // The page moves on to the sign-in screen by itself.
            toast({
              tone: "success",
              title: "Found your team's accounts",
              description: "Sign in with the username and password your admin gave you.",
            });
            return;
          }
          setUsedEmpty(path);
        },
        onError: (error) => setProblem(errorMessage(error)),
      },
    );
  };

  return (
    <div className="mb-6 rounded-md border border-line bg-surface-2/50 px-4 py-3">
      <p className="flex items-center gap-2 text-[13px] font-semibold text-ink">
        <Users className="size-4 text-accent-text" aria-hidden />
        Joining a team?
      </p>
      <p className="mt-1 text-xs text-ink-muted">
        If your team already uses CashCow Studio, do not create a new admin here. Choose the team&apos;s shared Google Drive folder, then sign in with the account
        your admin made for you.
      </p>
      {currentFolder ? (
        <p className="mt-1.5 break-all text-[11px] text-ink-faint">
          Accounts are kept in the shared folder. This laptop uses: <span className="font-mono">{currentFolder}</span>
        </p>
      ) : null}

      {usedEmpty ? (
        <p role="status" className="mt-3 rounded-md border border-info/30 bg-info/10 px-3 py-2 text-xs text-ink">
          This laptop now uses <span className="break-all font-mono">{usedEmpty}</span>. It has no accounts yet, so the account you create below is the first admin
          of that folder. If your team already has accounts there, Google Drive may still be downloading them: wait, then choose the folder again.
        </p>
      ) : null}

      {open ? (
        <form noValidate onSubmit={submit} className="mt-3 flex flex-col gap-2">
          <Field label="The team's shared folder" htmlFor="join-folder" hint="The same folder the other laptops use: Settings, Folders, Shared folder.">
            <Input
              id="join-folder"
              value={folder}
              autoFocus
              spellCheck={false}
              placeholder="G:\My Drive\CashCow Studio"
              className="h-9 font-mono text-[13px]"
              invalid={Boolean(problem)}
              onChange={(event) => setFolder(event.target.value)}
            />
          </Field>
          {problem ? (
            <p role="alert" className="rounded-md border border-fail/30 bg-fail/10 px-3 py-2 text-xs text-ink">
              {problem}
            </p>
          ) : null}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={() => setOpen(false)} disabled={choose.isPending}>
              Cancel
            </Button>
            <Button type="submit" variant="secondary" size="sm" icon={<FolderOpen />} loading={choose.isPending}>
              Use this folder
            </Button>
          </div>
        </form>
      ) : (
        <Button variant="secondary" size="sm" icon={<FolderOpen />} className="mt-3" onClick={() => setOpen(true)}>
          Choose the team&apos;s shared folder
        </Button>
      )}
    </div>
  );
}

/** /first-run: a fresh install has no accounts and no default password; the first account is the admin. */
export function FirstRunPage() {
  const status = useAuthStatus();
  const system = useSystemInfo();
  const me = useMe();
  const create = useFirstRun();
  const { toast } = useToast();
  const [problem, setProblem] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const usernameEdited = useRef(false);

  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { display_name: "", username: "", password: "", confirm: "" },
    mode: "onTouched",
  });
  const { register, handleSubmit, formState, control, setValue, setError } = form;
  const displayName = useWatch({ control, name: "display_name" }) ?? "";
  const password = useWatch({ control, name: "password" }) ?? "";
  const longEnough = password.length >= PASSWORD_MIN_LENGTH;
  const warning = status.data?.first_run_warning?.trim() || null;

  // The username follows the name until the person types one of their own.
  useEffect(() => {
    if (!usernameEdited.current) setValue("username", suggestUsername(displayName));
  }, [displayName, setValue]);

  if (me.data) return <Navigate to={HOME_PATH} replace />;
  if (status.data && !status.data.needs_first_run) return <Navigate to="/login" replace />;

  const onValid = (values: Values) => {
    if (warning && !confirmed) {
      setProblem("Tick the box above to confirm that nobody on your team has an account yet, or wait for Google Drive to finish syncing.");
      return;
    }
    setProblem(null);
    create.mutate(
      {
        username: values.username,
        display_name: values.display_name.trim(),
        password: values.password,
        ...(warning ? { confirm_team_folder: confirmed } : {}),
      },
      {
        onSuccess: (user) =>
          toast({
            tone: "success",
            title: `Welcome, ${user.display_name || user.username}`,
            description: "You are the admin. Add the rest of the team in Settings, Users & roles.",
          }),
        onError: (error) => {
          if (error instanceof ApiError && error.fieldErrors.length > 0) {
            for (const field of error.fieldErrors) {
              if (field.path === "username" || field.path === "display_name" || field.path === "password") {
                setError(field.path, { message: field.message });
              }
            }
          }
          setProblem(errorMessage(error));
          // The folder may have changed meanwhile (accounts arrived, channels appeared).
          void status.refetch();
        },
      },
    );
  };

  const machine = status.data?.machine;

  return (
    <AuthLayout
      pageTitle="Create the first admin account"
      below={
        machine ? (
          <span className="inline-flex items-center gap-1.5">
            <Laptop className="size-3.5" aria-hidden />
            This laptop: <span className="font-mono text-ink-muted">{machine}</span>
          </span>
        ) : null
      }
    >
      <div className="mb-5 flex items-start gap-3">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-full bg-accent/15 text-accent-text">
          <ShieldCheck className="size-4" aria-hidden />
        </div>
        <div>
          <h1 className="text-lg font-semibold text-ink">Create the first admin account</h1>
          <p className="mt-1 text-sm text-ink-muted">
            This laptop sees no accounts yet, and there is no default password. The first admin adds the rest of the team and looks after channels, settings and
            keys.
          </p>
        </div>
      </div>

      {status.isPending ? (
        <div className="flex items-center justify-center gap-2 py-10 text-sm text-ink-muted" role="status">
          <Spinner className="size-4" />
          Checking this laptop...
        </div>
      ) : (
        <>
          <JoinTeamFolder currentFolder={system.data?.shared_dir} />

          <form noValidate onSubmit={handleSubmit(onValid)} className="flex flex-col gap-4">
            {warning ? (
              <div className="rounded-md border border-warn/30 bg-warn/10 px-3 py-2.5 text-[13px] text-ink">
                <p className="font-semibold">This shared folder already holds channels</p>
                <p className="mt-0.5 text-xs text-ink-muted">{warning}</p>
                <label htmlFor="fr-confirm-folder" className="mt-2 flex cursor-pointer items-start gap-2.5">
                  <input
                    id="fr-confirm-folder"
                    type="checkbox"
                    checked={confirmed}
                    onChange={(event) => setConfirmed(event.target.checked)}
                    className="mt-0.5 size-4 shrink-0 cursor-pointer rounded border-line-strong accent-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60"
                  />
                  <span className="text-xs font-medium text-ink">Nobody on my team has an account yet. Create the first admin here.</span>
                </label>
              </div>
            ) : null}
            <Field label="Your name" htmlFor="fr-name" hint="Shown in the top bar and next to your approvals." error={formState.errors.display_name?.message}>
              <Input
                id="fr-name"
                autoComplete="name"
                autoFocus
                maxLength={MAX_NAME}
                className="h-10"
                invalid={Boolean(formState.errors.display_name)}
                {...register("display_name")}
              />
            </Field>
            <Field label="Username" htmlFor="fr-username" hint={`For signing in. ${USERNAME_RULE}`} error={formState.errors.username?.message}>
              <Controller
                control={control}
                name="username"
                render={({ field }) => (
                  <Input
                    id="fr-username"
                    autoComplete="username"
                    autoCapitalize="off"
                    autoCorrect="off"
                    spellCheck={false}
                    className="h-10 font-mono"
                    invalid={Boolean(formState.errors.username)}
                    name={field.name}
                    ref={field.ref}
                    value={field.value}
                    onBlur={field.onBlur}
                    onChange={(event) => {
                      usernameEdited.current = true;
                      field.onChange(event.target.value.toLowerCase().replace(/\s+/g, ""));
                    }}
                  />
                )}
              />
            </Field>
            <Field label="Password" htmlFor="fr-password" error={formState.errors.password?.message}>
              <PasswordInput id="fr-password" autoComplete="new-password" invalid={Boolean(formState.errors.password)} {...register("password")} />
            </Field>
            <ul className="-mt-1 flex flex-col gap-1 text-xs" aria-live="polite">
              <li className={cn("flex items-center gap-1.5", longEnough ? "text-ok" : "text-ink-faint")}>
                {longEnough ? <Check className="size-3.5" aria-hidden /> : <Circle className="size-3" aria-hidden />}
                At least {PASSWORD_MIN_LENGTH} characters
              </li>
              <li className="text-ink-faint">Tip: three or four unrelated words are easy to type and hard to guess.</li>
            </ul>
            <Field label="Password again" htmlFor="fr-confirm" error={formState.errors.confirm?.message}>
              <PasswordInput id="fr-confirm" autoComplete="new-password" invalid={Boolean(formState.errors.confirm)} {...register("confirm")} />
            </Field>

            {problem ? (
              <div role="alert" className="rounded-md border border-fail/30 bg-fail/10 px-3 py-2 text-[13px] text-ink">
                {problem}
                {create.error instanceof ApiError && (create.error.status === 403 || create.error.status === 409) && !warning ? (
                  <>
                    {" "}
                    <Link to="/login" className="font-medium text-accent-text underline-offset-2 hover:underline">
                      Go to sign in
                    </Link>
                  </>
                ) : null}
              </div>
            ) : null}

            <Button type="submit" variant="primary" loading={create.isPending} className="h-10 w-full text-sm">
              Create admin account and sign in
            </Button>
            <p className="text-center text-xs text-ink-faint">Nobody can read your password: only a scrambled fingerprint of it is stored.</p>
          </form>
        </>
      )}
    </AuthLayout>
  );
}
