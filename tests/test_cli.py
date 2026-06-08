import threading
from pathlib import Path
from unittest.mock import patch

from tv_recorder.cli import _source_key_from_recording
from tv_recorder.cli import main
from tv_recorder.cli import run_record_command
from tv_recorder.recorder import _ActivityIndicator


def test_source_key_from_recording_uses_filename_prefix() -> None:
    config = {
        "sources": {
            "tvaplus.ca": {},
            "radio-canada.ca": {},
        }
    }

    assert (
        _source_key_from_recording(
            config,
            Path("recordings/tvaplus.ca-20260529-190942.mp4"),
        )
        == "tvaplus.ca"
    )


def test_source_key_from_recording_uses_longest_match() -> None:
    config = {
        "sources": {
            "globalnews": {},
            "globalnews-national": {},
        }
    }

    assert (
        _source_key_from_recording(
            config,
            Path("recordings/globalnews-national-20260529-190942.mp4"),
        )
        == "globalnews-national"
    )


def test_help_shows_current_directory_as_default_output_dir() -> None:
    from click.testing import CliRunner

    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    assert "current directory" in result.output


def test_activity_indicator_calls_callback_when_stderr_is_not_tty() -> None:
    frames = []
    indicator = _ActivityIndicator("ffmpeg activity", callback=frames.append)

    indicator._tick()

    assert frames == ["|"]


def test_record_command_honors_cancel_before_stream_detection() -> None:
    cancelled = threading.Event()
    cancelled.set()
    config = {
        "sources": {
            "test": {
                "display_name": "Test",
                "start_url": "https://example.com",
            },
        },
    }

    with patch("tv_recorder.cli.find_stream") as find_stream:
        exit_code = run_record_command(
            config,
            source_key="test",
            start_value="now",
            duration_value="30m",
            output_dir=Path("recordings"),
            headful=False,
            timeout_ms=45_000,
            ffmpeg_path="ffmpeg",
            comskip=False,
            dry_run=False,
            log_level="info",
            cancellation_event=cancelled,
        )

    assert exit_code == 130
    find_stream.assert_not_called()
