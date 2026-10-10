import { Clock, FileCheck2, FolderOpen, Music, Search, ShieldAlert } from "lucide-react";
import { useMemo, useState } from "react";

import { useChannels } from "../../api/channels";
import { useLibraryMusic, useLibraryMusicFolders } from "../../api/library";
import { StatCard } from "../../components/dashboard/StatCard";
import { channelNamer, ChannelLink } from "../../components/master/ChannelLink";
import { CountLine } from "../../components/master/CountLine";
import { bytesText, durationText, lastSegment, matchesText, sumKnown, totalDurationText } from "../../components/master/format";
import { SearchBox } from "../../components/master/SearchBox";
import { Badge } from "../../components/ui/Badge";
import { Card } from "../../components/ui/Card";
import { Select } from "../../components/ui/Input";
import { PageHeader } from "../../components/ui/PageHeader";
import { LoadingBlock } from "../../components/ui/Spinner";
import { EmptyState, ErrorState, Notice } from "../../components/ui/States";
import { Table, TBody, TD, TH, THead, TR } from "../../components/ui/Table";
import { plural } from "../../lib/format";
import type { LibraryMusicFolder } from "../../types/library";

type LicenceFilter = "" | "licensed" | "missing";

function UsedBy({ slugs, channelName }: { slugs: string[]; channelName: (slug: string) => string }) {
  if (slugs.length === 0) return <span className="text-xs text-ink-faint">No channel</span>;
  return (
    <div className="flex flex-wrap gap-x-2 gap-y-0.5">
      {slugs.map((slug, index) => (
        <span key={slug} className="text-[13px]">
          <ChannelLink slug={slug} name={channelName(slug)} className="font-normal" />
          {index < slugs.length - 1 ? <span className="text-ink-faint">,</span> : null}
        </span>
      ))}
    </div>
  );
}

