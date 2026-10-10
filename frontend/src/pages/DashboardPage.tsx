import { useQueries, useQuery } from "@tanstack/react-query";
import {
  Activity,
  ArrowRight,
  ArrowUpRight,
  Clapperboard,
  CloudOff,
  FolderOpen,
  ListChecks,
  MonitorPlay,
  PackageCheck,
  Plus,
  Search,
  type LucideIcon,
} from "lucide-react";
import { useMemo, type ReactNode } from "react";
import { Link } from "react-router";

import { useAuthStatus, useCurrentUser } from "../api/auth";
import { useChannels } from "../api/channels";
import { api } from "../api/client";
import { useDoctor } from "../api/doctor";
import { queryKeys } from "../api/keys";
import { getProject, useProjects } from "../api/projects";
import { getSettings } from "../api/settings";
import { useSystemInfo } from "../api/system";
import { useProjectEvents } from "../api/ws";
import { GatedLinkButton } from "../components/auth/Gated";
import { RecentActivity } from "../components/auth/RecentActivity";
import { GetStartedChecklist, type ChecklistStep } from "../components/dashboard/GetStartedChecklist";
import { STAGE_LABELS } from "../components/projects/stageMeta";
import { StageStatusBadge } from "../components/projects/StageStatusBadge";
import { ChannelStatusBadge } from "../components/ui/Badge";
import { LinkButton } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Notice } from "../components/ui/States";
import { summarizeDoctor } from "../layout/DoctorPill";
import { cn } from "../lib/cn";
import { formatRelative, plural } from "../lib/format";
import { can, DENIED_REASONS, roleLabel } from "../lib/roles";
import type { SystemInfo } from "../types";
import { isProjectFinished, WAITING_STATUSES, type Project } from "../types/project";

/** Monday 00:00 of this week, on this laptop's clock. */
function startOfWeek(now = new Date()): Date {
  const day = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  day.setDate(day.getDate() - ((day.getDay() + 6) % 7));
  return day;
}

