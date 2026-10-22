"""
Slides, whiteboards and on-screen code (Phase 2.5, L2): read what a video shows, locally.

Per video: download the video only (no audio, 480p by default) as a whole file, anonymously; pick
frames at scene changes plus one at least every 30 s (typing and scrolling code never trip scene
detection); drop near-duplicates with a difference hash; read each frame with the local vision
model through Ollama (on-screen text in reading order, a one-sentence description of any diagram,
and whether the frame has real content); keep small thumbnails; delete the video.
Design numbers come from the spike (docs/PRD-Phase-2.5.md §3).
"""
import base64
import json
import os
import random
import re
import shutil
import subprocess
import time
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(ROOT, "media_cache", "visual")   # temporary video and raw frames
FRAMES = os.path.join(ROOT, "frames")                 # kept thumbnails: frames/<video_id>/<second>.jpg (git-ignored)

SCENE = 0.25        # ffmpeg scene-change threshold
EVERY = 30          # also a frame at least this often
MIN_GAP = 10        # seconds between kept frames
HAMMING = 6         # dHash bits that may differ for "the same slide"
MAX_FRAMES = 150
THUMB_WIDTH = 320
SECONDS_PER_FRAME = 7.0   # spike: 4-29 s per frame with qwen3.5:4b, typically 4-8
FRAMES_PER_MINUTE = 1.4   # spike: 11-15 kept frames per 10 minutes
DOWNLOAD_DELAY = 4

PROMPT = ("This is a frame from a YouTube video. Decide whether it shows content worth reading: slides, code, a terminal, "
          "a web page, a diagram, a chart or a whiteboard. A person talking with only background objects (books, posters) "
          "is NOT content. Reply as JSON: {\"content\": true or false, \"text\": \"the main on-screen text in reading order, "
          "keeping line breaks, at most 60 lines\", \"visual\": \"one sentence describing any diagram, chart or drawing, "
          "or empty\"}. If content is false, leave text and visual empty.")


class BotCheck(Exception):
    """YouTube asked to confirm we are not a bot: stop and try later."""


def available() -> bool:
    try:
        import PIL  # noqa: F401
    except ImportError:
        return False
    return shutil.which("ffmpeg") is not None


def vision_model(settings: Dict[str, Any]) -> str:
    """settings['vision_model'], else the Ollama model (qwen3.5 small models read images)."""
    return settings.get("vision_model") or settings["providers"]["ollama"].get("model") or ""


