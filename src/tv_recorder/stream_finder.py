from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import parse_qsl, unquote, urljoin, urlparse, urlunparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from tv_recorder.config import SourceConfig


@dataclass(frozen=True)
class StreamInfo:
    url: str
    page_url: str
    user_agent: str | None
    cookies: str
    input_urls: tuple[str, ...] = ()
    discovered_url: str = ""


@dataclass(frozen=True)
class StepResult:
    page: object
    done: bool


def find_stream(
    source: SourceConfig,
    *,
    headless: bool = True,
    timeout_ms: int = 45_000,
    debug_log: Callable[[str], None] | None = None,
) -> StreamInfo:
    pattern = re.compile(source.stream_url_pattern)
    response_patterns = [re.compile(item) for item in source.stream_response_url_patterns]
    reject_patterns = [re.compile(item) for item in source.stream_url_reject_patterns]
    candidates: list[str] = []

    with sync_playwright() as playwright:
        _install_chromium()
        browser = playwright.chromium.launch(headless=headless)
        context_kwargs = {}
        if source.user_agent:
            context_kwargs["user_agent"] = source.user_agent
        context = browser.new_context(**context_kwargs)

        def remember(url: str) -> None:
            for stream_url in _stream_urls_from_observed_url(url, pattern):
                if any(reject.search(stream_url) for reject in reject_patterns):
                    _debug(debug_log, f"Rejected HLS candidate: {stream_url}")
                    continue
                if stream_url not in candidates:
                    candidates.append(stream_url)
                    _debug(debug_log, f"Detected HLS candidate: {stream_url}")

        def watch_page(watched_page) -> None:
            watched_page.on("request", lambda request: remember(request.url))
            watched_page.on("response", remember_response)

        def remember_response(response) -> None:
            remember(response.url)
            if not any(response_pattern.search(response.url) for response_pattern in response_patterns):
                return
            try:
                for stream_url in _stream_urls_from_json(response.json(), source.stream_response_json_keys):
                    remember(stream_url)
            except Exception:
                return

        context.on("page", watch_page)
        page = context.new_page()
        watch_page(page)

        for url in source.stream_request_urls:
            try:
                _debug(debug_log, f"Requesting configured stream URL: {url}")
                remember(url)
                response = context.request.get(url, timeout=timeout_ms)
                remember(response.url)
                for stream_url in _stream_urls_from_json(response.json(), source.stream_response_json_keys):
                    remember(stream_url)
                _debug(debug_log, f"Configured stream request completed: {response.status} {response.url}")
            except Exception as exc:
                _debug(debug_log, f"Configured stream request failed: {url} ({exc})")
                pass

        if not candidates or source.steps:
            page.goto(source.start_url, wait_until="domcontentloaded", timeout=timeout_ms)
            _debug(debug_log, f"Opened page: {page.url}")

            for step in source.steps:
                page = _run_step(page, context, step, timeout_ms, candidates, debug_log).page

        if not candidates:
            try:
                page.wait_for_event(
                    "response",
                    predicate=lambda response: pattern.search(response.url) is not None,
                    timeout=timeout_ms,
                )
            except PlaywrightTimeoutError:
                pass

        if not candidates:
            browser.close()
            raise RuntimeError(
                "No m3u8 stream was detected. Try --headful, increase --timeout-ms, "
                "or adjust the click recipe in the config."
            )

        discovered = _prefer_master_playlist(candidates)
        selected = _resolve_configured_variant(context, discovered, source.recording)
        if selected != discovered:
            _debug(debug_log, f"Selected configured HLS variant: {selected}")
        input_urls = _resolve_separate_hls_inputs(context, selected, source.recording)
        for index, input_url in enumerate(input_urls, start=1):
            _debug(debug_log, f"Selected separate HLS input {index}: {input_url}")
        cookies = _cookie_header(context.cookies([selected]))
        user_agent = source.user_agent or page.evaluate("navigator.userAgent")
        page_url = page.url if page.url != "about:blank" else source.start_url
        browser.close()

    return StreamInfo(
        url=selected,
        page_url=page_url,
        user_agent=user_agent,
        cookies=cookies,
        input_urls=input_urls,
        discovered_url=discovered,
    )


