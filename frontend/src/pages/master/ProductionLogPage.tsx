import { CalendarClock, Clock, Download, FolderOpen, HardDrive, PackageCheck, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router";

import { useChannels } from "../../api/channels";
import { errorMessage } from "../../api/client";
import { useOpenExportFolder } from "../../api/export";
import { productionLogCsvUrl, useProductionLog } from "../../api/library";
import { StatCard } from "../../components/dashboard/StatCard";
import { channelNamer, ChannelLink } from "../../components/master/ChannelLink";
import { CopyButton } from "../../components/master/CopyButton";
import { CountLine } from "../../components/master/CountLine";
import { bytesText, durationText, isWithinDays, matchesText, sumKnown, totalDurationText } from "../../components/master/format";
import { PERIODS, productionLogTsv, sinceFor, type PeriodId } from "../../components/master/productionLog";
import { projectPath } from "../../components/master/routes";
import { SearchBox } from "../../components/master/SearchBox";
import { Badge } from "../../components/ui/Badge";
import { Button, buttonClasses } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Select } from "../../components/ui/Input";
import { PageHeader } from "../../components/ui/PageHeader";
import { LoadingBlock } from "../../components/ui/Spinner";
import { EmptyState, ErrorState } from "../../components/ui/States";
import { Table, TBody, TD, TH, THead, TR } from "../../components/ui/Table";
import { useToast } from "../../components/ui/Toast";
import { formatDateTime, formatRelative } from "../../lib/format";
import type { ProductionLogRow } from "../../types/library";
import { renderPresetLabel } from "../../types/timeline";

function formatLabel(format: string): string {
  if (format === "shorts") return "Short";
  if (format === "long") return "Long video";
  return format || "-";
}

