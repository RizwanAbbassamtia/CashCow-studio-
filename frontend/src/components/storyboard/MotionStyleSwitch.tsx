import { MOTION_STYLE_DESCRIPTIONS, MOTION_STYLE_LABELS, MOTION_STYLES, type MotionStyle } from "../../types";
import { SegmentedControl } from "../form/SegmentedControl";

export interface MotionStyleSwitchProps {
  /** this video's choice; null follows the channel */
  value: MotionStyle | null;
  /** the channel's motion style (Channel Setup > Images) */
  channelDefault: MotionStyle;
  /** null when the choice equals the channel's default */
  onChange: (next: MotionStyle | null) => void;
}

/**
 * "Motion style: Ken Burns / 2.5D parallax" for one video (docs/TRANSITIONS-V2-CONTRACT.md
 * section 5). It starts on the channel's default; picking the other style overrides it for
 * this video only, picking the default again follows the channel.
 */
export function MotionStyleSwitch({ value, channelDefault, onChange }: MotionStyleSwitchProps) {
  const effective = value ?? channelDefault;
  const overridden = value !== null && value !== channelDefault;
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <span className="text-xs font-medium text-ink-muted">Motion style</span>
      <SegmentedControl
        aria-label="Motion style for this video"
        options={MOTION_STYLES.map((style) => ({ value: style, label: MOTION_STYLE_LABELS[style], description: MOTION_STYLE_DESCRIPTIONS[style] }))}
        value={effective}
        onChange={(next) => onChange(next === channelDefault ? null : next)}
      />
      <span className="text-[11px] text-ink-faint" title={MOTION_STYLE_DESCRIPTIONS[effective]}>
        {overridden ? "This video only" : "Channel default"}
      </span>
    </span>
  );
}
