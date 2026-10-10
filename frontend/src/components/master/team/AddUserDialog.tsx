import { Laptop, UserPlus } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { errorMessage } from "../../../api/client";
import { useCreateUser } from "../../../api/users";
import { cn } from "../../../lib/cn";
import { ROLE_DESCRIPTIONS, ROLE_LABELS } from "../../../lib/roles";
import { ROLES, type Role } from "../../../types/auth";
import { DEFAULT_ROLE, USERNAME_RULE, usernameProblem } from "../../../types/users";
import { Button } from "../../ui/Button";
import { Dialog } from "../../ui/Dialog";
import { Field } from "../../ui/Field";
import { Input } from "../../ui/Input";
import { Notice } from "../../ui/States";
import { useToast } from "../../ui/Toast";
import { OneTimePassword } from "./OneTimePassword";

export interface AddUserDialogProps {
  open: boolean;
  onClose: () => void;
  /** usernames already taken, lower-case */
  taken: ReadonlySet<string>;
  /** this laptop's computer name, offered for the laptop lock */
  thisMachine: string;
}

/** A radio card per role, with what the role may do. */
export function RolePicker({ value, onChange, name, disabledRoles = [] }: { value: Role; onChange: (role: Role) => void; name: string; disabledRoles?: readonly Role[] }) {
  return (
    <div role="radiogroup" aria-label="Role" className="grid gap-2">
      {ROLES.map((role) => {
        const selected = role === value;
        const disabled = disabledRoles.includes(role);
        return (
          <label
            key={role}
            className={cn(
              "flex cursor-pointer items-start gap-3 rounded-md border px-3 py-2.5 transition-colors",
              selected ? "border-accent/60 bg-accent/10" : "border-line hover:border-line-strong",
              disabled && "cursor-not-allowed opacity-50",
            )}
          >
            <input type="radio" name={name} className="mt-1 accent-accent" checked={selected} disabled={disabled} onChange={() => onChange(role)} />
            <span className="min-w-0">
              <span className="block text-[13px] font-semibold text-ink">{ROLE_LABELS[role]}</span>
              <span className="block text-xs text-ink-muted">{ROLE_DESCRIPTIONS[role]}</span>
            </span>
          </label>
        );
      })}
    </div>
  );
}

/**
 * Adds a person to the team. The server makes up a password, which this dialog shows once
 * with a Copy button; closing the dialog forgets it.
 */
export function AddUserDialog({ open, onClose, taken, thisMachine }: AddUserDialogProps) {
  const { toast } = useToast();
  const create = useCreateUser();
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [role, setRole] = useState<Role>(DEFAULT_ROLE);
  const [lock, setLock] = useState(false);
  const [machine, setMachine] = useState("");
  const [checked, setChecked] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  // A fresh form every time the dialog opens.
  useEffect(() => {
    if (!open) return;
    setUsername("");
    setDisplayName("");
    setRole(DEFAULT_ROLE);
    setLock(false);
    setMachine(thisMachine);
    setChecked(false);
    setServerError(null);
  }, [open, thisMachine]);

  const created = create.data ?? null;

  const close = () => {
    create.reset(); // forgets the one-time password
    onClose();
  };

  const cleanUsername = username.trim().toLowerCase();
  const usernameError = usernameProblem(username.trim()) ?? (taken.has(cleanUsername) ? "Someone on the team already has this username." : null);
  const machineError = lock && !machine.trim() ? "Type the laptop's computer name, or untick the box." : null;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setChecked(true);
    setServerError(null);
    if (usernameError || machineError) return;
    create.mutate(
      {
        username: cleanUsername,
        display_name: displayName.trim() || cleanUsername,
        role,
        ...(lock ? { machine: machine.trim() } : {}),
      },
      {
        onSuccess: (result) => toast({ tone: "success", title: `${result.user.display_name} was added`, description: "Copy the password before you close the window." }),
        onError: (error) => setServerError(errorMessage(error)),
      },
    );
  };

  if (created) {
    const name = created.user.display_name;
    return (
      <Dialog
        open={open}
        title={`${name} can sign in now`}
        description={
          <>
            Username <span className="font-mono font-semibold text-ink">{created.user.username}</span>, {ROLE_LABELS[created.user.role].toLowerCase()}
            {created.user.machine ? `, only on the laptop ${created.user.machine}` : ", on any laptop"}.
          </>
        }
        // The password is gone once this closes, so only the button closes it.
        onClose={() => undefined}
        className="max-w-lg!"
        footer={
          <Button variant="primary" onClick={close}>
            Done, I have copied it
          </Button>
        }
      >
        <OneTimePassword password={created.password} name={name} />
      </Dialog>
    );
  }

  return (
    <Dialog open={open} title="Add a person" description="They get a username and a password that works once; they choose their own when they first sign in." onClose={close} className="max-w-lg!">
      <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
        <Field label="Username" htmlFor="new-user-username" required error={checked && usernameError ? usernameError : undefined} hint={USERNAME_RULE}>
          <Input
            id="new-user-username"
            value={username}
            autoFocus
            autoComplete="off"
            spellCheck={false}
            invalid={checked && Boolean(usernameError)}
            placeholder="sara.khan"
            onChange={(event) => setUsername(event.target.value.toLowerCase())}
          />
        </Field>
        <Field label="Name shown in the app" htmlFor="new-user-name" hint="For example Sara Khan. Leave it empty to use the username.">
          <Input id="new-user-name" value={displayName} autoComplete="off" placeholder="Sara Khan" maxLength={120} onChange={(event) => setDisplayName(event.target.value)} />
        </Field>
        <div className="flex flex-col gap-1.5">
          <span className="text-[13px] font-medium text-ink-muted">What they can do</span>
          <RolePicker value={role} onChange={setRole} name="new-user-role" />
        </div>
        <div className="flex flex-col gap-2 rounded-md border border-line px-3 py-2.5">
          <label className="flex items-start gap-3">
            <input type="checkbox" className="mt-0.5 size-4 accent-accent" checked={lock} onChange={(event) => setLock(event.target.checked)} />
            <span>
              <span className="flex items-center gap-1.5 text-[13px] font-medium text-ink">
                <Laptop className="size-3.5 text-ink-faint" aria-hidden />
                Only allow this account on one laptop
              </span>
              <span className="block text-xs text-ink-muted">Signing in on any other laptop is refused. You can change this later.</span>
            </span>
          </label>
          {lock ? (
            <Field label="Laptop (Windows computer name)" htmlFor="new-user-machine" error={checked && machineError ? machineError : undefined} hint={thisMachine ? `This laptop is ${thisMachine}.` : undefined}>
              <div className="flex gap-2">
                <Input id="new-user-machine" value={machine} spellCheck={false} maxLength={64} placeholder="LAPTOP-7Q2M" invalid={checked && Boolean(machineError)} onChange={(event) => setMachine(event.target.value)} />
                {thisMachine && machine.trim() !== thisMachine ? (
                  <Button variant="secondary" onClick={() => setMachine(thisMachine)}>
                    This laptop
                  </Button>
                ) : null}
              </div>
            </Field>
          ) : null}
        </div>
        {serverError ? <Notice tone="fail">{serverError}</Notice> : null}
        <div className="mt-2 flex justify-end gap-2">
          <Button variant="secondary" onClick={close} disabled={create.isPending}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" icon={<UserPlus />} loading={create.isPending}>
            Add and make a password
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
