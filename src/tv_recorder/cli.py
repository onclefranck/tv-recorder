from __future__ import annotations

import shlex
import time
from pathlib import Path

import click

from tv_recorder import recorder
from tv_recorder.comskip import build_comskip_plan, cut_commercials, run_comskip
from tv_recorder.config import get_source, load_config
from tv_recorder.duration import parse_duration, parse_start, seconds_until
from tv_recorder.recorder import build_ffmpeg_plan, build_output_path, run_recording
from tv_recorder.stream_finder import find_stream


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument("source", required=False)
@click.argument("start", required=False)
@click.argument("duration", required=False)
@click.option("--config", "config_path", type=click.Path(path_type=Path), help="Path to a YAML source config file.")
@click.option("--list", "list_channels", is_flag=True, help="List available sources.")
@click.option("--output-dir", type=click.Path(path_type=Path), default=Path.cwd, show_default="current directory", help="Output directory.")
@click.option("--headful", is_flag=True, help="Show Chromium while detecting the stream.")
@click.option("--timeout-ms", type=int, default=45_000, show_default=True, help="Playwright timeout in milliseconds.")
@click.option("--ffmpeg", "ffmpeg_path", default="ffmpeg", show_default=True, help="Override the bundled imageio-ffmpeg binary.")
@click.option("--comskip", is_flag=True, help="Run Comskip after recording and create a commercial-free MP4.")
@click.option("--dry-run", is_flag=True, help="Detect the stream and print the ffmpeg command.")
@click.option("--info", "log_level", flag_value="info", default="info", help="Show normal progress and selected HLS URLs.")
@click.option("--debug", "log_level", flag_value="debug", help="Show discovery steps and ffmpeg output.")
def main(
    source: str | None,
    start: str | None,
    duration: str | None,
    config_path: Path | None,
    list_channels: bool,
    output_dir: Path,
    headful: bool,
    timeout_ms: int,
    ffmpeg_path: str,
    comskip: bool,
    dry_run: bool,
    log_level: str,
) -> None:
    """Find an HLS stream and record it."""
    try:
        config = load_config(config_path)
        if list_channels:
            list_sources(config)
            return

        if source == "comskip":
            if start is None or duration is not None:
                raise click.UsageError("comskip requires exactly one recording file path")
            exit_code = run_existing_comskip(
                config,
                Path(start),
                ffmpeg_path=ffmpeg_path,
                log_level=log_level,
            )
            raise click.exceptions.Exit(exit_code)

        if source is None or start is None or duration is None:
            raise click.UsageError("source, start, and duration are required unless --list is used")

        exit_code = run_record_command(
            config,
            source_key=source,
            start_value=start,
            duration_value=duration,
            output_dir=output_dir,
            headful=headful,
            timeout_ms=timeout_ms,
            ffmpeg_path=ffmpeg_path,
            comskip=comskip,
            dry_run=dry_run,
            log_level=log_level,
        )
        raise click.exceptions.Exit(exit_code)
    except click.exceptions.Exit:
        raise
    except click.ClickException:
        raise
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        raise click.exceptions.Exit(1)


def run_record_command(
    config: dict,
    *,
    source_key: str,
    start_value: str,
    duration_value: str,
    output_dir: Path,
    headful: bool,
    timeout_ms: int,
    ffmpeg_path: str,
    comskip: bool,
    dry_run: bool,
    log_level: str,
) -> int:
    source = get_source(config, source_key)
    start = parse_start(start_value)
    duration_seconds = parse_duration(duration_value)
    delay = seconds_until(start)

    if delay > 0:
        _info(log_level, f"Waiting until {start.isoformat()} ({int(delay)} s).")
        time.sleep(delay)

    _info(log_level, f"Detecting stream for {source.display_name}...")
    stream = find_stream(
        source,
        headless=not headful,
        timeout_ms=timeout_ms,
        debug_log=lambda message: _debug(log_level, message),
    )
    output_path = build_output_path(output_dir, source, start)
    plan = build_ffmpeg_plan(
        stream,
        output_path,
        duration_seconds,
        ffmpeg_path=ffmpeg_path,
        require_ffmpeg=not dry_run,
        recording=source.recording,
    )
    _print_stream_urls(log_level, stream)

    if dry_run:
        click.echo("ffmpeg command:")
        click.echo(shlex.join(plan.command))
        if comskip:
            comskip_plan = build_comskip_plan(
                plan.output_path,
                require_comskip=False,
                auto_install=False,
                options=source.comskip,
            )
            click.echo("comskip command:")
            click.echo(shlex.join(comskip_plan.command))
        return 0

    _info(log_level, f"Recording to {plan.output_path}")
    exit_code = run_recording(plan, debug=log_level == "debug")
    if exit_code != 0:
        click.echo(f"ffmpeg exited with code {exit_code}.", err=True)
        return exit_code

    if comskip:
        return run_comskip_pipeline(
            config,
            plan.output_path,
            ffmpeg_path=plan.ffmpeg_path,
            log_level=log_level,
        )

    return 0


