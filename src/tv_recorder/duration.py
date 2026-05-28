from __future__ import annotations

from datetime import datetime, timedelta

from pytimeparse2 import parse


def parse_duration(value: str) -> int:
    text = value.strip()
    if not text:
        raise ValueError("Duration cannot be empty.")

    seconds = parse(text)
    if seconds is None:
        raise ValueError(f"Invalid duration: {value}. Examples: 30m, 2h, 90s, 01:30:00.")

    if seconds <= 0:
        raise ValueError("Duration must be greater than zero.")
    return seconds


def parse_start(value: str, now: datetime | None = None) -> datetime:
    current = now or datetime.now()
    text = value.strip()
    if text.lower() == "now":
        return current
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("Start must be 'now' or an ISO date, ex: 2026-05-24T20:00:00.") from exc


def seconds_until(start: datetime, now: datetime | None = None) -> float:
    current = now or datetime.now()
    delta: timedelta = start - current
    return max(0.0, delta.total_seconds())
