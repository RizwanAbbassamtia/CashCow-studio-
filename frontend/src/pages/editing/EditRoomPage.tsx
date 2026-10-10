import { Clapperboard, Film, ListChecks, MousePointerClick, PanelLeftOpen } from "lucide-react";
import { useMemo, useState } from "react";
import { useParams } from "react-router";

import { useChannels } from "../../api/channels";
import { useProjects } from "../../api/projects";
import { useProjectEvents } from "../../api/ws";
import { compareEditing, isInEditRoom } from "../../components/editing/editingProjects";
import { EditProjectPicker, EditProjectRail } from "../../components/editing/EditProjectPicker";
import { EditRoomProject } from "../../components/editing/EditRoomProject";
import { channelNamer } from "../../components/master/ChannelLink";
import { PROJECTS_PATH, RENDERS_PATH, REVIEW_PATH } from "../../components/master/routes";
import { LiveIndicator } from "../../components/projects/LiveIndicator";
import { Button, LinkButton } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { PageHeader } from "../../components/ui/PageHeader";
import { LoadingBlock } from "../../components/ui/Spinner";
import { EmptyState } from "../../components/ui/States";
import { cn } from "../../lib/cn";

const LIST_HIDDEN_KEY = "ccs-edit-room-list-hidden";

function readListHidden(): boolean {
  try {
    return localStorage.getItem(LIST_HIDDEN_KEY) === "1";
  } catch {
    return false;
  }
}

function storeListHidden(hidden: boolean) {
  try {
    if (hidden) localStorage.setItem(LIST_HIDDEN_KEY, "1");
    else localStorage.removeItem(LIST_HIDDEN_KEY);
  } catch {
    /* storage unavailable: the choice holds for this visit */
  }
}

const STEPS = [
  { title: "Check the preview", detail: "Play the low-resolution preview and click the scene strip to jump to a scene." },
  { title: "Choose and render", detail: "Pick the music, captions, popups and sizes (720p, 1080p, 4K), then render them." },
  { title: "Approve and export", detail: "Approve the edit, pick the thumbnail, check the title and description, and export." },
];

function NoVideoChosen({ count, loading, listHidden, onShowList }: { count: number; loading: boolean; listHidden: boolean; onShowList: () => void }) {
  if (loading) {
    return (
      <Card>
        <LoadingBlock label="Loading the edit room..." />
      </Card>
    );
  }
  if (count === 0) {
    return (
      <Card>
        <EmptyState
          icon={Clapperboard}
          title="Nothing to edit yet"
          description="Videos come here when their pictures are approved and the edit stage starts. Keep the pipeline moving from the Review queue."
          action={
            <div className="flex flex-wrap justify-center gap-2">
              <LinkButton to={REVIEW_PATH} variant="primary" size="sm" icon={<ListChecks />}>
                Open the Review queue
              </LinkButton>
              <LinkButton to={PROJECTS_PATH} variant="secondary" size="sm">
                See all projects
              </LinkButton>
            </div>
          }
        />
      </Card>
    );
  }
  return (
    <Card>
      <EmptyState
        icon={MousePointerClick}
        title={listHidden ? "Pick a video from the list" : "Pick a video on the left"}
        description={`${count} video${count === 1 ? " is" : "s are"} at the edit or export stage. Open one to work on it here.`}
        action={
          listHidden ? (
            <Button variant="secondary" size="sm" icon={<PanelLeftOpen />} onClick={onShowList}>
              Show the list
            </Button>
          ) : null
        }
      />
      <ol className="mx-auto grid max-w-3xl gap-3 pb-4 sm:grid-cols-3">
        {STEPS.map((step, index) => (
          <li key={step.title} className="rounded-md border border-line bg-surface-2/40 px-4 py-3">
            <p className="flex items-center gap-2 text-[13px] font-semibold text-ink">
              <span className="flex size-5 items-center justify-center rounded-full bg-accent text-[11px] font-bold text-accent-ink">{index + 1}</span>
              {step.title}
            </p>
            <p className="mt-1 text-xs text-ink-muted">{step.detail}</p>
          </li>
        ))}
      </ol>
    </Card>
  );
}

/**
 * /editing and /editing/:projectId - the Edit room: the videos at the edit or export stage
 * on the left, the Edit and Export panels of the chosen one on the right.
 */
export function EditRoomPage() {
  const params = useParams();
  const projectId = params.projectId ?? params.id;
  const projects = useProjects();
  const channels = useChannels();
  const events = useProjectEvents();
  const [listHidden, setListHidden] = useState(readListHidden);

  const channelName = useMemo(() => channelNamer(channels.data ?? []), [channels.data]);
  const roomProjects = useMemo(() => (projects.data ?? []).filter(isInEditRoom).sort(compareEditing), [projects.data]);

  const hideList = (hidden: boolean) => {
    setListHidden(hidden);
    storeListHidden(hidden);
  };

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Edit room"
        description="Videos at the edit and export stages. Check the preview, choose music and sizes, render, then export."
        actions={
          <>
            <LiveIndicator state={events.state} />
            <LinkButton to={RENDERS_PATH} variant="secondary" icon={<Film />}>
              All renders
            </LinkButton>
          </>
        }
      />

      <div className={cn("grid items-start gap-6", listHidden ? "grid-cols-[52px_minmax(0,1fr)]" : "lg:grid-cols-[264px_minmax(0,1fr)] 2xl:grid-cols-[300px_minmax(0,1fr)]")}>
        <div className="lg:sticky lg:top-6 lg:self-start">
          {listHidden ? (
            <EditProjectRail count={roomProjects.length} onShow={() => hideList(false)} />
          ) : (
            <EditProjectPicker
              projects={roomProjects}
              channelName={channelName}
              selectedId={projectId}
              loading={projects.isPending}
              error={projects.isError ? projects.error : null}
              onRetry={() => void projects.refetch()}
              onHide={() => hideList(true)}
            />
          )}
        </div>
        <div className="min-w-0">
          {projectId ? (
            <EditRoomProject key={projectId} projectId={projectId} />
          ) : (
            <NoVideoChosen count={roomProjects.length} loading={projects.isPending} listHidden={listHidden} onShowList={() => hideList(false)} />
          )}
        </div>
      </div>
    </div>
  );
}
