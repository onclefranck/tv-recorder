# tv-recorder

Command-line recorder for public live TV streams.

## Local Installation

```powershell
python -m pip install -e .
```

Chromium is installed automatically on first use if Playwright does not already have it.

The recorder uses the `ffmpeg` binary provided by `imageio-ffmpeg`.

## Usage

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
```

`START` accepts `now` or a local ISO date. `DURATION` accepts values such as `90s`, `30m`, `2h`, or `01:30:00`.

By default, the CLI runs at `--info` level and prints the effective recording URL or inputs. During recording, a small activity indicator moves when ffmpeg emits progress. Use `--debug` to show discovery details, step results, and ffmpeg output.

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

Before publishing, update `version` in `pyproject.toml` and `__version__` in `src/tv_recorder/__init__.py`, then rebuild from a clean `dist` directory.
