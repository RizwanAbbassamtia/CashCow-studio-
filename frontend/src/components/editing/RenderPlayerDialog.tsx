import { renderPresetLabel } from "../../types/timeline";
import { bytesText, durationText } from "../master/format";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import type { RenderRow } from "./useRenders";

/** Plays one finished render in a large dialog. Escape or Close stops it. */
export function RenderPlayerDialog({ render, onClose }: { render: RenderRow | null; onClose: () => void }) {
  const facts = render
    ? [
        renderPresetLabel(render.preset),
        render.width && render.height ? `${render.width} x ${render.height}` : null,
        render.duration_s !== null ? durationText(render.duration_s) : null,
        render.size_bytes !== null ? bytesText(render.size_bytes) : null,
      ].filter(Boolean)
    : [];
  return (
    <Dialog
      open={render !== null}
      title={render ? render.project.title || "Untitled video" : ""}
      description={facts.join(" - ")}
      onClose={onClose}
      className="max-w-4xl!"
      footer={
        <Button variant="secondary" onClick={onClose}>
          Close
        </Button>
      }
    >
      {render?.url ? (
        <video key={render.url} src={render.url} controls autoPlay preload="metadata" className="max-h-[70vh] w-full rounded-md bg-black">
          Your system cannot play this video here. Open the folder and play it with your video player.
        </video>
      ) : (
        <p className="text-[13px] text-ink-muted">This file cannot be played here. Open the folder and play it with your video player.</p>
      )}
    </Dialog>
  );
}
