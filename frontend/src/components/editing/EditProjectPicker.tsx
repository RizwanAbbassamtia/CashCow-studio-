import { Clapperboard, PanelLeftClose, PanelLeftOpen, Search } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router";

import { cn } from "../../lib/cn";
import { formatDateTime, formatRelative } from "../../lib/format";
import type { ProjectSummary } from "../../types/project";
import { FORMAT_SHORT_LABELS } from "../projects/stageMeta";
import { StageStatusBadge } from "../projects/StageStatusBadge";
import { matchesText } from "../master/format";
import { editRoomPath, PROJECTS_PATH } from "../master/routes";
import { SearchBox } from "../master/SearchBox";
import { Button, LinkButton } from "../ui/Button";
import { LoadingBlock } from "../ui/Spinner";
import { EmptyState, ErrorState } from "../ui/States";
import { EDITING_GROUP_HINTS, EDITING_GROUP_LABELS, EDITING_GROUPS, editingGroup, thumbnailSources, type EditingGroup } from "./editingProjects";
import { ProjectThumb } from "./ProjectThumb";

type GroupFilter = "all" | EditingGroup;

export interface EditProjectPickerProps {
  /** the videos at the edit or export stage (or exported), already sorted */
  projects: ProjectSummary[];
  channelName: (slug: string) => string;
  selectedId?: string;
  loading: boolean;
  error: unknown;
  onRetry: () => void;
  onHide: () => void;
}

/** The list on the left of the Edit room: search, Editing / Exporting / Done, one row per video. */
export function EditProjectPicker({ projects, channelName, selectedId, loading, error, onRetry, onHide }: EditProjectPickerProps) {
  const [search, setSearch] = useState("");
  const [group, setGroup] = useState<GroupFilter>("all");
  const selectedRef = useRef<HTMLAnchorElement | null>(null);

  const counts = useMemo(() => {
    const result: Record<EditingGroup, number> = { edit: 0, export: 0, done: 0 };
    for (const project of projects) {
      const key = editingGroup(project);
      if (key) result[key] += 1;
    }
    return result;
  }, [projects]);

  const rows = useMemo(
    () =>
      projects.filter(
        (project) =>
          (group === "all" || editingGroup(project) === group) &&
          matchesText(search, project.title, channelName(project.channel_slug), project.language),
      ),
    [projects, group, search, channelName],
  );

  // Keep the open video in view when it changes (for example after a reload).
  useEffect(() => {
    selectedRef.current?.scrollIntoView({ block: "nearest" });
  }, [selectedId, loading]);

  const chips: Array<{ id: GroupFilter; label: string; count: number; hint: string }> = [
    { id: "all", label: "All", count: projects.length, hint: "Every video in the edit room" },
    ...EDITING_GROUPS.map((id) => ({ id, label: EDITING_GROUP_LABELS[id], count: counts[id], hint: EDITING_GROUP_HINTS[id] })),
  ];

  return (
    <section className="flex min-h-0 flex-col rounded-card border border-line bg-surface shadow-card lg:max-h-[calc(100vh-7rem)]" aria-label="Videos in the edit room">
      <header className="flex items-center justify-between gap-2 border-b border-line px-4 py-3">
        <div className="min-w-0">
          <h2 className="text-[15px] font-semibold text-ink">Videos</h2>
          <p className="text-xs text-ink-muted">At the edit or export stage</p>
        </div>
        <Button variant="ghost" size="sm" className="px-2" onClick={onHide} aria-label="Hide the video list" title="Hide the list to give the preview more room">
          <PanelLeftClose />
        </Button>
      </header>

      <div className="flex flex-col gap-2 border-b border-line px-4 py-3">
        <SearchBox value={search} onChange={setSearch} placeholder="Search title or channel" />
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Show">
          {chips.map((chip) => (
            <button
              key={chip.id}
              type="button"
              title={chip.hint}
              aria-pressed={group === chip.id}
              onClick={() => setGroup(chip.id)}
              className={cn(
                "inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-xs font-medium transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60",
                group === chip.id ? "border-accent/40 bg-accent/10 text-accent-text" : "border-line bg-surface-2 text-ink-muted hover:text-ink",
              )}
            >
              {chip.label}
              <span className="tabular-nums">{chip.count}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {loading ? (
          <LoadingBlock label="Loading videos..." />
        ) : error ? (
          <ErrorState error={error} title="Could not load the videos" onRetry={onRetry} />
        ) : projects.length === 0 ? (
          <EmptyState
            icon={Clapperboard}
            title="No videos here yet"
            description="A video comes to the edit room when its pictures are approved and the edit stage starts."
            action={
              <LinkButton to={PROJECTS_PATH} size="sm" variant="secondary">
                See all projects
              </LinkButton>
            }
          />
        ) : rows.length === 0 ? (
          <EmptyState icon={Search} title="No videos match" description="Clear the search or pick another group." />
        ) : (
          <ul className="flex flex-col gap-0.5 p-2">
            {rows.map((project) => {
              const selected = project.id === selectedId;
              return (
                <li key={project.id}>
                  <Link
                    ref={selected ? selectedRef : undefined}
                    to={editRoomPath(project.id)}
                    aria-current={selected ? "page" : undefined}
                    className={cn(
                      "group relative flex items-start gap-3 rounded-md px-2.5 py-2 transition-colors",
                      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60",
                      selected ? "bg-surface-2" : "hover:bg-surface-2/60",
                    )}
                  >
                    {selected ? <span className="absolute left-0 top-1/2 h-8 w-0.5 -translate-y-1/2 rounded-r bg-accent" aria-hidden /> : null}
                    <ProjectThumb key={project.id} projectId={project.id} sources={thumbnailSources(project)} className="w-16" />
                    <div className="min-w-0 flex-1">
                      <div className={cn("line-clamp-2 text-[13px] font-medium leading-snug", selected ? "text-ink" : "text-ink group-hover:text-ink")}>
                        {project.title || "Untitled video"}
                      </div>
                      <div className="mt-0.5 truncate text-[11px] text-ink-faint" title={formatDateTime(project.updated_at)}>
                        {channelName(project.channel_slug)} - {FORMAT_SHORT_LABELS[project.format]} - {formatRelative(project.updated_at)}
                      </div>
                      <div className="mt-1 flex flex-wrap items-center gap-1.5">
                        <span className="text-[11px] font-medium text-ink-muted">{EDITING_GROUP_LABELS[editingGroup(project) ?? "edit"]}</span>
                        <StageStatusBadge status={project.status} />
                      </div>
                    </div>
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </section>
  );
}

/** What the list collapses to: a narrow strip with a button to bring it back. */
export function EditProjectRail({ count, onShow }: { count: number; onShow: () => void }) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-card border border-line bg-surface py-3 shadow-card">
      <Button variant="ghost" size="sm" className="px-2" onClick={onShow} aria-label="Show the video list" title="Show the video list">
        <PanelLeftOpen />
      </Button>
      <span className="text-[11px] font-medium tabular-nums text-ink-faint" title={`${count} video${count === 1 ? "" : "s"} in the edit room`}>
        {count}
      </span>
    </div>
  );
}
