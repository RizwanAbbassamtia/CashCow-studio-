"""Edit stage data: ``07_edit/timeline.json`` (:class:`Timeline`), the approval edits and the
review payload. See docs/M3-M4-CONTRACT.md section 3.

``timeline.json`` is the source of truth for the video: the M5 editor edits this file and
the renderer (``render/ffmpeg.py``) reads nothing else. Every time is a second on the voice
clock (``05_voice/timing.json``); every rectangle is ``[x, y, w, h]`` in pixels of the source
image and has the frame's aspect ratio, so the renderer only has to interpolate and zoom.

The front end mirrors these in ``frontend/src/types/timeline.ts``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .transitions import DEFAULT_MOTION_STYLE, MotionStyle

Aspect = Literal["16:9", "9:16"]
PopupAnim = Literal["slide_left", "fade", "pop"]
RenderStatus = Literal["not_rendered", "queued", "rendering", "done", "failed"]

DEFAULT_PRESETS: tuple[str, ...] = ("1080p",)
PROXY_PRESET = "proxy"
TIMELINE_FILE = "timeline.json"


def _check_rect(value: Any) -> list[float]:
    if not isinstance(value, list | tuple) or len(value) != 4:
        raise ValueError("a rectangle is [x, y, w, h]")
    rect = [float(v) for v in value]
    if rect[2] <= 0 or rect[3] <= 0:
        raise ValueError("a rectangle needs a positive width and height")
    return rect


class VoiceTrack(BaseModel):
    path: str = "05_voice/voice.wav"
    """Relative to the project folder (or absolute)."""
    start_s: float = Field(default=0.0, ge=0)


class MusicTrack(BaseModel):
    path: str | None = None
    """Absolute path of the track in the channel's music folder; ``None`` = no music."""
    start_s: float = Field(default=0.0, ge=0, description="offset into the track")
    gain_db: float = -18.0
    duck_db: float = -12.0
    duck_attack_s: float = Field(default=0.3, ge=0)
    duck_release_s: float = Field(default=0.8, ge=0)
    fade_in_s: float = Field(default=1.0, ge=0)
    fade_out_s: float = Field(default=2.0, ge=0)
    license_ok: bool = False


# Motion v2 (docs/TRANSITIONS-V2-CONTRACT.md section 2): how the camera moves over a picture.
# ``ken_burns`` pans and zooms the flat picture (eased); ``parallax`` adds 2.5D depth from the
# depth model and falls back to ``ken_burns`` (saying so in the edit review) without it.
DEFAULT_PARALLAX_STRENGTH = 0.035
_MOTION_STYLE_ALIASES: dict[str, MotionStyle] = {
    "ken_burns": "ken_burns",
    "kenburns": "ken_burns",
    "kb": "ken_burns",
    "pan_and_zoom": "ken_burns",
    "flat": "ken_burns",
    "parallax": "parallax",
    "2.5d": "parallax",
    "2_5d": "parallax",
    "2.5d_parallax": "parallax",
    "depth": "parallax",
}


def normalize_motion_style(value: Any) -> MotionStyle:
    """``"Ken Burns"``, ``"kenburns"`` -> ``ken_burns``; ``"2.5D parallax"`` -> ``parallax``;
    empty or unknown -> :data:`DEFAULT_MOTION_STYLE`."""
    key = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _MOTION_STYLE_ALIASES.get(key, DEFAULT_MOTION_STYLE)


class TimelineMotion(BaseModel):
    preset: str = "hold"
    start_rect: list[float] = Field(description="[x, y, w, h] in source-image pixels")
    end_rect: list[float] = Field(description="[x, y, w, h] in source-image pixels")
    style: MotionStyle = Field(
        default=DEFAULT_MOTION_STYLE,
        description="ken_burns (eased pan and zoom) or parallax (2.5D depth; needs the depth "
        "model, else rendered as ken_burns)",
    )
    strength: float = Field(
        default=DEFAULT_PARALLAX_STRENGTH,
        ge=0.0,
        le=0.2,
        description="parallax only: share of the frame width the nearest things move against "
        "the farthest during a typical pan",
    )

    @field_validator("start_rect", "end_rect", mode="before")
    @classmethod
    def _rects(cls, value: Any) -> list[float]:
        return _check_rect(value)

    @field_validator("style", mode="before")
    @classmethod
    def _style(cls, value: Any) -> MotionStyle:
        return normalize_motion_style(value)


