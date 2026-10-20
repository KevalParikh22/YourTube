"""
Video details ("enrich"): description, chapters, tags and captions for each video.

Per-video reads are made WITHOUT cookies: a signed-in full extraction fails with
"The page needs to be reloaded", while anonymous reads work for public and unlisted
videos (playlists may be private, but the videos in them are usually public). This also
keeps signed-in traffic to the minimum.

Requests are paced and the run stops at the first sign of YouTube's bot check, so a large
library is fetched over several runs instead of in a burst.
"""
import json
import random
import re
import shutil
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import yt_dlp

DEFAULT_DELAY = 3.0      # seconds between videos, plus jitter
MAX_TRIES = 3            # failed videos are retried on later runs up to this many times
DEFAULT_LIMIT = 200      # videos per run

# Category names that default to "no transcripts" (music, mixes, devotional songs...)
MUSIC_NAME = re.compile(r"\b(music|songs?|mix|mixtapes?|bhajans?|lo-?fi|albums?|qawwali|ghazals?)\b", re.I)

class _QuietLogger:
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg, only_once=False): pass
    def error(self, msg): pass

class BotCheck(Exception):
    """YouTube asked to confirm we are not a bot: stop the run and try again later."""


def _js_runtimes() -> Dict[str, Dict]:
    for name in ("deno", "node", "bun"):
        if shutil.which(name):
            return {name: {}}
    return {}

def make_ydl() -> yt_dlp.YoutubeDL:
    # No cookies here on purpose (see module docstring)
    opts: Dict[str, Any] = {'skip_download': True, 'quiet': True, 'no_warnings': True,
                            'logger': _QuietLogger(), 'socket_timeout': 30}
    runtimes = _js_runtimes()
    if runtimes:
        opts['js_runtimes'] = runtimes
    return yt_dlp.YoutubeDL(opts)


def transcripts_wanted(category: str, settings: Dict[str, Any]) -> bool:
    """Whether videos in this category should get transcripts (user override, else the name heuristic)."""
    overrides = settings.get("transcripts") or {}
    if category in overrides:
        return bool(overrides[category])
    return not MUSIC_NAME.search(category or "")


def _base(lang: Optional[str]) -> str:
    return (lang or "").split("-")[0].lower()

def choose_track(info: Dict[str, Any]) -> Optional[Tuple[str, str, List[Dict[str, Any]]]]:
    """
    Picks a caption track: manual captions in the video's language, then auto captions in
    the original language, then manual English, then auto English. Returns (kind, lang, formats).
    """
    subs = {k: v for k, v in (info.get('subtitles') or {}).items() if k != 'live_chat'}
    auto = info.get('automatic_captions') or {}
    lang = _base(info.get('language'))
    candidates = []
    if lang:
        candidates += [("manual", k, subs[k]) for k in subs if _base(k) == lang]
        candidates += [("auto", k, auto[k]) for k in auto if k == f"{lang}-orig" or (k.endswith("-orig") and _base(k) == lang)]
    candidates += [("auto", k, auto[k]) for k in auto if k.endswith("-orig")]   # original language, whatever it is
    candidates += [("manual", k, subs[k]) for k in subs if _base(k) == "en"]
    candidates += [("auto", k, auto[k]) for k in auto if k == "en"]
    for kind, key, formats in candidates:
        if any(f.get('ext') == 'json3' for f in formats):
            return kind, key.replace("-orig", ""), formats
    return None

