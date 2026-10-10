import { ArrowUpRight, FolderOpen, SearchX } from "lucide-react";
import { useEffect, useRef } from "react";
import { useSearchParams } from "react-router";

import { useChannel } from "../../api/channels";
import { ApiError, errorMessage } from "../../api/client";
import { useOpenProjectFolder, useProject } from "../../api/projects";
import { formatDateTime, formatRelative } from "../../lib/format";
import type { StageName } from "../../types/channel";
import { isProjectFinished, projectStatus } from "../../types/project";
import { ChannelLink } from "../master/ChannelLink";
import { editRoomPath, projectPath } from "../master/routes";
import { FORMAT_SHORT_LABELS, STAGE_LABELS } from "../projects/stageMeta";
import { StageStatusBadge } from "../projects/StageStatusBadge";
import { ReviewPanel } from "../review/ReviewPanel";
import { Badge } from "../ui/Badge";
import { Button, LinkButton } from "../ui/Button";
import { Card } from "../ui/Card";
import { LoadingBlock } from "../ui/Spinner";
import { EmptyState, ErrorState, Notice } from "../ui/States";
import { useToast } from "../ui/Toast";
import { thumbnailSources } from "./editingProjects";
import { ProjectThumb } from "./ProjectThumb";
import { StageSwitch, type RoomStage } from "./StageSwitch";
import { TimelineEditorStrip } from "./TimelineEditorStrip";

const PANEL_ID = "edit-room-panel";

function asRoomStage(value: string | null): RoomStage | null {
  return value === "edit" || value === "export" ? value : null;
}

/**
 * The right side of the Edit room for one video: its header, the M5 strip, and the Edit or
 * Export review panel (the same panels as on the project page, with their status, checks,
 * notes and buttons). Give it `key={projectId}` so switching videos starts fresh.
 */
export function EditRoomProject({ projectId }: { projectId: string }) {
  const { toast } = useToast();
  const project = useProject(projectId);
  const channel = useChannel(project.data?.channel_slug);
  const openFolder = useOpenProjectFolder();
  const [searchParams, setSearchParams] = useSearchParams();

  const current = project.data?.current_stage;
  const defaultStage: RoomStage = current === "export" ? "export" : "edit";
  const requested = asRoomStage(searchParams.get("stage"));
  const stage = requested ?? defaultStage;
  const explicitChoice = useRef(false);
  explicitChoice.current = requested !== null;

  // Follow the pipeline: when the video moves on (the edit is approved and export starts),
  // drop an earlier explicit choice so the panel for the new stage shows, as on the project page.
  const lastCurrent = useRef<StageName | undefined>(undefined);
  useEffect(() => {
    if (current && lastCurrent.current && current !== lastCurrent.current && explicitChoice.current) {
      setSearchParams(
        (params) => {
          const next = new URLSearchParams(params);
          next.delete("stage");
          return next;
        },
        { replace: true },
      );
    }
    lastCurrent.current = current;
  }, [current, setSearchParams]);

  const choose = (next: RoomStage) => {
    setSearchParams(
      (params) => {
        const updated = new URLSearchParams(params);
        if (next === defaultStage) updated.delete("stage");
        else updated.set("stage", next);
        return updated;
      },
      { replace: true },
    );
  };

  if (project.isPending) {
    return (
      <Card>
        <LoadingBlock label="Loading the video..." />
      </Card>
    );
  }

  if (project.isError) {
    const notFound = project.error instanceof ApiError && project.error.status === 404;
    return (
      <Card>
        {notFound ? (
          <EmptyState
            icon={SearchX}
            title="This video is not here any more"
            description="It may have been archived. Archived folders live in _archived inside the projects folder."
            action={
              <LinkButton to={editRoomPath()} variant="secondary" size="sm">
                Back to the edit room
              </LinkButton>
            }
          />
        ) : (
          <ErrorState error={project.error} title="Could not load the video" onRetry={() => void project.refetch()} />
        )}
      </Card>
    );
  }

  const data = project.data;
  const status = projectStatus(data);
  const finished = isProjectFinished({ current_stage: data.current_stage, status });
  const inRoom = data.current_stage === "edit" || data.current_stage === "export";
  const title = data.title || data.source.topic_text || "Untitled video";

  const onOpenFolder = () =>
    openFolder.mutate(data.id, {
      onError: (error) => toast({ tone: "error", title: "Could not open the folder", description: errorMessage(error) }),
    });

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex min-w-0 flex-1 items-start gap-4">
            <ProjectThumb key={data.id} projectId={data.id} sources={thumbnailSources({ current_stage: data.current_stage, status })} className="hidden w-36 sm:block" />
            <div className="min-w-0">
              <h2 className="text-lg font-semibold leading-snug tracking-tight text-ink">{title}</h2>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <StageStatusBadge status={status} />
                <Badge tone="neutral">{STAGE_LABELS[data.current_stage]} stage</Badge>
                <Badge tone="neutral">{FORMAT_SHORT_LABELS[data.format]}</Badge>
                <Badge tone="neutral">{data.language}</Badge>
                {finished ? <Badge tone="ok">Exported</Badge> : null}
              </div>
              <p className="mt-2 text-xs text-ink-muted">
                <ChannelLink slug={data.channel_slug} name={channel.data?.channel.name} className="text-xs" />
                <span title={formatDateTime(data.updated_at)}> - updated {formatRelative(data.updated_at)}</span>
              </p>
            </div>
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            <Button variant="secondary" size="sm" icon={<FolderOpen />} loading={openFolder.isPending} onClick={onOpenFolder} title={data.folder}>
              Open folder
            </Button>
            <LinkButton to={projectPath(data.id)} variant="secondary" size="sm" icon={<ArrowUpRight />}>
              Project page
            </LinkButton>
          </div>
        </div>
      </Card>

      {inRoom ? (
        <>
          <TimelineEditorStrip />
          <StageSwitch value={stage} onChange={choose} project={data} panelId={PANEL_ID} />
          <div id={PANEL_ID} role="tabpanel" aria-labelledby={`${PANEL_ID}-${stage}`}>
            <ReviewPanel projectId={data.id} stage={stage} />
          </div>
        </>
      ) : (
        <Card>
          <Notice
            tone="info"
            title={`This video is at the ${STAGE_LABELS[data.current_stage]} stage`}
            action={
              <LinkButton to={projectPath(data.id)} variant="secondary" size="sm">
                Open the project page
              </LinkButton>
            }
          >
            It comes to the edit room once its pictures are approved and the edit stage starts. Until then, review it on the project page.
          </Notice>
        </Card>
      )}
    </div>
  );
}
