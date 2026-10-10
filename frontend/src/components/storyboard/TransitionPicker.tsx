import { Check, ChevronDown, Star, X } from "lucide-react";
import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import { createPortal } from "react-dom";

import { cn } from "../../lib/cn";
import {
  TRANSITION_BEAT_LABELS,
  TRANSITION_ENERGY_LABELS,
  TRANSITION_PACK_LABELS,
  TRANSITION_PACKS,
  findTransition,
  transitionDuration,
  transitionsInPack,
  type ProjectFormat,
  type TransitionCatalogue,
  type TransitionCatalogueEntry,
  type TransitionEnergy,
  type TransitionOption,
  type TransitionPack,
} from "../../types";
import { Spinner } from "../ui/Spinner";
import { transitionName, useTransitionCatalogue } from "./transitionCatalogue";

/** What a picker reads and writes: a scene's `transition_out` (type and length). */
export interface TransitionValue {
  type: string;
  duration_s: number;
}

export interface TransitionPickerProps {
  value: TransitionValue;
  onChange: (next: TransitionValue) => void;
  /** Shorts get shorter default lengths */
  format: ProjectFormat;
  /** the channel's pack: the tab that opens first, marked with a star */
  channelPack?: TransitionPack;
  /** names the control for screen readers, e.g. "Scene 3 transition" */
  label: string;
  /** the payload's list, used when catalogue.json cannot be loaded */
  fallbackOptions?: ReadonlyArray<TransitionOption>;
  /** a picked transition never runs longer than this (seconds) */
  maxDurationS?: number;
  /** "field": a select-like button; "compact": a small tile for the edit strip */
  variant?: "field" | "compact";
  disabled?: boolean;
  invalid?: boolean;
  /** outline the trigger (an unsaved change) */
  changed?: boolean;
  className?: string;
}

const PANEL_WIDTH = 588;
const PANEL_MAX_HEIGHT = 520;
const GAP = 6;
const ENERGY_RANK: Record<TransitionEnergy, number> = { calm: 0, medium: 1, high: 2 };

const ENERGY_TONE: Record<TransitionEnergy, string> = {
  calm: "border-ok/30 bg-ok/12 text-ok",
  medium: "border-info/30 bg-info/12 text-info",
  high: "border-accent/40 bg-accent/15 text-accent-text",
};

const ENERGY_DOT: Record<TransitionEnergy, string> = {
  calm: "bg-ok",
  medium: "bg-info",
  high: "bg-accent",
};

export function EnergyChip({ energy, className }: { energy: TransitionEnergy; className?: string }) {
  return (
    <span className={cn("inline-flex items-center rounded-full border px-1.5 text-[10px] font-semibold uppercase leading-4 tracking-wide", ENERGY_TONE[energy], className)}>
      {TRANSITION_ENERGY_LABELS[energy]}
    </span>
  );
}

