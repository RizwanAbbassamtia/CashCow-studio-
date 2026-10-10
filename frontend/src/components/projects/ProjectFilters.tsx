import { Search } from "lucide-react";

import type { ChannelSummary, StageName } from "../../types/channel";
import { STAGE_NAMES } from "../../types/channel";
import type { StageStatus } from "../../types/project";
import { Input, Select } from "../ui/Input";
import { STAGE_LABELS, STAGE_STATUS_LABELS } from "./stageMeta";

export const STATUS_FILTER_OPTIONS: ReadonlyArray<{ value: "" | StageStatus; label: string }> = [
  { value: "", label: "All statuses" },
  { value: "running", label: STAGE_STATUS_LABELS.running },
  { value: "awaiting_review", label: STAGE_STATUS_LABELS.awaiting_review },
  { value: "awaiting_manual", label: STAGE_STATUS_LABELS.awaiting_manual },
  { value: "failed", label: STAGE_STATUS_LABELS.failed },
  { value: "done", label: STAGE_STATUS_LABELS.done },
  { value: "approved", label: STAGE_STATUS_LABELS.approved },
  { value: "pending", label: STAGE_STATUS_LABELS.pending },
  { value: "skipped", label: STAGE_STATUS_LABELS.skipped },
];

export interface ProjectFiltersValue {
  channel: string;
  stage: "" | StageName;
  status: "" | StageStatus;
  search: string;
}

export interface ProjectFiltersProps {
  value: ProjectFiltersValue;
  onChange: (value: ProjectFiltersValue) => void;
  channels: ChannelSummary[];
  /** hide the status select (the review queue fixes the status itself) */
  showStatus?: boolean;
  /** lock the stage select (the /storyboard route pre-filters) */
  stageLocked?: boolean;
  statusOptions?: ReadonlyArray<{ value: string; label: string }>;
}

/** Channel, stage, status and free-text filters above a project list. */
export function ProjectFilters({ value, onChange, channels, showStatus = true, stageLocked = false, statusOptions = STATUS_FILTER_OPTIONS }: ProjectFiltersProps) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <div className="relative">
        <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-ink-faint" aria-hidden />
        <Input
          type="search"
          placeholder="Search titles"
          className="w-64 pl-9"
          value={value.search}
          onChange={(event) => onChange({ ...value, search: event.target.value })}
          aria-label="Search projects by title"
        />
      </div>
      <Select
        wrapperClassName="w-48"
        value={value.channel}
        onChange={(event) => onChange({ ...value, channel: event.target.value })}
        aria-label="Filter by channel"
      >
        <option value="">All channels</option>
        {channels.map((channel) => (
          <option key={channel.slug} value={channel.slug}>
            {channel.name}
          </option>
        ))}
      </Select>
      <Select
        wrapperClassName="w-44"
        value={value.stage}
        disabled={stageLocked}
        onChange={(event) => onChange({ ...value, stage: event.target.value as "" | StageName })}
        aria-label="Filter by stage"
      >
        <option value="">All stages</option>
        {STAGE_NAMES.map((stage) => (
          <option key={stage} value={stage}>
            {STAGE_LABELS[stage]}
          </option>
        ))}
      </Select>
      {showStatus ? (
        <Select
          wrapperClassName="w-48"
          value={value.status}
          onChange={(event) => onChange({ ...value, status: event.target.value as "" | StageStatus })}
          aria-label="Filter by status"
        >
          {statusOptions.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </Select>
      ) : null}
    </div>
  );
}

export function matchesFilters(project: { channel_slug: string; current_stage: StageName; status: StageStatus; title: string }, filters: ProjectFiltersValue): boolean {
  if (filters.channel && project.channel_slug !== filters.channel) return false;
  if (filters.stage && project.current_stage !== filters.stage) return false;
  if (filters.status && project.status !== filters.status) return false;
  const needle = filters.search.trim().toLowerCase();
  if (needle && !project.title.toLowerCase().includes(needle)) return false;
  return true;
}
