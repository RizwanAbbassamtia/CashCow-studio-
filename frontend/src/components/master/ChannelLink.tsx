import { Link } from "react-router";

import { cn } from "../../lib/cn";
import { channelPath } from "./routes";

/** A channel's name linking to its setup page; plain text when there is no slug. */
export function ChannelLink({ slug, name, className }: { slug: string; name?: string | null; className?: string }) {
  const label = name?.trim() || slug || "Unknown channel";
  if (!slug) return <span className={cn("text-ink-muted", className)}>{label}</span>;
  return (
    <Link
      to={channelPath(slug)}
      onClick={(event) => event.stopPropagation()}
      className={cn("font-medium text-ink hover:text-accent-text hover:underline", className)}
    >
      {label}
    </Link>
  );
}

/** Looks up channel names by slug, falling back to the slug itself. */
export function channelNamer(channels: ReadonlyArray<{ slug: string; name: string }>): (slug: string) => string {
  const names = new Map(channels.map((channel) => [channel.slug, channel.name]));
  return (slug: string) => names.get(slug) ?? slug;
}
