from io import BytesIO
from pathlib import Path
import shutil
from uuid import uuid4
from unittest.mock import patch

import tv_recorder.comskip as comskip
from tv_recorder.comskip import ComskipPlan, build_comskip_plan, cut_commercials, run_comskip


def test_build_comskip_plan_uses_sidecar_paths() -> None:
    recording = Path("recordings/show.ts")

    plan = build_comskip_plan(
        recording,
        comskip_path="custom-comskip",
        require_comskip=False,
    )

    assert plan.recording_path == recording
    assert plan.edl_path == Path("recordings/show.edl")
    assert plan.ini_path == Path("recordings/show.comskip.ini")
    assert plan.commercial_free_path == Path("recordings/show.commercial-free.mp4")
    assert plan.command == [
        "custom-comskip",
        f"--ini={Path('recordings/show.comskip.ini')}",
        f"--output={Path('recordings')}",
        str(Path("recordings/show.ts")),
    ]


def test_comskip_install_dir_lives_under_python_prefix(monkeypatch) -> None:
    python_prefix = Path("C:/python-env")
    monkeypatch.setattr(comskip.sys, "prefix", str(python_prefix))

    assert comskip._comskip_install_dir() == (
        python_prefix / "share" / "tv-recorder" / "comskip" / comskip.COMSKIP_VERSION
    )


def test_run_comskip_accepts_completed_processing_with_nonzero_exit() -> None:
    work_dir = Path("recordings") / f"tv-recorder-comskip-test-{uuid4().hex}"
    work_dir.mkdir(parents=True)
    try:
        recording = work_dir / "show.mp4"
        recording.write_bytes(b"video")
        plan = ComskipPlan(
            recording_path=recording,
            edl_path=work_dir / "show.edl",
            ini_path=work_dir / "show.comskip.ini",
            commercial_free_path=work_dir / "show.commercial-free.mp4",
            command=["comskip", str(recording)],
            options={
                "min_segment_seconds": 15,
                "min_break_seconds": 105,
            },
        )
        plan.edl_path.write_text("1.0\t2.0\t0\n", encoding="utf-8")
        recording.with_suffix(".txt").write_text(
            "FILE PROCESSING COMPLETE  100 FRAMES AT  2997\n",
            encoding="utf-8",
        )

        with patch("tv_recorder.comskip.subprocess.run") as run:
            run.return_value.returncode = 1

            assert run_comskip(plan) == 0
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def test_run_comskip_reports_activity_with_callback() -> None:
    work_dir = Path("recordings") / f"tv-recorder-comskip-test-{uuid4().hex}"
    work_dir.mkdir(parents=True)
    try:
        recording = work_dir / "show.mp4"
        recording.write_bytes(b"video")
        plan = ComskipPlan(
            recording_path=recording,
            edl_path=work_dir / "show.edl",
            ini_path=work_dir / "show.comskip.ini",
            commercial_free_path=work_dir / "show.commercial-free.mp4",
            command=["comskip", str(recording)],
            options={},
        )
        plan.edl_path.write_text("1.0\t2.0\t0\n", encoding="utf-8")
        recording.with_suffix(".txt").write_text(
            "FILE PROCESSING COMPLETE\n",
            encoding="utf-8",
        )
        frames = []

        with patch("tv_recorder.comskip.subprocess.Popen") as popen:
            popen.return_value.stdout = BytesIO(b"activity")
            popen.return_value.wait.return_value = 0

            assert run_comskip(plan, activity_callback=frames.append) == 0

        assert frames
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def test_read_edl_cuts() -> None:
    work_dir = Path("recordings") / f"tv-recorder-comskip-test-{uuid4().hex}"
    work_dir.mkdir(parents=True)
    try:
        edl_path = work_dir / "show.edl"
        edl_path.write_text("10.0\t20.5\t0\nbad line\n30\t40\t0\n", encoding="utf-8")

        assert comskip._read_edl_cuts(edl_path) == [(10.0, 20.5), (30.0, 40.0)]
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def test_keep_intervals_are_complement_of_cuts() -> None:
    assert comskip._keep_intervals([(10.0, 20.0), (30.0, 40.0)], 50.0) == [
        (0.0, 10.0),
        (20.0, 30.0),
        (40.0, 50.0),
    ]


def test_cut_commercials_remuxes_when_edl_is_empty() -> None:
    plan = ComskipPlan(
        recording_path=Path("recordings/show.mp4"),
        edl_path=Path("recordings/show.edl"),
        ini_path=Path("recordings/show.comskip.ini"),
        commercial_free_path=Path("recordings/show.commercial-free.mp4"),
        command=["comskip", "recordings/show.mp4"],
        options={},
    )

    with patch("tv_recorder.comskip.subprocess.run") as run:
        run.return_value.returncode = 0

        assert cut_commercials(plan, ffmpeg_path="ffmpeg") == 0

    command = run.call_args.args[0]
    assert command[-1] == str(plan.commercial_free_path)


def test_cut_commercials_copies_single_remaining_interval() -> None:
    work_dir = Path("recordings") / f"tv-recorder-comskip-test-{uuid4().hex}"
    work_dir.mkdir(parents=True)
    try:
        recording = work_dir / "show.mp4"
        recording.write_bytes(b"video")
        edl_path = work_dir / "show.edl"
        edl_path.write_text("0.07\t234.11\t0\n", encoding="utf-8")
        plan = ComskipPlan(
            recording_path=recording,
            edl_path=edl_path,
            ini_path=work_dir / "show.comskip.ini",
            commercial_free_path=work_dir / "show.commercial-free.mp4",
            command=["comskip", str(recording)],
            options={},
        )

        with (
            patch("tv_recorder.comskip._media_duration_seconds", return_value=1500.05),
            patch("tv_recorder.comskip.subprocess.run") as run,
        ):
            run.return_value.returncode = 0

            assert cut_commercials(plan, ffmpeg_path="ffmpeg") == 0

        command = run.call_args.args[0]
        assert command[:8] == [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "info",
            "-ss",
            "234.110",
            "-i",
        ]
        assert "-f" not in command
        assert command[-1] == str(plan.commercial_free_path)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def test_build_ini_merges_channel_options() -> None:
    ini = comskip._build_ini({
        "ini": {
            "min_commercialbreak": 105,
            "max_commercialbreak": 600,
        }
    })

    assert "output_edl=1\n" in ini
    assert "min_commercialbreak=105\n" in ini
    assert "max_commercialbreak=600\n" in ini


def test_filter_cuts_rejects_short_false_positive() -> None:
    cuts = [(598.73, 599.93)]
    options = {
        "min_segment_seconds": 15,
        "min_break_seconds": 105,
    }

    assert comskip._filter_cuts(cuts, options) == []


def test_filter_cuts_keeps_long_break() -> None:
    cuts = [
        (100.0, 130.0),
        (130.5, 160.5),
        (161.0, 191.0),
        (191.5, 221.5),
    ]
    options = {
        "min_segment_seconds": 15,
        "min_break_seconds": 105,
        "break_gap_tolerance_seconds": 2,
    }

    assert comskip._filter_cuts(cuts, options) == cuts