def _debug(debug_log: Callable[[str], None] | None, message: str) -> None:
    if debug_log:
        debug_log(message)


def _install_chromium() -> None:
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)


def _click_button_by_text(page, text: str, timeout_ms: int, *, required: bool) -> bool:
    css_text = json.dumps(text)
    try:
        page.locator(f"button:has-text({css_text})").first.click(timeout=timeout_ms)
        return True
    except PlaywrightTimeoutError as exc:
        if required:
            raise RuntimeError(f"Could not click button '{text}'.") from exc
        return False


def _click_button_by_selector(page, selector: str, timeout_ms: int, *, required: bool) -> bool:
    try:
        page.locator(selector).first.click(timeout=timeout_ms)
        return True
    except PlaywrightTimeoutError as exc:
        if required:
            raise RuntimeError(f"Could not click selector '{selector}'.") from exc
        return False


def _run_step(
    page,
    context,
    step: dict,
    default_timeout_ms: int,
    candidates: list[str],
    debug_log: Callable[[str], None] | None = None,
) -> StepResult:
    action = _required_value(step, "action")
    try:
        handler = globals()[f"_run_{action}_step"]
    except KeyError as exc:
        raise ValueError(f"Unknown recipe action: {action}") from exc
    result = handler(page, context, step, default_timeout_ms, candidates)
    _debug(
        debug_log,
        f"Step {action}: done={result.done} page={getattr(result.page, 'url', 'unknown')} candidates={len(candidates)}",
    )
    if not result.done and step.get("fallback"):
        _debug(debug_log, f"Step {action}: running fallback")
        return _run_step(page, context, step["fallback"], default_timeout_ms, candidates, debug_log)
    return result


def _run_wait_step(page, context, step: dict, default_timeout_ms: int, candidates: list[str]) -> StepResult:
    del context
    del default_timeout_ms
    del candidates
    page.wait_for_timeout(int(_required_value(step, "ms")))
    return StepResult(page, True)


def _run_wait_for_selector_step(page, context, step: dict, default_timeout_ms: int, candidates: list[str]) -> StepResult:
    del context
    del candidates
    timeout_ms = int(step.get("timeout_ms") or default_timeout_ms)
    selector = str(_required_value(step, "selector"))
    try:
        page.locator(selector).first.wait_for(timeout=timeout_ms)
        return StepResult(page, True)
    except PlaywrightTimeoutError as exc:
        if _is_required(step):
            raise RuntimeError(f"Selector '{selector}' did not appear.") from exc
        return StepResult(page, False)


def _run_wait_for_stream_step(page, context, step: dict, default_timeout_ms: int, candidates: list[str]) -> StepResult:
    del context
    timeout_ms = int(step.get("timeout_ms") or default_timeout_ms)
    if step.get("autostart", True) and not candidates:
        _auto_start_player(page, step, default_timeout_ms)
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if candidates:
            return StepResult(page, True)
        page.wait_for_timeout(250)
    if _is_required(step):
        raise RuntimeError("No m3u8 stream was detected before the timeout.")
    return StepResult(page, False)


def _run_auto_start_player_step(page, context, step: dict, default_timeout_ms: int, candidates: list[str]) -> StepResult:
    del context
    del candidates
    _auto_start_player(page, step, default_timeout_ms)
    return StepResult(page, True)


