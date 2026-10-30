"""
Metadata extractor using yt-dlp.
Extracts video metadata and exact durations non-destructively without downloading media.
Supports public, unlisted, and private playlists (via browser cookies or cookies file).
"""
import re
import time

import yt_dlp
from typing import Dict, Any, List, Optional

WATCH_LATER_ALIASES = {"wl", "watch later", "watchlater", ":ytwatchlater"}
# Order tried by browser="auto". Firefox first: its cookies never need a keyring.
AUTO_BROWSERS = ("firefox", "chrome", "chromium", "brave", "edge", "vivaldi", "opera")
# yt-dlp only counts a session as signed in with LOGIN_INFO plus one of these (youtube _has_auth_cookies)
_SAPISID_COOKIES = {"SAPISID", "__Secure-1PAPISID", "__Secure-3PAPISID"}
# Pause before re-reading a browser's cookies after YouTube rotated them mid-read
ROTATION_RETRY_SECONDS = 5

class _QuietLogger:
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg, only_once=False): pass
    def error(self, msg): pass

class _ErrorLogger(_QuietLogger):
    """Keeps yt-dlp's errors, which ignoreerrors would otherwise swallow, and its warnings."""
    def __init__(self):
        self.errors: List[str] = []
        self.warnings: List[str] = []
    def warning(self, msg, only_once=False):
        self.warnings.append(msg)
    def error(self, msg):
        self.errors.append(msg)
    def cookies_rotated(self) -> bool:
        return any("no longer valid" in w for w in self.warnings)

def _clean_error(msg: str) -> str:
    """'ERROR: [youtube:tab] PLx: YouTube said: ...' -> 'YouTube said: ...'"""
    msg = re.sub(r"^ERROR:\s*", "", msg.strip())
    return re.sub(r"^\[[^\]]+\]\s*(?:[\w-]+:\s+)?", "", msg)

def signed_in_browsers() -> List[str]:
    """Every installed browser that is signed in to YouTube, in AUTO_BROWSERS order."""
    from yt_dlp.cookies import extract_cookies_from_browser
    found = []
    for browser in AUTO_BROWSERS:
        try:
            jar = extract_cookies_from_browser(browser, logger=_QuietLogger())
        except Exception:
            continue  # not installed, locked or undecryptable
        names = {c.name for c in jar if c.domain.endswith("youtube.com") and c.value}
        if "LOGIN_INFO" in names and names & _SAPISID_COOKIES:
            found.append(browser)
    return found

def detect_browser() -> Optional[str]:
    """Returns the first installed browser that is signed in to YouTube, or None."""
    browsers = signed_in_browsers()
    return browsers[0] if browsers else None

_VIDEO_URL = re.compile(r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/|embed/)|youtu\.be/)([\w-]{11})(?![\w-])")

def video_id_from_url(value: str) -> Optional[str]:
    """The video ID when the text is a link to one video (not a playlist: a link with list= syncs the playlist)."""
    value = (value or "").strip()
    if "list=" in value:
        return None
    m = _VIDEO_URL.search(value)
    return m.group(1) if m else None

def extract_video(video_id: str) -> Dict[str, Any]:
    """One video's title, channel and duration, read anonymously (per-video reads never use the sign-in)."""
    opts = {"skip_download": True, "quiet": True, "no_warnings": True, "logger": _QuietLogger()}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    if not info or not info.get("id"):
        raise ValueError("YouTube did not return this video. It may be private, deleted or age-restricted.")
    return {"video_id": info["id"], "title": info.get("title") or "Untitled Video",
            "channel": info.get("uploader") or info.get("channel") or "Unknown Channel",
            "duration_seconds": int(info.get("duration") or 0), "url": f"https://www.youtube.com/watch?v={info['id']}",
            "position": 0, "category": "Uncategorized", "status": "unwatched"}

def normalize_playlist_url(playlist_url: str) -> str:
    """Accepts a full URL, a bare playlist ID, or 'WL' / 'watch later'."""
    value = playlist_url.strip()
    if value.lower() in WATCH_LATER_ALIASES:
        return "https://www.youtube.com/playlist?list=WL"
    if "://" not in value and not value.startswith(":") and "youtube" not in value:
        return f"https://www.youtube.com/playlist?list={value}"
    return value

def list_my_playlists(browser_cookies: Optional[str] = "auto", cookies_file: Optional[str] = None) -> List[Dict[str, str]]:
    """Lists the signed-in account's playlists (read-only): [{'id', 'title'}]."""
    opts: Dict[str, Any] = {'extract_flat': True, 'skip_download': True, 'quiet': True,
                            'no_warnings': True, 'ignoreerrors': True}
    if browser_cookies and browser_cookies.lower() == 'auto':
        browser_cookies = detect_browser()
        if not browser_cookies:
            raise ValueError("No browser signed in to YouTube was found.")
    if browser_cookies:
        opts['cookiesfrombrowser'] = (browser_cookies.lower(),)
    elif cookies_file:
        opts['cookiefile'] = cookies_file
    else:
        raise ValueError("Listing your playlists needs a sign-in (--browser or --cookies).")
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info("https://www.youtube.com/feed/playlists", download=False) or {}
    # "RD…" entries are auto-generated mixes, not saved playlists
    return [{'id': e['id'], 'title': e.get('title') or e['id']}
            for e in info.get('entries') or [] if e and e.get('id') and not e['id'].startswith('RD')]

