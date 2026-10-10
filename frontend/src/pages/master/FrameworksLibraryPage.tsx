import { AlertTriangle, BookOpen, ExternalLink, FileCheck2, Search, Tv } from "lucide-react";
import { useMemo, useState } from "react";

import { useLibraryFrameworks } from "../../api/library";
import { StatCard } from "../../components/dashboard/StatCard";
import { ChannelLink } from "../../components/master/ChannelLink";
import { CountLine } from "../../components/master/CountLine";
import { bytesText, isWebLink, matchesText } from "../../components/master/format";
import { SearchBox } from "../../components/master/SearchBox";
import { Badge, type BadgeTone } from "../../components/ui/Badge";
import { Card } from "../../components/ui/Card";
import { Select } from "../../components/ui/Input";
import { PageHeader } from "../../components/ui/PageHeader";
import { LoadingBlock } from "../../components/ui/Spinner";
import { EmptyState, ErrorState } from "../../components/ui/States";
import { Table, TBody, TD, TH, THead, TR } from "../../components/ui/Table";
import { formatDateTime, formatRelative, humanize } from "../../lib/format";
import { FORMAT_LABELS, FRAMEWORK_TYPE_LABELS } from "../../lib/labels";
import { FRAMEWORK_TYPES, VIDEO_FORMATS, type VideoFormat } from "../../types/channel";
import { isFrameworkType, type LibraryFramework } from "../../types/library";

function typeLabel(type: string): string {
  return isFrameworkType(type) ? FRAMEWORK_TYPE_LABELS[type] : humanize(type);
}

function formatsLabel(formats: string): string {
  return (VIDEO_FORMATS as readonly string[]).includes(formats) ? FORMAT_LABELS[formats as VideoFormat] : humanize(formats);
}

function typeOrder(type: string): number {
  const index = (FRAMEWORK_TYPES as readonly string[]).indexOf(type);
  return index === -1 ? FRAMEWORK_TYPES.length : index;
}

interface FrameworkStatus {
  tone: BadgeTone;
  label: string;
  detail: string;
}

/** Whether the writing AI can use the file, in plain English. */
function frameworkStatus(framework: LibraryFramework): FrameworkStatus {
  if (framework.problem) return { tone: "fail", label: "Needs attention", detail: framework.problem };
  if (framework.is_link) return { tone: "info", label: "Web link", detail: "Shown for reference. The app does not open links; upload the file to let the AI read it." };
  if (!framework.exists) return { tone: "warn", label: "File missing", detail: "The file is not in the shared folder. Upload it again on the channel's Frameworks tab." };
  if (framework.extracted) return { tone: "ok", label: "Ready", detail: "The text is ready for the writing AI." };
  return { tone: "neutral", label: "Read on first use", detail: "The app pulls the text out of this file the first time a video uses it." };
}

function needsAttention(framework: LibraryFramework): boolean {
  return Boolean(framework.problem) || (!framework.is_link && !framework.exists);
}

