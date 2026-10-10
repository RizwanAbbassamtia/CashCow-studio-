import { useQueries } from "@tanstack/react-query";
import { CalendarClock, ChevronRight, Hand, Moon, Play, Search, Tv } from "lucide-react";
import { useMemo } from "react";
import { Link, useNavigate } from "react-router";

import { useCan } from "../../api/auth";
import { getChannel, useChannels } from "../../api/channels";
import { queryKeys } from "../../api/keys";
import { useProjects } from "../../api/projects";
import { Gated, GatedLinkButton } from "../../components/auth/Gated";
import { Badge, ChannelStatusBadge } from "../../components/ui/Badge";
import { Button, LinkButton } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { PageHeader } from "../../components/ui/PageHeader";
import { LoadingBlock } from "../../components/ui/Spinner";
import { EmptyState, ErrorState, Notice } from "../../components/ui/States";
import { Table, TBody, TD, TH, THead, TR } from "../../components/ui/Table";
import { FORMAT_LABELS } from "../../lib/labels";
import { DENIED_REASONS } from "../../lib/roles";
import { isProjectFinished } from "../../types/project";

const STEPS = [
  {
    icon: Tv,
    title: "Every active channel takes part",
    detail: "Channels set to Active join the batch. Channels that are paused or still being set up are left out.",
  },
  {
    icon: CalendarClock,
    title: "Each channel gets its weekly number",
    detail: "The plan follows each channel's \"videos per week\", set in the Master Sheet on the channel's Channel tab.",
  },
  {
    icon: Moon,
    title: "Production starts on its own",
    detail: "Research scans the competitors, the AI picks a video and production starts, a few videos at a time, so the laptop stays usable.",
  },
  {
    icon: Hand,
    title: "People still decide",
    detail: "Stages set to Review wait in the Review queue exactly as they do today. Nothing is uploaded or published.",
  },
] as const;

function perDay(perWeek: number): string {
  const value = perWeek / 7;
  if (value === 0) return "0";
  return value < 10 ? value.toFixed(1).replace(/\.0$/, "") : String(Math.round(value));
}

/**
 * /automation/batch: planned for M6. Lists the channels with their "videos per week" so the
 * team can check the plan now, and explains what a batch run will do.
 */