function FoldersCard({ folders, loading, error, onRetry, channelName }: { folders: LibraryMusicFolder[]; loading: boolean; error: unknown; onRetry: () => void; channelName: (slug: string) => string }) {
  return (
    <Card
      title="Music folders"
      description="Set on each channel's Channel tab. A track counts as licensed when a <track>.license.txt sits next to it or a LICENSE file is in its folder."
      flush
    >
      {loading ? (
        <LoadingBlock label="Checking the folders..." />
      ) : error ? (
        <ErrorState error={error} title="Could not check the music folders" onRetry={onRetry} />
      ) : folders.length === 0 ? (
        <p className="px-5 py-6 text-center text-[13px] text-ink-faint">No channel has a music folder yet.</p>
      ) : (
        <ul className="divide-y divide-line">
          {folders.map((folder) => (
            <li key={folder.folder} className="flex flex-wrap items-start justify-between gap-x-6 gap-y-2 px-5 py-3">
              <div className="flex min-w-0 items-start gap-3">
                <FolderOpen className="mt-0.5 size-4 shrink-0 text-ink-faint" aria-hidden />
                <div className="min-w-0">
                  <p className="break-all font-mono text-xs text-ink">{folder.folder}</p>
                  <div className="mt-1 text-xs text-ink-muted">
                    <UsedBy slugs={folder.used_by_channels} channelName={channelName} />
                  </div>
                  {folder.problem ? <p className="mt-1 text-xs text-warn">{folder.problem}</p> : null}
                </div>
              </div>
              <div className="flex shrink-0 flex-wrap items-center gap-2">
                {!folder.exists ? (
                  <Badge tone="fail" dot>
                    Not found
                  </Badge>
                ) : (
                  <>
                    <Badge tone="neutral">{plural(folder.tracks, "track")}</Badge>
                    <Badge tone={folder.tracks > 0 && folder.licensed_tracks === folder.tracks ? "ok" : folder.licensed_tracks > 0 ? "warn" : "neutral"}>
                      {folder.licensed_tracks} licensed
                    </Badge>
                    {folder.license_file ? (
                      <Badge tone="ok" className="font-mono">
                        {folder.license_file}
                      </Badge>
                    ) : null}
                  </>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

/** /master/music - every track in every channel's music folder, with length and licence. */
export function MusicLibraryPage() {
  const music = useLibraryMusic();
  const folders = useLibraryMusicFolders();
  const channels = useChannels();
  const [search, setSearch] = useState("");
  const [folder, setFolder] = useState("");
  const [licence, setLicence] = useState<LicenceFilter>("");
  const [channel, setChannel] = useState("");

  const channelName = useMemo(() => channelNamer(channels.data ?? []), [channels.data]);
  const all = useMemo(
    () => [...(music.data ?? [])].sort((a, b) => a.folder.localeCompare(b.folder) || a.file.localeCompare(b.file, undefined, { numeric: true })),
    [music.data],
  );
  const folderOptions = useMemo(() => [...new Set(all.map((track) => track.folder))], [all]);
  const channelOptions = useMemo(
    () => [...new Set(all.flatMap((track) => track.used_by_channels))].sort((a, b) => channelName(a).localeCompare(channelName(b))),
    [all, channelName],
  );

  const rows = useMemo(
    () =>
      all.filter(
        (track) =>
          (!folder || track.folder === folder) &&
          (!channel || track.used_by_channels.includes(channel)) &&
          (licence === "" || (licence === "licensed" ? track.license_ok : !track.license_ok)) &&
          matchesText(search, track.file, track.folder, track.license_file),
      ),
    [all, folder, channel, licence, search],
  );

  const licensed = all.filter((track) => track.license_ok).length;
  const unlicensed = all.length - licensed;
  const totalSeconds = sumKnown(all.map((track) => track.duration_s));
  const folderProblems = (folders.data ?? []).filter((item) => item.problem || !item.exists);
  const loading = music.isPending;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Music"
        description="Every track in the channels' music folders, with its length and licence. The edit stage picks background music from these folders."
      />

      <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        <StatCard label="Tracks" icon={Music} value={loading ? "..." : all.length} hint={`In ${plural(folderOptions.length, "folder")}`} />
        <StatCard label="Total length" icon={Clock} value={loading ? "..." : totalDurationText(totalSeconds)} hint="Of all tracks together" />
        <StatCard label="Licensed" icon={FileCheck2} value={loading ? "..." : `${licensed} of ${all.length}`} tone={!loading && all.length > 0 && unlicensed === 0 ? "ok" : "default"} hint="A licence file was found" />
        <StatCard
          label="No licence file"
          icon={ShieldAlert}
          value={loading ? "..." : unlicensed}
          tone={unlicensed > 0 ? "warn" : "muted"}
          hint={unlicensed > 0 ? "The edit stage warns when these are used" : "Every track is covered"}
        />
      </div>

      {folderProblems.length > 0 ? (
        <Notice tone="warn" title={`${folderProblems.length === 1 ? "One music folder" : `${folderProblems.length} music folders`} cannot be used`}>
          <ul className="mt-1 flex flex-col gap-1">
            {folderProblems.map((item) => (
              <li key={item.folder}>
                <span className="break-all font-mono text-xs text-ink">{item.folder}</span>
                {item.problem ? <span>: {item.problem}</span> : <span>: the folder was not found on this laptop.</span>}
              </li>
            ))}
          </ul>
        </Notice>
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        <SearchBox value={search} onChange={setSearch} placeholder="Search tracks" className="w-64" />
        <Select wrapperClassName="w-56" value={folder} onChange={(event) => setFolder(event.target.value)} aria-label="Filter by folder">
          <option value="">All folders</option>
          {folderOptions.map((path) => (
            <option key={path} value={path} title={path}>
              {lastSegment(path)}
            </option>
          ))}
        </Select>
        <Select wrapperClassName="w-52" value={channel} onChange={(event) => setChannel(event.target.value)} aria-label="Filter by channel">
          <option value="">All channels</option>
          {channelOptions.map((slug) => (
            <option key={slug} value={slug}>
              {channelName(slug)}
            </option>
          ))}
        </Select>
        <Select wrapperClassName="w-48" value={licence} onChange={(event) => setLicence(event.target.value as LicenceFilter)} aria-label="Filter by licence">
          <option value="">Any licence</option>
          <option value="licensed">Licensed</option>
          <option value="missing">No licence file</option>
        </Select>
      </div>

      <Card flush>
        {loading ? (
          <LoadingBlock label="Reading the music folders (the first time, every track is measured)..." />
        ) : music.isError ? (
          <ErrorState error={music.error} title="Could not load the music" onRetry={() => void music.refetch()} />
        ) : all.length === 0 ? (
          <EmptyState
            icon={Music}
            title="No music yet"
            description="Set a music folder on a channel's Channel tab and put MP3, WAV or M4A tracks in it, each with a licence file."
          />
        ) : rows.length === 0 ? (
          <EmptyState icon={Search} title="No tracks match" description="Clear the search or pick another folder, channel or licence." />
        ) : (
          <>
            <Table>
              <THead>
                <TR>
                  <TH className="min-w-[260px]">Track</TH>
                  <TH className="w-[90px] whitespace-nowrap text-right">Length</TH>
                  <TH className="w-[100px] whitespace-nowrap text-right">Size</TH>
                  <TH className="w-[220px]">Licence</TH>
                  <TH className="w-[220px]">Used by</TH>
                </TR>
              </THead>
              <TBody>
                {rows.map((track) => (
                  <TR key={`${track.folder}|${track.file}`}>
                    <TD>
                      <div className="font-medium text-ink">{track.file}</div>
                      <div className="max-w-[420px] truncate font-mono text-[11px] text-ink-faint" title={track.folder}>
                        {track.folder}
                      </div>
                    </TD>
                    <TD className="whitespace-nowrap text-right tabular-nums text-ink-muted">{durationText(track.duration_s)}</TD>
                    <TD className="whitespace-nowrap text-right tabular-nums text-ink-muted">{bytesText(track.size_bytes)}</TD>
                    <TD>
                      {track.license_ok ? (
                        <>
                          <Badge tone="ok" dot>
                            Licensed
                          </Badge>
                          {track.license_file ? (
                            <p className="mt-1 max-w-[220px] truncate font-mono text-[11px] text-ink-faint" title={track.license_file}>
                              {track.license_file}
                            </p>
                          ) : null}
                        </>
                      ) : (
                        <>
                          <Badge tone="warn" dot>
                            No licence file
                          </Badge>
                          <p className="mt-1 text-[11px] text-ink-faint">Add {track.file.replace(/\.[^.]+$/, "")}.license.txt next to it</p>
                        </>
                      )}
                    </TD>
                    <TD>
                      <UsedBy slugs={track.used_by_channels} channelName={channelName} />
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
            <CountLine shown={rows.length} total={all.length} noun="track" />
          </>
        )}
      </Card>

      <FoldersCard
        folders={folders.data ?? []}
        loading={folders.isPending}
        error={folders.isError ? folders.error : null}
        onRetry={() => void folders.refetch()}
        channelName={channelName}
      />
    </div>
  );
}