# Transitions v2 (docs/TRANSITIONS-V2-CONTRACT.md section 1): the ids the transition engine
# (render/transitions/) can draw. Older timeline.json files carry FFmpeg xfade names; they are
# mapped to their v2 look when the file is read, and anything unknown becomes a film dissolve.
TRANSITION_IDS: tuple[str, ...] = (
    "film_dissolve",
    "dip_black",
    "dip_white",
    "blur_dissolve",
    "zoom_through",
    "push_left",
    "push_right",
    "push_up",
    "push_down",
    "whip_left",
    "whip_right",
    "whip_up",
    "whip_down",
    "luma_wipe_left",
    "luma_wipe_right",
    "luma_wipe_up",
    "luma_wipe_down",
    "luma_wipe_diag",
    "crosswarp",
    "directional_warp",
    "light_leak_warm",
    "film_burn",
    "glitch_subtle",
    "spin_zoom",
    "morph_blend",
    "cube_left",
    "cube_right",
)
DEFAULT_TRANSITION = "film_dissolve"
LEGACY_TRANSITIONS: dict[str, str] = {
    "fade": "film_dissolve",
    "wipeleft": "luma_wipe_left",
    "slideleft": "push_left",
    "zoomin": "zoom_through",
    "fadeblack": "dip_black",
    "fadewhite": "dip_white",
}


def _catalogue_legacy(name: str) -> str | None:
    """The ``legacy`` table of ``config/transitions.yaml`` (wiperight, slideup ...), read
    lazily so this model stays importable without the config folder."""
    try:
        from ..llm.config import transition_catalog

        return transition_catalog().resolve(name)
    except Exception:  # noqa: BLE001 - the contract's own table below still applies
        return None


def normalize_transition_id(value: Any) -> str:
    """A v2 transition id for ``value``: v2 ids pass through, old xfade names map to their
    v2 look (the contract's table, extended by the catalogue's ``legacy`` table), anything
    else becomes :data:`DEFAULT_TRANSITION`."""
    text = str(value or "").strip()
    if text in TRANSITION_IDS:
        return text
    key = text.lower().replace("-", "_").replace(" ", "_")
    if key in TRANSITION_IDS:
        return key
    if key in LEGACY_TRANSITIONS:
        return LEGACY_TRANSITIONS[key]
    resolved = _catalogue_legacy(text) if text else None
    return resolved if resolved in TRANSITION_IDS else DEFAULT_TRANSITION


class TimelineTransition(BaseModel):
    type: str = DEFAULT_TRANSITION
    """A transition id of the v2 engine (render/transitions/); old xfade names are mapped."""
    duration_s: float = Field(default=0.6, ge=0)

    @field_validator("type", mode="before")
    @classmethod
    def _v2_type(cls, value: Any) -> str:
        return normalize_transition_id(value)


class TimelineScene(BaseModel):
    index: int = Field(ge=0)
    image: str = Field(description="relative to the project folder, e.g. 06_images/scene_01.png")
    start_s: float = Field(ge=0)
    end_s: float = Field(ge=0)
    motion: TimelineMotion
    transition_out: TimelineTransition = TimelineTransition()
    locked: bool = False

    @model_validator(mode="after")
    def _ordered(self) -> TimelineScene:
        if self.end_s <= self.start_s:
            raise ValueError(
                f"scene {self.index + 1} ends ({self.end_s}) before it starts ({self.start_s})"
            )
        return self

    @property
    def duration_s(self) -> float:
        return round(self.end_s - self.start_s, 6)


class TimelinePopup(BaseModel):
    scene: int = Field(ge=0, description="the scene index this popup belongs to")
    text: str = Field(min_length=1)
    style: str = ""
    position: str = "bottom-left"
    start_s: float = Field(ge=0)
    end_s: float = Field(ge=0)
    anim: PopupAnim = "slide_left"
    png: str = Field(default="", description="the rendered PNG, relative to the project folder")

    @model_validator(mode="after")
    def _ordered(self) -> TimelinePopup:
        if self.end_s < self.start_s:
            raise ValueError(f"the popup {self.text!r} ends before it starts")
        return self


class CaptionStyle(BaseModel):
    font: str = "Noto Sans"
    size: int = Field(default=48, ge=8, description="pixels at 1080p; scaled with the frame")
    primary: str = "#FFFFFF"
    outline: int = Field(default=2, ge=0)
    position: str = "bottom"
    max_lines: int = Field(default=2, ge=1)
    max_chars_per_line: int = Field(default=42, ge=4)
    rtl: bool = False
    # Additive to the contract shape: what config/captions.yaml's named styles carry.
    bold: bool = True
    outline_colour: str = "#000000"


class CaptionCue(BaseModel):
    start_s: float = Field(ge=0)
    end_s: float = Field(ge=0)
    text: str


class TimelineCaptions(BaseModel):
    enabled: bool = True
    style: CaptionStyle = CaptionStyle()
    cues: list[CaptionCue] = []


class TimelineClip(BaseModel):
    """An intro or outro video file; ``None`` = none."""

    path: str | None = None


class ProxySize(BaseModel):
    width: int = Field(default=854, gt=0)
    height: int = Field(default=480, gt=0)