export function BatchPage() {
  const navigate = useNavigate();
  const channels = useChannels();
  const projects = useProjects();
  const mayRun = useCan("run");
  const mayEditChannels = useCan("edit_channels");

  const list = useMemo(() => [...(channels.data ?? [])].sort((a, b) => a.name.localeCompare(b.name)), [channels.data]);
  // "videos per week" lives in the full channel, not in the list row.
  const details = useQueries({
    queries: list.map((channel) => ({
      queryKey: queryKeys.channel(channel.slug),
      queryFn: () => getChannel(channel.slug),
      staleTime: 60_000,
    })),
  });

  const inProduction = useMemo(() => {
    const counts = new Map<string, number>();
    for (const project of projects.data ?? []) {
      if (isProjectFinished(project)) continue;
      counts.set(project.channel_slug, (counts.get(project.channel_slug) ?? 0) + 1);
    }
    return counts;
  }, [projects.data]);

  const rows = list.map((channel, index) => ({
    channel,
    perWeek: details[index]?.data?.channel.videos_per_week ?? null,
    loading: details[index]?.isPending ?? false,
  }));
  const active = rows.filter((row) => row.channel.status === "active");
  const weeklyTotal = active.reduce((sum, row) => sum + (row.perWeek ?? 0), 0);
  const stillLoading = rows.some((row) => row.loading);

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={
          <span className="inline-flex items-center gap-2">
            Batch runs
            <Badge tone="accent">Arrives in M6</Badge>
          </span>
        }
        description="Make a whole week of videos for every active channel in one go. Until batch runs arrive, start videos one at a time."
        actions={
          <>
            <Gated allowed={false} reason="Batch runs arrive in M6. Until then, start videos one at a time from New video.">
              <Button variant="secondary" icon={<Play />} disabled>
                Start a batch run
              </Button>
            </Gated>
            <GatedLinkButton allowed={mayRun} reason={DENIED_REASONS.run} to="/automation/research" variant="primary" icon={<Search />}>
              New video
            </GatedLinkButton>
          </>
        }
      />

      <div className="grid gap-6 xl:grid-cols-[3fr_2fr]">
        <Card
          title="Channels in the plan"
          description={
            channels.isSuccess
              ? stillLoading
                ? "Reading each channel's weekly number..."
                : `${weeklyTotal} video${weeklyTotal === 1 ? "" : "s"} a week across ${active.length} active channel${active.length === 1 ? "" : "s"}, about ${perDay(weeklyTotal)} a day.`
              : "Every channel with its weekly number."
          }
          flush
        >
          {channels.isPending ? (
            <LoadingBlock label="Loading channels..." />
          ) : channels.isError ? (
            <ErrorState error={channels.error} title="Could not load the channels" onRetry={() => void channels.refetch()} />
          ) : rows.length === 0 ? (
            <EmptyState
              icon={Tv}
              title="No channels yet"
              description="Batch runs work through your channels. Add one in the Master Sheet first."
              action={
                mayEditChannels ? (
                  <LinkButton to="/master/channels/new" variant="primary" size="sm">
                    New channel
                  </LinkButton>
                ) : null
              }
            />
          ) : (
            <Table>
              <THead>
                <TR>
                  <TH>Channel</TH>
                  <TH className="w-[112px]">Status</TH>
                  <TH className="w-[96px]">Formats</TH>
                  <TH className="w-[92px] text-right" title="Videos per week">Per week</TH>
                  <TH className="w-[112px] text-right">In production</TH>
                  <TH className="w-10" />
                </TR>
              </THead>
              <TBody>
                {rows.map(({ channel, perWeek, loading }) => {
                  const href = `/master/channels/${encodeURIComponent(channel.slug)}`;
                  const inPlan = channel.status === "active";
                  return (
                    <TR
                      key={channel.slug}
                      interactive
                      tabIndex={0}
                      onClick={() => navigate(href)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") navigate(href);
                      }}
                      className={inPlan ? undefined : "opacity-70"}
                    >
                      <TD>
                        <div className="font-medium text-ink">{channel.name}</div>
                        <div className="text-xs text-ink-faint">
                          {channel.language}
                          {inPlan ? "" : " - not in the batch"}
                        </div>
                      </TD>
                      <TD>
                        <ChannelStatusBadge status={channel.status} />
                      </TD>
                      <TD className="text-ink-muted">{FORMAT_LABELS[channel.formats]}</TD>
                      <TD className="text-right text-base font-semibold tabular-nums text-ink">{loading ? "..." : (perWeek ?? "-")}</TD>
                      <TD className="text-right tabular-nums text-ink-muted">{inProduction.get(channel.slug) ?? 0}</TD>
                      <TD className="text-right">
                        <ChevronRight className="inline size-4 text-ink-faint" aria-hidden />
                      </TD>
                    </TR>
                  );
                })}
              </TBody>
            </Table>
          )}
          {rows.length > 0 ? (
            <p className="border-t border-line px-5 py-2.5 text-xs text-ink-faint">
              {mayEditChannels
                ? "Change a channel's weekly number in its setup: open the row, Channel tab, Videos per week."
                : "An admin sets each channel's weekly number in the Master Sheet."}
            </p>
          ) : null}
        </Card>

        <div className="flex flex-col gap-6">
          <Card title="What a batch run will do" description="Planned for milestone M6.">
            <ol className="flex flex-col gap-4">
              {STEPS.map((step, index) => (
                <li key={step.title} className="flex items-start gap-3">
                  <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-surface-2 text-accent-text">
                    <step.icon className="size-4" aria-hidden />
                  </span>
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-ink">
                      {index + 1}. {step.title}
                    </p>
                    <p className="mt-0.5 text-[13px] text-ink-muted">{step.detail}</p>
                  </div>
                </li>
              ))}
            </ol>
          </Card>

          <Notice tone="info" title="Until then">
            Start each video from{" "}
            <Link to="/automation/research" className="font-medium text-accent-text hover:underline">
              New video
            </Link>
            , then follow it in{" "}
            <Link to="/automation/projects" className="font-medium text-accent-text hover:underline">
              Projects
            </Link>{" "}
            and the{" "}
            <Link to="/automation/review" className="font-medium text-accent-text hover:underline">
              Review queue
            </Link>
            .
          </Notice>
        </div>
      </div>
    </div>
  );
}