def _auto_start_player(page, step: dict, default_timeout_ms: int) -> None:
    timeout_ms = int(step.get("timeout_ms") or min(default_timeout_ms, 2000))
    texts = step.get("texts") or (
        "Tout accepter",
        "Accepter",
        "Accept all",
        "Accept",
        "I agree",
        "Agree",
        "Continuer sans accepter",
        "Play",
        "Watch live",
        "Regarder",
        "Lire",
    )
    selectors = step.get("selectors") or (
        ".vjs-big-play-button",
        "button[aria-label*='Play']",
        "button[title*='Play']",
        "video",
    )

    for text in texts:
        try:
            page.get_by_text(str(text), exact=False).first.click(timeout=timeout_ms)
            page.wait_for_timeout(500)
        except Exception:
            pass

    for selector in selectors:
        try:
            page.locator(str(selector)).first.click(timeout=timeout_ms)
            page.wait_for_timeout(500)
        except Exception:
            pass


def _run_click_by_text_step(page, context, step: dict, default_timeout_ms: int, candidates: list[str]) -> StepResult:
    del context
    del candidates
    timeout_ms = int(step.get("timeout_ms") or default_timeout_ms)
    done = _click_button_by_text(
        page,
        str(_required_value(step, "text")),
        timeout_ms,
        required=_is_required(step),
    )
    return StepResult(page, done)


def _run_click_by_selector_step(page, context, step: dict, default_timeout_ms: int, candidates: list[str]) -> StepResult:
    del context
    del candidates
    timeout_ms = int(step.get("timeout_ms") or default_timeout_ms)
    done = _click_button_by_selector(
        page,
        str(_required_value(step, "selector")),
        timeout_ms,
        required=_is_required(step),
    )
    return StepResult(page, done)


def _run_click_and_switch_by_text_step(page, context, step: dict, default_timeout_ms: int, candidates: list[str]) -> StepResult:
    del candidates
    timeout_ms = int(step.get("timeout_ms") or default_timeout_ms)
    text = str(_required_value(step, "text"))
    try:
        with context.expect_page(timeout=timeout_ms) as new_page_info:
            page.get_by_text(text, exact=True).first.click(timeout=timeout_ms)
        new_page = new_page_info.value
        new_page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        return StepResult(new_page, True)
    except PlaywrightTimeoutError as exc:
        if _is_required(step):
            raise RuntimeError(f"Could not click text '{text}' and open a new page.") from exc
        return StepResult(page, False)


def _required_value(step: dict, key: str):
    value = step.get(key)
    if value is None:
        raise ValueError(f"The step must define '{key}'.")
    return value


def _is_required(step: dict) -> bool:
    return bool(step.get("required", True)) and not bool(step.get("fallback"))


def _stream_urls_from_observed_url(url: str, pattern: re.Pattern) -> list[str]:
    urls = []
    if pattern.search(url):
        urls.append(url)

    decoded = unquote(url)
    parsed = urlparse(decoded)
    for _, value in parse_qsl(parsed.query, keep_blank_values=True):
        if pattern.search(value):
            urls.append(value)

    return urls


def _stream_urls_from_json(data, keys: tuple[str, ...]) -> list[str]:
    urls = []
    if isinstance(data, dict):
        for key, value in data.items():
            if key in keys and isinstance(value, str):
                urls.append(value)
            urls.extend(_stream_urls_from_json(value, keys))
    elif isinstance(data, list):
        for item in data:
            urls.extend(_stream_urls_from_json(item, keys))
    return urls


def _prefer_master_playlist(candidates: list[str]) -> str:
    for candidate in candidates:
        lowered = candidate.lower()
        if "master" in lowered or "playlist" in lowered:
            return candidate
    return candidates[0]


def _resolve_configured_variant(context, master_url: str, recording: dict | None) -> str:
    video = ((recording or {}).get("video") or {})
    if video.get("direct_variant") is False:
        return master_url

    height = video.get("height")
    if not height:
        return master_url

    try:
        response = context.request.get(master_url)
        if not response.ok:
            return master_url
        return _select_variant_by_height(master_url, response.text(), int(height)) or master_url
    except Exception:
        return master_url


