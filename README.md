# tv-recorder

Command-line recorder for public live TV streams.

## Installation

```powershell
pipx install tv-recorder
```

Chromium is installed automatically on first use if Playwright does not already have it.

The recorder uses the `ffmpeg` binary provided by `imageio-ffmpeg`.

For local development from a checkout:

```powershell
pipx install --editable .
```

## Usage

Start `tv-recorder` without arguments to open the native desktop GUI:

```powershell
tv-recorder
```

The GUI uses Python's built-in Tkinter toolkit and exposes the common recording
and Comskip options with a log panel for command output. Custom YAML
configuration remains available from the CLI.

```powershell
tv-recorder radio-canada.ca now 2h
```

The general form is:

```text
tv-recorder SOURCE START DURATION
```

Examples:

```powershell
tv-recorder radio-canada.ca now 30m
tv-recorder radio-canada.ca 2026-05-24T20:00:00 2h --output-dir recordings
tv-recorder tvaplus.ca now 30m
tv-recorder telequebec.tv now 30m
tv-recorder cpac-english now 30m
tv-recorder cpac-francais now 30m
tv-recorder canal-assemblee-nationale now 30m
tv-recorder globalnews-national now 30m
tv-recorder globalnews-montreal now 30m
tv-recorder radio-canada.ca now 10m --headful
tv-recorder radio-canada.ca now 10m --dry-run
tv-recorder radio-canada.ca now 10m --debug
tv-recorder radio-canada.ca now 10m --comskip
tv-recorder comskip recordings\tvaplus.ca-20260529-190942.mp4
```

`START` accepts `now` or a local ISO date. `DURATION` accepts values such as `90s`, `30m`, `2h`, or `01:30:00`.

By default, the CLI runs at `--info` level and prints the effective recording URL or inputs. During recording, a small activity indicator moves when ffmpeg emits progress. Use `--debug` to show discovery details, step results, and ffmpeg output.

## Commercial Marking

Use `--comskip` to run Comskip after a successful recording and create a commercial-free MP4. The original recording is kept unchanged. Comskip writes sidecar files next to it, including an `.edl` file when commercials are detected:

```powershell
tv-recorder radio-canada.ca now 1h --comskip
```

Run the same post-processing on an existing recording:

```powershell
tv-recorder comskip recordings\tvaplus.ca-20260529-190942.mp4
```

The channel settings are selected from the file name prefix, such as `tvaplus.ca-...mp4`.

On Windows, Comskip is downloaded automatically on first use if `comskip.exe` is not already available. It is installed inside the active Python environment under `share\tv-recorder\comskip\`, which keeps a `pipx` installation self-contained.

## Configuration YAML

The package includes a default configuration. To replace it, provide your own YAML file:

```powershell
tv-recorder radio-canada.ca now 2h --config my-file.yaml
```

Each source can define:

- `start_url`: page to open with Playwright.
- `stream_url_pattern`: regular expression used to identify stream URLs.
- `stream_request_urls`: optional JSON endpoints to call before browser steps.
- `stream_response_url_patterns`: response URL patterns whose JSON can contain stream URLs.
- `stream_response_json_keys`: JSON keys whose values should be treated as stream URLs.
- `stream_url_reject_patterns`: stream URL patterns to ignore.
- `recording`: video/audio track selection.
- `comskip`: commercial marking and cutting settings.
- `steps`: browser interaction recipe before stream detection.
- `output_extension`: output file extension.
- `user_agent`: optional user-agent for browser and recorder requests.

Track selection:

```yaml
recording:
  video:
    height: 720
  audio:
    language: "fre"
    reject_comments:
      - "audio_dv"
```

If no video track matches the requested height, the highest available resolution is used. For audio, rejected comments are filtered first, then language and comment preferences are used to choose the main track.

Supported steps:

```yaml
steps:
  - action: wait_for_stream
  - action: wait_for_selector
    selector: "button.vjs-big-play-button"
  - action: click_by_text
    text: "EN DIRECT"
  - action: click_by_selector
    selector: "button:has-text('Play')"
    fallback:
      action: click_by_text
      text: "Play"
    required: true
    timeout_ms: 30000
```

## Notes

The recorder uses browser automation to discover streams when a channel does not expose a stable feed URL, then records the selected audio/video tracks without re-encoding. This version starts the recording at the requested time in the current process. A real system scheduler can be layered on top of the same command later.

## Publishing

Install packaging tools:

```powershell
python -m pip install -e ".[dev]"
```

Build and check the package:

```powershell
python scripts/check_version.py --tag v0.1.0
python -m build
python -m twine check dist/*
```

Publish to TestPyPI first:

```powershell
python -m twine upload --repository testpypi dist/*
```

Publish to PyPI:

```powershell
python -m twine upload dist/*
```

Before publishing, update `version` in `pyproject.toml`, then rebuild from a clean `dist` directory. The package `__version__` is resolved from the package metadata generated from `pyproject.toml`. The GitHub release tag must match the version in `pyproject.toml`; a leading `v` is accepted, so `v0.1.0` matches `0.1.0`.
