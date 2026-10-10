"""``config/llm.yaml`` (and the other YAML files in ``config/``) as Python objects.

The ``config`` folder sits next to ``backend`` in the repo; ``CCS_CONFIG_DIR`` overrides it.
Prices are USD per million tokens and the cost of a call is computed here from the usage the
API reports, so the numbers in the ``llm_calls`` table and on the project page agree.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

# Re-exported: the storyboard stage and older callers import these from here.
from ..models.transitions import Transition as Transition
from ..models.transitions import TransitionCatalog as TransitionCatalog

LLM_FILE = "llm.yaml"
VOICE_FILE = "voice.yaml"
TRANSITIONS_FILE = "transitions.yaml"

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_LIGHT_MODEL = "claude-sonnet-5-5"
DEFAULT_EFFORT = "medium"
DEFAULT_MAX_TOKENS = 16000
DEFAULT_WPM = 150

EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")


class ConfigError(Exception):
    """A YAML file under ``config/`` is missing or malformed (plain-English message)."""


def config_dir() -> Path:
    override = os.environ.get("CCS_CONFIG_DIR", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "config"


def load_yaml(name: str) -> dict[str, Any]:
    """Parse ``config/<name>``; a missing file is an empty mapping, a broken one an error."""
    path = config_dir() / name
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"The settings file {path} could not be read: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"The settings file {path} must contain a mapping at the top level.")
    return data


@dataclass(frozen=True)
class ModelPrice:
    """USD per million tokens."""

    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write_5m: float = 0.0
    cache_write_1h: float = 0.0

    @classmethod
    def from_mapping(cls, data: Any) -> ModelPrice:
        if not isinstance(data, dict):
            return cls()
        write = data.get("cache_write", 0.0)
        return cls(
            input=float(data.get("input", 0.0) or 0.0),
            output=float(data.get("output", 0.0) or 0.0),
            cache_read=float(data.get("cache_read", 0.0) or 0.0),
            cache_write_5m=float(data.get("cache_write_5m", write) or 0.0),
            cache_write_1h=float(data.get("cache_write_1h", write) or 0.0),
        )


def compute_cost(
    price: ModelPrice,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_5m_tokens: int = 0,
    cache_write_1h_tokens: int = 0,
) -> float:
    """Cost in USD of one call, rounded to 6 decimals. Unknown models cost 0."""
    per_million = (
        input_tokens * price.input
        + output_tokens * price.output
        + cache_read_tokens * price.cache_read
        + cache_write_5m_tokens * price.cache_write_5m
        + cache_write_1h_tokens * price.cache_write_1h
    )
    return round(per_million / 1_000_000, 6)


@dataclass
class LLMConfig:
    provider: str = "anthropic"
    models: dict[str, str] = field(default_factory=dict)
    effort: dict[str, str] = field(default_factory=dict)
    max_tokens: dict[str, int] = field(default_factory=dict)
    stream_tasks: set[str] = field(default_factory=set)
    fallback_enabled: bool = True
    fallback_beta: str = "server-side-fallback-2026-07-01"
    fallback_mode: str = "default"
    cache_ttl: str = "1h"
    prices: dict[str, ModelPrice] = field(default_factory=dict)

    @classmethod
    def load(cls, overrides: dict[str, Any] | None = None) -> LLMConfig:
        """``config/llm.yaml`` merged with per-user overrides (``settings.json`` key ``llm``).

        Overrides use the same keys as the YAML file (``models``, ``effort``, ``max_tokens``);
        anything else in them is ignored.
        """
        data = load_yaml(LLM_FILE)
        for key in ("models", "effort", "max_tokens"):
            extra = (overrides or {}).get(key)
            if isinstance(extra, dict):
                merged = dict(data.get(key) or {})
                merged.update({k: v for k, v in extra.items() if v not in (None, "")})
                data[key] = merged
        fallback = data.get("fallback") or {}
        prices_raw = data.get("prices_usd_per_million") or {}
        config = cls(
            provider=str(data.get("provider") or "anthropic"),
            models={str(k): str(v) for k, v in (data.get("models") or {}).items()},
            effort={str(k): str(v) for k, v in (data.get("effort") or {}).items()},
            max_tokens={str(k): int(v) for k, v in (data.get("max_tokens") or {}).items()},
            stream_tasks={str(t) for t in (data.get("stream_tasks") or [])},
            fallback_enabled=bool(fallback.get("enabled", True)),
            fallback_beta=str(fallback.get("beta") or "server-side-fallback-2026-07-01"),
            fallback_mode=str(fallback.get("mode") or "default"),
            cache_ttl=str(data.get("cache_ttl") or "1h"),
            prices={str(k): ModelPrice.from_mapping(v) for k, v in prices_raw.items()},
        )
        for task, level in list(config.effort.items()):
            if level not in EFFORT_LEVELS:
                config.effort[task] = DEFAULT_EFFORT
        return config

    def model_for(self, task: str) -> str:
        return self.models.get(task) or self.models.get("default") or DEFAULT_MODEL

    def effort_for(self, task: str) -> str:
        return self.effort.get(task) or self.effort.get("default") or DEFAULT_EFFORT

    def max_tokens_for(self, task: str) -> int:
        return self.max_tokens.get(task) or self.max_tokens.get("default") or DEFAULT_MAX_TOKENS

    def streams(self, task: str) -> bool:
        return task in self.stream_tasks

    def price_for(self, model: str) -> ModelPrice:
        return self.prices.get(model, ModelPrice())


# Speaking rate -----------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _voice_config() -> dict[str, Any]:
    return load_yaml(VOICE_FILE)


def speaking_rate_wpm(language: str | None, override: int | None = None) -> int:
    """Words per minute for a language from ``config/voice.yaml`` (default 150).

    ``override`` is the rate chosen in Settings > Models and providers (``settings.voice
    .speaking_rate_wpm``); when set it applies to every language.
    """
    if override:
        try:
            return max(1, int(override))
        except (TypeError, ValueError):
            pass
    data = _voice_config().get("speaking_rate") or {}
    by_language = data.get("by_language") or {}
    default = int(data.get("default_wpm") or DEFAULT_WPM)
    if language and language in by_language:
        try:
            return int(by_language[language])
        except (TypeError, ValueError):
            return default
    return default


# Transitions -------------------------------------------------------------------------------
# config/transitions.yaml v2 (packs, energy, easing, engine params, the legacy xfade names);
# the shapes and the lookups live in models/transitions.py, shared with the preview tool.


@lru_cache(maxsize=1)
def transition_catalog() -> TransitionCatalog:
    """``config/transitions.yaml`` as a :class:`TransitionCatalog` (cached; see
    :func:`clear_caches`). A missing or half-broken file still gives a working catalogue."""
    return TransitionCatalog.from_mapping(load_yaml(TRANSITIONS_FILE))


def clear_caches() -> None:
    """Forget cached YAML (tests that point ``CCS_CONFIG_DIR`` somewhere else call this)."""
    _voice_config.cache_clear()
    transition_catalog.cache_clear()