def _select_variant_by_height(master_url: str, manifest: str, height: int) -> str | None:
    lines = [line.strip() for line in manifest.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF:"):
            continue
        match = re.search(r"RESOLUTION=\d+x(?P<height>\d+)", line)
        if not match or int(match.group("height")) != height:
            continue
        for uri in lines[index + 1 :]:
            if not uri.startswith("#"):
                return _variant_url(master_url, uri)
    return None


def _resolve_separate_hls_inputs(context, master_url: str, recording: dict | None) -> tuple[str, ...]:
    hls = ((recording or {}).get("hls") or {})
    if not hls.get("separate_audio"):
        return ()

    try:
        response = context.request.get(master_url)
        if not response.ok:
            return ()
        selected = _select_separate_hls_inputs(master_url, response.text(), recording or {})
        return selected or ()
    except Exception:
        return ()


def _select_separate_hls_inputs(master_url: str, manifest: str, recording: dict) -> tuple[str, str] | None:
    video_options = recording.get("video") or {}
    audio_options = recording.get("audio") or {}
    height = int(video_options["height"]) if video_options.get("height") else None
    audio_language = audio_options.get("language")

    media = []
    variants = []
    lines = [line.strip() for line in manifest.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if line.startswith("#EXT-X-MEDIA:"):
            attributes = _parse_hls_attributes(line.removeprefix("#EXT-X-MEDIA:"))
            if attributes.get("TYPE") == "AUDIO" and attributes.get("URI"):
                media.append(attributes)
            continue

        if not line.startswith("#EXT-X-STREAM-INF:"):
            continue
        attributes = _parse_hls_attributes(line.removeprefix("#EXT-X-STREAM-INF:"))
        uri = _next_hls_uri(lines, index)
        if uri:
            variants.append((attributes, uri))

    variant = _select_hls_variant(variants, height)
    if not variant:
        return None

    variant_attributes, video_uri = variant
    audio = _select_hls_audio(media, variant_attributes.get("AUDIO"), audio_language)
    if not audio:
        return None

    return (
        _variant_url(master_url, video_uri),
        _variant_url(master_url, audio["URI"]),
    )


def _select_hls_variant(variants: list[tuple[dict[str, str], str]], height: int | None):
    if height:
        for attributes, uri in variants:
            resolution = attributes.get("RESOLUTION") or ""
            match = re.search(r"\d+x(?P<height>\d+)", resolution)
            if match and int(match.group("height")) == height:
                return attributes, uri
    return variants[0] if variants else None


def _select_hls_audio(media: list[dict[str, str]], group_id: str | None, language: str | None) -> dict[str, str] | None:
    candidates = [item for item in media if not group_id or item.get("GROUP-ID") == group_id]
    if not candidates:
        return None

    if language:
        wanted = {language.casefold(), language[:2].casefold()}
        for item in candidates:
            values = {
                (item.get("LANGUAGE") or "").casefold(),
                (item.get("NAME") or "").casefold(),
            }
            if values & wanted:
                return item

    return candidates[0]


def _next_hls_uri(lines: list[str], index: int) -> str | None:
    for uri in lines[index + 1 :]:
        if not uri.startswith("#"):
            return uri
    return None


def _parse_hls_attributes(value: str) -> dict[str, str]:
    attributes = {}
    for match in re.finditer(r'(?P<key>[A-Z0-9-]+)=((?P<quoted>"[^"]*")|(?P<bare>[^,]*))', value):
        raw = match.group("quoted") or match.group("bare") or ""
        attributes[match.group("key")] = raw.strip('"')
    return attributes


def _variant_url(master_url: str, uri: str) -> str:
    absolute = urljoin(master_url, uri)
    parsed_absolute = urlparse(absolute)
    if parsed_absolute.query or urlparse(uri).query:
        return absolute

    master_query = urlparse(master_url).query
    if not master_query:
        return absolute

    return urlunparse(parsed_absolute._replace(query=master_query))


def _cookie_header(cookies: list[dict]) -> str:
    return "; ".join(f"{cookie['name']}={cookie['value']}" for cookie in cookies)
