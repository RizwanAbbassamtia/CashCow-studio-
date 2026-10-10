import { KeyRound, Laptop, Power, PowerOff } from "lucide-react";

import { cn } from "../../../lib/cn";
import { formatDateTime, formatRelative } from "../../../lib/format";
import { ROLE_DESCRIPTIONS, ROLE_LABELS } from "../../../lib/roles";
import { ROLES, type Role } from "../../../types/auth";
import type { TeamUser } from "../../../types/users";
import { Gated } from "../../auth/Gated";
import { Badge } from "../../ui/Badge";
import { Button } from "../../ui/Button";
import { Select } from "../../ui/Input";
import { Table, TBody, TD, TH, THead, TR } from "../../ui/Table";

export const LAST_ADMIN_REASON = "This is the only admin. Make another person an admin first, so the team is never locked out of Users & roles.";
/** The server refuses it too: an admin cannot lock themselves out mid-session. */
export const SELF_TURN_OFF_REASON = "You cannot turn off the account you are signed in with. Ask another admin.";

export interface UsersTableProps {
  users: TeamUser[];
  /** the signed-in admin's username */
  me: string | null;
  thisMachine: string;
  /** the team list is this laptop's saved copy: nothing can be changed */
  readOnly: boolean;
  readOnlyReason: string;
  /** the account whose change is being saved */
  busyUsername: string | null;
  isLastAdmin: (user: TeamUser) => boolean;
  onRole: (user: TeamUser, role: Role) => void;
  onReset: (user: TeamUser) => void;
  onLock: (user: TeamUser) => void;
  onToggleDisabled: (user: TeamUser) => void;
}

function sameMachine(a: string | null, b: string): boolean {
  return Boolean(a) && a!.trim().toLowerCase() === b.trim().toLowerCase();
}

/** One row per account: name, role, laptop lock, last sign-in, status and the admin's buttons. */
export function UsersTable({ users, me, thisMachine, readOnly, readOnlyReason, busyUsername, isLastAdmin, onRole, onReset, onLock, onToggleDisabled }: UsersTableProps) {
  return (
    <Table>
      <THead>
        <TR>
          <TH className="min-w-[220px]">Person</TH>
          <TH className="w-[170px]">Role</TH>
          <TH className="w-[190px]">Laptop</TH>
          <TH className="w-[140px]">Last sign-in</TH>
          <TH className="w-[120px]">Status</TH>
          <TH className="w-[360px] text-right">Actions</TH>
        </TR>
      </THead>
      <TBody>
        {users.map((user) => {
          const isMe = user.username === me;
          const lastAdmin = isLastAdmin(user);
          const busy = busyUsername === user.username;
          const created = user.created_at ? `Added ${formatDateTime(user.created_at)}${user.created_by ? ` by ${user.created_by}` : ""}` : undefined;
          const lockedHere = sameMachine(user.machine, thisMachine);
          return (
            <TR key={user.username} className={cn(user.disabled && "opacity-70")}>
              <TD>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium text-ink">{user.display_name}</span>
                  {isMe ? <Badge tone="accent">You</Badge> : null}
                </div>
                <div className="font-mono text-xs text-ink-faint" title={created}>
                  {user.username}
                </div>
                {user.must_change_password ? <div className="mt-0.5 text-[11px] text-warn">Picks a new password at the next sign-in</div> : null}
              </TD>
              <TD>
                <Gated allowed={!readOnly} reason={readOnlyReason}>
                  <Select
                    value={user.role}
                    disabled={readOnly || busy}
                    onChange={(event) => onRole(user, event.target.value as Role)}
                    aria-label={`Role of ${user.display_name}`}
                    title={lastAdmin ? LAST_ADMIN_REASON : ROLE_DESCRIPTIONS[user.role]}
                    className="h-8 text-[13px]"
                  >
                    {ROLES.map((role) => (
                      <option key={role} value={role} disabled={lastAdmin && role !== "admin"}>
                        {ROLE_LABELS[role]}
                      </option>
                    ))}
                  </Select>
                </Gated>
                {lastAdmin ? (
                  <p className="mt-1 text-[11px] leading-snug text-ink-faint" title={LAST_ADMIN_REASON}>
                    The only admin
                  </p>
                ) : null}
              </TD>
              <TD>
                {user.machine ? (
                  <div className="flex items-start gap-1.5">
                    <Laptop className="mt-0.5 size-3.5 shrink-0 text-ink-faint" aria-hidden />
                    <div className="min-w-0">
                      <div className="truncate font-mono text-xs text-ink" title={user.machine}>
                        {user.machine}
                      </div>
                      {lockedHere ? <div className="text-[11px] text-ink-faint">This laptop</div> : null}
                    </div>
                  </div>
                ) : (
                  <span className="text-xs text-ink-faint">Any laptop</span>
                )}
              </TD>
              <TD className="text-ink-muted" title={formatDateTime(user.last_login_at)}>
                {user.last_login_at ? formatRelative(user.last_login_at) : <span className="text-ink-faint">Never</span>}
              </TD>
              <TD>
                {user.disabled ? (
                  <Badge tone="neutral" dot className="whitespace-nowrap">
                    Turned off
                  </Badge>
                ) : (
                  <Badge tone="ok" dot className="whitespace-nowrap">
                    Active
                  </Badge>
                )}
              </TD>
              <TD className="text-right">
                <div className="inline-flex flex-wrap items-center justify-end gap-1">
                  <Gated allowed={!readOnly} reason={readOnlyReason}>
                    <Button variant="ghost" size="sm" icon={<KeyRound />} onClick={() => onReset(user)} disabled={readOnly || busy}>
                      Reset password
                    </Button>
                  </Gated>
                  <Gated allowed={!readOnly} reason={readOnlyReason}>
                    <Button variant="ghost" size="sm" icon={<Laptop />} onClick={() => onLock(user)} disabled={readOnly || busy}>
                      {user.machine ? "Change lock" : "Lock to laptop"}
                    </Button>
                  </Gated>
                  {user.disabled ? (
                    <Gated allowed={!readOnly} reason={readOnlyReason}>
                      <Button variant="ghost" size="sm" icon={<Power />} onClick={() => onToggleDisabled(user)} disabled={readOnly || busy} loading={busy}>
                        Turn back on
                      </Button>
                    </Gated>
                  ) : (
                    <Gated
                      allowed={!readOnly && !lastAdmin && !isMe}
                      reason={readOnly ? readOnlyReason : isMe ? SELF_TURN_OFF_REASON : LAST_ADMIN_REASON}
                    >
                      <Button
                        variant="ghost"
                        size="sm"
                        icon={<PowerOff />}
                        onClick={() => onToggleDisabled(user)}
                        disabled={readOnly || lastAdmin || isMe || busy}
                        className="hover:text-fail"
                      >
                        Turn off
                      </Button>
                    </Gated>
                  )}
                </div>
              </TD>
            </TR>
          );
        })}
      </TBody>
    </Table>
  );
}