def model_sees_images(base_url: str, model: str) -> bool:
    import llm
    try:
        req = urllib.request.Request(llm.ollama_base(base_url) + "/api/show", method="POST",
                                     data=json.dumps({"model": model}).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return "vision" in (json.load(r).get("capabilities") or [])
    except (OSError, ValueError):
        return False


def visual_categories(settings: Dict[str, Any]) -> List[str]:
    """Categories whose "Slides" switch is on (settings['visuals'])."""
    return [c for c, on in (settings.get("visuals") or {}).items() if on]


def estimate_minutes(seconds_of_video: float) -> float:
    return seconds_of_video / 60 * FRAMES_PER_MINUTE * SECONDS_PER_FRAME / 60


def download_video(video_id: str, height: int = 480, dest: str = CACHE) -> str:
    """Video-only stream up to `height`, whole file (section downloads were very slow), anonymously."""
    import yt_dlp
    from enrich import _classify_error, _js_runtimes, _QuietLogger
    os.makedirs(dest, exist_ok=True)
    opts: Dict[str, Any] = {"format": f"bv[height<={height}][vcodec!=none]/bv*[height<={height}]/worstvideo",
                            "quiet": True, "no_warnings": True, "noprogress": True, "logger": _QuietLogger(),
                            "outtmpl": os.path.join(dest, "%(id)s.video.%(ext)s"), "socket_timeout": 30}
    runtimes = _js_runtimes()
    if runtimes:
        opts["js_runtimes"] = runtimes
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
            return y.prepare_filename(info)
    except yt_dlp.utils.DownloadError as e:
        if _classify_error(str(e)) == "bot":
            raise BotCheck(str(e)) from None
        raise


def dhash(path: str, size: int = 8) -> int:
    from PIL import Image
    img = Image.open(path).convert("L").resize((size + 1, size), Image.LANCZOS)
    px = list(img.get_flattened_data() if hasattr(img, "get_flattened_data") else img.getdata())
    return sum(1 << i for i in range(size * size)
               if px[(i // size) * (size + 1) + i % size] > px[(i // size) * (size + 1) + i % size + 1])


def select_frames(candidates: List[Tuple[float, int]], min_gap: float = MIN_GAP, hamming: int = HAMMING,
                  limit: int = MAX_FRAMES) -> List[int]:
    """Indexes of candidates [(time, dhash)] to keep: at least min_gap apart and not a near-duplicate of a kept one."""
    kept: List[int] = []
    last = -1e9
    for i, (t, h) in enumerate(candidates):
        if len(kept) >= limit or t - last < min_gap:
            continue
        if any(bin(h ^ candidates[k][1]).count("1") <= hamming for k in kept):
            continue
        kept.append(i)
        last = t
    return kept


def extract_frames(video_path: str, out_dir: str) -> List[Tuple[float, str]]:
    """Scene changes plus a frame at least every EVERY seconds, deduplicated: [(seconds, jpg path)]."""
    os.makedirs(out_dir, exist_ok=True)
    proc = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-i", video_path, "-vf",
                           f"select='isnan(prev_selected_t)+gt(scene,{SCENE})+gte(t-prev_selected_t,{EVERY})',showinfo",
                           "-vsync", "vfr", "-q:v", "3", os.path.join(out_dir, "raw_%05d.jpg")],
                          capture_output=True, text=True)
    times = [float(x) for x in re.findall(r"pts_time:([\d.]+)", proc.stderr)]
    paths = [os.path.join(out_dir, f) for f in sorted(os.listdir(out_dir)) if f.startswith("raw_")]
    pairs = list(zip(times, paths))
    keep = set(select_frames([(t, dhash(p)) for t, p in pairs]))
    return [pair for i, pair in enumerate(pairs) if i in keep]


def read_frame(path: str, model: str, base_url: str) -> Dict[str, Any]:
    """The vision model's reading of one frame: {'content': bool, 'text': str, 'visual': str}."""
    import llm
    with open(path, "rb") as f:
        img = base64.b64encode(f.read()).decode()
    body = {"model": model, "stream": False, "format": "json", "think": False, "keep_alive": "5m",
            "options": {"temperature": 0, "num_ctx": 8192},
            "messages": [{"role": "user", "content": PROMPT, "images": [img]}]}
    req = urllib.request.Request(llm.ollama_base(base_url) + "/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=600) as r:
        reply = json.load(r).get("message", {}).get("content", "")
    data = llm.parse_json(reply)
    if not isinstance(data, dict):
        raise ValueError("vision model did not return an object")
    content = data.get("content")
    content = content if isinstance(content, bool) else str(content).strip().lower() in ("true", "yes", "1")
    return {"content": content, "text": str(data.get("text") or "").strip()[:4000],
            "visual": " ".join(str(data.get("visual") or "").split())[:400]}


def save_thumb(src: str, video_id: str, t: float) -> str:
    """Small JPEG for the slide strip; returns its file name under frames/<video_id>/."""
    from PIL import Image
    folder = os.path.join(FRAMES, video_id)
    os.makedirs(folder, exist_ok=True)
    name = f"{int(t):05d}.jpg"
    img = Image.open(src).convert("RGB")
    img.thumbnail((THUMB_WIDTH, THUMB_WIDTH))
    img.save(os.path.join(folder, name), "JPEG", quality=72, optimize=True)
    return name


def _clear_cache():
    shutil.rmtree(CACHE, ignore_errors=True)


def run_visual(
    video_ids: Optional[List[str]] = None,
    should_stop: Callable[[], bool] = lambda: False,
    download: Callable[[str], str] = None,
    extract: Callable[[str, str], List[Tuple[float, str]]] = extract_frames,
    read: Callable[[str], Dict[str, Any]] = None,
    thumb: Callable[[str, str, float], str] = save_thumb,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Reads slides/whiteboard/code from marked videos (or the given ones). Prints progress for the job log."""
    import llm
    import storage
    from local_asr import _cool_down

    settings = llm.load_settings()
    queue = storage.videos_needing_visuals(video_ids, visual_categories(settings))
    print(f"🖼️ Reading slides and whiteboards: {len(queue)} video{'s' if len(queue) != 1 else ''}.")
    if not queue:
        return True
    model, base = vision_model(settings), settings["providers"]["ollama"].get("base_url")
    if read is None:
        if not available():
            print("❌ Reading slides needs ffmpeg and Pillow. Run: bash scripts/setup.sh --local-ai")
            return False
        if not model or not model_sees_images(base, model):
            print(f"❌ The vision model ({model or 'none'}) isn't available in Ollama or can't read images. "
                  "Choose one on the AI page (e.g. qwen3.5:4b).")
            return False
        read = lambda path: read_frame(path, model, base)
    height = int(settings.get("visual_height") or 480)
    download = download or (lambda vid: download_video(vid, height))
    minutes = estimate_minutes(sum(v.get("duration_seconds") or 0 for v in queue))
    print(f"   Vision model {model}, {height}p frames. About {max(1, round(minutes))} min plus downloads. "
          "Videos are deleted after reading; small thumbnails are kept.")
    _clear_cache()
    for v in queue:
        storage.set_job(v["video_id"], "visual", status="queued", error=None, progress=0)

    done = failed = 0
    try:
        for n, v in enumerate(queue, 1):
            vid, label = v["video_id"], (v.get("title") or v["video_id"])[:60]
            if should_stop():
                print("⏹️ Stopped. Finished videos are saved; run again for the rest.")
                break
            timings: Dict[str, float] = {}
            work = os.path.join(CACHE, vid)
            path = None
            try:
                storage.set_job(vid, "visual", status="downloading")
                t = time.time()
                path = download(vid)
                timings["download_s"] = round(time.time() - t, 1)
                t = time.time()
                frames = extract(path, work)
                timings["frames_s"] = round(time.time() - t, 1)
                storage.set_job(vid, "visual", status="reading")
                t = time.time()
                kept: List[Dict[str, Any]] = []
                old_thumbs = os.path.join(FRAMES, vid)
                shutil.rmtree(old_thumbs, ignore_errors=True)
                for i, (ts, frame) in enumerate(frames, 1):
                    if should_stop():
                        raise InterruptedError("stopped")
                    r = read(frame)
                    if r["content"] and (r["text"] or r["visual"]):
                        kept.append({"t": round(ts, 1), "text": r["text"], "visual": r["visual"], "thumb": thumb(frame, vid, ts)})
                    storage.set_job(vid, "visual", progress=round(i / max(1, len(frames)), 2))
                timings["read_s"] = round(time.time() - t, 1)
                timings["frames"], timings["kept"] = len(frames), len(kept)
                storage.save_visuals(vid, kept)
                storage.set_job(vid, "visual", status="done", progress=1.0, timings=timings)
                done += 1
                print(f"   [{n}/{len(queue)}] ✓ {label} · {len(kept)} of {len(frames)} frames had content · "
                      f"{timings['read_s']:.0f} s reading")
            except BotCheck:
                storage.set_job(vid, "visual", status="queued")
                print("🛑 YouTube asked to confirm we're not a bot. Stopping; try again in an hour or two.")
                return False
            except InterruptedError:
                storage.set_job(vid, "visual", status="queued")
                print("⏹️ Stopped mid-video; it will start over next time.")
                break
            except Exception as e:   # one bad video shouldn't stop the run
                failed += 1
                storage.set_job(vid, "visual", status="failed", error=str(e)[:300], timings=timings)
                print(f"   [{n}/{len(queue)}] ⚠️ {label}: {str(e)[:120]}")
            finally:
                if path and os.path.exists(path):
                    os.remove(path)            # videos are temporary (Phase 2.5 rule)
                shutil.rmtree(work, ignore_errors=True)
            if n < len(queue):
                _cool_down(sleep)
                sleep(DOWNLOAD_DELAY + random.uniform(0, DOWNLOAD_DELAY / 2))
    finally:
        _clear_cache()
    print(f"✅ {done} read" + (f", {failed} failed" if failed else "") + ".")
    return done > 0 or failed == 0