def list_sources(config: dict) -> None:
    sources = config.get("sources") or {}
    if not sources:
        click.echo("No sources available.")
        return

    width = max(len(key) for key in sources)
    for key in sorted(sources):
        display_name = sources[key].get("display_name") or key
        click.echo(f"{key.ljust(width)}  {display_name}")


def run_existing_comskip(
    config: dict,
    recording_path: Path,
    *,
    ffmpeg_path: str,
    log_level: str,
) -> int:
    if not recording_path.exists():
        raise FileNotFoundError(recording_path)
    resolved_ffmpeg = recorder._resolve_ffmpeg(ffmpeg_path, require_ffmpeg=True)
    return run_comskip_pipeline(
        config,
        recording_path,
        ffmpeg_path=resolved_ffmpeg,
        log_level=log_level,
    )


def run_comskip_pipeline(
    config: dict,
    recording_path: Path,
    *,
    ffmpeg_path: str,
    log_level: str,
) -> int:
    source_key = _source_key_from_recording(config, recording_path)
    source = get_source(config, source_key) if source_key else None
    if source:
        _info(log_level, f"Using Comskip settings for {source.display_name}.")
    else:
        _info(log_level, "Using default Comskip settings.")

    comskip_plan = build_comskip_plan(
        recording_path,
        auto_install=True,
        options=source.comskip if source else None,
        log=lambda message: _info(log_level, message),
    )
    _info(log_level, f"Running Comskip on {comskip_plan.recording_path}")
    _debug(log_level, f"Comskip command: {shlex.join(comskip_plan.command)}")
    exit_code = run_comskip(comskip_plan, debug=log_level == "debug")
    if exit_code != 0:
        click.echo(f"comskip exited with code {exit_code}.", err=True)
        return exit_code

    _info(log_level, f"Comskip EDL: {comskip_plan.edl_path}")
    _info(log_level, f"Writing commercial-free MP4 to {comskip_plan.commercial_free_path}")
    exit_code = cut_commercials(
        comskip_plan,
        ffmpeg_path=ffmpeg_path,
        debug=log_level == "debug",
    )
    if exit_code != 0:
        click.echo(f"commercial cutting exited with code {exit_code}.", err=True)
        return exit_code
    _info(log_level, f"Commercial-free MP4: {comskip_plan.commercial_free_path}")
    return 0


def _source_key_from_recording(config: dict, recording_path: Path) -> str | None:
    sources = config.get("sources") or {}
    name = recording_path.name
    matches = [key for key in sources if name.startswith(f"{key}-")]
    if not matches:
        return None
    return max(matches, key=len)


def _info(log_level: str, message: str) -> None:
    if log_level in {"info", "debug"}:
        click.echo(message)


def _debug(log_level: str, message: str) -> None:
    if log_level == "debug":
        click.echo(f"DEBUG {message}")


def _print_stream_urls(log_level: str, stream) -> None:
    discovered = stream.discovered_url or stream.url
    _debug(log_level, f"Discovered HLS URL: {discovered}")
    if stream.input_urls:
        for index, input_url in enumerate(stream.input_urls, start=1):
            _info(log_level, f"Recording HLS input {index}: {input_url}")
        return
    _info(log_level, f"Recording HLS URL: {stream.url}")


if __name__ == "__main__":
    main()
