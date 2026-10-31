"""
Tools helper functions.
Supports length calculation across multiple video and playlist links without database writes.
"""
import re
from typing import Any, Dict, List, Optional

import extractor

_VIDEO_KEYS = ("video_id", "title", "channel", "duration_seconds", "position", "url")


def _clean_error(err: Any) -> str:
    """Extracts a clean, human-readable error message from an exception or error string."""
    msg = str(err).strip()
    msg = extractor._clean_error(msg)
    m = re.search(r"Could not extract info from playlist URL: .*? \((.*)\)$", msg, re.DOTALL)
    if m:
        msg = extractor._clean_error(m.group(1).strip())
    msg = re.sub(r"^(?:error|warning):\s*", "", msg, flags=re.IGNORECASE).strip()
    if "\n" in msg:
        msg = msg.split("\n")[0].strip()
    return msg or "Could not read link"


def read_links(links: List[str], browser: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Reads up to 10 video or playlist links, returning metadata and video items for length calculations.
    Errors for individual links become {"link": link, "error": msg} and do not fail the batch.
    Does not write to the database or modify settings.
    """
    cleaned = [l.strip() for l in links if isinstance(l, str) and l.strip()][:10]
    results: List[Dict[str, Any]] = []

    for link in cleaned:
        try:
            vid = extractor.video_id_from_url(link)
            if vid and "list=" not in link:
                v = extractor.extract_video(vid)
                results.append({"kind": "video", "id": v["video_id"], "title": v["title"], "url": v["url"],
                                "videos": [{k: v[k] for k in _VIDEO_KEYS}], "unavailable": 0})
            else:
                meta = extractor.extract_playlist_metadata(link, browser_cookies=browser)
                results.append({"kind": "playlist", "id": meta["id"], "title": meta["title"], "url": meta["url"],
                                "videos": [{k: item[k] for k in _VIDEO_KEYS} for item in meta["videos"]],
                                "unavailable": int(meta.get("skipped_unavailable") or 0)})
        except Exception as e:
            results.append({"link": link, "error": _clean_error(e)})

    return results
