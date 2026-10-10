import { ArrowUpRight, CalendarClock, Clapperboard, Film, FolderOpen, HardDrive, Play, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router";

import { useChannels } from "../../api/channels";
import { errorMessage } from "../../api/client";
import { useOpenProjectFolder } from "../../api/projects";
import { useProjectEvents } from "../../api/ws";
import { StatCard } from "../../components/dashboard/StatCard";
import { ProjectThumb } from "../../components/editing/ProjectThumb";
import { RenderPlayerDialog } from "../../components/editing/RenderPlayerDialog";
import { thumbnailSources } from "../../components/editing/editingProjects";
import { useRenders, type RenderRow } from "../../components/editing/useRenders";
import { channelNamer } from "../../components/master/ChannelLink";
import { CountLine } from "../../components/master/CountLine";
import { bytesText, durationText, isWithinDays, matchesText, sumKnown } from "../../components/master/format";
import { editRoomPath } from "../../components/master/routes";
import { SearchBox } from "../../components/master/SearchBox";
import { LiveIndicator } from "../../components/projects/LiveIndicator";
import { STAGE_LABELS } from "../../components/projects/stageMeta";
import { StageStatusBadge } from "../../components/projects/StageStatusBadge";
import { Badge } from "../../components/ui/Badge";
import { Button, LinkButton } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Select } from "../../components/ui/Input";
import { PageHeader } from "../../components/ui/PageHeader";
import { LoadingBlock } from "../../components/ui/Spinner";
import { EmptyState, ErrorState, Notice } from "../../components/ui/States";
import { Table, TBody, TD, TH, THead, TR } from "../../components/ui/Table";
import { useToast } from "../../components/ui/Toast";
import { formatDateTime, formatRelative, plural } from "../../lib/format";
import { isProjectFinished } from "../../types/project";
import { RENDER_PRESETS, renderPresetLabel } from "../../types/timeline";

function presetOrder(preset: string): number {
  const index = (RENDER_PRESETS as readonly string[]).indexOf(preset);
  return index === -1 ? RENDER_PRESETS.length : index;
}

function StageCell({ row }: { row: RenderRow }) {
  const project = row.project;
  if (isProjectFinished(project)) return <Badge tone="ok">Exported</Badge>;
  return (
    <div className="flex flex-col items-start gap-1">
      <span className="text-xs text-ink-muted">{STAGE_LABELS[project.current_stage]} stage</span>
      <StageStatusBadge status={project.status} />
    </div>
  );
}

