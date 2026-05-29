from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from tv_recorder.config import SourceConfig
from tv_recorder.stream_finder import StreamInfo


@dataclass(frozen=True)
class RecordingPlan:
    output_path: Path
    capture_path: Path
    command: list[str]
    final_command: list[str] | None
    duration_seconds: int
    ffmpeg_path: str


def build_output_path(output_dir: Path, source: SourceConfig, start: datetime) -> Path:
    safe_source = re.sub(r"[^A-Za-z0-9_.-]+", "-", source.key).strip("-")
    stamp = start.strftime("%Y%m%d-%H%M%S")
    return output_dir / f"{safe_source}-{stamp}.{source.output_extension}"


def build_ffmpeg_plan(
    stream: StreamInfo,
    output_path: Path,
    duration_seconds: int,
    *,
    ffmpeg_path: str = "ffmpeg",
    require_ffmpeg: bool = True,
    recording: dict | None = None,
) -> RecordingPlan:
    ffmpeg = _resolve_ffmpeg(ffmpeg_path, require_ffmpeg=require_ffmpeg)
    capture_path = _capture_path(output_path)

    headers = []
    if stream.user_agent:
        headers.append(f"User-Agent: {stream.user_agent}")
    if stream.page_url:
        headers.append(f"Referer: {stream.page_url}")
    if stream.cookies:
        headers.append(f"Cookie: {stream.cookies}")

    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "info",
    ]
    if stream.input_urls:
        for input_url in stream.input_urls:
            if headers:
                command.extend(["-headers", "\r\n".join(headers) + "\r\n"])
            command.extend(_input_options())
            command.extend(["-i", input_url])
        command.extend(["-t", str(duration_seconds)])
        command.extend(["-map", "0:0", "-map", "1:0"])
        maps = []
    else:
        if headers:
            command.extend(["-headers", "\r\n".join(headers) + "\r\n"])
        command.extend(_input_options())
        command.extend([
            "-t",
            str(duration_seconds),
            "-i",
            stream.url,
        ])
        maps = _build_stream_maps(ffmpeg, stream, headers, recording)
    command.extend([
        *maps,
        "-dn",
        "-sn",
        "-c",
        "copy",
    ])
    if capture_path.suffix.lower() == ".mp4":
        command.extend(["-movflags", "+faststart"])
    if capture_path.suffix.lower() == ".ts":
        command.extend(["-f", "mpegts"])
    command.append(str(capture_path))

    final_command = None
    if capture_path != output_path:
        final_command = _final_command(ffmpeg, capture_path, output_path)

    return RecordingPlan(
        output_path=output_path,
        capture_path=capture_path,
        command=command,
        final_command=final_command,
        duration_seconds=duration_seconds,
        ffmpeg_path=ffmpeg,
    )


def _input_options() -> list[str]:
    return [
        "-http_persistent",
        "0",
        "-reconnect",
        "1",
        "-reconnect_streamed",
        "1",
        "-reconnect_on_network_error",
        "1",
        "-reconnect_on_http_error",
        "4xx,5xx",
        "-reconnect_delay_max",
        "2",
        "-live_start_index",
        "-1",
        "-analyzeduration",
        "10000000",
        "-probesize",
        "10000000",
    ]


def _capture_path(output_path: Path) -> Path:
    if output_path.suffix.lower() == ".mp4":
        return output_path.with_suffix(".part.mkv")
    return output_path


def _final_command(ffmpeg: str, capture_path: Path, output_path: Path) -> list[str] | None:
    if capture_path.suffix.lower() == output_path.suffix.lower():
        return None
    return [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "info",
        "-i",
        str(capture_path),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    ]


def _build_stream_maps(
    ffmpeg: str,
    stream: StreamInfo,
    headers: list[str],
    recording: dict | None,
) -> list[str]:
    if not recording:
        return []

    streams = _probe_streams(ffmpeg, stream, headers)
    video = _select_video_stream(streams, recording.get("video") or {})
    audio = _select_audio_stream(streams, recording.get("audio") or {}, video)
    selected = [video, audio]
    return [item for stream in selected if stream is not None for item in ("-map", f"0:{stream['index']}")]


def _probe_streams(ffmpeg: str, stream: StreamInfo, headers: list[str]) -> list[dict]:
    command = [ffmpeg, "-hide_banner", "-loglevel", "info"]
    if headers:
        command.extend(["-headers", "\r\n".join(headers) + "\r\n"])
    command.extend(["-analyzeduration", "10000000", "-probesize", "10000000", "-i", stream.url])
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=45,
    )
    _raise_for_protected_stream(completed.stderr)
    return _parse_ffmpeg_streams(completed.stderr)


def _raise_for_protected_stream(output: str) -> None:
    if "METHOD=SAMPLE-AES" in output or "KEYFORMAT=" in output:
        raise RuntimeError(
            "The detected stream appears to be protected by DRM or HLS SAMPLE-AES encryption. "
            "ffmpeg can download the segments, but the produced file will not be playable."
        )


