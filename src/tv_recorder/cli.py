from __future__ import annotations

import argparse
import shlex
import sys
import time
from pathlib import Path

from tv_recorder.config import get_source, load_config
from tv_recorder.duration import parse_duration, parse_start, seconds_until
from tv_recorder.recorder import build_ffmpeg_plan, build_output_path, run_recording
from tv_recorder.stream_finder import find_stream


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tv-recorder",
        description="Find an HLS stream with Playwright and record it with ffmpeg.",
    )
    parser.add_argument("source", nargs="?", help="Source defined in YAML, ex: radio-canada.ca")
    parser.add_argument("start", nargs="?", help="'now' or local ISO date, ex: 2026-05-24T20:00:00")
    parser.add_argument("duration", nargs="?", help="Duration, ex: 30m, 2h, 90s, 01:30:00")
    parser.add_argument("--config", type=Path, help="Path to a YAML source config file.")
    parser.add_argument("--list", action="store_true", help="List available sources.")
    parser.add_argument("--output-dir", type=Path, default=Path("recordings"), help="Output directory.")
    parser.add_argument("--headful", action="store_true", help="Show Chromium while detecting the stream.")
    parser.add_argument("--timeout-ms", type=int, default=45_000, help="Playwright timeout in milliseconds.")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="Override the bundled imageio-ffmpeg binary.")
    parser.add_argument("--dry-run", action="store_true", help="Detect the stream and print the ffmpeg command.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
        if args.list:
            list_sources(config)
            return 0

        missing = [name for name in ("source", "start", "duration") if getattr(args, name) is None]
        if missing:
            parser.error("source, start, and duration are required unless --list is used")

        source = get_source(config, args.source)

        start = parse_start(args.start)
        duration_seconds = parse_duration(args.duration)
        delay = seconds_until(start)

        if delay > 0:
            print(f"Waiting until {start.isoformat()} ({int(delay)} s).", flush=True)
            time.sleep(delay)

        print(f"Detecting stream for {source.display_name}...", flush=True)
        stream = find_stream(source, headless=not args.headful, timeout_ms=args.timeout_ms)
        output_path = build_output_path(args.output_dir, source, start)
        plan = build_ffmpeg_plan(
            stream,
            output_path,
            duration_seconds,
            ffmpeg_path=args.ffmpeg,
            require_ffmpeg=not args.dry_run,
            recording=source.recording,
        )

        if args.dry_run:
            print(f"Detected stream: {stream.url}")
            print("ffmpeg command:")
            print(shlex.join(plan.command))
            return 0

        print(f"Recording to {plan.output_path}", flush=True)
        exit_code = run_recording(plan)
        if exit_code != 0:
            print(f"ffmpeg exited with code {exit_code}.", file=sys.stderr)
        return exit_code
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def list_sources(config: dict) -> None:
    sources = config.get("sources") or {}
    if not sources:
        print("No sources available.")
        return

    width = max(len(key) for key in sources)
    for key in sorted(sources):
        display_name = sources[key].get("display_name") or key
        print(f"{key.ljust(width)}  {display_name}")


if __name__ == "__main__":
    raise SystemExit(main())
