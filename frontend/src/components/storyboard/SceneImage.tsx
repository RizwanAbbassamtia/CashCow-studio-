import { Focus, ImageIcon, ImageOff, MoveDown, MoveLeft, MoveRight, MoveUp, Pause, ZoomIn, ZoomOut, type LucideIcon } from "lucide-react";
import { useEffect, useState } from "react";

import { cn } from "../../lib/cn";
import type { MotionPreset, SceneImage as SceneImageModel, StoryboardAspect } from "../../types";
import { Badge, type BadgeTone } from "../ui/Badge";

/** One icon per camera move, for the motion chip on the picture. */
export const MOTION_ICONS: Record<MotionPreset, LucideIcon> = {
  zoom_in: ZoomIn,
  zoom_out: ZoomOut,
  pan_left: MoveLeft,
  pan_right: MoveRight,
  pan_up: MoveUp,
  pan_down: MoveDown,
  hold: Pause,
  slow_push: Focus,
};

const STATUS: Record<SceneImageModel["status"], { label: string; tone: BadgeTone }> = {
  pending: { label: "No image yet", tone: "neutral" },
  generated: { label: "Generated", tone: "info" },
  approved: { label: "Approved", tone: "ok" },
  rejected: { label: "Rejected", tone: "fail" },
};

export interface SceneImageProps {
  image: SceneImageModel;
  /** resolved http(s) URL or null for the placeholder */
  src: string | null;
  aspect: StoryboardAspect;
  /** shown on the placeholder so the card still tells the story */
  prompt: string;
  motionLabel: string;
  /** shows the move's icon next to its name */
  motionPreset?: MotionPreset;
  /** the video uses 2.5D parallax: the chip says so */
  parallax?: boolean;
  className?: string;
}

/** The scene's picture, or a placeholder that previews the prompt until the images stage runs. */
export function SceneImage({ image, src, aspect, prompt, motionLabel, motionPreset, parallax = false, className }: SceneImageProps) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [src]);
  const status = STATUS[image.status];
  const showImage = Boolean(src) && !failed;
  const MotionIcon = motionPreset ? MOTION_ICONS[motionPreset] : null;

  return (
    <div
      className={cn(
        "relative w-full overflow-hidden rounded-md border border-line bg-canvas",
        aspect === "9:16" ? "aspect-[9/16] max-h-72" : "aspect-video",
        className,
      )}
    >
      {showImage ? (
        <img src={src ?? undefined} alt={prompt || "Scene image"} className="size-full object-cover" onError={() => setFailed(true)} loading="lazy" />
      ) : (
        <div className="flex size-full flex-col items-center justify-center gap-2 bg-gradient-to-br from-surface-2 to-canvas p-4 text-center">
          {failed ? <ImageOff className="size-6 text-ink-faint" aria-hidden /> : <ImageIcon className="size-6 text-ink-faint" aria-hidden />}
          <p className="line-clamp-3 text-xs leading-5 text-ink-muted">
            {failed ? "The image file could not be loaded." : prompt ? prompt : "No image prompt yet."}
          </p>
        </div>
      )}
      <div className="pointer-events-none absolute inset-x-2 bottom-2 flex items-end justify-between gap-2">
        <Badge tone={status.tone} className="bg-surface/90 backdrop-blur">
          {status.label}
        </Badge>
        <span
          className="inline-flex items-center gap-1 rounded-full bg-navy/70 px-2 py-0.5 text-[11px] font-medium text-white backdrop-blur"
          title={parallax ? `${motionLabel} with 2.5D parallax (near and far layers move apart)` : `Camera move: ${motionLabel}`}
        >
          {MotionIcon ? <MotionIcon className="size-3" aria-hidden /> : null}
          {motionLabel}
          {parallax ? <span className="rounded bg-white/20 px-1 text-[10px] font-semibold">2.5D</span> : null}
        </span>
      </div>
    </div>
  );
}
