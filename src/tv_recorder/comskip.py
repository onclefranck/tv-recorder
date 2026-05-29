from __future__ import annotations

import platform
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


COMSKIP_VERSION = "0.82.012"
COMSKIP_WINDOWS_URL = "https://www.kaashoek.com/files/comskip82_012.zip"
COMSKIP_WINDOWS_SHA256: str | None = None

DEFAULT_INI = {
    "output_edl": 1,
    "output_txt": 1,
    "live_tv": 1,
}


@dataclass(frozen=True)
class ComskipPlan:
    recording_path: Path
    edl_path: Path
    ini_path: Path
    commercial_free_path: Path
    command: list[str]
    options: dict


def build_comskip_plan(
    recording_path: Path,
    *,
    comskip_path: str = "comskip",
    require_comskip: bool = True,
    auto_install: bool = False,
    options: dict | None = None,
    log: Callable[[str], None] | None = None,
) -> ComskipPlan:
    comskip = _resolve_comskip(
        comskip_path,
        require_comskip=require_comskip,
        auto_install=auto_install,
        log=log,
    )
    ini_path = recording_path.with_suffix(".comskip.ini")
    edl_path = recording_path.with_suffix(".edl")
    commercial_free_path = recording_path.with_name(
        f"{recording_path.stem}.commercial-free.mp4"
    )
    command = [
        comskip,
        f"--ini={ini_path}",
        f"--output={recording_path.parent}",
        str(recording_path),
    ]
    return ComskipPlan(
        recording_path=recording_path,
        edl_path=edl_path,
        ini_path=ini_path,
        commercial_free_path=commercial_free_path,
        command=command,
        options=options or {},
    )


def run_comskip(plan: ComskipPlan, *, debug: bool = False) -> int:
    plan.ini_path.write_text(_build_ini(plan.options), encoding="utf-8")
    output = None if debug else subprocess.DEVNULL
    completed = subprocess.run(plan.command, stdout=output, stderr=output)
    if not plan.edl_path.exists():
        if completed.returncode != 0:
            return completed.returncode
        print(f"Comskip completed, but no EDL file was created at {plan.edl_path}.")
        return 1
    if _processing_completed(plan):
        return 0
    if completed.returncode != 0:
        return completed.returncode
    return 0


def _processing_completed(plan: ComskipPlan) -> bool:
    txt_path = plan.recording_path.with_suffix(".txt")
    if not txt_path.exists():
        return False
    content = txt_path.read_text(encoding="utf-8", errors="replace")
    return "FILE PROCESSING COMPLETE" in content


def _build_ini(options: dict) -> str:
    settings = {**DEFAULT_INI, **(options.get("ini") or {})}
    return "".join(f"{key}={value}\n" for key, value in settings.items())


def cut_commercials(plan: ComskipPlan, *, ffmpeg_path: str, debug: bool = False) -> int:
    cuts = _filter_cuts(_read_edl_cuts(plan.edl_path), plan.options)
    duration = _media_duration_seconds(ffmpeg_path, plan.recording_path) if cuts else None
    if cuts and duration is None:
        print(f"Could not determine media duration for {plan.recording_path}.")
        return 1

    keep_intervals = _keep_intervals(cuts, duration)
    if len(keep_intervals) == 1 and keep_intervals[0][0] == 0 and keep_intervals[0][1] is None:
        return _remux_to_mp4(
            ffmpeg_path,
            plan.recording_path,
            plan.commercial_free_path,
            debug=debug,
        )

    temp_dir = plan.recording_path.with_name(f".{plan.recording_path.stem}.comskip-parts")
    temp_dir.mkdir(parents=True, exist_ok=True)
    output = None if debug else subprocess.DEVNULL
    segment_paths: list[Path] = []
    try:
        for index, (start, end) in enumerate(keep_intervals, start=1):
            segment_path = temp_dir / f"part-{index:04d}.mp4"
            command = [
                ffmpeg_path,
                "-y",
                "-hide_banner",
                "-loglevel",
                "info",
                "-ss",
                _format_seconds(start),
                "-i",
                str(plan.recording_path),
            ]
            if end is not None:
                command.extend(["-t", _format_seconds(end - start)])
            command.extend([
                "-map",
                "0",
                "-dn",
                "-sn",
                "-c",
                "copy",
                "-avoid_negative_ts",
                "make_zero",
                str(segment_path),
            ])
            completed = subprocess.run(command, stdout=output, stderr=output)
            if completed.returncode != 0:
                return completed.returncode
            segment_paths.append(segment_path)

        concat_path = temp_dir / "concat.txt"
        concat_path.write_text(
            "".join(f"file '{_concat_path(path)}'\n" for path in segment_paths),
            encoding="utf-8",
        )
        command = [
            ffmpeg_path,
            "-y",
            "-hide_banner",
            "-loglevel",
            "info",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(plan.commercial_free_path),
        ]
        completed = subprocess.run(command, stdout=output, stderr=output)
        return completed.returncode
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _read_edl_cuts(path: Path) -> list[tuple[float, float]]:
    if not path.exists():
        return []
    cuts = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            start = float(parts[0])
            end = float(parts[1])
        except ValueError:
            continue
        if end > start:
            cuts.append((start, end))
    return sorted(cuts)


