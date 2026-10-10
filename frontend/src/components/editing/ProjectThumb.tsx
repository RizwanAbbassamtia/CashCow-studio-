import { Film } from "lucide-react";
import { useState } from "react";

import { projectFileUrl } from "../../api/files";
import { cn } from "../../lib/cn";

export interface ProjectThumbProps {
  projectId: string;
  /** files inside the project folder, tried in order until one loads */
  sources: readonly string[];
  className?: string;
}

/**
 * A small 16:9 picture of a video from its own folder. Falls through the sources when a file
 * is missing and ends on a film icon, so a fresh project never shows a broken image.
 * Give it a `key` of the project id so a new project starts from the first source again.
 */
export function ProjectThumb({ projectId, sources, className }: ProjectThumbProps) {
  const [attempt, setAttempt] = useState(0);
  const source = sources[attempt];
  return (
    <div className={cn("relative aspect-video shrink-0 overflow-hidden rounded-md border border-line bg-surface-2", className)}>
      {source ? (
        <img
          src={projectFileUrl(projectId, source)}
          alt=""
          loading="lazy"
          decoding="async"
          draggable={false}
          onError={() => setAttempt((current) => current + 1)}
          className="size-full object-cover"
        />
      ) : (
        <div className="flex size-full items-center justify-center text-ink-faint">
          <Film className="size-4" aria-hidden />
        </div>
      )}
    </div>
  );
}
