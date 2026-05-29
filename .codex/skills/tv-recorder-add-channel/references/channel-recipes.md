# Channel Recipes

Use this reference when a channel is not obvious from `defaults.yaml`.

## Direct HLS

Use `stream_request_urls` with `steps: []` when a manifest records directly. This is fastest and most stable when the URL is not tokenized per session.

Validate with ffmpeg, because a master manifest can be alive while its variants return `400`, `403`, or `404`.

Examples observed in this project:
- Global News regional feeds: direct Corus `.isml/.m3u8` manifests.
- CPAC: direct master with separate audio tracks.
- NHK: old master returned `200` but children failed; `https://masterpl.hls.nhkworld.jp/hls/w/live/smarttv.m3u8` recorded successfully.

## API-Returned Stream

Use `stream_response_url_patterns` and `stream_response_json_keys` when a public endpoint returns a stream URL. Keep browser steps empty if the API is enough.

Example pattern:

```yaml
stream_request_urls:
  - "https://service.example/api/live?output=json"
stream_response_url_patterns:
  - "service\\.example/api/live"
stream_response_json_keys:
  - "url"
steps: []
```

## Browser Discovery

Use browser discovery when:
- the manifest is tokenized per session,
- a consent or play action is required,
- the source is embedded in an iframe/player,
- the direct URL expires quickly.

Start with:

```yaml
steps:
  - action: wait_for_stream
```

The project's auto-start logic tries common consent and play controls. Add explicit steps only when required.

## Reject Patterns

Add `stream_url_reject_patterns` for candidates that are detected but not recordable or not the desired program:

- `dai\\.google\\.com` for ad-insertion manifests.
- `originpath=/linear/hls` when a provider exposes an ad/linear wrapper instead of the clean live stream.
- `dmxleo\\.dailymotion\\.com` for Dailymotion manifest wrappers that are not final media.
- `cdndirector\\.dailymotion\\.com` when ffmpeg receives `403`; prefer the final `live.*.dmcdn.net/...m3u8` variant if discovered.

## Video and Audio Selection

Set `recording.video.height` when the stream has multiple variants. Use `direct_variant: false` when ffmpeg should receive the master manifest, especially when separate audio groups are involved.

For master manifests with separate audio:

```yaml
recording:
  hls:
    separate_audio: true
  video:
    height: 720
    direct_variant: false
  audio:
    language: "en"
```

For French audio, observed language codes may be `fre`, `fra`, or `fr`; use what ffmpeg or the HLS manifest reports.

When audio-description tracks exist, use `reject_comments` if ffmpeg metadata exposes the descriptive track:

```yaml
audio:
  language: "fre"
  comment: "Français"
  reject_comments:
    - "audio_dv"
```

## Failure Meanings

- `403 Forbidden` on the manifest or variants: likely geoblocking, missing headers, expired token, or a redirector that should be rejected.
- `Output file does not contain any stream`: master was reachable but variants failed or were empty.
- `SAMPLE-AES` or `KEYFORMAT`: protected HLS; do not attempt to bypass.
- A small partial file with nonzero size and rc=1: often a late stream interruption, timestamp issue, or too-short capture; retest before changing config.
- A live endpoint returning `false`, `is_on_air: false`, or no player data can mean the channel is event-based rather than broken.
- YouTube embeds can expose no HLS and return `EMBEDDER_IDENTITY_DENIED`; do not treat that as a normal HLS channel unless the project intentionally adds a YouTube resolver.
- Do not replace a channel with a different but similarly named feed just to make a smoke test pass. Keep `TV5MONDE Europe` as Europe, `UK Parliament` as UK Parliament, etc., unless the user explicitly accepts a substitute.

## Smoke Testing

Use 1-minute tests for confidence:

```powershell
.\.venv\Scripts\tv-recorder.exe <source-key> now 1m --output-dir recordings\channel-smoke --timeout-ms 60000
```

For many channels, use a small worker pool. This project previously used 3 or 5 simultaneous recordings. Treat repeated failures across 3 attempts as real configuration problems; treat one failure followed by successes as a possible transient.