def _filter_cuts(cuts: list[tuple[float, float]], options: dict) -> list[tuple[float, float]]:
    if not cuts:
        return []

    min_segment_seconds = float(options.get("min_segment_seconds") or 0)
    min_break_seconds = float(options.get("min_break_seconds") or 0)
    gap_tolerance_seconds = float(options.get("break_gap_tolerance_seconds") or 0)

    filtered = [
        (start, end)
        for start, end in cuts
        if end - start >= min_segment_seconds
    ]
    if not filtered or not min_break_seconds:
        return filtered

    breaks: list[list[tuple[float, float]]] = []
    for cut in filtered:
        if not breaks or cut[0] - breaks[-1][-1][1] > gap_tolerance_seconds:
            breaks.append([cut])
        else:
            breaks[-1].append(cut)

    kept: list[tuple[float, float]] = []
    for commercial_break in breaks:
        break_duration = sum(end - start for start, end in commercial_break)
        if break_duration >= min_break_seconds:
            kept.extend(commercial_break)
    return kept


def _keep_intervals(cuts: list[tuple[float, float]], duration: float | None) -> list[tuple[float, float | None]]:
    if not cuts:
        return [(0, None)]

    assert duration is not None
    intervals: list[tuple[float, float | None]] = []
    cursor = 0.0
    for start, end in cuts:
        start = max(0.0, min(start, duration))
        end = max(0.0, min(end, duration))
        if start > cursor:
            intervals.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < duration:
        intervals.append((cursor, duration))
    return [(start, end) for start, end in intervals if end is None or end - start > 0.05]


def _remux_to_mp4(ffmpeg_path: str, input_path: Path, output_path: Path, *, debug: bool) -> int:
    output = None if debug else subprocess.DEVNULL
    command = [
        ffmpeg_path,
        "-y",
        "-hide_banner",
        "-loglevel",
        "info",
        "-i",
        str(input_path),
        "-map",
        "0",
        "-dn",
        "-sn",
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    completed = subprocess.run(command, stdout=output, stderr=output)
    return completed.returncode


def _media_duration_seconds(ffmpeg_path: str, path: Path) -> float | None:
    completed = subprocess.run(
        [ffmpeg_path, "-hide_banner", "-i", str(path)],
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


def _format_seconds(value: float) -> str:
    return f"{value:.3f}"


def _concat_path(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "'\\''")


def _resolve_comskip(
    comskip_path: str,
    *,
    require_comskip: bool,
    auto_install: bool = False,
    log: Callable[[str], None] | None = None,
) -> str:
    resolved = shutil.which(comskip_path)
    if resolved:
        return resolved
    if comskip_path != "comskip":
        return comskip_path
    installed = _installed_comskip_path()
    if installed.exists():
        return str(installed)
    if auto_install:
        return str(install_comskip(log=log))
    if require_comskip:
        raise RuntimeError(
            "Comskip was not found. On Windows, run with --comskip to auto-install it "
            "inside the active Python environment."
        )
    return comskip_path


def install_comskip(log: Callable[[str], None] | None = None) -> Path:
    if platform.system() != "Windows":
        raise RuntimeError("Automatic Comskip installation is currently supported on Windows only.")

    target = _installed_comskip_path()
    if target.exists():
        return target

    install_dir = target.parent
    install_dir.mkdir(parents=True, exist_ok=True)
    archive_path = install_dir / "comskip.zip"

    _log(log, f"Downloading Comskip {COMSKIP_VERSION}...")
    urllib.request.urlretrieve(COMSKIP_WINDOWS_URL, archive_path)
    if COMSKIP_WINDOWS_SHA256:
        _verify_sha256(archive_path, COMSKIP_WINDOWS_SHA256)
    else:
        _log(log, "Comskip checksum is not pinned; trusting the HTTPS download source.")

    _log(log, f"Installing Comskip to {install_dir}")
    with zipfile.ZipFile(archive_path) as archive:
        _extract_zip_safely(archive, install_dir)

    archive_path.unlink(missing_ok=True)
    discovered = _find_comskip_exe(install_dir)
    if not discovered:
        raise RuntimeError(f"Comskip was downloaded, but comskip.exe was not found in {install_dir}.")
    if discovered != target:
        discovered.replace(target)
    return target


def _installed_comskip_path() -> Path:
    return _comskip_install_dir() / "comskip.exe"


def _comskip_install_dir() -> Path:
    return Path(sys.prefix) / "share" / "tv-recorder" / "comskip" / COMSKIP_VERSION


def _verify_sha256(path: Path, expected: str) -> None:
    import hashlib

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest.casefold() != expected.casefold():
        path.unlink(missing_ok=True)
        raise RuntimeError("Downloaded Comskip archive did not match the expected SHA256 checksum.")


def _extract_zip_safely(archive: zipfile.ZipFile, target_dir: Path) -> None:
    root = target_dir.resolve()
    for member in archive.infolist():
        target = (target_dir / member.filename).resolve()
        if root != target and root not in target.parents:
            raise RuntimeError(f"Unsafe path in Comskip archive: {member.filename}")
    archive.extractall(target_dir)


def _find_comskip_exe(root: Path) -> Path | None:
    for path in root.rglob("*"):
        if path.name.casefold() == "comskip.exe":
            return path
    return None


def _log(log: Callable[[str], None] | None, message: str) -> None:
    if log:
        log(message)