function isoDate(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function since(iso: string | null | undefined, start: Date): boolean {
  if (!iso) return false;
  const time = new Date(iso).getTime();
  return !Number.isNaN(time) && time >= start.getTime();
}

/** The edit stage (the render) finished this week and its result was kept. */
function renderedSince(project: Project, start: Date): boolean {
  const edit = project.stages.edit;
  if (!edit || !["awaiting_review", "approved", "done"].includes(edit.status)) return false;
  return since(edit.finished_at ?? edit.approved_at, start);
}

function greeting(now = new Date()): string {
  const hour = now.getHours();
  if (hour < 5) return "Working late";
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

/** One row of GET /api/production-log; only the fields the dashboard counts. */
interface ProductionLogRow {
  at: string;
  project_id?: string | null;
}

type Tone = "default" | "ok" | "warn" | "fail" | "muted";
const toneClasses: Record<Tone, string> = {
  default: "text-ink",
  ok: "text-ok",
  warn: "text-warn",
  fail: "text-fail",
  muted: "text-ink-faint",
};

/** A count for one area of the app, linking to the page that lists it. */
function AreaCard({ area, label, value, hint, icon: Icon, to, tone = "default" }: { area: string; label: string; value: ReactNode; hint: ReactNode; icon: LucideIcon; to: string; tone?: Tone }) {
  return (
    <Link
      to={to}
      className="group relative block rounded-card border border-line bg-surface p-5 shadow-card transition-colors hover:border-line-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-faint">{area}</div>
          <div className="mt-0.5 text-[13px] font-medium text-ink-muted">{label}</div>
        </div>
        <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-surface-2 text-ink-muted">
          <Icon className="size-4" aria-hidden />
        </span>
      </div>
      <div className={cn("mt-3 text-2xl font-semibold tracking-tight tabular-nums", toneClasses[tone])}>{value}</div>
      <div className="mt-1 text-xs text-ink-faint">{hint}</div>
      <ArrowUpRight className="absolute bottom-4 right-4 size-4 text-ink-faint opacity-0 transition-opacity group-hover:opacity-100" aria-hidden />
    </Link>
  );
}

export function DashboardPage() {
  const user = useCurrentUser();
  const status = useAuthStatus();
  const channels = useChannels();
  const projects = useProjects();
  const doctor = useDoctor();
  const system = useSystemInfo();
  useProjectEvents();

  const isAdmin = can(user, "settings");
  const mayRun = can(user, "run");
  const mayEditChannels = can(user, "edit_channels");

  // Keys are an admin matter; other accounts read the doctor's key check instead.
  const settings = useQuery({ queryKey: queryKeys.settings, queryFn: getSettings, enabled: isAdmin });

  const weekStart = useMemo(() => startOfWeek(), []);
  const productionLog = useQuery({
    queryKey: ["dashboard", "production-log", isoDate(weekStart)],
    // A day early: the server may count days in UTC; the exact cut is made here.
    queryFn: () => {
      const dayBefore = new Date(weekStart);
      dayBefore.setDate(dayBefore.getDate() - 1);
      return api.get<ProductionLogRow[]>(`/api/production-log?since=${isoDate(dayBefore)}`);
    },
    staleTime: 30_000,
    retry: 0,
  });

  const channelList = channels.data ?? [];
  const projectList = projects.data ?? [];
  const health = summarizeDoctor(doctor.data, doctor.error);

  const inProgress = projectList.filter((p) => !isProjectFinished(p));
  const running = inProgress.filter((p) => p.status === "running").length;
  const awaitingReview = projectList.filter((p) => p.status === "awaiting_review");
  const waiting = projectList
    .filter((p) => WAITING_STATUSES.includes(p.status))
    .sort((a, b) => a.updated_at.localeCompare(b.updated_at))
    .slice(0, 5);

  // Renders this week: only projects touched this week at or past the edit stage can have one.
  const renderCandidates = projectList.filter((p) => since(p.updated_at, weekStart) && (p.current_stage === "edit" || p.current_stage === "export"));
  const renderDetails = useQueries({
    queries: renderCandidates.map((p) => ({
      queryKey: queryKeys.project(p.id),
      queryFn: () => getProject(p.id),
      staleTime: 30_000,
    })),
  });
  const rendersPending = projects.isPending || renderDetails.some((q) => q.isPending);
  const rendersThisWeek = renderDetails.filter((q) => q.data && renderedSince(q.data, weekStart)).length;
  const exportsThisWeek = (productionLog.data ?? []).filter((row) => since(row.at, weekStart)).length;

  // Get started: keys from the settings (admins) or the doctor's Anthropic key check (everyone else).
  const keysSet = settings.data ? Object.values(settings.data.keys).filter((k) => k.set).length : 0;
  const anthropicSet = settings.data
    ? Boolean(settings.data.keys.ANTHROPIC_API_KEY?.set)
    : doctor.data?.checks.some((check) => check.id === "anthropic_key" && check.status === "ok") ?? false;
  const settingsDone = isAdmin ? settings.isSuccess && keysSet > 0 : anthropicSet;
  const channelsDone = channels.isSuccess && channelList.length > 0;
  const researchDone = projects.isSuccess && projectList.length > 0;

  const steps: ChecklistStep[] = [
    {
      id: "settings",
      title: "Add your API keys and folders",
      detail: isAdmin
        ? settingsDone
          ? `${plural(keysSet, "key")} set${anthropicSet ? "" : ", but no Anthropic key yet"}${system.data?.shared_dir_is_default ? ". The shared folder still uses the default on this computer." : "."}`
          : "Set at least the Anthropic key, then point the shared folder at your synced Google Drive folder."
        : settingsDone
          ? "The keys are in place on this laptop."
          : "An admin adds the API keys and folders in Settings.",
      state: settingsDone ? "done" : "todo",
      action: (
        <LinkButton to="/settings" variant={settingsDone || !isAdmin ? "secondary" : "primary"} size="sm" icon={<ArrowRight />}>
          Open Settings
        </LinkButton>
      ),
    },
    {
      id: "channel",
      title: "Create your first channel",
      detail: channelsDone
        ? `${plural(channelList.length, "channel")} set up. Open one in the Master Sheet to see its competitors, voice or images.`
        : "Name, competitors, frameworks, voice, images, thumbnail rules and stage modes.",
      state: channelsDone ? "done" : "todo",
      action: (
        <GatedLinkButton
          allowed={mayEditChannels}
          reason={DENIED_REASONS.edit_channels}
          to="/master/channels/new"
          variant={channelsDone || !settingsDone ? "secondary" : "primary"}
          size="sm"
          icon={<Plus />}
        >
          New channel
        </GatedLinkButton>
      ),
    },
    {
      id: "research",
      title: "Run research on a channel",
      detail: researchDone
        ? `${plural(projectList.length, "video")} started so far. Scan again whenever you want fresh ideas.`
        : "Scan the competitors for outlier videos and start production from the AI pick.",
      state: researchDone ? "done" : "todo",
      action: (
        <LinkButton to="/automation/research" variant={researchDone || !channelsDone ? "secondary" : "primary"} size="sm" icon={<Search />}>
          Open New video
        </LinkButton>
      ),
    },
  ];

  const recent = [...channelList].sort((a, b) => (b.updated_at ?? "").localeCompare(a.updated_at ?? "")).slice(0, 5);
  const channelName = (slug: string) => channelList.find((c) => c.slug === slug)?.name ?? slug;
  const firstName = (user?.display_name?.trim() || user?.username || "").split(/\s+/)[0] ?? "";
  const machine = status.data?.machine || (system.data as (SystemInfo & { machine?: string }) | undefined)?.machine || "";

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <h2 className="text-xl font-semibold tracking-tight text-ink">
            {greeting()}
            {firstName ? `, ${firstName}` : ""}
          </h2>
          <p className="mt-1 text-sm text-ink-muted">
            {user ? `Signed in as ${roleLabel(user.role)}` : "Signed in"}
            {machine ? ` on ${machine}` : ""}.{" "}
            {awaitingReview.length > 0
              ? `${plural(awaitingReview.length, "video")} ${awaitingReview.length === 1 ? "waits" : "wait"} for a review.`
              : "Nothing waits for a review right now."}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <LinkButton to="/automation/review" variant="secondary" icon={<ListChecks />}>
            Review queue
          </LinkButton>
          <GatedLinkButton allowed={mayRun} reason={DENIED_REASONS.run} to="/automation/research" variant="primary" icon={<Plus />}>
            New video
          </GatedLinkButton>
        </div>
      </div>

      {status.data?.roster_source === "cache" ? (
        <Notice tone="warn" title="The team list comes from this laptop's saved copy">
          <span className="inline-flex items-start gap-2">
            <CloudOff className="mt-0.5 size-4 shrink-0 text-warn" aria-hidden />
            <span>
              {status.data.roster_notice?.trim() ||
                "The shared Google Drive folder could not be read, so sign-ins use the copy saved on this laptop."}{" "}
              Accounts added or changed on another laptop show up once the shared folder is back.
            </span>
          </span>
        </Notice>
      ) : null}

      {channels.isError ? (
        <Notice tone="fail" title="The server is not answering">
          Start the backend (ccs serve) and reload. The pages keep working once it is up.
        </Notice>
      ) : null}

      <div className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-5">
        <AreaCard
          area="Automation"
          label="Videos in progress"
          icon={Clapperboard}
          to="/automation/projects"
          value={projects.isPending ? "..." : projects.isError ? "-" : inProgress.length}
          tone={inProgress.length > 0 ? "default" : "muted"}
          hint={projects.isSuccess ? (inProgress.length > 0 ? `${running} running right now` : "Start one from New video") : "Open the project list"}
        />
        <AreaCard
          area="Automation"
          label="Awaiting review"
          icon={ListChecks}
          to="/automation/review"
          value={projects.isPending ? "..." : projects.isError ? "-" : awaitingReview.length}
          tone={awaitingReview.length > 0 ? "warn" : "muted"}
          hint={awaitingReview.length > 0 ? "Open the Review queue" : "Nothing waits for you"}
        />
        <AreaCard
          area="Editing"
          label="Renders this week"
          icon={MonitorPlay}
          to="/editing/renders"
          value={projects.isError ? "-" : rendersPending ? "..." : rendersThisWeek}
          tone={rendersThisWeek > 0 ? "default" : "muted"}
          hint="Videos rendered since Monday"
        />
        <AreaCard
          area="Master Sheet"
          label="Exports this week"
          icon={PackageCheck}
          to="/master/production-log"
          value={productionLog.isPending ? "..." : productionLog.isError ? "-" : exportsThisWeek}
          tone={exportsThisWeek > 0 ? "ok" : "muted"}
          hint={productionLog.isError ? "The production log is not available" : "Finished videos since Monday"}
        />
        <AreaCard
          area="Settings"
          label="Health checks"
          icon={Activity}
          to="/settings#doctor"
          value={health.label}
          tone={health.level === "pending" ? "muted" : health.level}
          hint={doctor.data ? `${doctor.data.checks.length} checks on this computer` : "Open the Doctor panel"}
        />
      </div>

      <div className="grid gap-6 xl:grid-cols-[3fr_2fr]">
        <div className="flex flex-col gap-6">
          <Card
            title="Waiting for you"
            description="Stages that stopped for a review, a file or after a problem. Oldest first."
            actions={
              <Link to="/automation/review" className="text-xs font-medium text-accent-text hover:underline">
                Open the queue
              </Link>
            }
            flush
          >
            {waiting.length === 0 ? (
              <p className="px-5 py-6 text-center text-[13px] text-ink-faint">{projects.isPending ? "Loading..." : "Nothing waits for you right now."}</p>
            ) : (
              <ul className="divide-y divide-line">
                {waiting.map((project) => (
                  <li key={project.id}>
                    <Link
                      to={`/automation/projects/${encodeURIComponent(project.id)}`}
                      className="flex items-center justify-between gap-3 px-5 py-3 transition-colors hover:bg-surface-2/60"
                    >
                      <div className="min-w-0">
                        <div className="truncate text-sm font-medium text-ink">{project.title || "Untitled video"}</div>
                        <div className="text-xs text-ink-faint">
                          {channelName(project.channel_slug)} - {STAGE_LABELS[project.current_stage]} - {formatRelative(project.updated_at)}
                        </div>
                      </div>
                      <StageStatusBadge status={project.status} />
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <GetStartedChecklist steps={steps} />

          <Card
            title="Recent channels"
            description={channels.isSuccess ? `${plural(channelList.length, "channel")} in the Master Sheet` : undefined}
            actions={
              <Link to="/master/channels" className="text-xs font-medium text-accent-text hover:underline">
                See all
              </Link>
            }
            flush
          >
            {recent.length === 0 ? (
              <p className="px-5 py-6 text-center text-[13px] text-ink-faint">{channels.isPending ? "Loading..." : "No channels yet."}</p>
            ) : (
              <ul className="divide-y divide-line">
                {recent.map((channel) => (
                  <li key={channel.slug}>
                    <Link
                      to={`/master/channels/${encodeURIComponent(channel.slug)}`}
                      className="flex items-center justify-between gap-3 px-5 py-3 transition-colors hover:bg-surface-2/60"
                    >
                      <div className="min-w-0">
                        <div className="truncate text-sm font-medium text-ink">{channel.name}</div>
                        <div className="text-xs text-ink-faint">
                          {channel.language} - {plural(channel.competitors, "competitor")}
                          {channel.updated_at ? ` - ${formatRelative(channel.updated_at)}` : ""}
                        </div>
                      </div>
                      <ChannelStatusBadge status={channel.status} />
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>

        <div className="flex flex-col gap-6">
          <RecentActivity limit={10} />

          <Card title="Your folders" description="From Settings. Shared data syncs through Google Drive; videos stay local.">
            {system.data ? (
              <dl className="flex flex-col gap-3 text-[13px]">
                {[
                  { label: "Shared folder", value: system.data.shared_dir, note: system.data.shared_dir_is_default ? "default, not shared yet" : "" },
                  { label: "Projects", value: system.data.projects_dir, note: "" },
                  { label: "Exports", value: system.data.exports_dir, note: "" },
                ].map((row) => (
                  <div key={row.label} className="flex items-start gap-3">
                    <FolderOpen className="mt-0.5 size-4 shrink-0 text-ink-faint" aria-hidden />
                    <div className="min-w-0">
                      <dt className="text-ink-muted">
                        {row.label}
                        {row.note ? <span className="ml-2 text-warn">{row.note}</span> : null}
                      </dt>
                      <dd className="break-all font-mono text-xs text-ink">{row.value}</dd>
                    </div>
                  </div>
                ))}
              </dl>
            ) : (
              <p className="text-[13px] text-ink-faint">{system.isError ? "Not available while the server is offline." : "Loading..."}</p>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
