"""
Metadata extractor using yt-dlp.
Extracts video metadata and exact durations non-destructively without downloading media.
Supports public, unlisted, and private playlists (via browser cookies or cookies file).
"""
import yt_dlp
from typing import Dict, Any, List, Optional

WATCH_LATER_ALIASES = {"wl", "watch later", "watchlater", ":ytwatchlater"}

def normalize_playlist_url(playlist_url: str) -> str:
    """Accepts a full URL, a bare playlist ID, or 'WL' / 'watch later'."""
    value = playlist_url.strip()
    if value.lower() in WATCH_LATER_ALIASES:
        return "https://www.youtube.com/playlist?list=WL"
    if "://" not in value and not value.startswith(":") and "youtube" not in value:
        return f"https://www.youtube.com/playlist?list={value}"
    return value

def extract_playlist_metadata(
    playlist_url: str,
    browser_cookies: Optional[str] = None,
    cookies_file: Optional[str] = None
) -> Dict[str, Any]:
    """
    Extracts all video items, titles, channels, and durations from a YouTube playlist.
    Non-destructive: No videos are downloaded; zero quota API units consumed.
    
    :param playlist_url: YouTube playlist URL or ID.
    :param browser_cookies: Name of browser to read session cookies from ('chrome', 'edge', 'firefox', 'brave').
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
        'videos': parsed_videos
    }
