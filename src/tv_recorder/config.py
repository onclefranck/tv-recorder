from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class SourceConfig:
    key: str
    display_name: str
    start_url: str
    stream_url_pattern: str = r"\.m3u8(\?|$)"
    stream_request_urls: tuple[str, ...] = ()
    stream_response_url_patterns: tuple[str, ...] = ()
    stream_response_json_keys: tuple[str, ...] = ()
    stream_url_reject_patterns: tuple[str, ...] = ()
    output_extension: str = "mp4"
    recording: dict[str, Any] | None = None
    steps: tuple[dict[str, Any], ...] = ()
    user_agent: str | None = None


def load_config(path: Path | None = None) -> dict[str, Any]:
    if path:
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}

    default_text = resources.files("tv_recorder").joinpath("defaults.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(default_text) or {}


def get_source(config: dict[str, Any], source_key: str) -> SourceConfig:
    sources = config.get("sources") or {}
    raw = sources.get(source_key)
    if not raw:
        available = ", ".join(sorted(sources)) or "none"
        raise KeyError(f"Unknown source: {source_key}. Available sources: {available}.")

    if not raw.get("start_url"):
        raise ValueError(f"Source {source_key} must define start_url.")

    return SourceConfig(
        key=source_key,
        display_name=raw.get("display_name") or source_key,
        start_url=raw["start_url"],
        stream_url_pattern=raw.get("stream_url_pattern") or r"\.m3u8(\?|$)",
        stream_request_urls=tuple(raw.get("stream_request_urls") or ()),
        stream_response_url_patterns=tuple(raw.get("stream_response_url_patterns") or ()),
        stream_response_json_keys=tuple(raw.get("stream_response_json_keys") or ()),
        stream_url_reject_patterns=tuple(raw.get("stream_url_reject_patterns") or ()),
        output_extension=raw.get("output_extension") or "mp4",
        recording=raw.get("recording"),
        steps=tuple(raw.get("steps") or ()),
        user_agent=raw.get("user_agent"),
    )
