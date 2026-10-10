import { AudioLines, FileWarning, Mic, Search, ShieldAlert, ShieldCheck } from "lucide-react";
import { useMemo, useState } from "react";

import { useLibraryVoices } from "../../api/library";
import { StatCard } from "../../components/dashboard/StatCard";
import { ChannelLink } from "../../components/master/ChannelLink";
import { CountLine } from "../../components/master/CountLine";
import { lastSegment, matchesText } from "../../components/master/format";
import { SearchBox } from "../../components/master/SearchBox";
import { Badge } from "../../components/ui/Badge";
import { Card } from "../../components/ui/Card";
import { Select } from "../../components/ui/Input";
import { PageHeader } from "../../components/ui/PageHeader";
import { LoadingBlock } from "../../components/ui/Spinner";
import { EmptyState, ErrorState, Notice } from "../../components/ui/States";
import { Table, TBody, TD, TH, THead, TR } from "../../components/ui/Table";
import { formatDateTime } from "../../lib/format";
import { voiceNeedsConsent, voiceToolName, type LibraryVoice } from "../../types/library";

type Show = "all" | "attention";

function consentMissing(voice: LibraryVoice): boolean {
  return voiceNeedsConsent(voice) && !voice.consent.present;
}

function sampleMissing(voice: LibraryVoice): boolean {
  return Boolean(voice.sample_path.trim()) && !voice.sample_exists;
}

function ConsentCell({ voice }: { voice: LibraryVoice }) {
  const consent = voice.consent;
  if (!voiceNeedsConsent(voice)) {
    return (
      <div>
        <Badge tone="neutral">Not needed</Badge>
        <p className="mt-1 text-[11px] text-ink-faint">A stock voice of the tool, not a clone.</p>
      </div>
    );
  }
  if (consent.present) {
    const when = formatDateTime(consent.consented_at);
    return (
      <div>
        <Badge tone="ok" dot>
          <ShieldCheck className="size-3" aria-hidden />
          Recorded
        </Badge>
        <p className="mt-1 text-[11px] leading-snug text-ink-faint" title={consent.ref ?? undefined}>
          {consent.owner_name ? `Agreed by ${consent.owner_name}` : "Consent on file"}
          {when ? `, ${when}` : ""}
        </p>
        {consent.problem ? <p className="mt-0.5 text-[11px] leading-snug text-warn">{consent.problem}</p> : null}
      </div>
    );
  }
  return (
    <div>
      <Badge tone="fail" dot>
        <ShieldAlert className="size-3" aria-hidden />
        Missing
      </Badge>
      <p className="mt-1 line-clamp-3 text-[11px] leading-snug text-ink-muted" title={consent.problem ?? undefined}>
        {consent.problem ?? "The voice stage stops until the voice's owner has agreed."}
      </p>
    </div>
  );
}

function SampleCell({ voice }: { voice: LibraryVoice }) {
  if (!voice.sample_path.trim()) return <span className="text-xs text-ink-faint">No sample</span>;
  return (
    <div className="min-w-0">
      {voice.sample_exists ? <Badge tone="ok">Found</Badge> : <Badge tone="warn">Not found on this laptop</Badge>}
      <p className="mt-1 max-w-[240px] truncate font-mono text-[11px] text-ink-faint" title={voice.sample_path}>
        {lastSegment(voice.sample_path)}
      </p>
    </div>
  );
}

