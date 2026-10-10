import { Lock, Search, ShieldCheck, UserPlus, Users } from "lucide-react";
import { useMemo, useState } from "react";

import { useAuthStatus, useCurrentUser } from "../../api/auth";
import { errorMessage } from "../../api/client";
import { isForbidden, useUpdateUser, useUsers } from "../../api/users";
import { Gated, RequireRole } from "../../components/auth/Gated";
import { CountLine } from "../../components/master/CountLine";
import { matchesText } from "../../components/master/format";
import { ACTIVITY_PATH } from "../../components/master/routes";
import { SearchBox } from "../../components/master/SearchBox";
import { AddUserDialog } from "../../components/master/team/AddUserDialog";
import { LockDialog } from "../../components/master/team/LockDialog";
import { ResetPasswordDialog } from "../../components/master/team/ResetPasswordDialog";
import { LAST_ADMIN_REASON, SELF_TURN_OFF_REASON, UsersTable } from "../../components/master/team/UsersTable";
import { Button, LinkButton } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { ConfirmDialog } from "../../components/ui/Dialog";
import { PageHeader } from "../../components/ui/PageHeader";
import { LoadingBlock } from "../../components/ui/Spinner";
import { EmptyState, ErrorState, Notice } from "../../components/ui/States";
import { useToast } from "../../components/ui/Toast";
import { plural } from "../../lib/format";
import { ROLE_DESCRIPTIONS, ROLE_LABELS } from "../../lib/roles";
import { ROLES, type Role } from "../../types/auth";
import type { TeamUser, UserUpdate } from "../../types/users";

const READ_ONLY_REASON =
  "The team list is this laptop's saved copy right now, so accounts cannot be changed. Check that Google Drive is running, then restart the app.";

function withArticle(role: Role): string {
  const label = ROLE_LABELS[role].toLowerCase();
  return `${role === "viewer" ? "a" : "an"} ${label}`;
}

