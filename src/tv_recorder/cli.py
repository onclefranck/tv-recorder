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
    log_group = parser.add_mutually_exclusive_group()
    log_group.add_argument("--info", dest="log_level", action="store_const", const="info", default="info", help="Show normal progress and selected HLS URLs.")
    log_group.add_argument("--debug", dest="log_level", action="store_const", const="debug", help="Show discovery steps and ffmpeg output.")
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
            _info(args, f"Waiting until {start.isoformat()} ({int(delay)} s).")
            time.sleep(delay)

        _info(args, f"Detecting stream for {source.display_name}...")
        stream = find_stream(
            source,
            headless=not args.headful,
            timeout_ms=args.timeout_ms,
            debug_log=lambda message: _debug(args, message),
        )
        output_path = build_output_path(args.output_dir, source, start)
        plan = build_ffmpeg_plan(
            stream,
            output_path,
            duration_seconds,
            ffmpeg_path=args.ffmpeg,
            require_ffmpeg=not args.dry_run,
            recording=source.recording,
        )
        _print_stream_urls(args, stream)

        if args.dry_run:
            print("ffmpeg command:")
            print(shlex.join(plan.command))
            return 0

        _info(args, f"Recording to {plan.output_path}")
        exit_code = run_recording(plan, debug=args.log_level == "debug")
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


def _info(args, message: str) -> None:
    if args.log_level in {"info", "debug"}:
        print(message, flush=True)


def _debug(args, message: str) -> None:
    if args.log_level == "debug":
        print(f"DEBUG {message}", flush=True)


def _print_stream_urls(args, stream) -> None:
    discovered = stream.discovered_url or stream.url
    _debug(args, f"Discovered HLS URL: {discovered}")
    if stream.input_urls:
        for index, input_url in enumerate(stream.input_urls, start=1):
            _info(args, f"Recording HLS input {index}: {input_url}")
        return
    _info(args, f"Recording HLS URL: {stream.url}")


if __name__ == "__main__":
    raise SystemExit(main())