function usePrefersReducedMotion(): boolean {
  const query = "(prefers-reduced-motion: reduce)";
  const [reduced, setReduced] = useState(() => typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia(query).matches);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const media = window.matchMedia(query);
    const update = () => setReduced(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return reduced;
}

/**
 * The preview of one transition: a still from the middle of it, and the animation while
 * `playing` (pointer over or keyboard focus on the card) unless the system asks for reduced
 * motion.
 */
export function TransitionPreview({ entry, playing = false, className }: { entry: TransitionCatalogueEntry; playing?: boolean; className?: string }) {
  const reduced = usePrefersReducedMotion();
  const src = playing && !reduced ? entry.preview_url : entry.poster_url;
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  if (failedSrc === src) {
    return (
      <span aria-hidden className={cn("flex items-center justify-center bg-gradient-to-br from-surface-2 to-canvas text-[10px] text-ink-faint", className)}>
        {entry.label}
      </span>
    );
  }
  return <img src={src} alt="" aria-hidden draggable={false} className={cn("object-cover", className)} onError={() => setFailedSrc(src)} />;
}

function packOrder(channelPack: TransitionPack | undefined): TransitionPack[] {
  return channelPack ? [channelPack, ...TRANSITION_PACKS.filter((pack) => pack !== channelPack)] : [...TRANSITION_PACKS];
}

function sortedForPack(catalogue: TransitionCatalogue, pack: TransitionPack): TransitionCatalogueEntry[] {
  const order = new Map(catalogue.transitions.map((entry, index) => [entry.id, index]));
  return transitionsInPack(catalogue, pack).sort((a, b) => ENERGY_RANK[a.energy] - ENERGY_RANK[b.energy] || (order.get(a.id) ?? 0) - (order.get(b.id) ?? 0));
}

function formatLength(seconds: number): string {
  return `${seconds.toFixed(seconds < 1 ? 2 : 1).replace(/0$/, "")} s`;
}

/** Where the popover goes: under the trigger when it fits, else above; never off screen. */
function placePanel(trigger: HTMLElement): CSSProperties {
  const rect = trigger.getBoundingClientRect();
  const viewportW = window.innerWidth;
  const viewportH = window.innerHeight;
  const width = Math.min(PANEL_WIDTH, viewportW - 24);
  const left = Math.min(Math.max(12, rect.left + rect.width / 2 - width / 2), viewportW - width - 12);
  const below = viewportH - rect.bottom - GAP - 12;
  const above = rect.top - GAP - 12;
  const placeBelow = below >= Math.min(PANEL_MAX_HEIGHT, 380) || below >= above;
  const maxHeight = Math.max(220, Math.min(PANEL_MAX_HEIGHT, placeBelow ? below : above));
  return placeBelow
    ? { position: "fixed", top: rect.bottom + GAP, left, width, maxHeight }
    : { position: "fixed", bottom: viewportH - rect.top + GAP, left, width, maxHeight };
}

/**
 * Transition picker (docs/TRANSITIONS-V2-CONTRACT.md section 5): a button that opens a
 * popover grid of animated previews from `/transitions/catalogue.json`, one tab per pack
 * (the channel's pack first), each card with its name, energy and default length. Hovering
 * or focusing a card plays it. Keyboard: arrows move through the grid, Enter picks, Escape
 * closes; Left and Right on the tabs switch packs. Picking writes the type and the default
 * length for this video.
 */
export function TransitionPicker({
  value,
  onChange,
  format,
  channelPack,
  label,
  fallbackOptions = [],
  maxDurationS,
  variant = "field",
  disabled = false,
  invalid = false,
  changed = false,
  className,
}: TransitionPickerProps) {
  const catalogueQuery = useTransitionCatalogue();
  const catalogue = catalogueQuery.data ?? null;
  const current = findTransition(catalogue, value.type);
  const currentName = transitionName(value.type, catalogue, fallbackOptions);
  const known = Boolean(current) || fallbackOptions.some((option) => option.type === value.type);
  // Only call a name unknown once the catalogue has answered (no red flash while it loads).
  const unknown = !known && !catalogueQuery.isPending;

  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const close = useCallback((refocus: boolean) => {
    setOpen(false);
    if (refocus) window.requestAnimationFrame(() => triggerRef.current?.focus());
  }, []);

  const pick = (type: string, defaultSeconds: number) => {
    const seconds = typeof maxDurationS === "number" && maxDurationS > 0 ? Math.min(defaultSeconds, maxDurationS) : defaultSeconds;
    onChange({ type, duration_s: Math.round(seconds * 100) / 100 });
    close(true);
  };

  const title = `${currentName}, ${formatLength(value.duration_s)}. Click to choose another transition.`;

  return (
    <>
      {variant === "compact" ? (
        <button
          ref={triggerRef}
          type="button"
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-label={`${label}: ${currentName}`}
          title={title}
          disabled={disabled}
          onClick={() => setOpen((state) => !state)}
          className={cn(
            "flex w-16 flex-col items-center gap-1 rounded-md border p-1 text-center transition-colors",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 disabled:cursor-not-allowed disabled:opacity-60",
            changed ? "border-accent bg-accent/10" : invalid || unknown ? "border-fail/60 bg-fail/5" : "border-line bg-surface hover:border-line-strong",
            className,
          )}
        >
          <span className="relative block aspect-video w-full overflow-hidden rounded-sm bg-surface-2">
            {current ? <TransitionPreview entry={current} className="size-full" /> : null}
          </span>
          <span className="line-clamp-2 text-[10px] font-medium leading-3 text-ink-muted">{currentName}</span>
        </button>
      ) : (
        <button
          ref={triggerRef}
          type="button"
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-label={`${label}: ${currentName}`}
          aria-invalid={invalid || unknown || undefined}
          title={title}
          disabled={disabled}
          onClick={() => setOpen((state) => !state)}
          className={cn(
            "flex h-8 w-full min-w-0 items-center gap-2 rounded-md border bg-canvas pl-1 pr-2 text-left text-[13px] text-ink transition-colors",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 disabled:cursor-not-allowed disabled:opacity-60",
            invalid || unknown ? "border-fail/70" : changed ? "border-accent" : "border-line hover:border-line-strong",
            className,
          )}
        >
          <span className="relative block h-6 w-[42px] shrink-0 overflow-hidden rounded-sm bg-surface-2">
            {current ? <TransitionPreview entry={current} className="size-full" /> : null}
          </span>
          <span className="min-w-0 flex-1 truncate">
            {currentName}
            {unknown ? <span className="text-fail"> (unknown)</span> : null}
          </span>
          {current ? <span aria-hidden className={cn("size-2 shrink-0 rounded-full", ENERGY_DOT[current.energy])} title={`${TRANSITION_ENERGY_LABELS[current.energy]} energy`} /> : null}
          <ChevronDown aria-hidden className="size-4 shrink-0 text-ink-faint" />
        </button>
      )}
      {open ? (
        <TransitionPopover
          triggerRef={triggerRef}
          catalogue={catalogue}
          loading={catalogueQuery.isPending}
          current={current}
          currentType={value.type}
          format={format}
          channelPack={channelPack}
          label={label}
          fallbackOptions={fallbackOptions}
          maxDurationS={maxDurationS}
          onPick={pick}
          onClose={close}
        />
      ) : null}
    </>
  );
}

interface TransitionPopoverProps {
  triggerRef: { current: HTMLButtonElement | null };
  catalogue: TransitionCatalogue | null;
  loading: boolean;
  current: TransitionCatalogueEntry | null;
  currentType: string;
  format: ProjectFormat;
  channelPack?: TransitionPack;
  label: string;
  fallbackOptions: ReadonlyArray<TransitionOption>;
  maxDurationS?: number;
  onPick: (type: string, defaultSeconds: number) => void;
  onClose: (refocus: boolean) => void;
}

function TransitionPopover({
  triggerRef,
  catalogue,
  loading,
  current,
  currentType,
  format,
  channelPack,
  label,
  fallbackOptions,
  maxDurationS,
  onPick,
  onClose,
}: TransitionPopoverProps) {
  const baseId = useId();
  const panelRef = useRef<HTMLDivElement | null>(null);
  const optionRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const [style, setStyle] = useState<CSSProperties>({ position: "fixed", visibility: "hidden" });

  const packs = packOrder(channelPack);
  const [tab, setTab] = useState<TransitionPack>(() => {
    if (current && channelPack && current.pack.includes(channelPack)) return channelPack;
    return current?.pack[0] ?? channelPack ?? packs[0]!;
  });
  const entries = useMemo(() => (catalogue ? sortedForPack(catalogue, tab) : []), [catalogue, tab]);
  const initialIndex = Math.max(0, entries.findIndex((entry) => entry.id === current?.id));
  const [active, setActive] = useState(initialIndex);
  const [hovered, setHovered] = useState<string | null>(null);
  const [focused, setFocused] = useState<number | null>(null);
  const columns = typeof style.width === "number" ? (style.width >= 520 ? 3 : style.width >= 360 ? 2 : 1) : 3;

  // Place the panel next to the trigger, and keep it there while the page scrolls or resizes.
  useLayoutEffect(() => {
    const trigger = triggerRef.current;
    if (!trigger) return;
    const update = () => setStyle(placePanel(trigger));
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [triggerRef]);

  // Close on a click outside the panel and the trigger.
  useEffect(() => {
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as Node | null;
      if (!target) return;
      if (panelRef.current?.contains(target) || triggerRef.current?.contains(target)) return;
      onClose(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [onClose, triggerRef]);

  // A new tab starts on its selected card (or the first one).
  useEffect(() => {
    const index = entries.findIndex((entry) => entry.id === current?.id);
    setActive(index >= 0 ? index : 0);
  }, [entries, current?.id]);

  // Focus the active card when the panel opens.
  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      const target = optionRefs.current[initialIndex] ?? optionRefs.current[0] ?? tabRefs.current[0];
      target?.focus({ preventScroll: true });
      target?.scrollIntoView({ block: "nearest" });
    });
    return () => window.cancelAnimationFrame(frame);
    // Only when the panel first shows (or the catalogue arrives), not on every tab change.
  }, [catalogue !== null]); // eslint-disable-line react-hooks/exhaustive-deps

  const moveTo = (index: number) => {
    if (entries.length === 0) return;
    const next = Math.min(Math.max(index, 0), entries.length - 1);
    setActive(next);
    const target = optionRefs.current[next];
    target?.focus();
    target?.scrollIntoView({ block: "nearest" });
  };

  const onGridKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    const keys: Record<string, number> = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: columns, ArrowUp: -columns };
    if (event.key in keys) {
      event.preventDefault();
      moveTo(active + keys[event.key]!);
    } else if (event.key === "Home") {
      event.preventDefault();
      moveTo(0);
    } else if (event.key === "End") {
      event.preventDefault();
      moveTo(entries.length - 1);
    }
  };

  const onTabKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>, index: number) => {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    event.preventDefault();
    const next = (index + (event.key === "ArrowRight" ? 1 : -1) + packs.length) % packs.length;
    setTab(packs[next]!);
    tabRefs.current[next]?.focus();
  };

  const onPanelKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      onClose(true);
    }
  };

  const focusEntry = entries.find((entry) => entry.id === hovered) ?? entries[active] ?? current;
  const standIn = catalogue?.transitions.some((entry) => entry.preview_engine === "stand-in") ?? false;
  const lengthFor = (entry: TransitionCatalogueEntry) => {
    const seconds = catalogue ? transitionDuration(catalogue, entry, tab, format) : entry.duration_s;
    return typeof maxDurationS === "number" && maxDurationS > 0 ? Math.min(seconds, maxDurationS) : seconds;
  };
  const gridId = `${baseId}-grid`;

  return createPortal(
    <div
      ref={panelRef}
      role="dialog"
      aria-label={`Choose the ${label.toLowerCase()}`}
      style={style}
      onKeyDown={onPanelKeyDown}
      className="z-50 flex flex-col overflow-hidden rounded-card border border-line bg-surface shadow-card"
    >
      <div className="flex items-center justify-between gap-3 border-b border-line px-3 py-2">
        <div className="min-w-0">
          <p className="text-[13px] font-semibold text-ink">Transition</p>
          <p className="truncate text-[11px] text-ink-muted">
            Now: {current?.label ?? currentType}
            {current ? ` (${TRANSITION_ENERGY_LABELS[current.energy].toLowerCase()} energy)` : ""}
          </p>
        </div>
        <button
          type="button"
          aria-label="Close"
          onClick={() => onClose(true)}
          className="rounded p-1 text-ink-muted hover:bg-surface-2 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60"
        >
          <X className="size-4" aria-hidden />
        </button>
      </div>

      {catalogue ? (
        <>
          <div role="tablist" aria-label="Transition packs" className="flex gap-1 border-b border-line px-2 pt-2">
            {packs.map((pack, index) => {
              const selected = pack === tab;
              return (
                <button
                  key={pack}
                  ref={(node) => {
                    tabRefs.current[index] = node;
                  }}
                  type="button"
                  role="tab"
                  id={`${baseId}-tab-${pack}`}
                  aria-selected={selected}
                  aria-controls={gridId}
                  tabIndex={selected ? 0 : -1}
                  onClick={() => setTab(pack)}
                  onKeyDown={(event) => onTabKeyDown(event, index)}
                  title={catalogue.packs[pack].description}
                  className={cn(
                    "-mb-px inline-flex items-center gap-1 rounded-t-md border border-b-0 px-3 py-1.5 text-xs font-medium transition-colors",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60",
                    selected ? "border-line bg-surface text-ink" : "border-transparent text-ink-muted hover:text-ink",
                  )}
                >
                  {pack === channelPack ? <Star className="size-3 fill-accent text-accent" aria-hidden /> : null}
                  {catalogue.packs[pack].label || TRANSITION_PACK_LABELS[pack]}
                  {pack === channelPack ? <span className="sr-only"> (this channel's pack)</span> : null}
                </button>
              );
            })}
          </div>
          <div
            id={gridId}
            role="listbox"
            aria-labelledby={`${baseId}-tab-${tab}`}
            onKeyDown={onGridKeyDown}
            className={cn("grid min-h-0 flex-1 gap-2 overflow-y-auto p-2", columns === 3 ? "grid-cols-3" : columns === 2 ? "grid-cols-2" : "grid-cols-1")}
          >
            {entries.map((entry, index) => {
              const selected = entry.id === current?.id;
              const playing = hovered === entry.id || (hovered === null && focused === index);
              return (
                <button
                  key={entry.id}
                  ref={(node) => {
                    optionRefs.current[index] = node;
                  }}
                  type="button"
                  role="option"
                  aria-selected={selected}
                  tabIndex={index === active ? 0 : -1}
                  onClick={() => onPick(entry.id, lengthFor(entry))}
                  onFocus={() => {
                    setActive(index);
                    setFocused(index);
                  }}
                  onBlur={() => setFocused((value) => (value === index ? null : value))}
                  onMouseEnter={() => setHovered(entry.id)}
                  onMouseLeave={() => setHovered((id) => (id === entry.id ? null : id))}
                  className={cn(
                    "group relative flex flex-col gap-1 rounded-md border p-1 text-left transition-colors",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60",
                    selected ? "border-accent bg-accent/10" : "border-line hover:border-line-strong hover:bg-surface-2/50",
                  )}
                >
                  <span className="relative block aspect-video w-full overflow-hidden rounded-sm bg-surface-2">
                    <TransitionPreview entry={entry} playing={playing} className="size-full" />
                    {selected ? (
                      <span className="absolute right-1 top-1 rounded-full bg-accent p-0.5 text-accent-ink" aria-hidden>
                        <Check className="size-3" />
                      </span>
                    ) : null}
                  </span>
                  <span className="flex items-center justify-between gap-1 px-0.5">
                    <span className="truncate text-xs font-medium text-ink">{entry.label}</span>
                    <span className="shrink-0 text-[11px] tabular-nums text-ink-faint">{formatLength(lengthFor(entry))}</span>
                  </span>
                  <span className="px-0.5 pb-0.5">
                    <EnergyChip energy={entry.energy} />
                  </span>
                </button>
              );
            })}
          </div>
          {focusEntry ? (
            <div className="border-t border-line px-3 py-2 text-xs leading-5 text-ink-muted" aria-live="polite">
              <span className="font-medium text-ink">{focusEntry.label}.</span> {focusEntry.feel}
              {focusEntry.beats.length > 0 ? ` Suits ${focusEntry.beats.map((beat) => TRANSITION_BEAT_LABELS[beat] ?? beat).join(", ")}.` : ""}
              {channelPack && !focusEntry.pack.includes(channelPack)
                ? ` Not in this channel's ${TRANSITION_PACK_LABELS[channelPack]} pack: the AI will not choose it, but you can.`
                : ""}
              {standIn ? <span className="block text-ink-faint">The previews are simple placeholders for now and all look like a dissolve.</span> : null}
            </div>
          ) : null}
        </>
      ) : loading ? (
        <div className="flex items-center justify-center gap-2 p-6 text-xs text-ink-muted" role="status">
          <Spinner className="size-4" />
          Loading the transitions...
        </div>
      ) : (
        <div className="flex flex-col gap-2 p-3">
          <p className="text-xs text-ink-muted">The previews could not be loaded. Pick a transition by name:</p>
          <div role="listbox" aria-label="Transitions" className="grid max-h-80 grid-cols-2 gap-1.5 overflow-y-auto">
            {fallbackOptions.map((option) => (
              <button
                key={option.type}
                type="button"
                role="option"
                aria-selected={option.type === currentType}
                onClick={() => onPick(option.type, option.duration_s)}
                className={cn(
                  "flex items-center justify-between gap-2 rounded-md border px-2 py-1.5 text-left text-xs transition-colors",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60",
                  option.type === currentType ? "border-accent bg-accent/10 text-ink" : "border-line text-ink hover:border-line-strong",
                )}
              >
                <span className="truncate">{option.label}</span>
                <span className="flex shrink-0 items-center gap-1.5 text-ink-faint">
                  {option.energy ? <EnergyChip energy={option.energy} /> : null}
                  {formatLength(option.duration_s)}
                </span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>,
    document.body,
  );
}