/** /master/frameworks - every channel's framework files in one list. */
export function FrameworksLibraryPage() {
  const frameworks = useLibraryFrameworks();
  const [search, setSearch] = useState("");
  const [channel, setChannel] = useState("");
  const [type, setType] = useState("");

  const all = useMemo(
    () =>
      [...(frameworks.data ?? [])].sort(
        (a, b) => a.channel_name.localeCompare(b.channel_name) || typeOrder(a.type) - typeOrder(b.type) || a.name.localeCompare(b.name),
      ),
    [frameworks.data],
  );

  const channelOptions = useMemo(() => {
    const names = new Map<string, string>();
    for (const item of all) if (!names.has(item.channel_slug)) names.set(item.channel_slug, item.channel_name);
    return [...names.entries()].sort((a, b) => a[1].localeCompare(b[1]));
  }, [all]);

  const typeOptions = useMemo(() => [...new Set(all.map((item) => item.type))].sort((a, b) => typeOrder(a) - typeOrder(b)), [all]);

  const rows = useMemo(
    () =>
      all.filter(
        (item) =>
          (!channel || item.channel_slug === channel) &&
          (!type || item.type === type) &&
          matchesText(search, item.name, item.path, item.channel_name, typeLabel(item.type), item.version),
      ),
    [all, channel, type, search],
  );

  const ready = all.filter((item) => item.extracted).length;
  const attention = all.filter(needsAttention).length;
  const loading = frameworks.isPending;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Frameworks"
        description="The title, script, scene, thumbnail and SEO frameworks of every channel in one list. Add or change them on each channel's Frameworks tab."
      />

      <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        <StatCard label="Frameworks" icon={BookOpen} value={loading ? "..." : all.length} hint="Files and links across all channels" />
        <StatCard label="Channels" icon={Tv} value={loading ? "..." : channelOptions.length} hint="Channels with at least one framework" />
        <StatCard label="Ready for the AI" icon={FileCheck2} value={loading ? "..." : ready} tone={ready > 0 ? "ok" : "muted"} hint="Text already pulled out of the file" />
        <StatCard label="Need attention" icon={AlertTriangle} value={loading ? "..." : attention} tone={attention > 0 ? "warn" : "muted"} hint={attention > 0 ? "Missing files or paths the app cannot use" : "Nothing to fix"} />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <SearchBox value={search} onChange={setSearch} placeholder="Search name, file or channel" className="w-72" />
        <Select wrapperClassName="w-52" value={channel} onChange={(event) => setChannel(event.target.value)} aria-label="Filter by channel">
          <option value="">All channels</option>
          {channelOptions.map(([slug, name]) => (
            <option key={slug} value={slug}>
              {name}
            </option>
          ))}
        </Select>
        <Select wrapperClassName="w-60" value={type} onChange={(event) => setType(event.target.value)} aria-label="Filter by type">
          <option value="">All types</option>
          {typeOptions.map((id) => (
            <option key={id} value={id}>
              {typeLabel(id)}
            </option>
          ))}
        </Select>
      </div>

      <Card flush>
        {loading ? (
          <LoadingBlock label="Reading the channels' frameworks..." />
        ) : frameworks.isError ? (
          <ErrorState error={frameworks.error} title="Could not load the frameworks" onRetry={() => void frameworks.refetch()} />
        ) : all.length === 0 ? (
          <EmptyState
            icon={BookOpen}
            title="No frameworks yet"
            description="Upload a title, script or scene framework on a channel's Frameworks tab. Channels without one use the app's built-in frameworks."
          />
        ) : rows.length === 0 ? (
          <EmptyState icon={Search} title="No frameworks match" description="Clear the search or pick another channel or type." />
        ) : (
          <>
            <Table>
              <THead>
                <TR>
                  <TH className="min-w-[260px]">Framework</TH>
                  <TH className="w-[170px]">Type</TH>
                  <TH className="w-[170px]">Channel</TH>
                  <TH className="w-[110px]">For</TH>
                  <TH className="w-[90px]">Version</TH>
                  <TH className="w-[90px] whitespace-nowrap text-right">Size</TH>
                  <TH className="w-[120px]">Changed</TH>
                  <TH className="w-[200px]">Status</TH>
                </TR>
              </THead>
              <TBody>
                {rows.map((item, index) => {
                  const status = frameworkStatus(item);
                  const link = item.is_link || isWebLink(item.path);
                  return (
                    <TR key={`${item.channel_slug}:${item.type}:${item.path}:${index}`}>
                      <TD>
                        <div className="font-medium text-ink">{item.name}</div>
                        {link ? (
                          <a href={item.path} target="_blank" rel="noreferrer" className="mt-0.5 inline-flex max-w-[340px] items-center gap-1 truncate text-xs text-accent-text hover:underline" title={item.path}>
                            <span className="truncate">{item.path}</span>
                            <ExternalLink className="size-3 shrink-0" aria-hidden />
                          </a>
                        ) : (
                          <div className="max-w-[360px] truncate font-mono text-[11px] text-ink-faint" title={item.path}>
                            {item.path || "No file chosen"}
                          </div>
                        )}
                      </TD>
                      <TD className="text-ink-muted">{typeLabel(item.type)}</TD>
                      <TD>
                        <ChannelLink slug={item.channel_slug} name={item.channel_name} />
                      </TD>
                      <TD className="text-ink-muted">{formatsLabel(item.formats)}</TD>
                      <TD className="text-ink-muted">{item.version || "-"}</TD>
                      <TD className="whitespace-nowrap text-right tabular-nums text-ink-muted">{link ? "-" : bytesText(item.size_bytes)}</TD>
                      <TD className="text-ink-muted" title={formatDateTime(item.updated_at)}>
                        {item.updated_at ? formatRelative(item.updated_at) : "-"}
                      </TD>
                      <TD>
                        <Badge tone={status.tone} dot>
                          {status.label}
                        </Badge>
                        <p className="mt-1 line-clamp-2 text-[11px] leading-snug text-ink-faint" title={status.detail}>
                          {status.detail}
                        </p>
                      </TD>
                    </TR>
                  );
                })}
              </TBody>
            </Table>
            <CountLine shown={rows.length} total={all.length} noun="framework" />
          </>
        )}
      </Card>
    </div>
  );
}