function UsersPageBody() {
  const { toast } = useToast();
  const me = useCurrentUser();
  const status = useAuthStatus();
  const users = useUsers();
  const update = useUpdateUser();

  const [search, setSearch] = useState("");
  const [adding, setAdding] = useState(false);
  const [resetting, setResetting] = useState<TeamUser | null>(null);
  const [locking, setLocking] = useState<TeamUser | null>(null);
  const [turningOff, setTurningOff] = useState<TeamUser | null>(null);
  const [selfRole, setSelfRole] = useState<{ user: TeamUser; role: Role } | null>(null);

  const thisMachine = status.data?.machine ?? "";
  const rosterNotice = (status.data as { roster_notice?: unknown } | undefined)?.roster_notice;
  const readOnly = status.data?.roster_source === "cache";

  const all = useMemo(() => users.data ?? [], [users.data]);
  const rows = useMemo(() => all.filter((user) => matchesText(search, user.display_name, user.username, user.machine, ROLE_LABELS[user.role])), [all, search]);
  const taken = useMemo(() => new Set(all.map((user) => user.username.toLowerCase())), [all]);
  const activeAdmins = all.filter((user) => user.role === "admin" && !user.disabled);
  const turnedOff = all.filter((user) => user.disabled).length;
  const isLastAdmin = (user: TeamUser) => user.role === "admin" && !user.disabled && activeAdmins.length <= 1;
  const myName = me?.username ?? null;
  const busyUsername = update.isPending ? (update.variables?.username ?? null) : null;

  const change = (user: TeamUser, body: UserUpdate, success: string, after?: () => void) =>
    update.mutate(
      { username: user.username, body },
      {
        onSuccess: () => {
          toast({ tone: "success", title: success });
          after?.();
        },
        onError: (error) => {
          toast({ tone: "error", title: `Could not change the account of ${user.display_name}`, description: errorMessage(error) });
          after?.();
        },
      },
    );

  const onRole = (user: TeamUser, role: Role) => {
    if (role === user.role) return;
    if (isLastAdmin(user) && role !== "admin") {
      toast({ tone: "error", title: "The only admin keeps the admin role", description: LAST_ADMIN_REASON });
      return;
    }
    if (user.username === myName && role !== "admin") {
      setSelfRole({ user, role });
      return;
    }
    change(user, { role }, `${user.display_name} is now ${withArticle(role)}`);
  };

  const onToggleDisabled = (user: TeamUser) => {
    if (user.disabled) {
      change(user, { disabled: false }, `${user.display_name} can sign in again`);
      return;
    }
    if (user.username === myName) {
      toast({ tone: "error", title: "This is your own account", description: SELF_TURN_OFF_REASON });
      return;
    }
    if (isLastAdmin(user)) {
      toast({ tone: "error", title: "The only admin cannot be turned off", description: LAST_ADMIN_REASON });
      return;
    }
    setTurningOff(user);
  };

  const content = users.isPending ? (
    <LoadingBlock label="Loading the team..." />
  ) : users.isError ? (
    isForbidden(users.error) ? (
      <EmptyState icon={Lock} title="Only an admin can manage team accounts" description="Ask an admin to add people, change roles or reset passwords." />
    ) : (
      <ErrorState error={users.error} title="Could not load the team" onRetry={() => void users.refetch()} />
    )
  ) : all.length === 0 ? (
    <EmptyState icon={Users} title="No accounts yet" description="Add the people who work on the channels. Each gets a username and a one-time password." />
  ) : rows.length === 0 ? (
    <EmptyState icon={Search} title="Nobody matches" description="Clear the search to see the whole team." />
  ) : (
    <>
      <UsersTable
        users={rows}
        me={myName}
        thisMachine={thisMachine}
        readOnly={readOnly}
        readOnlyReason={READ_ONLY_REASON}
        busyUsername={busyUsername}
        isLastAdmin={isLastAdmin}
        onRole={onRole}
        onReset={setResetting}
        onLock={setLocking}
        onToggleDisabled={onToggleDisabled}
      />
      <CountLine shown={rows.length} total={all.length} noun="person" nounPlural="people" />
    </>
  );

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Users & roles"
        description="Who can sign in to CashCow Studio and what each person can do. The team list is shared by every laptop through the shared folder."
        actions={
          <>
            <LinkButton to={ACTIVITY_PATH} variant="secondary">
              Activity
            </LinkButton>
            <Gated allowed={!readOnly} reason={READ_ONLY_REASON}>
              <Button variant="primary" icon={<UserPlus />} onClick={() => setAdding(true)} disabled={readOnly || users.isError}>
                Add a person
              </Button>
            </Gated>
          </>
        }
      >
        {users.isSuccess && all.length > 0 ? (
          <p className="mt-2 text-xs text-ink-faint">
            {plural(all.length, "person", "people")} - {plural(activeAdmins.length, "admin")}
            {turnedOff > 0 ? ` - ${turnedOff} turned off` : ""}
          </p>
        ) : null}
      </PageHeader>

      {readOnly ? (
        <Notice tone="warn" title="Working from this laptop's saved copy of the team list">
          {typeof rosterNotice === "string" && rosterNotice.trim() ? rosterNotice : READ_ONLY_REASON}
        </Notice>
      ) : null}

      {users.isSuccess && all.length > 1 ? <SearchBox value={search} onChange={setSearch} placeholder="Search name, username or laptop" className="w-80" /> : null}

      <Card flush>{content}</Card>

      <Card title="What each role can do" description="The server checks the same rules, so a person never gets further than their role allows.">
        <div className="grid gap-4 md:grid-cols-3">
          {ROLES.map((role) => (
            <div key={role} className="rounded-md border border-line bg-surface-2/40 px-4 py-3">
              <p className="flex items-center gap-2 text-[13px] font-semibold text-ink">
                <ShieldCheck className="size-4 text-ink-faint" aria-hidden />
                {ROLE_LABELS[role]}
              </p>
              <p className="mt-1 text-xs text-ink-muted">{ROLE_DESCRIPTIONS[role]}</p>
            </div>
          ))}
        </div>
      </Card>

      <AddUserDialog open={adding} onClose={() => setAdding(false)} taken={taken} thisMachine={thisMachine} />
      <ResetPasswordDialog user={resetting} isSelf={resetting?.username === myName} onClose={() => setResetting(null)} />
      <LockDialog user={locking} thisMachine={thisMachine} isSelf={locking?.username === myName} onClose={() => setLocking(null)} />

      <ConfirmDialog
        open={turningOff !== null}
        title={turningOff ? `Turn off the account of ${turningOff.display_name}?` : ""}
        description="They are signed out and cannot sign in on any laptop until an admin turns the account back on. Their videos and their history stay."
        confirmLabel="Turn off"
        tone="danger"
        loading={update.isPending}
        onConfirm={() => {
          if (turningOff) change(turningOff, { disabled: true }, `${turningOff.display_name} can no longer sign in`, () => setTurningOff(null));
        }}
        onCancel={() => setTurningOff(null)}
      />

      <ConfirmDialog
        open={selfRole !== null}
        title={selfRole ? `Make yourself ${withArticle(selfRole.role)}?` : ""}
        description="You will no longer be able to open Users & roles or change the settings. Another admin can give you the admin role back."
        confirmLabel="Change my role"
        tone="danger"
        loading={update.isPending}
        onConfirm={() => {
          if (selfRole) change(selfRole.user, { role: selfRole.role }, `You are now ${withArticle(selfRole.role)}`, () => setSelfRole(null));
        }}
        onCancel={() => setSelfRole(null)}
      />
    </div>
  );
}

/** /settings/users - Users & roles (admins only). */
export function UsersPage() {
  return (
    <RequireRole action="manage_users">
      <UsersPageBody />
    </RequireRole>
  );
}
