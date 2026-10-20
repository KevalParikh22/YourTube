"""
Metadata extractor using yt-dlp.
Extracts video metadata and exact durations non-destructively without downloading media.
Supports public, unlisted, and private playlists (via browser cookies or cookies file).
"""
import yt_dlp
from typing import Dict, Any, List, Optional

WATCH_LATER_ALIASES = {"wl", "watch later", "watchlater", ":ytwatchlater"}
# Order tried by browser="auto". Firefox first: its cookies never need a keyring.
AUTO_BROWSERS = ("firefox", "chrome", "chromium", "brave", "edge", "vivaldi", "opera")
_LOGIN_COOKIES = {"SID", "__Secure-3PSID", "LOGIN_INFO"}

class _QuietLogger:
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg, only_once=False): pass
    def error(self, msg): pass

def detect_browser() -> Optional[str]:
    """Returns the first installed browser that is signed in to YouTube, or None."""
    from yt_dlp.cookies import extract_cookies_from_browser
    for browser in AUTO_BROWSERS:
        try:
            jar = extract_cookies_from_browser(browser, logger=_QuietLogger())
        except Exception:
            continue  # not installed, locked or undecryptable
        names = {c.name for c in jar if c.domain.endswith("youtube.com") and c.value}
        if names & _LOGIN_COOKIES:
            return browser
    return None

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
                            or 'auto' to use the first browser signed in to YouTube.
    :param cookies_file: Path to a cookies.txt file if preferred.
    :return: Dictionary containing playlist info and parsed video list.
    """
    ydl_opts: Dict[str, Any] = {
        'extract_flat': 'in_playlist',
        'skip_download': True,
        'quiet': True,
        'no_warnings': True,
        'ignoreerrors': True,  # Skip deleted or unavailable videos without crashing
    }
    
    if browser_cookies and browser_cookies.lower() == 'auto':
        browser_cookies = detect_browser()
        if not browser_cookies:
            raise ValueError("No browser signed in to YouTube was found. Sign in to YouTube in "
                             "Firefox or Chrome, or use a cookies.txt file.")
    if browser_cookies:
        ydl_opts['cookiesfrombrowser'] = (browser_cookies.lower(),)
    elif cookies_file:
        ydl_opts['cookiefile'] = cookies_file

    playlist_url = normalize_playlist_url(playlist_url)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        raw_info = ydl.extract_info(playlist_url, download=False)
        
    if not raw_info:
        raise ValueError(f"Could not extract info from playlist URL: {playlist_url}")
        
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
        'browser': browser_cookies,
        'videos': parsed_videos
    }
