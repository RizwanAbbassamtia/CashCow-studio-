import { ChevronRight } from "lucide-react";
import { useNavigate } from "react-router";

import { formatRelative } from "../../lib/format";
import type { ChannelSummary } from "../../types/channel";
import type { ProjectSummary } from "../../types/project";
import { projectPath } from "../master/routes";
import { Badge } from "../ui/Badge";
import { Table, TBody, TD, TH, THead, TR } from "../ui/Table";
import { FORMAT_SHORT_LABELS, formatUsd, STAGE_LABELS } from "./stageMeta";
import { StageStatusBadge } from "./StageStatusBadge";

export interface ProjectsTableProps {
  projects: ProjectSummary[];
  /** to show channel names instead of slugs */
  channels?: ChannelSummary[];
  showCost?: boolean;
}

/** One row per project; the whole row opens the project page. */
export function ProjectsTable({ projects, channels = [], showCost = true }: ProjectsTableProps) {
  const navigate = useNavigate();
  const channelName = (slug: string) => channels.find((channel) => channel.slug === slug)?.name ?? slug;

  return (
    <Table>
      <THead>
        <TR>
          <TH className="min-w-[220px]">Video</TH>
          <TH className="w-[160px]">Channel</TH>
          <TH className="w-[110px]">Format</TH>
          <TH className="w-[110px]">Stage</TH>
          <TH className="w-[170px]">Status</TH>
          {showCost ? <TH className="w-[80px] text-right">Cost</TH> : null}
          <TH className="w-[130px]">Updated</TH>
          <TH className="w-10" />
        </TR>
      </THead>
      <TBody>
        {projects.map((project) => {
          const href = projectPath(project.id);
          return (
            <TR
              key={project.id}
              interactive
              tabIndex={0}
              onClick={() => navigate(href)}
              onKeyDown={(event) => {
                if (event.key === "Enter") navigate(href);
              }}
            >
              <TD>
                <div className="line-clamp-2 font-medium text-ink">{project.title || "Untitled video"}</div>
                <div className="text-xs text-ink-faint">{project.language}</div>
              </TD>
              <TD className="text-ink-muted">{channelName(project.channel_slug)}</TD>
              <TD>
                <Badge tone="neutral">{FORMAT_SHORT_LABELS[project.format]}</Badge>
              </TD>
              <TD className="text-ink">{STAGE_LABELS[project.current_stage]}</TD>
              <TD>
                <StageStatusBadge status={project.status} />
              </TD>
              {showCost ? <TD className="text-right tabular-nums text-ink-muted">{formatUsd(project.costs_total_usd)}</TD> : null}
              <TD className="text-ink-muted">{formatRelative(project.updated_at) || "-"}</TD>
              <TD className="text-right">
                <ChevronRight className="inline size-4 text-ink-faint" aria-hidden />
              </TD>
            </TR>
          );
        })}
      </TBody>
    </Table>
  );
}
