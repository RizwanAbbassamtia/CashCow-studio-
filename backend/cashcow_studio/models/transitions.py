"""The transition catalogue: ``config/transitions.yaml`` (v2) as Python objects.

docs/TRANSITIONS-V2-CONTRACT.md sections 1 and 4. Every cut between two pictures is one entry
of this catalogue: an ``id`` the render engine knows (``render/transitions/``), a plain-English
``label`` for the editors, the ``pack`` (or packs) it belongs to, an ``energy`` (calm, medium,
high), a default length, the easing curve and the engine parameters.

Channels choose a pack (``images.transition_pack``, default ``cinematic``); the storyboard AI
only picks from that pack and the deterministic fix-ups in ``pipeline/stages/storyboard.py``
keep the cuts varied. Old FFmpeg ``xfade`` names from M3/M4 storyboards and timelines map to
their v2 equivalents through the ``legacy`` table (``fade -> film_dissolve`` and so on);
anything else falls back to ``default``.

:class:`TransitionCatalogueFile` validates the YAML strictly (the tests and the preview tool
use it); :meth:`TransitionCatalog.from_mapping` is the forgiving runtime loader (a broken
entry is skipped, an empty file still gives a working catalogue). The front end reads the same
data from ``frontend/public/transitions/catalogue.json`` (written by
``tools/make_transition_previews.py``) and mirrors the shapes in
``frontend/src/types/storyboard.ts``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

log = logging.getLogger(__name__)

TransitionPack = Literal["cinematic", "clean", "dynamic"]
Energy = Literal["calm", "medium", "high"]
Easing = Literal["ease_in_out_cubic", "ease_out_quart", "linear", "snap"]
Beat = Literal["calm", "reveal", "topic_jump", "section"]
MotionStyle = Literal["ken_burns", "parallax"]

TRANSITION_PACKS: tuple[TransitionPack, ...] = ("cinematic", "clean", "dynamic")
ENERGIES: tuple[Energy, ...] = ("calm", "medium", "high")
EASINGS: tuple[Easing, ...] = ("ease_in_out_cubic", "ease_out_quart", "linear", "snap")
BEATS: tuple[Beat, ...] = ("calm", "reveal", "topic_jump", "section")
MOTION_STYLES: tuple[MotionStyle, ...] = ("ken_burns", "parallax")

DEFAULT_TRANSITION_PACK: TransitionPack = "cinematic"
DEFAULT_MOTION_STYLE: MotionStyle = "ken_burns"
DEFAULT_TRANSITION = "film_dissolve"
DEFAULT_CLOSER = "dip_black"
SHORTS_DURATION_FACTOR = 0.6
PREVIEW_DIR = "transitions"
"""Previews live in ``frontend/public/transitions/`` and are served at ``/transitions/``."""

ID_PATTERN = r"^[a-z][a-z0-9_]*$"
_ID_RE = re.compile(ID_PATTERN)

PACK_LABELS: dict[str, str] = {"cinematic": "Cinematic", "clean": "Clean", "dynamic": "Dynamic"}
ENERGY_LABELS: dict[str, str] = {"calm": "Calm", "medium": "Medium", "high": "High"}
BEAT_LABELS: dict[str, str] = {
    "calm": "calm moments",
    "reveal": "reveals",
    "topic_jump": "jumps to a new idea",
    "section": "the end of a section",
}


def preview_url(transition_id: str) -> str:
    """The animated preview the front end shows for a transition."""
    return f"/{PREVIEW_DIR}/{transition_id}.webp"


def poster_url(transition_id: str) -> str:
    """A still frame from the middle of the transition (shown until the pointer is over it)."""
    return f"/{PREVIEW_DIR}/{transition_id}.poster.webp"


def normalise_name(name: str | None) -> str:
    """``" Film-Dissolve "`` -> ``"film_dissolve"`` (ids and legacy names are compared this way)."""
    return re.sub(r"[\s-]+", "_", str(name or "").strip().lower())


def _as_packs(value: Any) -> list[str]:
    """``all``, ``"cinematic"``, ``"cinematic, clean"`` or a list -> a list of pack ids."""
    if value is None:
        return []
    if isinstance(value, str):
        items = [part.strip().lower() for part in value.split(",") if part.strip()]
    elif isinstance(value, Iterable):
        items = [str(part).strip().lower() for part in value if str(part).strip()]
    else:
        return []
    if "all" in items:
        return list(TRANSITION_PACKS)
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen


# The YAML file (strict) ---------------------------------------------------------------------


class TransitionEntry(BaseModel):
    """One transition of ``config/transitions.yaml``."""

    id: str = Field(pattern=ID_PATTERN, description="the render engine's name for it")
    label: str = Field(min_length=1, max_length=40, description="what the editors see")
    pack: list[TransitionPack] = Field(min_length=1, description="the packs it belongs to")
    energy: Energy
    duration_s: float = Field(gt=0, le=3, description="default length before the pack range")
    easing: Easing = "ease_in_out_cubic"
    params: dict[str, Any] = {}
    preview: str = Field(default="", description="transitions/<id>.webp under frontend/public")
    feel: str = Field(default="", description="one plain line for the AI and the picker")
    beats: list[Beat] = Field(default=[], description="narrative beats it suits")
    min_gap_s: float = Field(default=0.0, ge=0, description="never again within this many s")

    @field_validator("pack", mode="before")
    @classmethod
    def _packs(cls, value: Any) -> list[str]:
        return _as_packs(value)


class TransitionPackInfo(BaseModel):
    """One pack: the look a channel chooses."""

    label: str
    description: str = ""
    duration_s: list[float] = Field(
        default=[0.5, 0.8], min_length=2, max_length=2,
        description="[shortest, longest] default length of a transition in this pack",
    )
    closer: str = Field(default=DEFAULT_CLOSER, description="the transition out of the last scene")

    @field_validator("duration_s")
    @classmethod
    def _ordered(cls, value: list[float]) -> list[float]:
        low, high = float(value[0]), float(value[1])
        if low <= 0 or high < low:
            raise ValueError("duration_s is [shortest, longest] with 0 < shortest <= longest")
        return [low, high]


class TransitionCatalogueFile(BaseModel):
    """The whole of ``config/transitions.yaml`` v2 (and of ``catalogue.json`` without URLs)."""

    version: int = 2
    default: str = DEFAULT_TRANSITION
    default_pack: TransitionPack = DEFAULT_TRANSITION_PACK
    last_scene: str = DEFAULT_CLOSER
    shorts_duration_factor: float = Field(default=SHORTS_DURATION_FACTOR, gt=0, le=1)
    packs: dict[TransitionPack, TransitionPackInfo]
    legacy: dict[str, str] = {}
    transitions: list[TransitionEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> TransitionCatalogueFile:
        ids = [t.id for t in self.transitions]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"transition ids appear twice: {', '.join(duplicates)}")
        known = set(ids)
        for name in (self.default, self.last_scene):
            if name not in known:
                raise ValueError(f"'{name}' is not a transition id")
        for pack, info in self.packs.items():
            if info.closer not in known:
                raise ValueError(f"the closer of pack {pack} ('{info.closer}') is not an id")
            members = [t for t in self.transitions if pack in t.pack]
            if not members:
                raise ValueError(f"pack {pack} has no transitions")
            if not any(t.id == info.closer for t in members):
                raise ValueError(f"the closer of pack {pack} is not in that pack")
        missing_packs = [p for p in TRANSITION_PACKS if p not in self.packs]
        if missing_packs:
            raise ValueError(f"packs missing: {', '.join(missing_packs)}")
        for old, new in self.legacy.items():
            if new not in known:
                raise ValueError(f"legacy name {old} maps to '{new}', which is not an id")
            if old in known:
                raise ValueError(f"legacy name {old} is also a transition id")
        return self


# The runtime catalogue (forgiving) ---------------------------------------------------------


@dataclass(frozen=True)
class Transition:
    """One usable transition. ``type`` is the id (the name the storyboard and timeline store)."""

    type: str
    label: str
    duration_s: float
    packs: tuple[str, ...] = TRANSITION_PACKS
    energy: str = "medium"
    easing: str = "ease_in_out_cubic"
    params: Mapping[str, Any] = field(default_factory=dict, compare=False, hash=False)
    feel: str = ""
    beats: tuple[str, ...] = ()
    min_gap_s: float = 0.0
    preview: str = ""

    @property
    def id(self) -> str:
        return self.type

    def in_pack(self, pack: str | None) -> bool:
        return bool(pack) and pack in self.packs

    def to_json(self) -> dict[str, Any]:
        """The catalogue.json row (the YAML entry plus the preview URLs)."""
        return {
            "id": self.type,
            "label": self.label,
            "pack": list(self.packs),
            "energy": self.energy,
            "duration_s": self.duration_s,
            "easing": self.easing,
            "params": dict(self.params),
            "preview": self.preview or f"{PREVIEW_DIR}/{self.type}.webp",
            "feel": self.feel,
            "beats": list(self.beats),
            "min_gap_s": self.min_gap_s,
            "preview_url": preview_url(self.type),
            "poster_url": poster_url(self.type),
        }


@dataclass(frozen=True)
class PackInfo:
    id: str
    label: str
    description: str = ""
    duration_s: tuple[float, float] = (0.5, 0.8)
    closer: str = DEFAULT_CLOSER

    def to_json(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "description": self.description,
            "duration_s": list(self.duration_s),
            "closer": self.closer,
        }


FALLBACK_TRANSITIONS: tuple[Transition, ...] = (
    Transition(DEFAULT_TRANSITION, "Film dissolve", 0.7, energy="calm", beats=("calm",)),
    Transition(DEFAULT_CLOSER, "Dip to black", 0.8, energy="calm", beats=("section",)),
)


@dataclass(frozen=True)
class TransitionCatalog:
    """The transitions the app may use, with the pack rules and the legacy name map.

    The first four fields are the M2 shape (``types``, ``get``, ``duration_for``,
    ``default``, ``last_scene``) so older callers keep working; everything else is v2.
    """

    transitions: tuple[Transition, ...]
    default: str
    last_scene: str
    shorts_duration_factor: float
    packs: Mapping[str, PackInfo] = field(default_factory=dict, compare=False, hash=False)
    legacy: Mapping[str, str] = field(default_factory=dict, compare=False, hash=False)
    default_pack: str = DEFAULT_TRANSITION_PACK
    version: int = 2

    # Lookups -----------------------------------------------------------------------------

    @property
    def types(self) -> list[str]:
        """Every transition id, in catalogue order."""
        return [t.type for t in self.transitions]

    ids = types

    @property
    def known_names(self) -> list[str]:
        """Ids plus the old xfade names that map to them (what a storyboard may contain)."""
        return self.types + [name for name in self.legacy if name not in self.types]

    def _by_id(self) -> dict[str, Transition]:
        return {t.type: t for t in self.transitions}

    def resolve(self, name: str | None) -> str | None:
        """The v2 id for ``name``: an id, an old xfade name or a loosely written id; else None."""
        if not name:
            return None
        by_id = self._by_id()
        if name in by_id:
            return name
        if name in self.legacy:
            return self.legacy[name]
        clean = normalise_name(name)
        if clean in by_id:
            return clean
        squashed = clean.replace("_", "")
        if squashed in self.legacy:
            return self.legacy[squashed]
        if clean in self.legacy:
            return self.legacy[clean]
        return None

    def resolve_or_default(self, name: str | None) -> str:
        """Like :meth:`resolve`, with the contract's fallback: anything else -> ``default``."""
        return self.resolve(name) or self.default

    def is_legacy(self, name: str | None) -> bool:
        return bool(name) and name not in self._by_id() and self.resolve(name) is not None

    def get(self, name: str | None) -> Transition | None:
        """The transition for an id or an old name (``get("wipeleft")`` is the left wipe)."""
        resolved = self.resolve(name)
        return self._by_id().get(resolved) if resolved else None

    # Packs -------------------------------------------------------------------------------

    def pack_ids(self) -> list[str]:
        return list(self.packs) or list(TRANSITION_PACKS)

    def pack_or_default(self, pack: str | None) -> str:
        if pack and pack in self.packs:
            return pack
        return self.default_pack if self.default_pack in self.packs else (
            next(iter(self.packs), DEFAULT_TRANSITION_PACK)
        )

    def pack_info(self, pack: str | None) -> PackInfo | None:
        return self.packs.get(pack) if pack else None

    def for_pack(self, pack: str | None) -> list[Transition]:
        """The transitions of one pack in catalogue order (every one when the pack is unknown)."""
        if not pack or pack not in self.packs:
            return list(self.transitions)
        return [t for t in self.transitions if pack in t.packs]

    def closer_for(self, pack: str | None) -> str:
        """The transition out of the last scene for a pack (``dip_black`` by default)."""
        info = self.pack_info(pack)
        if info is not None and info.closer in self._by_id():
            return info.closer
        return self.last_scene

    def duration_for(self, name: str | None, fmt: str = "long", pack: str | None = None) -> float:
        """Default length in seconds: the entry's ``duration_s`` brought into the pack's range
        when the transition belongs to ``pack``, times ``shorts_duration_factor`` for Shorts."""
        transition = self.get(name) or self.get(self.default)
        base = transition.duration_s if transition else 0.6
        info = self.pack_info(pack)
        if info is not None and transition is not None and transition.in_pack(pack):
            low, high = info.duration_s
            base = min(max(base, low), high)
        if fmt == "shorts":
            base *= self.shorts_duration_factor
        return round(base, 2)

    def energy_of(self, name: str | None) -> str:
        transition = self.get(name)
        return transition.energy if transition else "medium"

    # Building ----------------------------------------------------------------------------

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> TransitionCatalog:
        """The parsed YAML -> a catalogue. Accepts v2 entries (``id``) and v1 entries
        (``type``); a broken entry is skipped with a log line instead of stopping the app."""
        data = data or {}
        rows: list[Transition] = []
        for raw in data.get("transitions") or []:
            transition = _transition_from(raw)
            if transition is not None and transition.type not in {t.type for t in rows}:
                rows.append(transition)
        if not rows:
            rows = list(FALLBACK_TRANSITIONS)
        ids = {t.type for t in rows}

        packs: dict[str, PackInfo] = {}
        raw_packs = data.get("packs")
        if isinstance(raw_packs, Mapping):
            for pack_id, raw in raw_packs.items():
                info = _pack_from(str(pack_id), raw, ids)
                if info is not None:
                    packs[info.id] = info
        if not packs:
            packs = {
                pack: PackInfo(pack, PACK_LABELS.get(pack, pack.title()), "", (0.5, 0.8),
                               DEFAULT_CLOSER if DEFAULT_CLOSER in ids else rows[0].type)
                for pack in TRANSITION_PACKS
            }

        legacy: dict[str, str] = {}
        raw_legacy = data.get("legacy")
        if isinstance(raw_legacy, Mapping):
            for old, new in raw_legacy.items():
                old_name, new_name = str(old).strip(), str(new).strip()
                if old_name and new_name in ids and old_name not in ids:
                    legacy[old_name] = new_name

        default = str(data.get("default") or "").strip()
        if default not in ids:
            default = DEFAULT_TRANSITION if DEFAULT_TRANSITION in ids else rows[0].type
        default_pack = str(data.get("default_pack") or DEFAULT_TRANSITION_PACK)
        if default_pack not in packs:
            default_pack = next(iter(packs))
        last = str(data.get("last_scene") or "").strip()
        if last not in ids:
            last = packs[default_pack].closer if packs[default_pack].closer in ids else default
        try:
            factor = float(data.get("shorts_duration_factor") or SHORTS_DURATION_FACTOR)
        except (TypeError, ValueError):
            factor = SHORTS_DURATION_FACTOR
        try:
            version = int(data.get("version") or 2)
        except (TypeError, ValueError):
            version = 2
        return cls(
            transitions=tuple(rows),
            default=default,
            last_scene=last,
            shorts_duration_factor=factor if 0 < factor <= 1 else SHORTS_DURATION_FACTOR,
            packs=packs,
            legacy=legacy,
            default_pack=default_pack,
            version=version,
        )

    def to_json(self) -> dict[str, Any]:
        """``catalogue.json``: the YAML as JSON with the preview URLs."""
        return {
            "version": self.version,
            "default": self.default,
            "default_pack": self.default_pack,
            "last_scene": self.last_scene,
            "shorts_duration_factor": self.shorts_duration_factor,
            "packs": {pack_id: info.to_json() for pack_id, info in self.packs.items()},
            "legacy": dict(self.legacy),
            "transitions": [t.to_json() for t in self.transitions],
        }


