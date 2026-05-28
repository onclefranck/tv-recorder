---
name: tv-recorder-add-channel
description: Add or repair a channel entry in a Python tv-recorder project's defaults.yaml. Use when the user asks to add a public TV/news/live channel, discover or stabilize an HLS m3u8 URL, choose between direct URL/API/browser discovery strategies, configure video/audio selection, or run smoke tests for a tv-recorder channel.
---

# TV Recorder Add Channel

## Workflow

Use this skill when editing `src/tv_recorder/defaults.yaml` for a `tv-recorder` project.

1. Read `defaults.yaml`, `config.py`, `stream_finder.py`, and `recorder.py` before editing. Preserve the existing schema and local style.
2. Read the requested channel URL or channel list. If a companion file such as `chaines.md` exists, inspect the relevant lines.
3. Prefer the least fragile working strategy:
   - Stable direct HLS URL or public API returning HLS.
   - Browser discovery with Playwright when the URL is tokenized, embedded, or only emitted after consent/play clicks.
   - Alternate public HLS only when the official site is geoblocked, stale, DRM-protected, or unusable from the current location.
4. Validate candidates with a real short ffmpeg capture, not only HTTP status or dry-run. A master manifest can return `200` while every child variant fails.
5. Patch `defaults.yaml` only after a candidate records successfully.
6. Run one focused smoke test for the new/changed channel. Use 1 minute unless the user asks otherwise.
7. Report the source key, strategy used, test result, output path, and any caveat such as geoblocking or third-party alternate feed.

## Discovery

Use these options in order, stopping once one gives a reliable recording:

- **Direct manifest/API**: Check the live page source, network calls, JSON APIs, and public stream indexes for `.m3u8`. Configure `stream_request_urls` and `steps: []`.
- **Browser discovery**: Configure `start_url`, `stream_url_pattern`, `steps: [{action: wait_for_stream}]`, and add reject patterns for ads, analytics, VOD, or redirector manifests.
- **JSON response extraction**: Configure `stream_response_url_patterns` and `stream_response_json_keys` when a validation/API endpoint returns the stream URL.
- **Separate audio HLS**: If the master manifest uses `#EXT-X-MEDIA` audio groups, set `recording.hls.separate_audio: true`, `video.direct_variant: false`, and configure `audio.language`.

Read `references/channel-recipes.md` for concrete recipes and failure patterns before repairing a difficult channel.

## Validation

For each new or repaired channel:

```powershell
.\.venv\Scripts\tv-recorder.exe <source-key> now 1m --output-dir recordings\smoke-<stamp> --timeout-ms 60000
```

If validating several channels, cap parallelism. Previous project smoke tests used 3 or 5 concurrent recordings.

After recording, inspect the file with the bundled ffmpeg:

```powershell
@'
import subprocess, imageio_ffmpeg
from pathlib import Path
ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
path = Path(r"<recorded-file.mp4>")
subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)])
'@ | .\.venv\Scripts\python.exe -
```

A successful entry should produce a playable MP4 with nonzero size, about the requested duration, and expected video/audio streams.

## Defaults YAML Pattern

Use stable, lowercase source keys. Keep display names human-readable.

```yaml
  source-key:
    display_name: Channel Name
    start_url: "https://example.com/live"
    stream_url_pattern: "\\.m3u8(\\?|$)"
    stream_request_urls:
      - "https://example.com/live/master.m3u8"
    stream_url_reject_patterns:
      - "dai\\.google\\.com"
    output_extension: "mp4"
    recording:
      video:
        height: 720
      audio:
        language: "eng"
    steps: []
    user_agent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
```

For browser discovery, omit `stream_request_urls` and use:

```yaml
    steps:
      - action: wait_for_stream
```

## Guardrails

- Do not trust a candidate because `Invoke-WebRequest` or `context.request.get()` returns `200`; run ffmpeg for a few seconds.
- Reject ad manifests such as `dai.google.com` and Dailymotion redirectors when they do not record.
- Prefer official sources, but accept that a stable direct alternate can be as reasonable as browsing a fragile page.
- If DRM or HLS SAMPLE-AES appears, do not try to bypass protection. Mark it as unsupported.
- Keep changes scoped to channel config unless the project lacks a capability needed by multiple channels.