def extract_playlist_metadata(
    playlist_url: str,
    browser_cookies: Optional[str] = None,
    cookies_file: Optional[str] = None
) -> Dict[str, Any]:
    """
    Extracts all video items, titles, channels, and durations from a YouTube playlist.
    Non-destructive: No videos are downloaded; zero quota API units consumed.
    
    :param playlist_url: YouTube playlist URL or ID.
    :param browser_cookies: Browser to read session cookies from ('firefox', 'chrome', ...),
                            or 'auto' to try each browser signed in to YouTube until one can read it.
    :param cookies_file: Path to a cookies.txt file if preferred.
    :return: Dictionary containing playlist info and parsed video list.
    """
    playlist_url = normalize_playlist_url(playlist_url)
    if browser_cookies and browser_cookies.lower() == 'auto':
        browsers = signed_in_browsers()
        if not browsers:
            raise ValueError("No browser signed in to YouTube was found. Sign in to YouTube in "
                             "Firefox or Chrome, or use a cookies.txt file.")
        # Browsers may be signed in to different accounts; use the first one that can see the playlist
        failures, empty = [], None
        for browser in browsers:
            try:
                raw_info = _read_playlist(playlist_url, {'cookiesfrombrowser': (browser,)})
            except ValueError as e:
                failures.append(f"{browser}: {e}")
                continue
            if raw_info.get('entries'):
                return _parse_playlist(raw_info, playlist_url, browser)
            empty = empty or (raw_info, browser)
        if empty:
            return _parse_playlist(empty[0], playlist_url, empty[1])
        raise ValueError("none of the browsers signed in to YouTube can read this playlist "
                         "(they may be signed in to other accounts). " + "; ".join(failures))

    cookie_opts: Dict[str, Any] = {}
    if browser_cookies:
        cookie_opts['cookiesfrombrowser'] = (browser_cookies.lower(),)
    elif cookies_file:
        cookie_opts['cookiefile'] = cookies_file
    try:
        raw_info = _read_playlist(playlist_url, cookie_opts)
    except ValueError as e:
        raise ValueError(f"Could not extract info from playlist URL: {playlist_url} ({e})") from e
    return _parse_playlist(raw_info, playlist_url, browser_cookies)

def _read_playlist(playlist_url: str, cookie_opts: Dict[str, Any]) -> Dict[str, Any]:
    """
    Runs the flat yt-dlp read; raises ValueError with YouTube's own reason on failure.
    YouTube sometimes rotates a browser's sign-in mid-read, which makes a private playlist look missing;
    the browser saves the new session soon after, so that case is read once more with fresh cookies.
    """
    browser = (cookie_opts.get('cookiesfrombrowser') or (None,))[0]
    try:
        return _read_playlist_once(playlist_url, cookie_opts)
    except _RotatedCookies as e:
        time.sleep(ROTATION_RETRY_SECONDS)
        try:
            return _read_playlist_once(playlist_url, cookie_opts)
        except _RotatedCookies:
            raise ValueError(f"{e.reason}. YouTube rotated the {browser} sign-in during the read: open youtube.com "
                             f"in {browser}, reload the page, wait a minute, then sync again") from None

class _RotatedCookies(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason

def _read_playlist_once(playlist_url: str, cookie_opts: Dict[str, Any]) -> Dict[str, Any]:
    logger = _ErrorLogger()
    ydl_opts: Dict[str, Any] = {
        'extract_flat': 'in_playlist',
        'skip_download': True,
        'quiet': True,
        'no_warnings': True,
        'ignoreerrors': True,  # Skip deleted or unavailable videos without crashing
        'logger': logger,
        **cookie_opts,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            raw_info = ydl.extract_info(playlist_url, download=False)
    except Exception as e:
        reason = _clean_error(str(e))
    else:
        if raw_info:
            return raw_info
        reason = _clean_error(logger.errors[-1]) if logger.errors else "YouTube returned nothing"
    rotated = 'cookiesfrombrowser' in cookie_opts and logger.cookies_rotated()
    raise (_RotatedCookies if rotated else ValueError)(reason)

def _parse_playlist(raw_info: Dict[str, Any], playlist_url: str, browser: Optional[str]) -> Dict[str, Any]:
    playlist_id = raw_info.get('id', 'unknown_playlist')
    playlist_title = raw_info.get('title', 'YouTube Playlist')
    raw_entries = raw_info.get('entries', []) or []
    
    parsed_videos: List[Dict[str, Any]] = []
    skipped_unavailable = 0

    for position, entry in enumerate(raw_entries):
        if not entry:
            skipped_unavailable += 1
            continue

        video_id = entry.get('id')
        if not video_id:
            skipped_unavailable += 1
            continue

        title = entry.get('title') or "Untitled Video"
        # Skip private/deleted placeholders if indicated
        if title in ["[Private video]", "[Deleted video]"]:
            skipped_unavailable += 1
            continue
            
        duration = entry.get('duration') or 0
        channel = entry.get('uploader') or entry.get('channel') or "Unknown Channel"
        url = entry.get('url') or f"https://www.youtube.com/watch?v={video_id}"
        
        parsed_videos.append({
            'video_id': video_id,
            'title': title,
            'channel': channel,
            'duration_seconds': int(duration) if duration else 0,
            'url': url,
            'position': position,
            'category': 'Uncategorized',
            'status': 'unwatched'
        })

    return {
        'id': playlist_id,
        'title': playlist_title,
        'url': playlist_url,
        'total_extracted': len(parsed_videos),
        'skipped_unavailable': skipped_unavailable,
        'browser': browser,
        'videos': parsed_videos
    }