def _transition_from(raw: Any) -> Transition | None:
    if not isinstance(raw, Mapping):
        return None
    data = dict(raw)
    if "id" not in data and data.get("type"):  # a v1 row: {type, label, duration_s}
        data["id"] = data["type"]
        data.setdefault("pack", "all")
        data.setdefault("energy", "medium")
    data.setdefault("label", str(data.get("id") or "").replace("_", " ").capitalize())
    try:
        entry = TransitionEntry.model_validate(data)
    except ValidationError as exc:
        log.warning("Skipping transition %r in transitions.yaml: %s", data.get("id"), exc)
        return None
    return Transition(
        type=entry.id,
        label=entry.label,
        duration_s=float(entry.duration_s),
        packs=tuple(entry.pack),
        energy=entry.energy,
        easing=entry.easing,
        params=dict(entry.params),
        feel=entry.feel,
        beats=tuple(entry.beats),
        min_gap_s=float(entry.min_gap_s),
        preview=entry.preview,
    )


def _pack_from(pack_id: str, raw: Any, ids: set[str]) -> PackInfo | None:
    if pack_id not in TRANSITION_PACKS or not isinstance(raw, Mapping):
        return None
    try:
        info = TransitionPackInfo.model_validate(
            {"label": PACK_LABELS.get(pack_id, pack_id.title()), **dict(raw)}
        )
    except ValidationError as exc:
        log.warning("Skipping pack %s in transitions.yaml: %s", pack_id, exc)
        return None
    closer = info.closer if info.closer in ids else (DEFAULT_CLOSER if DEFAULT_CLOSER in ids
                                                     else sorted(ids)[0])
    return PackInfo(pack_id, info.label, info.description,
                    (info.duration_s[0], info.duration_s[1]), closer)


def is_valid_id(value: str) -> bool:
    return bool(_ID_RE.match(value or ""))