class Timeline(BaseModel):
    """``07_edit/timeline.json``."""

    version: int = 1
    project_id: str
    format: Literal["long", "shorts"]
    aspect: Aspect
    fps: int = Field(default=30, ge=1, le=120)
    width: int = Field(gt=0, description="frame size of the first requested preset")
    height: int = Field(gt=0)
    duration_s: float = Field(ge=0, description="end of the last scene")
    voice: VoiceTrack = VoiceTrack()
    music: MusicTrack = MusicTrack()
    scenes: list[TimelineScene]
    popups: list[TimelinePopup] = []
    captions: TimelineCaptions = TimelineCaptions()
    intro: TimelineClip = TimelineClip()
    outro: TimelineClip = TimelineClip()
    presets: list[str] = list(DEFAULT_PRESETS)
    proxy: ProxySize = ProxySize()

    @model_validator(mode="after")
    def _scenes_in_order(self) -> Timeline:
        previous_end = 0.0
        for position, scene in enumerate(self.scenes):
            if scene.index != position:
                raise ValueError(
                    f"scene {position + 1} carries index {scene.index}; scenes must be numbered "
                    "0, 1, 2... in order"
                )
            if scene.start_s + 1e-6 < previous_end:
                raise ValueError(
                    f"scene {position + 1} starts at {scene.start_s} s, before the previous "
                    f"scene ends ({previous_end} s)"
                )
            previous_end = scene.end_s
        if self.scenes and self.duration_s + 1e-6 < previous_end:
            raise ValueError(
                f"duration_s ({self.duration_s}) is shorter than the last scene's end "
                f"({previous_end})"
            )
        return self

    @property
    def total_s(self) -> float:
        return round(self.scenes[-1].end_s, 3) if self.scenes else 0.0

    @property
    def transition_types(self) -> list[str]:
        """Transition types in order of first use."""
        seen: list[str] = []
        for scene in self.scenes:
            if scene.transition_out.type not in seen:
                seen.append(scene.transition_out.type)
        return seen


# Edits -------------------------------------------------------------------------------------


class EditApproveEdits(BaseModel):
    """``edits`` on ``POST /stage/edit/approve`` (and carried by a redo)."""

    presets: list[str] | None = Field(default=None, description="presets to render")
    music_path: str | None = Field(
        default=None, description="a track from the channel's music folder; '' removes music"
    )
    captions_enabled: bool | None = None
    popups_enabled: bool | None = None
    timeline: Timeline | None = Field(default=None, description="full replacement")
    rebuild: bool = Field(default=False, description="build the timeline again from the files")
    override_gates: bool = False

    @property
    def changes_music(self) -> bool:
        return "music_path" in self.model_fields_set

    @property
    def is_empty(self) -> bool:
        return not (
            self.presets is not None
            or self.changes_music
            or self.captions_enabled is not None
            or self.popups_enabled is not None
            or self.timeline is not None
            or self.rebuild
        )


# Review payload ------------------------------------------------------------------------------


class RenderOutput(BaseModel):
    """One render (``07_edit/proxy.mp4`` or ``final_<preset>.mp4``) and where it stands."""

    preset: str
    status: RenderStatus = "not_rendered"
    path: str | None = Field(default=None, description="relative to the project folder")
    play_url: str | None = None
    progress: float | None = None
    error: str | None = None
    duration_s: float | None = None
    size_bytes: int | None = None
    width: int | None = None
    height: int | None = None
    rendered_at: str | None = None
    # Transitions v2 (cached scene clips): what the last render could reuse.
    scene_clips_total: int | None = None
    scene_clips_reused: int | None = None
    reuse_note: str | None = Field(
        default=None, description='plain English, e.g. "Reused 2 of 3 scene clips."'
    )


class MusicTrackInfo(BaseModel):
    path: str
    name: str
    license_ok: bool = False
    duration_s: float | None = None


class TimelineSummary(BaseModel):
    scene_count: int = 0
    duration_s: float = 0.0
    transitions: list[str] = []
    popup_count: int = 0
    caption_cues: int = 0
    fps: int = 30
    width: int = 0
    height: int = 0
    aspect: Aspect | None = None


class EditSceneView(BaseModel):
    index: int
    start_s: float
    end_s: float
    duration_s: float = 0.0
    image_path: str | None = None
    image_url: str | None = None
    transition: str | None = None
    transition_s: float | None = None
    motion: str | None = None
    popup_text: str | None = None
    locked: bool = False


class EditReviewPayload(BaseModel):
    stage: Literal["edit"] = "edit"
    proxy_path: str | None = None
    proxy_url: str | None = None
    timeline: Timeline | None = None
    summary: TimelineSummary = TimelineSummary()
    scenes: list[EditSceneView] = []
    renders: list[RenderOutput] = []
    presets: list[str] = []
    available_presets: list[str] = []
    enable_4k: bool | None = None
    music_tracks: list[MusicTrackInfo] = []
    music_path: str | None = None
    music_license_ok: bool = False
    captions_enabled: bool = True
    popups_enabled: bool = True
    warnings: list[str] = []
    gate_results: list[dict[str, Any]] = []
    render_log_path: str | None = None
    timing_source: str | None = None
    render_notes: list[str] = Field(
        default=[],
        description="plain-English notes on the last renders (clips reused, parallax "
        "fallbacks)",
    )
