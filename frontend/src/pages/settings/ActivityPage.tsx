import { useQueryClient } from "@tanstack/react-query";
import { History, RefreshCw, Search } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router";

import { useCan, useCurrentUser } from "../../api/auth";
import { auditKeys, useAudit } from "../../api/audit";
import { useChannels } from "../../api/channels";
import { useProjects } from "../../api/projects";
import { useUsers } from "../../api/users";
import { useProjectEvents } from "../../api/ws";
import { auditIcon, auditTitle } from "../../components/auth/activity";
import { RequireRole } from "../../components/auth/Gated";
import { AUDIT_AREA_TONES, auditActionLabel, auditArea, auditDetailItems, describeAuditRow } from "../../components/master/audit";
import { ChannelLink, channelNamer } from "../../components/master/ChannelLink";
import { CountLine } from "../../components/master/CountLine";
import { matchesText } from "../../components/master/format";
import { projectPath, USERS_PATH } from "../../components/master/routes";
import { SearchBox } from "../../components/master/SearchBox";
import { Button, LinkButton } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Select } from "../../components/ui/Input";
import { PageHeader } from "../../components/ui/PageHeader";
import { LoadingBlock } from "../../components/ui/Spinner";
import { EmptyState, ErrorState, Notice } from "../../components/ui/States";
import { Table, TBody, TD, TH, THead, TR } from "../../components/ui/Table";
import { cn } from "../../lib/cn";
import { formatDateTime, formatRelative } from "../../lib/format";
import type { AuditRow } from "../../types/auth";
import type { ProjectEvent } from "../../types/project";
import { AUDIT_ACTION_GROUPS, AUDIT_ACTION_LABELS, AUDIT_DEFAULT_LIMIT, AUDIT_LIMIT_CHOICES } from "../../types/users";

const TONE_CLASSES = {
  accent: "bg-accent/15 text-accent-text",
  info: "bg-info/12 text-info",
  warn: "bg-warn/12 text-warn",
  ok: "bg-ok/12 text-ok",
  neutral: "bg-surface-2 text-ink-muted",
  fail: "bg-fail/12 text-fail",
} as const;

/** Refreshes the log when the pipeline reports a change, at most every few seconds. */
function useRefreshOnProjectEvents() {
  const queryClient = useQueryClient();
  const timer = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    },
    [],
  );
  const onEvent = useCallback(
    (event: ProjectEvent) => {
      if (event.type !== "project.update" || timer.current !== null) return;
      timer.current = window.setTimeout(() => {
        timer.current = null;
        void queryClient.invalidateQueries({ queryKey: auditKeys.all });
      }, 2_000);
    },
    [queryClient],
  );
  useProjectEvents(onEvent);
}

