"""
Spike 1: can audio and video be downloaded anonymously, slowly and cheaply?
Smallest audio-only stream per video, and 480p video-only clips for slide frames. No cookies.
"""
import os
import random
import time

import yt_dlp

from common import CLIPS, MEDIA, VIDEOS, save_result
from enrich import _js_runtimes, _QuietLogger

import sys

DELAY = 5  # seconds between downloads, plus jitter
ONLY = [a for a in sys.argv[1:] if not a.startswith("-")]   # optional video IDs: audio only, re-downloaded


def opts(**extra):
    o = {"quiet": True, "no_warnings": True, "logger": _QuietLogger(), "noprogress": True,
         "outtmpl": os.path.join(MEDIA, "%(id)s.%(ext)s"), "overwrites": False}
    runtimes = _js_runtimes()
    if runtimes:
        o["js_runtimes"] = runtimes
    o.update(extra)
    return o


def main():
    results = {"audio": [], "video": []}
    # Smallest audio stream: speech recognition resamples to 16 kHz mono anyway
    for vid in [v for v in VIDEOS if not ONLY or v in ONLY]:
        if ONLY:
            for f in os.listdir(MEDIA):
                if f.startswith(vid) and f.endswith((".m4a", ".webm")):
                    os.remove(os.path.join(MEDIA, f))
        t = time.time()
        # Auto-dubbed videos carry many audio tracks; the original has language_preference 10.
        # "worstaudio" alone picked an Arabic dub, so filter to the original first.
        fmt = "wa[language_preference>=10][acodec!=none]/wa[format_note*=original]/worstaudio[acodec!=none]/bestaudio"
        with yt_dlp.YoutubeDL(opts(format=fmt)) as y:
            info = y.extract_info(f"https://www.youtube.com/watch?v={vid}", download=True)
        path = y.prepare_filename(info)
        size = os.path.getsize(path)
        row = {"video": vid, "file": os.path.basename(path), "format": info.get("format_id"), "abr_kbps": info.get("abr"),
               "duration_s": info.get("duration"), "mb": round(size / 1e6, 2), "seconds": round(time.time() - t, 1),
               "mb_per_hour": round(size / 1e6 / (info.get("duration") or 1) * 3600, 1)}
        results["audio"].append(row)
        print("audio", row)
        time.sleep(DELAY + random.uniform(0, DELAY / 2))

    # 480p video-only clips (no audio track), just the section we test frames on
    for vid, start, end in ([] if ONLY else CLIPS):
        t = time.time()
        o = opts(format="bv[height<=480][vcodec!=none]/bv", outtmpl=os.path.join(MEDIA, f"%(id)s.{start}-{end}.video.%(ext)s"),
                 download_ranges=yt_dlp.utils.download_range_func(None, [(start, end)]))
        with yt_dlp.YoutubeDL(o) as y:
            info = y.extract_info(f"https://www.youtube.com/watch?v={vid}", download=True)
        path = next(os.path.join(MEDIA, f) for f in os.listdir(MEDIA) if f.startswith(f"{vid}.{start}-{end}.video"))
        size = os.path.getsize(path)
        row = {"video": vid, "clip_s": [start, end], "file": os.path.basename(path), "format": info.get("format_id"),
               "height": info.get("height"), "mb": round(size / 1e6, 2), "seconds": round(time.time() - t, 1),
               "mb_per_hour": round(size / 1e6 / (end - start) * 3600, 1)}
        results["video"].append(row)
        print("video", row)
        time.sleep(DELAY + random.uniform(0, DELAY / 2))
    save_result("download" + ("_rerun" if ONLY else ""), results)


if __name__ == "__main__":
    main()