/** /editing/renders - every finished render of every video, with size, length and folder. */
export function RendersPage() {
  const { toast } = useToast();
  const channels = useChannels();
  const events = useProjectEvents();
  const { rows, projects, pending, failed, videoCount } = useRenders();
  const openFolder = useOpenProjectFolder();

  const [search, setSearch] = useState("");
  const [channel, setChannel] = useState("");
  const [preset, setPreset] = useState("");
  const [playing, setPlaying] = useState<RenderRow | null>(null);

  const channelName = useMemo(() => channelNamer(channels.data ?? []), [channels.data]);
  const presets = useMemo(() => [...new Set(rows.map((row) => row.preset))].sort((a, b) => presetOrder(a) - presetOrder(b)), [rows]);
  const channelOptions = useMemo(() => [...new Set(rows.map((row) => row.project.channel_slug))].sort((a, b) => channelName(a).localeCompare(channelName(b))), [rows, channelName]);

  const filtered = useMemo(
    () =>
      rows.filter(
        (row) =>
          (!channel || row.project.channel_slug === channel) &&
          (!preset || row.preset === preset) &&
          matchesText(search, row.project.title, channelName(row.project.channel_slug), renderPresetLabel(row.preset)),
      ),
    [rows, channel, preset, search, channelName],
  );

  const totalBytes = sumKnown(rows.map((row) => row.size_bytes));
  const thisWeek = rows.filter((row) => isWithinDays(row.rendered_at, 7)).length;
  const videosWithRenders = new Set(rows.map((row) => row.project.id)).size;
  const reading = projects.isPending || (pending > 0 && rows.length === 0);

  const onOpenFolder = (row: RenderRow) =>
    openFolder.mutate(row.project.id, {
      onError: (error) => toast({ tone: "error", title: "Could not open the folder", description: errorMessage(error) }),
    });

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Renders"
        description="Every finished video file across your projects: its size, length and where it is. The low-resolution previews are not listed."
        actions={<LiveIndicator state={events.state} />}
      />

      <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        <StatCard label="Renders" icon={Film} value={reading ? "..." : rows.length} hint={reading ? "Reading the projects" : `${plural(videosWithRenders, "video")}`} />
        <StatCard label="This week" icon={CalendarClock} value={reading ? "..." : thisWeek} tone={thisWeek > 0 ? "default" : "muted"} hint="Rendered in the last 7 days" />
        <StatCard label="Disk space" icon={HardDrive} value={reading ? "..." : bytesText(totalBytes)} hint="All final renders together" />
        <StatCard label="In the edit room" icon={Clapperboard} to={editRoomPath()} value={projects.isPending ? "..." : videoCount} hint="Videos at the edit or export stage" />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <SearchBox value={search} onChange={setSearch} placeholder="Search videos" className="w-64" />
        <Select wrapperClassName="w-52" value={channel} onChange={(event) => setChannel(event.target.value)} aria-label="Filter by channel">
          <option value="">All channels</option>
          {channelOptions.map((slug) => (
            <option key={slug} value={slug}>
              {channelName(slug)}
            </option>
          ))}
        </Select>
        <Select wrapperClassName="w-40" value={preset} onChange={(event) => setPreset(event.target.value)} aria-label="Filter by size">
          <option value="">All sizes</option>
          {presets.map((id) => (
            <option key={id} value={id}>
              {renderPresetLabel(id)}
            </option>
          ))}
        </Select>
      </div>

      {failed > 0 ? (
        <Notice tone="warn" title={`The render list of ${plural(failed, "video")} could not be read`}>
          Those videos are left out below. Open them in the Edit room to see what happened.
        </Notice>
      ) : null}

      <Card flush>
        {projects.isError ? (
          <ErrorState error={projects.error} title="Could not load the projects" onRetry={() => void projects.refetch()} />
        ) : reading ? (
          <LoadingBlock label="Reading the render lists..." />
        ) : rows.length === 0 ? (
          <EmptyState
            icon={Film}
            title="No final renders yet"
            description="Renders appear here once a size (720p, 1080p or 4K) is rendered at the edit stage. Do that in the Edit room."
            action={
              <LinkButton to={editRoomPath()} variant="primary" size="sm" icon={<Clapperboard />}>
                Open the Edit room
              </LinkButton>
            }
          />
        ) : filtered.length === 0 ? (
          <EmptyState icon={Search} title="No renders match" description="Clear the search or pick another channel or size." />
        ) : (
          <>
            <Table>
              <THead>
                <TR>
                  <TH className="min-w-[280px]">Video</TH>
                  <TH className="w-[150px]">Size</TH>
                  <TH className="w-[90px] whitespace-nowrap text-right">Length</TH>
                  <TH className="w-[100px] whitespace-nowrap text-right">File size</TH>
                  <TH className="w-[130px]">Rendered</TH>
                  <TH className="w-[170px]">Where it is</TH>
                  <TH className="w-[260px] text-right">Actions</TH>
                </TR>
              </THead>
              <TBody>
                {filtered.map((row) => (
                  <TR key={row.key}>
                    <TD>
                      <div className="flex items-center gap-3">
                        <ProjectThumb key={row.project.id} projectId={row.project.id} sources={thumbnailSources(row.project)} className="w-20" />
                        <div className="min-w-0">
                          <Link to={editRoomPath(row.project.id)} className="line-clamp-2 font-medium text-ink hover:text-accent-text hover:underline">
                            {row.project.title || "Untitled video"}
                          </Link>
                          <div className="text-xs text-ink-faint">
                            {channelName(row.project.channel_slug)} - {row.project.language}
                          </div>
                        </div>
                      </div>
                    </TD>
                    <TD>
                      <div className="flex flex-col items-start gap-0.5">
                        <Badge tone={row.preset === "2160p" ? "accent" : "neutral"}>{renderPresetLabel(row.preset)}</Badge>
                        {row.width && row.height ? (
                          <span className="text-[11px] tabular-nums text-ink-faint">
                            {row.width} x {row.height}
                          </span>
                        ) : null}
                      </div>
                    </TD>
                    <TD className="whitespace-nowrap text-right tabular-nums text-ink-muted">{durationText(row.duration_s)}</TD>
                    <TD className="whitespace-nowrap text-right tabular-nums text-ink-muted">{bytesText(row.size_bytes)}</TD>
                    <TD className="text-ink-muted" title={formatDateTime(row.rendered_at)}>
                      {row.rendered_at ? formatRelative(row.rendered_at) : "-"}
                    </TD>
                    <TD>
                      <StageCell row={row} />
                    </TD>
                    <TD className="text-right">
                      <div className="inline-flex flex-wrap items-center justify-end gap-1">
                        <Button variant="ghost" size="sm" icon={<Play />} onClick={() => setPlaying(row)} disabled={!row.url}>
                          Watch
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          icon={<FolderOpen />}
                          loading={openFolder.isPending && openFolder.variables === row.project.id}
                          onClick={() => onOpenFolder(row)}
                          title={`Opens the project folder; the file is in 07_edit${row.path ? ` (${row.path})` : ""}`}
                        >
                          Open folder
                        </Button>
                        <LinkButton to={editRoomPath(row.project.id)} variant="ghost" size="sm" icon={<ArrowUpRight />} aria-label={`Open ${row.project.title || "this video"} in the Edit room`}>
                          Edit room
                        </LinkButton>
                      </div>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
            <CountLine shown={filtered.length} total={rows.length} noun="render" />
          </>
        )}
      </Card>

      <RenderPlayerDialog render={playing} onClose={() => setPlaying(null)} />
    </div>
  );
}
