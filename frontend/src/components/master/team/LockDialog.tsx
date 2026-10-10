import { Laptop, LockOpen } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { errorMessage } from "../../../api/client";
import { useUpdateUser } from "../../../api/users";
import type { TeamUser } from "../../../types/users";
import { Button } from "../../ui/Button";
import { Dialog } from "../../ui/Dialog";
import { Field } from "../../ui/Field";
import { Input } from "../../ui/Input";
import { Notice } from "../../ui/States";
import { useToast } from "../../ui/Toast";

const MAX_MACHINE = 64;

function machineProblem(value: string): string | null {
  const text = value.trim();
  if (!text) return "Type the laptop's computer name.";
  if (text.length > MAX_MACHINE) return `At most ${MAX_MACHINE} characters.`;
  if (/[\\/]/.test(text)) return "Only the computer name, without slashes. For example LAPTOP-7Q2M.";
  return null;
}

/** Locks an account to one laptop (its Windows computer name), or lifts the lock. */
export function LockDialog({ user, thisMachine, isSelf, onClose }: { user: TeamUser | null; thisMachine: string; isSelf: boolean; onClose: () => void }) {
  const { toast } = useToast();
  const update = useUpdateUser();
  const [machine, setMachine] = useState("");
  const [checked, setChecked] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  useEffect(() => {
    if (!user) return;
    setMachine(user.machine ?? thisMachine);
    setChecked(false);
    setServerError(null);
  }, [user, thisMachine]);

  if (!user) return null;

  const problem = machineProblem(machine);
  const lockingSelfOut = isSelf && thisMachine !== "" && machine.trim().toLowerCase() !== thisMachine.toLowerCase();

  const save = (value: string) => {
    setServerError(null);
    update.mutate(
      // An empty name lifts the lock.
      { username: user.username, body: { machine: value } },
      {
        onSuccess: () => {
          toast({
            tone: "success",
            title: value ? `${user.display_name} can only sign in on ${value}` : `${user.display_name} can sign in on any laptop`,
          });
          onClose();
        },
        onError: (error) => setServerError(errorMessage(error)),
      },
    );
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setChecked(true);
    if (problem) return;
    save(machine.trim());
  };

  return (
    <Dialog
      open
      title={`Laptop lock for ${user.display_name}`}
      description="A locked account can only sign in on that one laptop. Use the Windows computer name (Settings > System > About on the laptop)."
      onClose={onClose}
      className="max-w-lg!"
    >
      <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
        <Field label="Laptop (Windows computer name)" htmlFor="lock-machine" error={checked && problem ? problem : undefined} hint={thisMachine ? `This laptop is ${thisMachine}.` : undefined}>
          <div className="flex gap-2">
            <Input id="lock-machine" value={machine} autoFocus spellCheck={false} maxLength={MAX_MACHINE} placeholder="LAPTOP-7Q2M" invalid={checked && Boolean(problem)} onChange={(event) => setMachine(event.target.value)} />
            {thisMachine && machine.trim() !== thisMachine ? (
              <Button variant="secondary" onClick={() => setMachine(thisMachine)}>
                This laptop
              </Button>
            ) : null}
          </div>
        </Field>
        {lockingSelfOut && !problem ? (
          <Notice tone="warn" title="This is your own account">
            After this you can only sign in on {machine.trim()}, not on this laptop.
          </Notice>
        ) : null}
        {serverError ? <Notice tone="fail">{serverError}</Notice> : null}
        <div className="mt-2 flex flex-wrap justify-between gap-2">
          {user.machine ? (
            <Button variant="ghost" icon={<LockOpen />} onClick={() => save("")} disabled={update.isPending}>
              Remove the lock
            </Button>
          ) : (
            <span />
          )}
          <div className="flex gap-2">
            <Button variant="secondary" onClick={onClose} disabled={update.isPending}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" icon={<Laptop />} loading={update.isPending}>
              {user.machine ? "Save the lock" : "Lock to this laptop name"}
            </Button>
          </div>
        </div>
      </form>
    </Dialog>
  );
}