function ActivityPageBody() {
  const me = useCurrentUser();
  const isAdmin = useCan("manage_users");
  const users = useUsers({ enabled: isAdmin });
  const projects = useProjects();
  const channels = useChannels();
  useRefreshOnProjectEvents();

  const [who, setWho] = useState("");
  const [action, setAction] = useState("");
  const [projectId, setProjectId] = useState("");
  const [limit, setLimit] = useState<number>(AUDIT_DEFAULT_LIMIT);
  const [search, setSearch] = useState("");
  const [refreshing, setRefreshing] = useState(false);

  const filters = useMemo(() => ({ limit, user: who || undefined, action: action || undefined, project_id: projectId || undefined }), [limit, who, action, projectId]);
  const audit = useAudit(filters, { refetchInterval: 30_000 });

  const channelName = useMemo(() => channelNamer(channels.data ?? []), [channels.data]);
  const projectTitles = useMemo(() => new Map((projects.data ?? []).map((project) => [project.id, project.title || "Untitled video"])), [projects.data]);

  // Names for the "Who" column: the team list for admins, else at least the signed-in person.
  const displayNames = useMemo(() => {
    const names = new Map<string, string>();
    for (const user of users.data ?? []) names.set(user.username, user.display_name);
    if (me) names.set(me.username, me.display_name);
    return names;
  }, [users.data, me]);
  const personName = useCallback((username: string) => displayNames.get(username) ?? username, [displayNames]);

  // Everyone seen so far stays in the "Who" list, even while it filters to one person.
  const [seenUsers, setSeenUsers] = useState<string[]>([]);
  useEffect(() => {
    const found = new Set(seenUsers);
    let changed = false;
    for (const row of audit.data ?? []) {
      if (row.user && !found.has(row.user)) {
        found.add(row.user);
        changed = true;
      }
    }
    for (const user of users.data ?? []) {
      if (!found.has(user.username)) {
        found.add(user.username);
        changed = true;
      }
    }
    if (changed) setSeenUsers([...found]);
  }, [audit.data, users.data, seenUsers]);
  const whoOptions = useMemo(() => [...seenUsers].sort((a, b) => personName(a).localeCompare(personName(b))), [seenUsers, personName]);

  const projectOptions = useMemo(() => [...(projects.data ?? [])].sort((a, b) => b.updated_at.localeCompare(a.updated_at)), [projects.data]);

  const all = useMemo(() => audit.data ?? [], [audit.data]);
  const rows = useMemo(
    () =>
      all.filter((row) =>
        matchesText(
          search,
          describeAuditRow(row, channelName),
          personName(row.user),
          row.user,
          row.machine,
          row.project_id ? projectTitles.get(row.project_id) : null,
          auditTitle(row),
          row.channel_slug ? channelName(row.channel_slug) : null,
          ...auditDetailItems(row).map((item) => item.value),
        ),
      ),
    [all, search, channelName, projectTitles, personName],
  );

  const filtered = Boolean(who || action || projectId);
  const atLimit = all.length >= limit;

  const whereCell = (row: AuditRow) => {
    const title = row.project_id ? (projectTitles.get(row.project_id) ?? auditTitle(row)) : auditTitle(row);
    return (
      <div className="flex min-w-0 flex-col gap-0.5">
        {row.project_id ? (
          projectTitles.has(row.project_id) ? (
            <Link to={projectPath(row.project_id)} className="line-clamp-2 text-[13px] font-medium text-ink hover:text-accent-text hover:underline">
              {title}
            </Link>
          ) : (
            <span className="line-clamp-2 text-[13px] text-ink-muted" title="This video is archived or not on this laptop">
              {title || "A video no longer listed"}
            </span>
          )
        ) : title ? (
          <span className="line-clamp-2 text-[13px] text-ink-muted">{title}</span>
        ) : null}
        {row.channel_slug ? <ChannelLink slug={row.channel_slug} name={channelName(row.channel_slug)} className="text-xs font-normal text-ink-muted" /> : null}
        {!row.project_id && !row.channel_slug && !title ? <span className="text-xs text-ink-faint">-</span> : null}
      </div>
    );
  };

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Activity"
        description="Who approved, sent back, skipped, exported or changed what, on which laptop, and when. Newest first."
        actions={
          <>
            {isAdmin ? (
              <LinkButton to={USERS_PATH} variant="secondary">
                Users & roles
              </LinkButton>
            ) : null}
            <Button
              variant="secondary"
              icon={<RefreshCw />}
              loading={refreshing}
              onClick={() => {
                setRefreshing(true);
                void audit.refetch().finally(() => setRefreshing(false));
              }}
            >
              Refresh
            </Button>
          </>
        }
      />

      {!isAdmin ? (
        <Notice tone="info" title="You see your own activity">
          Admins see everyone's.
        </Notice>
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        <SearchBox value={search} onChange={setSearch} placeholder="Search what, who, video or note" className="w-72" />
        {isAdmin ? (
          <Select wrapperClassName="w-48" value={who} onChange={(event) => setWho(event.target.value)} aria-label="Who">
            <option value="">Everyone</option>
            {whoOptions.map((username) => (
              <option key={username} value={username}>
                {personName(username)}
              </option>
            ))}
          </Select>
        ) : null}
        <Select wrapperClassName="w-52" value={action} onChange={(event) => setAction(event.target.value)} aria-label="What">
          <option value="">Everything</option>
          {AUDIT_ACTION_GROUPS.map((group) => (
            <optgroup key={group.label} label={group.label}>
              {group.actions.map((id) => (
                <option key={id} value={id}>
                  {AUDIT_ACTION_LABELS[id]}
                </option>
              ))}
            </optgroup>
          ))}
        </Select>
        <Select wrapperClassName="w-60" value={projectId} onChange={(event) => setProjectId(event.target.value)} aria-label="Video">
          <option value="">All videos</option>
          {projectOptions.map((project) => (
            <option key={project.id} value={project.id}>
              {project.title || "Untitled video"}
            </option>
          ))}
        </Select>
        <Select wrapperClassName="w-40" value={String(limit)} onChange={(event) => setLimit(Number(event.target.value))} aria-label="How many">
          {AUDIT_LIMIT_CHOICES.map((choice) => (
            <option key={choice} value={choice}>
              Last {choice}
            </option>
          ))}
        </Select>
      </div>

      <Card flush>
        {audit.isPending ? (
          <LoadingBlock label="Loading the activity..." />
        ) : audit.isError ? (
          <ErrorState error={audit.error} title="Could not load the activity" onRetry={() => void audit.refetch()} />
        ) : all.length === 0 ? (
          <EmptyState
            icon={History}
            title={filtered ? "Nothing recorded for this choice" : "Nothing recorded yet"}
            description={filtered ? "Pick someone else, another kind of change or another video." : "Sign-ins, approvals, exports and changes to channels, settings and accounts show up here."}
          />
        ) : rows.length === 0 ? (
          <EmptyState icon={Search} title="Nothing matches" description="Clear the search to see every row." />
        ) : (
          <>
            <Table>
              <THead>
                <TR>
                  <TH className="w-[150px]">When</TH>
                  <TH className="w-[170px]">Who</TH>
                  <TH className="min-w-[230px]">What</TH>
                  <TH className="w-[230px]">Video or channel</TH>
                  <TH className="min-w-[200px]">Details</TH>
                </TR>
              </THead>
              <TBody>
                {rows.map((row, index) => {
                  const Icon = auditIcon(row.action);
                  const tone = AUDIT_AREA_TONES[auditArea(row.action)];
                  const details = auditDetailItems(row);
                  return (
                    <TR key={`${row.id}:${index}`}>
                      <TD className="align-top" title={formatDateTime(row.at)}>
                        <div className="text-ink">{formatRelative(row.at) || "-"}</div>
                        <div className="text-[11px] text-ink-faint">{formatDateTime(row.at)}</div>
                      </TD>
                      <TD className="align-top">
                        <div className="font-medium text-ink">{row.user ? personName(row.user) : <span className="font-normal text-ink-faint">Unknown</span>}</div>
                        {row.machine ? <div className="text-[11px] text-ink-faint">on {row.machine}</div> : null}
                      </TD>
                      <TD className="align-top">
                        <div className="flex items-start gap-2.5">
                          <span className={cn("mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-md", TONE_CLASSES[tone])} title={auditActionLabel(row.action)}>
                            <Icon className="size-3.5" aria-hidden />
                          </span>
                          <span className="text-[13px] text-ink">{describeAuditRow(row, channelName)}</span>
                        </div>
                      </TD>
                      <TD className="align-top">{whereCell(row)}</TD>
                      <TD className="align-top">
                        {details.length === 0 ? (
                          <span className="text-xs text-ink-faint">-</span>
                        ) : (
                          <dl className="flex flex-col gap-0.5 text-xs">
                            {details.map((item) => (
                              <div key={item.key} className="flex gap-1.5">
                                <dt className="shrink-0 text-ink-faint">{item.label}:</dt>
                                <dd className={cn("min-w-0 break-words text-ink-muted", (item.key === "notes" || item.key === "note") && "italic")}>{item.value}</dd>
                              </div>
                            ))}
                          </dl>
                        )}
                      </TD>
                    </TR>
                  );
                })}
              </TBody>
            </Table>
            <CountLine shown={rows.length} total={all.length} noun="entry" nounPlural="entries" />
            {atLimit ? (
              <p className="border-t border-line px-5 py-2.5 text-xs text-ink-faint">
                Showing the newest {limit}. Pick a larger number above, or narrow the list, to see older entries.
              </p>
            ) : null}
          </>
        )}
      </Card>
    </div>
  );
}

/** /settings/activity - the audit log (admins see everyone's rows, editors their own). */
export function ActivityPage() {
  return (
    <RequireRole action="view_activity">
      <ActivityPageBody />
    </RequireRole>
  );
}