/** /master/production-log - every exported video, newest first, with a CSV for the team sheet. */
export function ProductionLogPage() {
  const { toast } = useToast();
  const channels = useChannels();
  const openFolder = useOpenExportFolder();
  const [channel, setChannel] = useState("");
  const [period, setPeriod] = useState<PeriodId>("all");
  const [search, setSearch] = useState("");

  // The period becomes a date once per choice, so the query key stays the same between renders.
  const filters = useMemo(() => ({ channel_slug: channel || undefined, since: sinceFor(period) }), [channel, period]);
  const log = useProductionLog(filters);

  const channelName = useMemo(() => channelNamer(channels.data ?? []), [channels.data]);
  const all = useMemo(() => log.data ?? [], [log.data]);
  const rows = useMemo(
    () => all.filter((row) => matchesText(search, row.title, channelName(row.channel_slug), row.user, row.machine, row.language, row.export_folder)),
    [all, search, channelName],
  );

  const thisWeek = all.filter((row) => isWithinDays(row.at, 7)).length;
  const totalSeconds = sumKnown(all.map((row) => row.duration_s));
  const totalBytes = sumKnown(all.map((row) => row.size_bytes));
  const loading = log.isPending;
  const channelOptions = [...(channels.data ?? [])].sort((a, b) => a.name.localeCompare(b.name));

  const onOpenFolder = (row: ProductionLogRow) =>
    openFolder.mutate(
      { projectId: row.project_id, folder: row.export_folder || null },
      { onError: (error) => toast({ tone: "error", title: "Could not open the export folder", description: errorMessage(error) }) },
    );

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Production log"
        description="Every exported video, newest first: who exported it, on which laptop, in which sizes and where the files went."
        actions={
          <>
            <CopyButton
              text={productionLogTsv(rows, channelName)}
              what={`${rows.length} row${rows.length === 1 ? "" : "s"}`}
              label="Copy for the team sheet"
              variant="secondary"
              size="md"
              disabled={rows.length === 0}
              title="Copies the rows below so you can paste them straight into Google Sheets or Excel"
            />
            <a
              href={productionLogCsvUrl(filters)}
              download
              className={buttonClasses("primary", "md")}
              title="Saves the same rows as a CSV file (the search box does not apply)"
            >
              <Download aria-hidden />
              Download CSV
            </a>
          </>
        }
      />

      <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        <StatCard label="Exports" icon={PackageCheck} value={loading ? "..." : all.length} hint={PERIODS.find((item) => item.id === period)?.label ?? ""} />
        <StatCard label="This week" icon={CalendarClock} value={loading ? "..." : thisWeek} tone={thisWeek > 0 ? "ok" : "muted"} hint="Exported in the last 7 days" />
        <StatCard label="Total length" icon={Clock} value={loading ? "..." : totalDurationText(totalSeconds)} hint="Of the exported videos" />
        <StatCard label="Total size" icon={HardDrive} value={loading ? "..." : bytesText(totalBytes)} hint="Exported video files" />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <SearchBox value={search} onChange={setSearch} placeholder="Search title, person or laptop" className="w-72" />
        <Select wrapperClassName="w-52" value={channel} onChange={(event) => setChannel(event.target.value)} aria-label="Filter by channel">
          <option value="">All channels</option>
          {channelOptions.map((item) => (
            <option key={item.slug} value={item.slug}>
              {item.name}
            </option>
          ))}
        </Select>
        <Select wrapperClassName="w-44" value={period} onChange={(event) => setPeriod(event.target.value as PeriodId)} aria-label="Period">
          {PERIODS.map((item) => (
            <option key={item.id} value={item.id}>
              {item.label}
            </option>
          ))}
        </Select>
      </div>

      <Card flush>
        {loading ? (
          <LoadingBlock label="Reading the exports..." />
        ) : log.isError ? (
          <ErrorState error={log.error} title="Could not load the production log" onRetry={() => void log.refetch()} />
        ) : all.length === 0 ? (
          <EmptyState
            icon={PackageCheck}
            title={channel || period !== "all" ? "No exports in this view" : "No videos exported yet"}
            description={
              channel || period !== "all"
                ? "Pick another channel or a longer period."
                : "A video shows up here when its export is approved and the files are copied to the export folder."
            }
          />
        ) : rows.length === 0 ? (
          <EmptyState icon={Search} title="No exports match" description="Clear the search to see every row." />
        ) : (
          <>
            <Table>
              <THead>
                <TR>
                  <TH className="w-[150px]">Exported</TH>
                  <TH className="min-w-[240px]">Video</TH>
                  <TH className="w-[160px]">Channel</TH>
                  <TH className="w-[110px]">Sizes</TH>
                  <TH className="w-[80px] whitespace-nowrap text-right">Length</TH>
                  <TH className="w-[90px] whitespace-nowrap text-right">File size</TH>
                  <TH className="w-[150px]">By</TH>
                  <TH className="w-[150px] text-right">Folder</TH>
                </TR>
              </THead>
              <TBody>
                {rows.map((row, index) => (
                  <TR key={`${row.project_id}:${row.at}:${index}`}>
                    <TD className="text-ink-muted" title={formatDateTime(row.at)}>
                      <div className="text-ink">{formatRelative(row.at) || "-"}</div>
                      <div className="text-[11px] text-ink-faint">{formatDateTime(row.at)}</div>
                    </TD>
                    <TD>
                      {row.project_id && !row.archived ? (
                        <Link to={projectPath(row.project_id)} className="line-clamp-2 font-medium text-ink hover:text-accent-text hover:underline">
                          {row.title || "Untitled video"}
                        </Link>
                      ) : (
                        <div className="line-clamp-2 font-medium text-ink">{row.title || "Untitled video"}</div>
                      )}
                      <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-ink-faint">
                        <span>
                          {formatLabel(row.format)}
                          {row.language ? ` - ${row.language}` : ""}
                        </span>
                        {row.archived ? <Badge tone="neutral">Archived</Badge> : null}
                      </div>
                    </TD>
                    <TD>
                      <ChannelLink slug={row.channel_slug} name={channelName(row.channel_slug)} />
                    </TD>
                    <TD>
                      <div className="flex flex-wrap gap-1">
                        {row.presets.length > 0 ? (
                          row.presets.map((preset) => (
                            <Badge key={preset} tone={preset === "2160p" ? "accent" : "neutral"}>
                              {renderPresetLabel(preset)}
                            </Badge>
                          ))
                        ) : (
                          <span className="text-xs text-ink-faint">-</span>
                        )}
                      </div>
                    </TD>
                    <TD className="whitespace-nowrap text-right tabular-nums text-ink-muted">{durationText(row.duration_s)}</TD>
                    <TD className="whitespace-nowrap text-right tabular-nums text-ink-muted">{bytesText(row.size_bytes)}</TD>
                    <TD>
                      <div className="text-ink">{row.user || <span className="text-ink-faint">Not recorded</span>}</div>
                      {row.machine ? <div className="text-[11px] text-ink-faint">on {row.machine}</div> : null}
                    </TD>
                    <TD className="text-right">
                      <div className="inline-flex items-center justify-end gap-1">
                        {row.export_folder ? (
                          <CopyButton text={row.export_folder} what="Folder path" label="Copy path" variant="ghost" title={row.export_folder} />
                        ) : null}
                        {!row.archived && row.project_id ? (
                          <Button
                            variant="ghost"
                            size="sm"
                            className="px-2"
                            icon={<FolderOpen />}
                            loading={openFolder.isPending && openFolder.variables?.projectId === row.project_id}
                            onClick={() => onOpenFolder(row)}
                            aria-label="Open the export folder"
                            title="Open the export folder (on the laptop that exported it)"
                          />
                        ) : null}
                      </div>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
            <CountLine shown={rows.length} total={all.length} noun="export" />
          </>
        )}
      </Card>
    </div>
  );
}