/** /master/voices - the voice each channel narrates with, its consent record and sample. */
export function VoicesPage() {
  const voices = useLibraryVoices();
  const [search, setSearch] = useState("");
  const [show, setShow] = useState<Show>("all");

  const all = useMemo(() => [...(voices.data ?? [])].sort((a, b) => a.channel_name.localeCompare(b.channel_name)), [voices.data]);
  const rows = useMemo(
    () =>
      all.filter(
        (voice) =>
          (show === "all" || consentMissing(voice) || sampleMissing(voice)) &&
          matchesText(search, voice.channel_name, voice.name, voiceToolName(voice.tool), voice.clone_ref, voice.language),
      ),
    [all, show, search],
  );

  const clones = all.filter(voiceNeedsConsent).length;
  const missingConsent = all.filter(consentMissing);
  const missingSamples = all.filter(sampleMissing).length;
  const loading = voices.isPending;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Voices"
        description="The voice each channel narrates with. A cloned voice needs the owner's consent on record before the voice stage will run."
      />

      <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        <StatCard label="Channel voices" icon={Mic} value={loading ? "..." : all.length} hint="One voice per channel" />
        <StatCard label="Cloned voices" icon={AudioLines} value={loading ? "..." : clones} tone={clones > 0 ? "default" : "muted"} hint="Made from a real person's voice" />
        <StatCard
          label="Consent missing"
          icon={ShieldAlert}
          value={loading ? "..." : missingConsent.length}
          tone={missingConsent.length > 0 ? "fail" : "muted"}
          hint={missingConsent.length > 0 ? "The voice stage stops for these" : "Every clone has a record"}
        />
        <StatCard label="Samples not found" icon={FileWarning} value={loading ? "..." : missingSamples} tone={missingSamples > 0 ? "warn" : "muted"} hint="Sample files missing on this laptop" />
      </div>

      {missingConsent.length > 0 ? (
        <Notice tone="fail" title={`${missingConsent.length === 1 ? "One cloned voice has" : `${missingConsent.length} cloned voices have`} no consent record`}>
          {missingConsent.map((voice) => voice.channel_name).join(", ")}: the voice stage stops for {missingConsent.length === 1 ? "this channel" : "these channels"} until the owner of the voice
          has agreed. Fill in the Consent fields on the channel's Voice tab, or put a consent.json next to the voice sample.
        </Notice>
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        <SearchBox value={search} onChange={setSearch} placeholder="Search channel, voice or tool" className="w-72" />
        <Select wrapperClassName="w-56" value={show} onChange={(event) => setShow(event.target.value as Show)} aria-label="Show">
          <option value="all">All voices</option>
          <option value="attention">Only those that need attention</option>
        </Select>
      </div>

      <Card flush>
        {loading ? (
          <LoadingBlock label="Reading the channels' voices..." />
        ) : voices.isError ? (
          <ErrorState error={voices.error} title="Could not load the voices" onRetry={() => void voices.refetch()} />
        ) : all.length === 0 ? (
          <EmptyState icon={Mic} title="No channels yet" description="Each channel names its voice on the Voice tab of its setup." />
        ) : rows.length === 0 ? (
          <EmptyState
            icon={Search}
            title={show === "attention" ? "Nothing needs attention" : "No voices match"}
            description={show === "attention" ? "Every cloned voice has consent on record and every sample was found." : "Clear the search to see every voice."}
          />
        ) : (
          <>
            <Table>
              <THead>
                <TR>
                  <TH className="w-[200px]">Channel</TH>
                  <TH className="min-w-[200px]">Voice</TH>
                  <TH className="w-[220px]">Voice id in the tool</TH>
                  <TH className="w-[230px]">Consent</TH>
                  <TH className="w-[220px]">Voice sample</TH>
                </TR>
              </THead>
              <TBody>
                {rows.map((voice) => (
                  <TR key={voice.channel_slug || voice.channel_name}>
                    <TD>
                      <ChannelLink slug={voice.channel_slug} name={voice.channel_name} />
                      <div className="text-xs text-ink-faint">{voice.language}</div>
                    </TD>
                    <TD>
                      <div className="font-medium text-ink">{voice.name || <span className="font-normal text-ink-faint">No name given</span>}</div>
                      <div className="text-xs text-ink-muted">{voiceToolName(voice.tool)}</div>
                    </TD>
                    <TD>
                      {voice.clone_ref.trim() ? (
                        <span className="block max-w-[220px] select-all truncate font-mono text-xs text-ink" title={voice.clone_ref}>
                          {voice.clone_ref}
                        </span>
                      ) : (
                        <span className="text-xs text-ink-faint">Not set</span>
                      )}
                    </TD>
                    <TD>
                      <ConsentCell voice={voice} />
                    </TD>
                    <TD>
                      <SampleCell voice={voice} />
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
            <CountLine shown={rows.length} total={all.length} noun="voice" />
          </>
        )}
      </Card>
    </div>
  );
}