def _parse_ffmpeg_streams(output: str) -> list[dict]:
    streams: list[dict] = []
    current: dict | None = None
    current_program: int | None = None
    stream_re = re.compile(r"Stream #0:(?P<index>\d+)(?:\((?P<language>[^)]+)\))?: (?P<kind>Video|Audio): (?P<details>.*)")
    metadata_re = re.compile(r"\s*(?P<key>[A-Za-z0-9_.-]+)\s*:\s*(?P<value>.*)")
    program_re = re.compile(r"\s*Program (?P<program>\d+)")

    for line in output.splitlines():
        program_match = program_re.match(line)
        if program_match:
            current_program = int(program_match.group("program"))
            continue

        stream_match = stream_re.search(line)
        if stream_match:
            details = stream_match.group("details")
            current = {
                "index": int(stream_match.group("index")),
                "program": current_program,
                "kind": stream_match.group("kind").lower(),
                "language": stream_match.group("language"),
                "details": details,
                "metadata": {},
            }
            resolution = re.search(r"(?P<width>\d{3,5})x(?P<height>\d{3,5})", details)
            if resolution:
                current["width"] = int(resolution.group("width"))
                current["height"] = int(resolution.group("height"))
            streams.append(current)
            continue

        if current is None:
            continue
        metadata_match = metadata_re.match(line)
        if metadata_match:
            current["metadata"][metadata_match.group("key")] = metadata_match.group("value").strip()

    return streams


def _select_video_stream(streams: list[dict], options: dict) -> dict | None:
    videos = [stream for stream in streams if stream["kind"] == "video"]
    if not videos:
        return None

    height = options.get("height")
    if height:
        matching = [stream for stream in videos if stream.get("height") == int(height)]
        if matching:
            return _highest_video(matching)

    return _highest_video(videos)


def _highest_video(streams: list[dict]) -> dict:
    return max(streams, key=lambda stream: (stream.get("height") or 0, stream.get("width") or 0))


def _select_audio_stream(streams: list[dict], options: dict, video: dict | None) -> dict | None:
    audios = [stream for stream in streams if stream["kind"] == "audio"]
    if not audios:
        return None

    reject_comments = [item.casefold() for item in options.get("reject_comments") or ()]
    candidates = [
        stream for stream in audios
        if _stream_comment(stream).casefold() not in reject_comments
    ] or audios

    language = options.get("language")
    if language:
        language_matches = [stream for stream in candidates if stream.get("language") == language]
        if language_matches:
            candidates = language_matches

    if video and video.get("program") is not None:
        program_matches = [stream for stream in candidates if stream.get("program") == video.get("program")]
        if program_matches:
            candidates = program_matches

    comment = options.get("comment")
    if comment:
        comment_matches = [stream for stream in candidates if _stream_comment(stream) == comment]
        if comment_matches:
            candidates = comment_matches

    return candidates[0]


def _stream_comment(stream: dict) -> str:
    return stream.get("metadata", {}).get("comment", "")


def _resolve_ffmpeg(ffmpeg_path: str, *, require_ffmpeg: bool) -> str:
    if ffmpeg_path != "ffmpeg":
        return shutil.which(ffmpeg_path) or ffmpeg_path
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        if require_ffmpeg:
            raise RuntimeError("imageio-ffmpeg could not provide an ffmpeg binary.") from exc
        return ffmpeg_path


def _media_duration_seconds(ffmpeg: str, path: Path) -> float | None:
    completed = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    match = re.search(r"Duration: (?P<hours>\d+):(?P<minutes>\d+):(?P<seconds>\d+(?:\.\d+)?)", completed.stderr)
    if not match:
        return None
    return (
        int(match.group("hours")) * 3600
        + int(match.group("minutes")) * 60
        + float(match.group("seconds"))
    )


def run_recording(plan: RecordingPlan) -> int:
    plan.capture_path.parent.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(plan.command, stdin=subprocess.PIPE)
    try:
        exit_code = process.wait(timeout=plan.duration_seconds + 60)
    except subprocess.TimeoutExpired:
        if process.stdin:
            process.stdin.write(b"q\n")
            process.stdin.flush()
        try:
            exit_code = process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.terminate()
            exit_code = process.wait()
    except KeyboardInterrupt:
        if process.stdin:
            process.stdin.write(b"q\n")
            process.stdin.flush()
        exit_code = process.wait()

    if exit_code != 0:
        return exit_code

    if plan.capture_path == plan.output_path:
        return _validate_duration(plan)

    if not plan.final_command:
        plan.capture_path.replace(plan.output_path)
        return _validate_duration(plan)

    final = subprocess.run(plan.final_command)
    if final.returncode == 0:
        plan.capture_path.unlink(missing_ok=True)
        return _validate_duration(plan)
    return final.returncode


def _validate_duration(plan: RecordingPlan) -> int:
    duration = _media_duration_seconds(plan.ffmpeg_path, plan.output_path)
    if duration is None:
        return 0
    minimum = max(1, plan.duration_seconds * 0.95)
    if duration < minimum:
        print(
            f"Recording is too short: {duration:.1f}s for a requested duration "
            f"of {plan.duration_seconds}s."
        )
        return 1
    return 0