def parse_json3(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """YouTube json3 captions -> [{'start', 'dur', 'text'}] with empty and repeated lines dropped."""
    out: List[Dict[str, Any]] = []
    for e in data.get('events') or []:
        text = "".join(s.get('utf8', '') for s in e.get('segs') or []).replace("\n", " ").strip()
        text = re.sub(r"\s+", " ", text)
        if not text or (out and out[-1]['text'] == text):
            continue
        out.append({'start': round((e.get('tStartMs') or 0) / 1000, 2),
                    'dur': round((e.get('dDurationMs') or 0) / 1000, 2), 'text': text})
    return out


def _classify_error(msg: str) -> str:
    m = msg.lower()
    if "not a bot" in m or "http error 429" in m or "too many requests" in m:
        return "bot"
    if any(k in m for k in ("private video", "video unavailable", "has been removed", "members-only",
                            "confirm your age", "age-restricted", "incomplete youtube id", "not available",
                            "this video is unavailable", "premieres in", "live event will begin")):
        return "unavailable"
    return "failed"

def fetch_details(ydl: yt_dlp.YoutubeDL, video_id: str) -> Dict[str, Any]:
    """
    One video's details. Returns a dict for storage.save_details: status is 'done',
    'no_captions', 'music', 'unavailable' or 'failed'. Raises BotCheck to stop the run.
    """
    try:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    except yt_dlp.utils.DownloadError as e:
        kind = _classify_error(str(e))
        if kind == "bot":
            raise BotCheck(str(e)) from None
        return {'status': kind, 'error': re.sub(r"^ERROR:\s*", "", str(e))[:300]}

    details: Dict[str, Any] = {
        'description': info.get('description') or "",
        'tags': info.get('tags') or [],
        'chapters': [{'start': c.get('start_time') or 0, 'title': c.get('title') or ""} for c in info.get('chapters') or []],
        'language': info.get('language'),
        'yt_categories': info.get('categories') or [],
        'segments': [],
    }
    if "Music" in details['yt_categories']:
        return {**details, 'status': 'music'}
    track = choose_track(info)
    if not track:
        return {**details, 'status': 'no_captions'}
    kind, lang, formats = track
    url = next(f['url'] for f in formats if f.get('ext') == 'json3')
    try:
        data = json.loads(ydl.urlopen(url).read().decode('utf-8'))
    except Exception as e:  # captions fetch failed after the page worked: retry later
        if _classify_error(str(e)) == "bot":
            raise BotCheck(str(e)) from None
        return {**details, 'status': 'failed', 'error': f"captions: {str(e)[:200]}"}
    segments = parse_json3(data)
    if not segments:
        return {**details, 'status': 'no_captions'}
    return {**details, 'status': 'done', 'caption_lang': lang, 'caption_kind': kind, 'segments': segments}


def run_enrich(
    video_ids: Optional[List[str]] = None,
    limit: int = DEFAULT_LIMIT,
    delay: float = DEFAULT_DELAY,
    should_stop: Callable[[], bool] = lambda: False,
    fetch: Callable[..., Dict[str, Any]] = fetch_details,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Fetches details for videos that need them (or the given IDs). Prints progress for the job log."""
    import llm
    import storage

    settings = llm.load_settings()
    queue = storage.videos_needing_details(video_ids=video_ids, max_tries=MAX_TRIES)
    # Music-like categories are not fetched at all (no request is made for them)
    wanted = [v for v in queue if transcripts_wanted(v['category'], settings)]
    skipped = len(queue) - len(wanted)
    todo = wanted[:limit]
    print(f"📝 Video details: {len(wanted)} videos need them"
          + (f" (doing the first {limit} this run)" if len(wanted) > limit else "")
          + (f"; {skipped} in no-transcript categories left out" if skipped else "") + ".")
    if not todo:
        print("   Nothing to do.")
        return True
    eta = len(todo) * (delay + 2)
    print(f"   About {max(1, round(eta / 60))} min at one video every ~{delay + 2:.0f}s. Read without your sign-in.")

    counts: Dict[str, int] = {}
    ydl = make_ydl() if fetch is fetch_details else None
    try:
        for n, v in enumerate(todo, 1):
            if should_stop():
                print("⏹️ Stopped. Progress is saved; run again to continue.")
                break
            try:
                result = fetch(ydl, v['video_id'])
            except BotCheck:
                print("🛑 YouTube asked to confirm we're not a bot. Stopping here; try again in an hour or two.")
                return False
            storage.save_details(v['video_id'], result)
            counts[result['status']] = counts.get(result['status'], 0) + 1
            extra = f" · {len(result.get('segments') or [])} lines" if result['status'] == 'done' else \
                    f" · {result['error'][:80]}" if result.get('error') else ""
            print(f"   [{n}/{len(todo)}] {result['status']:<11} {v['title'][:60]}{extra}")
            if n < len(todo):
                sleep(delay + random.uniform(0, delay / 2))
    finally:
        if ydl:
            ydl.close()
    labels = {'done': 'with transcript', 'no_captions': 'no captions', 'music': 'music',
              'unavailable': 'unavailable', 'failed': 'failed (retried next run)'}
    print("✅ " + ", ".join(f"{c} {labels.get(k, k)}" for k, c in counts.items()) + ".")
    return True
