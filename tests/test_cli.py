from pathlib import Path

from tv_recorder.cli import _source_key_from_recording
from tv_recorder.cli import main


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
