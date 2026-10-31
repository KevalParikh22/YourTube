"""
Local web dashboard for YourTube - Phase 0.
Serves web/index.html plus a small JSON API over the local SQLite database.
Standard library only; binds to 127.0.0.1 so nothing is exposed to the network.

    python dashboard.py [--port 8765] [--no-open]
"""
import os
# yt-dlp's old plugin loader registers a module named 'extractor' that hides ours (extractor.py); no plugins are used
os.environ.setdefault("YTDLP_NO_PLUGINS", "1")
import io
import re
import sys
import json
import base64
import binascii
import argparse
import hmac
import secrets
import threading
from http.cookies import SimpleCookie
import webbrowser
import contextvars
import shutil
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import storage
import llm
import applog
import voice
import interests_ai
from classifier import load_taxonomy, save_taxonomy, classify_video, FALLBACK_CATEGORY
from exporter import videos_to_csv, video_markdown, markdown_zip, safe_filename

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(ROOT_DIR, "web")
# The only files /download/ serves (name -> path, content type); nothing else on disk is reachable
DOWNLOADS = {
    "setup.sh": (os.path.join(ROOT_DIR, "scripts", "setup.sh"), "text/x-shellscript; charset=utf-8"),
    "setup.ps1": (os.path.join(ROOT_DIR, "scripts", "setup.ps1"), "text/plain; charset=utf-8"),
    "SETUP.md": (os.path.join(ROOT_DIR, "docs", "SETUP.md"), "text/markdown; charset=utf-8"),
    "yourtube.log": (applog.LOG_FILE, "text/plain; charset=utf-8"),
}
FRAMES_DIR = os.path.join(os.environ.get("YTKB_DATA_DIR") or ROOT_DIR, "frames")
_FRAME_PATH = re.compile(r"^/frames/([\w-]{1,32})/(\d{1,6})\.jpg$")   # only frames/<video id>/<second>.jpg
ALLOWED_BROWSERS = {"auto", "chrome", "edge", "firefox", "brave", "opera", "vivaldi", "chromium", "safari"}
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}
MAX_BODY = 12_000_000
_VOICE_AVAILABLE = None


class BodyTooLarge(Exception):
    pass

# Access token (always on, decided 2026-10-28): protects the dashboard from other users of a shared computer.
# main() sets TOKEN; None turns the check off (tests that build a server directly).
TOKEN = None
TOKEN_COOKIE = "ytkb_token"
TOKEN_HELP = ("<!doctype html><meta charset=utf-8><title>YourTube</title><body style='font:15px system-ui;margin:48px;max-width:560px'>"
              "<h1 style='font-size:20px'>Open your dashboard link</h1><p>For your privacy, this dashboard opens only with its access link. "
              "Use the link printed in the terminal where you started it, or run:</p>"
              "<pre style='background:#eee;padding:10px'>.venv/bin/python dashboard.py --print-url{port}</pre>"
              "<p>After that, this browser remembers it.</p></body>")

def load_token():
    """The dashboard's access token, created on first use and kept in settings.json (readable only by you)."""
    settings = llm.load_settings()
    if not settings.get("dashboard_token"):
        settings["dashboard_token"] = secrets.token_urlsafe(24)
        llm.save_settings(settings)
    return settings["dashboard_token"]

def _same(a, b):
    return hmac.compare_digest(str(a).encode("utf-8"), str(b).encode("utf-8"))

LANES = ("youtube", "local", "cloud")   # reading YouTube, this computer's GPU/CPU, cloud AI: one task each at a time
_AI_TASK = {"rate": "rate", "knowledge": "rate", "summarize": "summarize", "suggest": "categorize", "discover": "categorize"}
_MEDIA_TASK = {"asr": "transcribe", "visual": "slides", "embed": "embed"}

def lane_for(kind, settings):
    """The lane a task kind runs in: work on this computer, cloud AI, or everything else (YouTube reads, library)."""
    if kind == "pull":
        return "local"
    if kind in _MEDIA_TASK:
        return "local" if llm.media_provider(settings, _MEDIA_TASK[kind]) == "local" else "cloud"
    if kind in _AI_TASK:
        return "local" if llm.is_local(settings, _AI_TASK[kind]) else "cloud"
    return "youtube"

# Per-task console output and stop flag. Context variables (not redirect_stdout, which is process-wide) let tasks in
# different lanes print and stop independently; helper threads started with contextvars.copy_context().run share them.
_OUT = contextvars.ContextVar("job_out", default=None)
_STOP = contextvars.ContextVar("job_stop", default=None)

class _RoutedStdout:
    def __init__(self, real):
        self.real = real

    def write(self, text):
        out = _OUT.get()
        return (self.real if out is None else out).write(text)

    def flush(self):
        out = _OUT.get()
        (self.real if out is None else out).flush()

    def __getattr__(self, name):
        return getattr(self.real, name)

def _idle(lane=None):
    return {"id": 0, "running": False, "ok": None, "log": "", "kind": None, "label": None, "lane": lane,
            "started_at": None, "finished_at": None, "dismissed": False}

class _Lane:
    def __init__(self, name):
        self.name = name
        self.pending = []    # [{'id', 'kind', 'label', 'fn', 'key', 'retry_key', 'lane', 'queued_at'}]
        self.state = _idle(name)
        self.buf = io.StringIO()
        self.stop = threading.Event()
        self.worker = None

class Job:
    """
    Background tasks (syncs, details, AI runs, transcription, downloads) in queues, one per lane (LANES): each lane
    runs one task at a time and the rest wait their turn, so a cloud AI task can run while the GPU transcribes.
    The same task (kind + key, default the label) can't be queued twice in any lane. Console output is captured per task.
    """
    HISTORY = 5   # finished tasks kept for the status panel

    def __init__(self):
        self.lock = threading.Lock()
        self.lanes = {name: _Lane(name) for name in LANES}
        self.last = _idle()  # the last finished task, with its log (shown when nothing runs)
        self.done = []       # recent finished tasks, newest first (without logs)
        self.tasks = {}      # retry functions for retained finished tasks
        self._next = 1

    def stop_requested(self):
        """True when the task calling this was asked to stop (False outside a task)."""
        stop = _STOP.get()
        return stop is not None and stop.is_set()

    def busy(self, kind, lane):
        with self.lock:
            state = self.lanes[lane].state
            return bool(state["running"] and state["kind"] == kind)

    def request_stop(self, task_id=None):
        """Stops a running task, or takes a waiting one off its queue. Without an id: stops every running task."""
        with self.lock:
            for lane in self.lanes.values():
                if task_id is not None and any(t["id"] == task_id for t in lane.pending):
                    lane.pending = [t for t in lane.pending if t["id"] != task_id]
                    return True
            hit = False
            for lane in self.lanes.values():
                if lane.state["running"] and task_id in (None, lane.state["id"]):
                    lane.stop.set()
                    hit = True
            return hit

    def snapshot(self):
        with self.lock:
            running = [l for l in self.lanes.values() if l.state["running"]]
            if running:   # the old single-task fields follow the most recently started task
                lane = max(running, key=lambda l: l.state["id"])
                snap = {k: v for k, v in lane.state.items() if k != "key"}
                snap["log"], snap["stopping"] = lane.buf.getvalue(), lane.stop.is_set()
            else:
                snap = {**self.last, "stopping": False}
            snap["lanes"] = [{"lane": l.name, "running": l.state["running"], "id": l.state["id"] if l.state["running"] else None,
                              "kind": l.state["kind"] if l.state["running"] else None,
                              "label": l.state["label"] if l.state["running"] else None,
                              "started_at": l.state["started_at"] if l.state["running"] else None,
                              "log": l.buf.getvalue() if l.state["running"] else "",
                              "stopping": bool(l.state["running"] and l.stop.is_set())} for l in self.lanes.values()]
            snap["queue"] = sorted(({k: t[k] for k in ("id", "kind", "label", "lane", "queued_at")}
                                    for l in self.lanes.values() for t in l.pending), key=lambda t: t["id"])
            snap["recent"] = list(self.done)
            snap["dismissed"] = bool(snap.get("dismissed"))
        return snap

    def start(self, kind, label, fn, key=None, lane=None):
        """
        Queues a task in its lane (lane_for(kind) unless given). Returns {'id', 'position', 'lane'} (position 0 =
        started now), or None when the same task (kind + key, default the label) is already waiting or running.
        """
        lane = lane or lane_for(kind, llm.load_settings())
        retry_key = key if key is not None else label
        key = json.dumps([kind, retry_key], sort_keys=True, default=str)
        with self.lock:
            for l in self.lanes.values():
                if (l.state["running"] and l.state.get("key") == key) or any(t["key"] == key for t in l.pending):
                    return None
            target = self.lanes[lane]
            task = {"id": self._next, "kind": kind, "label": label, "fn": fn, "key": key, "retry_key": retry_key, "lane": lane,
                    "queued_at": datetime.now().isoformat(timespec="seconds")}
            self._next += 1
            target.pending.append(task)
            position = len(target.pending) - (0 if target.state["running"] else 1)
            if not (target.worker and target.worker.is_alive()):
                target.worker = threading.Thread(target=self._work, args=(target,), daemon=True)
                target.worker.start()
        return {"id": task["id"], "position": position, "lane": lane}

    def retry(self, task_id):
        with self.lock:
            finished = next((t for t in self.done if t["id"] == task_id), None)
            # only a lane's latest finished task, if it failed: a later run of that lane replaces the failure
            if (finished is None or finished["ok"] or finished.get("stopped")
                    or next(t for t in self.done if t["lane"] == finished["lane"]) is not finished):
                return None
            task = self.tasks.get(task_id)
        if task is None:
            return None
        return self.start(task["kind"], task["label"], task["fn"], key=task["retry_key"], lane=task["lane"])

    def dismiss(self, task_id):
        with self.lock:
            if self.last.get("id") != task_id:
                return False
            self.last["dismissed"] = True
            return True

    def _work(self, lane):
        if not isinstance(sys.stdout, _RoutedStdout):
            sys.stdout = _RoutedStdout(sys.stdout)
        while True:
            with self.lock:
                if not lane.pending:
                    lane.worker = None
                    return
                task = lane.pending.pop(0)
                self.tasks[task["id"]] = task
                lane.buf = io.StringIO()
                lane.stop = threading.Event()
                lane.state = {**_idle(lane.name), "id": task["id"], "key": task["key"], "running": True, "kind": task["kind"],
                              "label": task["label"], "started_at": datetime.now().isoformat(timespec="seconds")}
            self._run(lane, task["fn"])

    def _run(self, lane, fn):
        ok = False
        out, stop = _OUT.set(lane.buf), _STOP.set(lane.stop)
        try:
            ok = fn()
        except Exception as e:  # surface anything unexpected in the job log
            lane.buf.write(f"\nUnexpected error: {e}\n")
            applog.get("job").exception("Unexpected error in task %s", lane.state.get("label"))
        finally:
            _OUT.reset(out)
            _STOP.reset(stop)
        with self.lock:
            stopped = lane.stop.is_set()
            state = {**lane.state, "running": False, "ok": bool(ok), "stopped": stopped, "log": lane.buf.getvalue(),
                     "finished_at": datetime.now().isoformat(timespec="seconds")}
            state.pop("key", None)
            lane.state = _idle(lane.name)
            self.last = state
            self.done.insert(0, {k: state.get(k) for k in ("id", "kind", "label", "lane", "ok", "stopped", "started_at", "finished_at")})
            del self.done[self.HISTORY:]
            retained = {t["id"] for t in self.done}
            self.tasks = {task_id: task for task_id, task in self.tasks.items() if task_id in retained}
        applog.log_task(state.get("kind"), state.get("label"), bool(ok), stopped,
                        state.get("started_at"), state.get("finished_at"), state["log"])
        # Keep the meaning index current after anything that adds or changes searchable text, as its own task
        # shortcut: an index update already running doesn't queue another, so text saved meanwhile waits for the next one
        if ok and not stopped and state["kind"] in AUTO_EMBED_KINDS:
            import embedder
            try:
                if embedder.available(llm.load_settings()):
                    self.start("embed", "Update meaning index",
                               lambda: embedder.run_embed(should_stop=self.stop_requested, quiet=True), key="auto")
            except Exception:  # never let this end the lane's worker thread
                applog.get("job").exception("Could not queue the meaning index update")

def _library_name(library):
    return next((l["name"] for l in storage.get_libraries() if l["id"] == library), "all libraries")

def _sync(url, browser, cookies_file, library=1):
    from main import run_pipeline  # deferred so the dashboard starts even if yt-dlp is missing
    settings = llm.load_settings()
    ok = run_pipeline(url, browser_cookies=browser, cookies_file=cookies_file, library=library)
    if ok and settings["auto_after_sync"] and llm.is_configured(settings, "categorize") and not JOB.stop_requested():
        from ai_classifier import run_suggest
        print()
        run_suggest("unsorted", library=library, should_stop=JOB.stop_requested)
    if ok and settings.get("details_after_sync"):
        from enrich import run_enrich
        print()
        run_enrich(limit=AFTER_SYNC_DETAILS_LIMIT, should_stop=JOB.stop_requested)  # a failure here doesn't fail the sync
    return ok

AFTER_SYNC_DETAILS_LIMIT = 50

def _remove_playlist(playlist_id, title):
    """Removes a playlist from YourTube only (storage.remove_playlist); the YouTube playlist is never touched."""
    print(f'Removing "{title}" from YourTube. Nothing changes on YouTube.')
    done = storage.remove_playlist(playlist_id)
    for vid in done["cleared"]:
        if re.fullmatch(r"[\w-]+", vid):   # never a path outside frames/
            shutil.rmtree(os.path.join(FRAMES_DIR, vid), ignore_errors=True)
    n = lambda k, word: f"{done[k]} {word}{'' if done[k] == 1 else 's'}"
    print(f"   {n('removed', 'video')} removed with their subtitles, ratings and summaries.")
    if done["kept_notes"]:
        print(f"   {n('kept_notes', 'video')} with your notes kept their notes.")
    if done["shared"]:
        print(f"   {n('shared', 'video')} also in other playlists stayed.")
    return True

def _add_video(video_id, library=1):
    """Adds one video by its link to the 'Added one by one' source, sorts it by keywords and fetches its details."""
    from classifier import classify_playlist_videos
    from extractor import extract_video
    print(f"Step 1: Reading the video {video_id} (without your sign-in)")
    try:
        video = extract_video(video_id)
    except Exception as e:
        print(f"Error: could not read the video: {e}")
        return False
    print(f"   {video['title']} · {video['channel']} · {round(video['duration_seconds'] / 60)} min")
    print("\nStep 2: Sorting into a category")
    classify_playlist_videos([video], load_taxonomy(library=library))
    print(f"   {video['category']}")
    print("\nStep 3: Saving")
    source = storage.SINGLE_SOURCE if library == 1 else {**storage.SINGLE_SOURCE, "id": f"_single-{library}"}
    counts = storage.save_playlist(source, [video], partial=True, library_id=library)
    print("   Added to the library (source: Added one by one)." if counts["added"] else "   Already in the library; refreshed.")
    settings = llm.load_settings()
    if settings.get("auto_after_sync") and llm.is_configured(settings, "categorize"):
        from ai_classifier import run_suggest
        print()
        run_suggest("unsorted")
    from enrich import run_enrich
    print()
    run_enrich(video_ids=[video_id], should_stop=JOB.stop_requested)   # its details now; a failure here doesn't fail the add
    return True

def _label(action, ids):
    """Task label: 'Rate: 3 videos', 'Rate: <title>' for one video, or the action alone for 'everything that needs it'."""
    if ids is None:
        return action
    if len(ids) == 1:
        title = next((v["title"] for v in storage.get_all_videos(include_removed=True) if v["video_id"] == ids[0]), ids[0])
        return f"{action}: {title[:60]}"
    return f"{action}: {len(ids)} videos"

def _pull_then_index(model):
    """Downloads a model; when it is the embedding model, builds the meaning index right away."""
    import embedder
    import local_setup
    ok = local_setup.pull_model(model, should_stop=JOB.stop_requested)
    if ok and model == embedder.model_name(llm.load_settings()) and not JOB.stop_requested():
        print()
        embedder.run_embed(should_stop=JOB.stop_requested)
    return ok

AUTO_EMBED_KINDS = {"sync", "details", "asr", "visual", "rate", "summarize"}
JOB = Job()

def _all_category_names():
    """Category names across every library, plus any still on a video."""
    names = {v["category"] for v in storage.get_all_videos(include_removed=True)}
    for lib in storage.get_libraries():
        names |= set(load_taxonomy(library=lib["id"]))
    return names

def _library(value):
    """A library id from a request: an existing library's id, else 1. 0 or 'all' means every library (None)."""
    if value in (0, "0", "all"):
        return None
    try:
        lib = int(value)
    except (TypeError, ValueError):
        return 1
    return lib if any(l["id"] == lib for l in storage.get_libraries()) else 1

def _library_videos(library):
    """IDs of the videos in a library."""
    return [vid for vid, lib in storage.video_libraries().items() if lib == library]

def _reclassify(library=None, ids=None, unlock=False):
    """Keyword re-sorting with each video's own library's categories. Returns how many changed."""
    libs = storage.video_libraries()
    targets = list(ids) if ids is not None else [v for v, l in libs.items() if library is None or l == library]
    by_lib = {}
    for vid in targets:
        by_lib.setdefault(libs.get(vid, 1), []).append(vid)
    return sum(storage.reclassify_videos(_classifier(lib), vids, unlock=unlock) for lib, vids in by_lib.items())

def _transcript_categories(settings):
    from enrich import transcripts_wanted
    return {n: transcripts_wanted(n, settings) for n in sorted(_all_category_names())}

def _visual_categories(settings):
    names = _all_category_names()
    on = settings.get("visuals") or {}
    return {n: bool(on.get(n)) for n in sorted(names)}

def build_state(library=1):
    """Everything the dashboard shows. Categories, presets, notes, the proposal and interests are those of `library`
    (None = all libraries: categories are the union, and the rest are library 1's)."""
    settings = llm.load_settings()
    if library is None:
        taxonomy = {}
        for lib in storage.get_libraries():
            for name, kws in load_taxonomy(library=lib["id"]).items():
                taxonomy.setdefault(name, kws)
    else:
        taxonomy = load_taxonomy(library=library)
    global _VOICE_AVAILABLE
    if not _VOICE_AVAILABLE:   # only "yes" is kept: installing faster-whisper later must not need a restart
        _VOICE_AVAILABLE = voice.available()
    return {
        "library": library or 0,
        "libraries": storage.get_libraries(),
        "playlists": storage.get_playlists(),
        "videos": storage.get_library(),
        "taxonomy": taxonomy,
        "tax_key": storage.taxonomy_key(taxonomy),   # matches videos.ai_no_match while the categories stay the same
        "fallback_category": FALLBACK_CATEGORY,
        "job": JOB.snapshot(),
        "ai": llm.public_settings(settings, library or 1),
        "transcripts": _transcript_categories(settings),
        "visual_categories": _visual_categories(settings),
        "details_after_sync": bool(settings.get("details_after_sync")),
        "use_descriptions": settings.get("use_descriptions", True) is not False,
        "platform": sys.platform,
        "voice": _VOICE_AVAILABLE,
        "summarised": list(storage.summary_times()),   # video ids with a summary (Knowledge: Summarise 5)
    }

class Handler(BaseHTTPRequestHandler):
    server_version = "YourTube/0.3"

    def log_message(self, fmt, *args):
        pass  # keep the console quiet; errors are returned to the UI

    # --- helpers -------------------------------------------------------------
    def _token_help(self):
        port = self.server.server_address[1]
        return TOKEN_HELP.replace("{port}", "" if port == 8765 else f" --port {port}")   # the link must name this server's port

    def _token_ok(self):
        if TOKEN is None:
            return True
        morsel = SimpleCookie(self.headers.get("Cookie") or "").get(TOKEN_COOKIE)
        return morsel is not None and _same(morsel.value, TOKEN)

    def _host_ok(self):
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        return host in ALLOWED_HOSTS

    def _send(self, code, body, content_type="application/json; charset=utf-8", extra_headers=None):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self._headers_sent = True
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, default=str))

    def _error(self, msg, code=400):
        if code in (401, 403):
            applog.get("http").warning("Request denied: %s %s (%s)", self.command,
                                        urlparse(self.path).path, msg)
        self._json({"error": msg}, code)

    def _queued(self, started):
        """Reply for a task handed to JOB: where it is in the queue, or 409 when it is already there."""
        if started is None:
            return self._error("That task is already running or waiting in the queue", 409)
        snap, lane = JOB.snapshot(), started.get("lane")
        ahead = [l["label"] for l in snap["lanes"] if l["running"] and l["lane"] == lane and l["id"] != started["id"]] + \
                [t["label"] for t in snap["queue"] if t["lane"] == lane and t["id"] < started["id"]]
        return self._json({"ok": True, "id": started["id"], "position": started["position"], "after": ahead})

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise json.JSONDecodeError("bad Content-Length", "", 0) from None
        if length < 0:
            raise json.JSONDecodeError("bad Content-Length", "", 0)
        if length > MAX_BODY:
            raise BodyTooLarge()
        raw = self.rfile.read(length) if length else b"{}"
        data = json.loads(raw or b"{}")
        if not isinstance(data, dict):
            raise json.JSONDecodeError("expected a JSON object", "", 0)
        return data

    # --- GET -------------------------------------------------------------------
    def do_GET(self):
        self._handle_request(self._do_get, "GET")

    def do_POST(self):
        self._handle_request(self._do_post, "POST")

    def _handle_request(self, handler, method):
        try:
            handler()
        except (BrokenPipeError, ConnectionResetError):
            pass  # the browser went away mid-reply (reload, closed tab); nothing to report
        except Exception:
            applog.get("http").exception("Request failed: %s %s", method, urlparse(self.path).path)
            if not getattr(self, "_headers_sent", False):
                self._headers_buffer = []
                try:
                    self._json({"error": "Something went wrong. Details are in logs/yourtube.log"}, 500)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass

    def _do_get(self):
        if not self._host_ok():
            return self._error("Forbidden host", 403)
        url = urlparse(self.path)
        qs = parse_qs(url.query)
        playlist = (qs.get("playlist") or ["all"])[0]

        if TOKEN is not None and url.path in ("/", "/index.html") and "t" in qs:
            if not _same(qs["t"][0], TOKEN):
                return self._send(200, self._token_help(), "text/html; charset=utf-8")
            # Swap the link's token for a cookie, then drop it from the address bar
            cookie = f"{TOKEN_COOKIE}={TOKEN}; HttpOnly; SameSite=Strict; Path=/; Max-Age=31536000"
            return self._send(303, b"", "text/plain", extra_headers={"Location": "/", "Set-Cookie": cookie})
        if not self._token_ok():
            if url.path in ("/", "/index.html"):
                return self._send(200, self._token_help(), "text/html; charset=utf-8")
            return self._error("Open the dashboard link with its access token", 401)

        if url.path in ("/", "/index.html"):
            with open(os.path.join(WEB_DIR, "index.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        m = _FRAME_PATH.match(url.path)
        if m:
            path = os.path.join(FRAMES_DIR, m.group(1), m.group(2) + ".jpg")
            if not os.path.isfile(path):
                return self._error("Not found", 404)
            with open(path, "rb") as f:
                return self._send(200, f.read(), "image/jpeg", extra_headers={"Cache-Control": "max-age=86400"})
        if url.path.startswith("/download/") or url.path == "/docs/setup":
            name = "SETUP.md" if url.path == "/docs/setup" else url.path[len("/download/"):]
            if name not in DOWNLOADS:
                return self._error("Not found", 404)
            path, ctype = DOWNLOADS[name]
            if name == "yourtube.log":
                path = applog.LOG_FILE
            if name == "yourtube.log" and not os.path.isfile(path):
                return self._error("Log file not found", 404)
            with open(path, "rb") as f:
                data = f.read()
            headers = {"Content-Disposition": f'attachment; filename="{name}"'} if url.path.startswith("/download/") else None
            return self._send(200, data, ctype, extra_headers=headers)
        if url.path == "/api/state":
            return self._json(build_state(_library((qs.get("library") or ["1"])[0])))
        if url.path == "/api/job":
            return self._json(JOB.snapshot())
        if url.path == "/api/logs":
            levels = {"debug": 10, "info": 20, "warning": 30, "error": 40, "critical": 50}
            level_name = (qs.get("level") or ["warning"])[0].lower()
            if level_name not in levels:
                return self._error("level must be debug, info, warning, error or critical")
            try:
                limit = max(1, min(1000, int((qs.get("lines") or ["200"])[0])))
            except ValueError:
                return self._error("lines must be a number")
            records = []
            if os.path.isfile(applog.LOG_FILE):
                with open(applog.LOG_FILE, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        if re.match(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d (DEBUG|INFO|WARNING|ERROR|CRITICAL) ", line):
                            records.append([line.rstrip("\n")])
                        elif records:
                            records[-1].append(line.rstrip("\n"))
                minimum = levels[level_name]
                records = [r for r in records if levels.get(re.match(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d (\w+)", r[0]).group(1).lower(), 0) >= minimum]
            selected = []
            count = 0
            for record in reversed(records):
                if count + len(record) > limit:
                    continue
                selected.append(record)
                count += len(record)
            lines = [line for record in reversed(selected) for line in record]
            return self._json({"path": applog.LOG_FILE, "lines": lines})
        if url.path == "/api/export/markdown":
            vid = (qs.get("id") or [""])[0]
            v = next((x for x in storage.get_library() if x["video_id"] == vid), None)
            if v is None:
                return self._error("Unknown video", 404)
            name = safe_filename(v["title"]) + ".md"
            return self._send(200, video_markdown(v, storage.get_details(vid), storage.get_summary(vid)), "text/markdown; charset=utf-8",
                              extra_headers={"Content-Disposition": _attachment(name)})
        if url.path == "/api/playlists/remove":
            try:
                return self._json(storage.remove_playlist_plan((qs.get("id") or [""])[0]))
            except ValueError as e:
                return self._error(str(e), 404)
        if url.path == "/api/retrieve":
            import retrieval
            q = (qs.get("q") or [""])[0][:500]
            if not q.strip():
                return self._error("q is required")
            split = lambda name: [x for x in (qs.get(name) or [""])[0].split(",") if x]
            filters = {"categories": split("categories"), "playlists": split("playlists"), "video_ids": split("videos"),
                       "include_done": (qs.get("done") or ["1"])[0] != "0"}
            k = max(1, min(30, int((qs.get("k") or ["8"])[0] or 8)))
            return self._json(retrieval.retrieve(q, k=k, filters=filters))
        if url.path == "/api/ask/history":
            return self._json({"items": storage.list_asks((qs.get("q") or [""])[0][:200])})
        if url.path in ("/api/ask/item", "/api/export/ask"):
            import asker
            from exporter import ask_markdown
            try:
                item = storage.get_ask(int((qs.get("id") or ["0"])[0]))
            except ValueError:
                item = None
            if item is None:
                return self._error("Unknown question", 404)
            if url.path == "/api/ask/item":
                return self._json({"thread": [asker.from_history(x) for x in storage.get_thread(item["id"])]})
            name = safe_filename("Q " + item["question"]) + ".md"
            return self._send(200, ask_markdown(storage.get_thread(item["id"])), "text/markdown; charset=utf-8",
                              extra_headers={"Content-Disposition": _attachment(name)})
        if url.path in ("/api/embed/status", "/api/embed/estimate"):
            import embedder
            settings = llm.load_settings()
            wanted = (qs.get("provider") or [""])[0]
            if wanted in llm.MEDIA_TASKS["embed"]["providers"]:   # estimate a choice before it is saved
                settings = {**settings, "tasks": {**settings.get("tasks", {}), "embed": wanted}}
            model = embedder.model_name(settings)
            status = storage.embedding_status(model)
            if url.path == "/api/embed/status":
                return self._json({"model": model, "available": embedder.available(settings), **status})
            engine = llm.media_provider(settings, "embed")
            missing = storage.docs_missing_embeddings(model)
            if engine == "local":
                cost, sends = 0.0, "Nothing is sent; indexed text is processed locally."
            else:
                price = llm.MEDIA_PRICES.get((engine, model.split(":", 1)[1]), 0.0)
                tokens = sum(max(1, len(row.get("text", "")) // 4) for row in missing)
                cost = round(tokens / 1_000_000 * price, 5)
                sends = f"About {tokens:,} tokens of indexed text are sent to {llm.PROVIDERS[engine]['label']}."
            return self._json({"model": model, "available": embedder.available(settings), "pieces": len(missing),
                               "cost": cost, "sends": sends, **status})
        if url.path == "/api/visuals/plan":
            import local_visual
            settings = llm.load_settings()
            cands = storage.videos_needing_visuals(None, local_visual.visual_categories(settings))
            secs = sum(v.get("duration_seconds") or 0 for v in cands)
            model = local_visual.vision_model(settings)
            base = settings["providers"]["ollama"].get("base_url")
            engine = llm.media_provider(settings, "slides")
            frames = max(1, round(secs / 60 * local_visual.FRAMES_PER_MINUTE)) if cands else 0
            helper = llm.helper_for(settings, "slides")
            cloud = engine if engine != "local" else (helper if helper and helper != "local" else None)
            if cloud is None:
                cost, sends = 0.0, "Nothing is sent; video downloads and frame analysis run locally."
            else:   # with a cloud helper: the most it can cost (as if the cloud read every video)
                cfg = settings["providers"][cloud]
                price = llm.PRICES.get(cfg.get("model"), (None, None))[0]
                tokens = frames * llm.IMAGE_TOKENS.get(llm.PROVIDERS[cloud]["kind"], 258)
                cost = round(tokens / 1_000_000 * price, 5) if price is not None else None
                sends = f"About {frames} slide frames are sent to {llm.PROVIDERS[cloud]['label']}" + (
                    " (at most: the helper shares the work)." if cloud != engine else ".")
            return self._json({"available": local_visual.available(), "model": model, "engine": engine,
                               "model_ok": bool(model) and local_visual.model_sees_images(base, model),
                               "videos": len(cands), "seconds": secs,
                               "minutes": round(local_visual.estimate_minutes(secs) + len(cands) * 15 / 60, 1),
                               "frames": frames, "cost": cost, "sends": sends})
        if url.path in ("/api/asr/plan", "/api/asr/estimate"):
            import local_asr
            ids = (qs.get("ids") or [""])[0].split(",") if "ids" in qs else None
            ids = [i.strip() for i in ids if i.strip()] if ids else None
            estimate = local_asr.estimate(ids)
            return self._json(estimate)
        if url.path == "/api/search":
            q = (qs.get("q") or [""])[0][:200]
            kinds = [k for k in (qs.get("kinds") or [""])[0].split(",") if k in storage.SEARCH_KINDS] or None
            if kinds is None and llm.load_settings().get("use_descriptions", True) is False:
                kinds = [k for k in storage.SEARCH_KINDS if k != "description"]
            if (qs.get("meaning") or ["0"])[0] == "1":
                import retrieval
                return self._json(retrieval.search_by_meaning(q, kinds))
            return self._json(storage.search(q, kinds))
        if url.path == "/api/local/status":
            import local_setup
            return self._json(local_setup.status())
        if url.path in ("/api/knowledge", "/api/knowledge/page", "/api/export/knowledge"):
            import knowledge
            lib = _library((qs.get("library") or ["1"])[0]) or 1
            if url.path == "/api/knowledge":
                return self._json({"library": lib, "categories": knowledge.status(lib)})
            category = (qs.get("category") or [""])[0]
            found = storage.get_page(lib, category)
            if url.path == "/api/knowledge/page":
                return self._json(found or {"page": None})
            if not found:
                return self._error("No page for this category yet", 404)
            return self._send(200, knowledge.page_markdown(category, found["page"], found["sources"]), "text/markdown; charset=utf-8",
                              extra_headers={"Content-Disposition": _attachment(safe_filename(category) + ".md")})
        if url.path == "/api/summary":
            import summarizer
            vid = (qs.get("id") or [""])[0]
            summary = storage.get_summary(vid)   # None: not summarised yet (not an error)
            return self._json({"summary": summary, "moments": summarizer.related_moments(vid, summary) if summary else {}})
        if url.path == "/api/video":
            vid = (qs.get("id") or [""])[0]
            details = storage.get_details(vid)
            return self._json({"details": details}) if details is not None else self._error("No details yet", 404)
        if url.path == "/api/export":
            fmt = (qs.get("format") or ["csv"])[0]
            videos = storage.get_all_videos(None if playlist == "all" else playlist, include_removed=True)
            stamp = datetime.now().strftime("%Y%m%d")
            if fmt == "json":
                return self._send(200, json.dumps(videos, indent=2, default=str, ensure_ascii=False),
                                  extra_headers={"Content-Disposition": f'attachment; filename="yourtube_{stamp}.json"'})
            return self._send(200, "﻿" + videos_to_csv(videos), "text/csv; charset=utf-8",
                              extra_headers={"Content-Disposition": f'attachment; filename="yourtube_{stamp}.csv"'})
        return self._error("Not found", 404)

    # --- POST ------------------------------------------------------------------
    def _do_post(self):
        # Custom header + JSON content type force a CORS preflight, which we never answer,
        # so other websites open in the browser cannot drive this API.
        if not self._host_ok() or self.headers.get("X-YTKB") != "1":
            return self._error("Forbidden", 403)
        if not self._token_ok():
            return self._error("Open the dashboard link with its access token", 401)
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            return self._error("Expected JSON", 415)
        try:
            body = self._body()
        except BodyTooLarge:
            return self._error("Request too large", 413)
        except json.JSONDecodeError:
            return self._error("Invalid JSON")

        path = urlparse(self.path).path
        if path == "/api/voice/transcribe":
            if not voice.available():
                return self._error("Voice input needs local transcription. Run: bash scripts/setup.sh --local-ai", 503)
            audio = body.get("audio")
            mime = body.get("mime")
            if not isinstance(audio, str):
                return self._error("Could not read the recording")
            try:
                data = base64.b64decode(audio, validate=True)
            except (binascii.Error, ValueError):
                return self._error("Could not read the recording")
            try:
                return self._json(voice.transcribe_clip(data, mime))
            except ValueError as e:
                applog.get("http").warning("Voice transcription failed: reason=%s bytes=%d", str(e), len(data))
                return self._error(str(e))
            except Exception:
                applog.get("http").exception("Voice transcription failed unexpectedly (%d bytes)", len(data))
                return self._error("Could not transcribe the recording", 500)
        try:
            if path == "/api/videos/update":
                ids = body.get("ids") or []
                if body.get("reset_category"):
                    changed = _reclassify(ids=ids, unlock=True)
                    return self._json({"ok": True, "changed": changed})
                n = storage.update_videos(ids, status=body.get("status"),
                                          category=_clean(body.get("category")), notes=body.get("notes"))
                return self._json({"ok": True, "updated": n})

            lib = _library(body.get("library"))
            if path in ("/api/taxonomy", "/api/categories/rename", "/api/categories/delete", "/api/reclassify",
                        "/api/presets", "/api/ai/discover", "/api/ai/proposal/apply", "/api/ai/proposal/dismiss",
                        "/api/categories/separate") and lib is None:
                return self._error("Choose a library first: categories belong to one library")

            if path == "/api/libraries":
                action = body.get("action")
                if action == "create":
                    return self._json({"ok": True, "id": storage.create_library(body.get("name")), "libraries": storage.get_libraries()})
                lib_id = body.get("id")
                if not isinstance(lib_id, int) or isinstance(lib_id, bool):
                    return self._error("id must be a number")
                if action == "rename":
                    storage.rename_library(lib_id, body.get("name"))
                elif action == "delete":
                    storage.delete_library(lib_id)
                else:
                    return self._error("Unknown action")
                return self._json({"ok": True, "libraries": storage.get_libraries()})

            if path == "/api/playlists/remove":
                pid = _clean(body.get("id")) or ""
                found = storage.get_playlist(pid)
                if found is None:
                    return self._error("Unknown playlist", 404)
                return self._queued(JOB.start("remove", f"Remove playlist: {found['title']}",
                                              lambda: _remove_playlist(pid, found["title"]), key=pid))

            if path == "/api/playlists/library":
                target = body.get("library")
                if not isinstance(target, int) or isinstance(target, bool):
                    return self._error("library must be a number")
                pid = _clean(body.get("id")) or ""
                storage.set_playlist_library(pid, target)
                # Its videos now sort with the new library's categories (ones set by hand or accepted keep theirs)
                moved = [v["video_id"] for v in storage.get_library() if v["library"] == target and any(x["id"] == pid for x in v["sources"])]
                changed = _reclassify(ids=moved)
                return self._json({"ok": True, "playlists": storage.get_playlists(), "changed": changed})

            if path == "/api/taxonomy":
                tax = body.get("taxonomy")
                if not isinstance(tax, dict):
                    return self._error("taxonomy must be an object")
                cleaned = {}
                for name, kws in tax.items():
                    name = _clean(name)
                    if not name or name == FALLBACK_CATEGORY:
                        continue
                    cleaned[name] = sorted({str(k).strip().lower() for k in (kws or []) if str(k).strip()})
                save_taxonomy(cleaned, library=lib)
                return self._json({"ok": True, "taxonomy": cleaned})

            if path == "/api/categories/rename":
                old, new = _clean(body.get("old")), _clean(body.get("new"))
                if not old or not new:
                    return self._error("Both old and new names are required")
                tax = load_taxonomy(library=lib)
                if new in tax and new != old:
                    return self._error(f'Category "{new}" already exists')
                if old in tax:
                    tax = {(new if k == old else k): v for k, v in tax.items()}
                    save_taxonomy(tax, library=lib)
                n = storage.rename_category(old, new, video_ids=_library_videos(lib))
                settings = llm.load_settings()
                own = llm.library_settings(settings, lib)
                notes = dict(own["category_notes"])
                if old in notes:
                    notes[new] = notes.pop(old)
                llm.set_library_settings(settings, lib, presets=[new if p == old else p for p in own["presets"]], category_notes=notes)
                for key in ("transcripts", "visuals"):
                    if old in settings.get(key, {}):
                        settings[key][new] = settings[key].pop(old)
                llm.save_settings(settings)
                return self._json({"ok": True, "updated": n})

            if path == "/api/categories/delete":
                name = _clean(body.get("name"))
                tax = load_taxonomy(library=lib)
                tax.pop(name, None)
                save_taxonomy(tax, library=lib)
                n = storage.release_category(name, video_ids=_library_videos(lib))
                settings = llm.load_settings()
                own = llm.library_settings(settings, lib)
                llm.set_library_settings(settings, lib, presets=[p for p in own["presets"] if p != name],
                                         category_notes={k: v for k, v in own["category_notes"].items() if k != name})
                if name not in _all_category_names():   # per-category switches are shared by name across libraries
                    settings.get("transcripts", {}).pop(name, None)
                    settings.get("visuals", {}).pop(name, None)
                llm.save_settings(settings)
                return self._json({"ok": True, "moved": n})

            if path == "/api/categories/separate":
                # Combined categories ("Health, Fitness & Wellness") become one category per topic, without duplicates;
                # their videos get AI suggestions for their best topic. preview: true only lists the topics.
                from ai_classifier import is_combined, separate_topics, run_suggest
                tax = load_taxonomy(library=lib)
                names = body.get("names")
                if not isinstance(names, list) or not names or not all(isinstance(n, str) and n in tax and is_combined(n) for n in names):
                    return self._error("Choose combined categories of this library")
                topics = separate_topics(names, tax)
                if body.get("preview"):
                    return self._json({"topics": topics})
                settings = llm.load_settings()
                own = llm.library_settings(settings, lib)
                presets = [p for p in own.get("presets") or [] if p not in names]
                notes = {k: v for k, v in (own.get("category_notes") or {}).items() if k not in names}
                for t in topics:
                    tax[t["name"]] = sorted(set(tax.get(t["name"], [])) | set(t["keywords"]))
                    if t["name"] not in presets:
                        presets.append(t["name"])
                for name in names:
                    tax.pop(name, None)   # its videos keep the old name until their suggestions are accepted
                save_taxonomy(tax, library=lib)
                # older suggestions for these videos, or into the removed categories, would be skipped or point nowhere
                stale = [v["video_id"] for v in storage.get_library()
                         if v["category"] in names or (v.get("suggestion") or {}).get("category") in names]
                if stale:
                    storage.dismiss_suggestions(stale)
                llm.set_library_settings(settings, lib, presets=presets, category_notes=notes)
                llm.save_settings(settings)
                queued = 0
                if llm.is_configured(llm.for_library(settings, lib), "categorize"):
                    for name in names:
                        scope = f"category:{name}"
                        queued += JOB.start("suggest", f"Suggest categories: {name}",
                                            lambda scope=scope: run_suggest(scope, library=lib, should_stop=JOB.stop_requested),
                                            key=[scope, lib]) is not None
                return self._json({"ok": True, "topics": topics, "queued": queued})

            if path == "/api/reclassify":
                changed = _reclassify(lib)
                return self._json({"ok": True, "changed": changed})

            if path == "/api/ingest":
                playlist_id = _clean(body.get("playlist_id"))
                stored = None
                if playlist_id:
                    stored = storage.get_playlist(playlist_id)
                    if stored is None:
                        return self._error("Unknown playlist", 404)
                url = _clean(body.get("url")) if not stored else stored.get("original_url")
                if not url:
                    return self._error("Playlist URL or ID is required")
                from extractor import video_id_from_url
                vid = video_id_from_url(url)
                if vid:   # a link to one video: added on its own, read without the sign-in
                    return self._queued(JOB.start("sync", f"Add video {vid}", lambda: _add_video(vid, lib or 1), key=vid))
                access = body.get("access") or (stored.get("access") if stored else None) or "public"
                browser = cookies = None
                if not isinstance(access, str):
                    return self._error("Unsupported access method")
                if access == "browser" or access.startswith("browser:"):
                    browser = (body.get("browser") or "").lower()
                    if access.startswith("browser:") and "access" not in body:
                        browser = access.split(":", 1)[1]
                    if browser not in ALLOWED_BROWSERS:
                        return self._error("Unsupported browser")
                elif access == "cookies":
                    cookies = _clean(body.get("cookies_file") or (stored or {}).get("cookies_file"))
                    if not cookies or not os.path.isfile(cookies):
                        return self._error("cookies.txt file not found: choose access again")
                return self._queued(JOB.start("sync", f"Sync {url}", lambda: _sync(url, browser, cookies, lib or 1)))

            if path.startswith("/api/ai/") or path == "/api/presets":
                return self._ai(path, body, lib)

            if path == "/api/export/markdown":
                ids = body.get("ids")
                if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
                    return self._error("ids must be a non-empty list of video IDs")
                wanted = set(ids)
                videos = [v for v in storage.get_library() if v["video_id"] in wanted]
                data = markdown_zip(videos, {v["video_id"]: storage.get_details(v["video_id"]) for v in videos},
                                    {v["video_id"]: summary for v in videos
                                     if (summary := storage.get_summary(v["video_id"])) is not None})
                stamp = datetime.now().strftime("%Y%m%d")
                return self._send(200, data, "application/zip",
                                  extra_headers={"Content-Disposition": f'attachment; filename="yourtube_notes_{stamp}.zip"'})

            if path == "/api/job/cancel":
                task_id = body.get("id")
                if task_id is not None and (not isinstance(task_id, int) or isinstance(task_id, bool)):
                    return self._error("id must be a number")
                ok = JOB.request_stop(task_id)
                return self._json({"ok": ok, "stopping": ok})

            if path in ("/api/job/retry", "/api/job/dismiss"):
                task_id = body.get("id")
                if not isinstance(task_id, int) or isinstance(task_id, bool):
                    return self._error("id must be a number")
                if path == "/api/job/retry":
                    started = JOB.retry(task_id)
                    return self._error("That task can no longer be retried", 404) if started is None else self._queued(started)
                return self._json({"ok": JOB.dismiss(task_id)})

            if path == "/api/details/fetch":
                from enrich import run_enrich
                ids = body.get("ids")
                if ids is not None and (not isinstance(ids, list) or not all(isinstance(i, str) for i in ids)):
                    return self._error("ids must be a list of video IDs")
                return self._queued(JOB.start("details", _label("Fetch details", ids),
                                              lambda: run_enrich(video_ids=ids, should_stop=JOB.stop_requested), key=ids))

            if path == "/api/local/pull":
                import local_setup
                model = _clean(body.get("model"))
                if not isinstance(model, str) or not re.fullmatch(r"[\w.\-/]+(:[\w.\-]+)?", model):
                    return self._error("Not a valid model name")
                return self._queued(JOB.start("pull", f"Download {model}", lambda: _pull_then_index(model)))

            if path == "/api/local/use":
                import local_setup
                model = _clean(body.get("model"))
                tasks = [t for t in body.get("tasks") or [] if t in llm.TASKS]
                if not isinstance(model, str) or not model:
                    return self._error("Model is required")
                local_setup.use_model(model, tasks)
                return self._json({"ok": True, "ai": llm.public_settings(llm.load_settings())})

            if path in ("/api/asr/plan", "/api/asr/estimate"):
                import local_asr
                ids = body.get("ids")
                if ids is not None and (not isinstance(ids, list) or not all(isinstance(i, str) for i in ids)):
                    return self._error("ids must be a list of video IDs")
                estimate = local_asr.estimate(ids)
                return self._json(estimate)

            if path == "/api/asr":
                import local_asr
                ids = body.get("ids")
                if ids is not None and (not isinstance(ids, list) or not all(isinstance(i, str) for i in ids)):
                    return self._error("ids must be a list of video IDs")
                settings = llm.load_settings()
                engine = llm.media_provider(settings, "transcribe")
                if engine == "local":
                    if not local_asr.available():
                        return self._error("Local transcription needs faster-whisper and ffmpeg: run bash scripts/setup.sh --local-ai")
                else:
                    if shutil.which("ffmpeg") is None:
                        return self._error("Transcription needs ffmpeg: run bash scripts/setup.sh")
                label = _label("Transcribe locally" if engine == "local" else f"Transcribe with {llm.PROVIDERS[engine]['label']}", ids)
                return self._queued(JOB.start("asr", label,
                                              lambda: local_asr.run_asr(ids, should_stop=JOB.stop_requested), key=ids))

            if path == "/api/ask":
                import asker
                question = body.get("question")
                if not isinstance(question, str) or not question.strip():
                    return self._error("Type a question")
                f = body.get("filters") or {}
                lists = lambda k: [x for x in f.get(k) or [] if isinstance(x, str)][:200] if isinstance(f.get(k), list) else []
                filters = {"categories": lists("categories"), "playlists": lists("playlists"), "video_ids": lists("video_ids"),
                           "include_done": f.get("include_done", True) is not False}
                follow = body.get("follow_up_of")
                if follow is not None and (not isinstance(follow, int) or isinstance(follow, bool)):
                    return self._error("follow_up_of must be a number")
                if JOB.busy("asr", "local"):   # Ollama would reload into the GPU whisper is using
                    return self._error("Transcribing on the GPU right now. Ask again when it finishes.", 409)
                if not llm.is_configured(llm.load_settings(), "ask"):
                    return self._error("Choose an AI for questions and answers in Settings → AI first")
                return self._json(asker.ask(question.strip()[:1000], filters=filters, follow_up_of=follow))

            if path == "/api/ask/delete":
                ask_id = body.get("id")
                if not isinstance(ask_id, int) or isinstance(ask_id, bool):
                    return self._error("id must be a number")
                return self._json({"ok": True, "deleted": storage.delete_ask(ask_id)})

            if path == "/api/embed":
                import embedder
                rebuild = body.get("rebuild") is True

                def run():
                    if rebuild:
                        model = embedder.model_name(llm.load_settings())
                        print(f"Rebuilding the meaning index: cleared {storage.clear_embeddings(model)} vectors of {model}.")
                    return embedder.run_embed(should_stop=JOB.stop_requested)
                return self._queued(JOB.start("embed", "Rebuild meaning index" if rebuild else "Update meaning index", run))

            if path == "/api/embed/switch":
                provider = body.get("provider")
                if provider not in llm.MEDIA_TASKS["embed"]["providers"]:
                    return self._error("Unknown embedding provider")
                settings = llm.load_settings()
                settings.setdefault("tasks", {})["embed"] = provider
                llm.save_settings(settings)

                def rebuild():
                    import embedder
                    model = embedder.model_name(llm.load_settings())
                    print(f"Rebuilding the meaning index for {model}.")
                    return embedder.run_embed(should_stop=JOB.stop_requested)

                started = JOB.start("embed", f"Rebuild meaning index with {provider}", rebuild)
                return self._json({"ok": True, "job": started, "ai": llm.public_settings(settings)})

            if path == "/api/visuals":
                import local_visual
                ids = body.get("ids")
                if ids is not None and (not isinstance(ids, list) or not all(isinstance(i, str) for i in ids)):
                    return self._error("ids must be a list of video IDs")
                return self._queued(JOB.start("visual", _label("Read slides", ids),
                                              lambda: local_visual.run_visual(ids, should_stop=JOB.stop_requested), key=ids))

            if path == "/api/videos/visuals":
                ids = body.get("ids")
                if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
                    return self._error("ids must be a list of video IDs")
                value = body.get("value")
                if value not in (True, False, None):
                    return self._error("value must be true, false or null")
                return self._json({"ok": True, "updated": storage.set_has_visuals(ids, value)})

            if path == "/api/visuals/settings":
                settings = llm.load_settings()
                name = _clean(body.get("category"))
                if not name:
                    return self._error("Category name is required")
                settings.setdefault("visuals", {})[name] = bool(body.get("on"))
                llm.save_settings(settings)
                return self._json({"ok": True, "visual_categories": _visual_categories(settings)})

            if path == "/api/transcript/source":
                storage.set_transcript_source(_clean(body.get("id")) or "", _clean(body.get("source")) or "")
                return self._json({"ok": True})

            if path in ("/api/rate", "/api/rate/estimate"):
                import rater
                ids = body.get("ids")
                if ids is not None and (not isinstance(ids, list) or not all(isinstance(i, str) for i in ids)):
                    return self._error("ids must be a list of video IDs")
                if path == "/api/rate/estimate":
                    return self._json(rater.estimate(ids))
                if not llm.is_configured(llm.load_settings(), "rate"):
                    return self._error("Choose an AI for ratings in Settings → AI first")
                return self._queued(JOB.start("rate", _label("Rate", ids), lambda: rater.run_rate(ids, should_stop=JOB.stop_requested), key=ids))

            if path == "/api/knowledge/build":
                import knowledge
                category = _clean(body.get("category"))
                if not isinstance(category, str) or not category:
                    return self._error("category is required")
                target = lib or 1
                if not llm.is_configured(llm.load_settings(), "summarize"):
                    return self._error("Choose an AI for summaries in Settings → AI first")
                return self._queued(JOB.start("knowledge", f"Knowledge page: {category}",
                                              lambda: knowledge.run_build(target, category), key=[target, category]))

            if path in ("/api/summarize", "/api/summarize/estimate"):
                import summarizer
                ids = body.get("ids")
                if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
                    return self._error("ids must be a non-empty list of video IDs")
                if len(ids) > 50:
                    return self._error("Summarise at most 50 videos at a time")
                if path == "/api/summarize/estimate":
                    return self._json(summarizer.estimate(ids))
                if not llm.is_configured(llm.load_settings(), "summarize"):
                    return self._error("Choose an AI for summaries in Settings → AI first")
                return self._queued(JOB.start("summarize", _label("Summarise", ids),
                                              lambda: summarizer.run_summarize(ids, should_stop=JOB.stop_requested), key=ids))

            if path == "/api/details/settings":
                settings = llm.load_settings()
                if "after_sync" in body:
                    settings["details_after_sync"] = bool(body["after_sync"])
                if "use_descriptions" in body:
                    settings["use_descriptions"] = bool(body["use_descriptions"])
                name = _clean(body.get("category"))
                if name:
                    settings.setdefault("transcripts", {})[name] = bool(body.get("on"))
                llm.save_settings(settings)
                return self._json({"ok": True, "transcripts": _transcript_categories(settings),
                                   "details_after_sync": bool(settings.get("details_after_sync")),
                                   "use_descriptions": settings.get("use_descriptions", True) is not False})

            if path == "/api/interests/draft":
                settings = llm.load_settings()
                if not llm.is_configured(settings, "interests"):
                    return self._error("Choose an AI for About you in Settings → AI by task")
                text = body.get("text")
                current = body.get("current") or ""
                lib = _library(body.get("library")) or 1
                lib_settings = llm.for_library(settings, lib)
                context = {
                    "library": _library_name(lib),
                    "categories": list(load_taxonomy(library=lib)),
                    "language": lib_settings.get("language") or settings.get("language", "English"),
                }
                try:
                    complete = llm.make_completer(settings, "interests")
                    draft = interests_ai.draft_interests(text, current, context, complete)
                    return self._json({"interests": draft})
                except ValueError as e:
                    return self._error(str(e))
                except (llm.LLMError, OSError) as e:
                    return self._error(str(e) or "Could not draft interests with AI", 502)
                except Exception as e:
                    applog.get("http").exception("AI draft interests failed unexpectedly")
                    return self._error(str(e) or "Could not draft interests with AI", 502)
        except ValueError as e:
            return self._error(str(e))
        except llm.LLMError as e:
            return self._error(str(e), 502)
        return self._error("Not found", 404)

    # --- AI ------------------------------------------------------------------------
    def _ai(self, path, body, lib=1):
        from ai_classifier import run_discover, run_suggest

        if path == "/api/ai/settings":
            settings = llm.load_settings()
            # Default provider ("none" turns AI off)
            if "provider" in body:
                if body["provider"] != "none" and body["provider"] not in llm.PROVIDERS:
                    return self._error("Unknown provider")
                settings["provider"] = body["provider"]
            # One provider's own configuration
            edit, cfg = body.get("edit"), body.get("config")
            if edit is not None:
                if edit not in llm.PROVIDERS or not isinstance(cfg, dict):
                    return self._error("Unknown provider")
                target = settings["providers"][edit]
                for k in ("model", "base_url"):
                    if isinstance(cfg.get(k), str):
                        target[k] = cfg[k].strip()
                if edit == "ollama":
                    target["base_url"] = llm.ollama_base(target.get("base_url"))
                    if "num_ctx" in cfg:
                        try:
                            target["num_ctx"] = max(2048, min(131072, int(cfg["num_ctx"])))
                        except (TypeError, ValueError):
                            return self._error("Context size must be a number")
                key = cfg.get("api_key")
                if key == "__clear__":
                    target["api_key"] = ""
                elif isinstance(key, str) and key.strip():
                    target["api_key"] = key.strip()
            # Which provider does which job (None = the default)
            if isinstance(body.get("tasks"), dict):
                for task, provider in body["tasks"].items():
                    if task in llm.MEDIA_TASKS:
                        if provider is not None and provider not in llm.MEDIA_TASKS[task]["providers"]:
                            return self._error("Unknown provider")
                        settings.setdefault("tasks", {})[task] = provider
                    elif task in llm.TASKS:
                        if provider is not None and provider not in llm.PROVIDERS:
                            return self._error("Unknown provider")
                        settings.setdefault("tasks", {})[task] = provider
            # Helper AI configuration
            if "helpers" in body:
                if not isinstance(body["helpers"], dict):
                    return self._error("Helpers must be an object")
                for task, provider in body["helpers"].items():
                    if task not in llm.HELPER_TASKS:
                        return self._error("Unknown task")
                    if provider is not None and provider != "none":
                        allowed = llm.MEDIA_TASKS[task]["providers"] if task in llm.MEDIA_TASKS else llm.PROVIDERS
                        if provider not in allowed:
                            return self._error("Unknown provider")
                    settings.setdefault("helpers", {})[task] = None if provider in (None, "none") else provider
            if "auto_after_sync" in body:
                settings["auto_after_sync"] = bool(body["auto_after_sync"])
            if "show_descriptions" in body:
                settings["show_descriptions"] = bool(body["show_descriptions"])
            if "local_only" in body:
                settings["local_only"] = bool(body["local_only"])
            if body.get("asr_model") in ("auto", "large-v3-turbo", "small", "medium", "distil-large-v3"):
                settings["asr_model"] = body["asr_model"]
            if isinstance(body.get("vision_model"), str) and re.fullmatch(r"[\w.\-/:]*", body["vision_model"]):
                settings["vision_model"] = body["vision_model"]
            if isinstance(body.get("embed_model"), str) and re.fullmatch(r"[\w.\-/]+(:[\w.\-]+)?", body["embed_model"]):
                settings["embed_model"] = body["embed_model"]
            if isinstance(body.get("rating_formula"), dict):
                import rater
                custom = body["rating_formula"]
                settings["rating_formula"] = {k: v for k, v in rater.formula_of({"rating_formula": custom}).items()
                                              if k in custom} if custom else {}
            if body.get("visual_height") in (480, 720):
                settings["visual_height"] = body["visual_height"]
            if "interests" in body:
                interests = body["interests"]
                if not isinstance(interests, str):
                    return self._error("Interests must be text")
                if len(interests) > 2000:
                    return self._error("Interests must be 2,000 characters or fewer")
                llm.set_library_settings(settings, lib or 1, interests=interests.strip())
            if "language" in body:
                lang = body["language"]
                # an older free-text language may be re-sent unchanged
                if not isinstance(lang, str) or (lang not in llm.LANGUAGES and lang != settings["language"]):
                    return self._error("Choose a supported language")
                settings["language"] = lang
            llm.save_settings(settings)
            return self._json({"ok": True, "ai": llm.public_settings(settings, lib or 1)})

        if path == "/api/ai/test":
            settings = llm.load_settings()
            provider = body.get("provider") or settings["provider"]
            if provider not in llm.PROVIDERS:
                return self._error("Unknown provider")
            reply = llm.make_completer({**settings, "provider": provider, "tasks": {}})(
                "Reply with JSON only.", 'Reply with {"ok": true} and nothing else.')
            llm.parse_json(reply)
            return self._json({"ok": True})

        if path == "/api/ai/models":
            return self._json({"models": llm.list_models(body.get("provider", ""), llm.load_settings())})

        if path == "/api/presets":
            name = _clean(body.get("name"))
            if not name:
                return self._error("Category name is required")
            settings = llm.load_settings()
            presets = [p for p in llm.library_settings(settings, lib)["presets"] if p != name]
            if body.get("preset"):
                presets.append(name)
            llm.set_library_settings(settings, lib, presets=presets)
            llm.save_settings(settings)
            return self._json({"ok": True, "presets": presets})

        if path in ("/api/ai/discover", "/api/ai/suggest"):
            if not llm.is_configured(llm.load_settings(), "categorize"):
                return self._error("Set up an AI provider in Settings → AI first")
            if path == "/api/ai/discover":
                if load_taxonomy(library=lib):
                    return self._error("This library already has categories. Sort unsorted videos instead.")
                started = JOB.start("discover", f"Propose categories: {_library_name(lib)}", lambda: run_discover(library=lib, should_stop=JOB.stop_requested), key=lib)
            else:
                scope = body.get("scope") or "unsorted"
                if scope not in ("unsorted", "unlocked"):
                    return self._error("Invalid scope")
                started = JOB.start("suggest", "Suggest categories" + (f": {_library_name(lib)}" if lib else ""),
                                    lambda: run_suggest(scope, library=lib, should_stop=JOB.stop_requested), key=[scope, lib])
            return self._queued(started)

        if path == "/api/ai/proposal/apply":
            cats = body.get("categories")
            if not isinstance(cats, list):
                return self._error("categories must be a list")
            remove = {_clean(n) for n in body.get("remove") or []}
            old = load_taxonomy(library=lib)
            settings = llm.load_settings()
            own = llm.library_settings(settings, lib)
            tax, notes = {}, dict(own["category_notes"])
            for c in cats:
                name = _clean((c or {}).get("name"))
                if not name or name == FALLBACK_CATEGORY or name in tax:
                    continue
                tax[name] = sorted({str(k).strip().lower() for k in c.get("keywords") or [] if str(k).strip()})
                if c.get("description"):
                    notes[name] = str(c["description"]).strip()
            for name, kws in old.items():
                if name not in tax and name not in remove:
                    tax[name] = kws
            save_taxonomy(tax, library=lib)
            ids = _library_videos(lib)
            moved = sum(storage.release_category(n, video_ids=ids) for n in remove if n not in tax)
            llm.set_library_settings(settings, lib, category_notes={k: v for k, v in notes.items() if k in tax},
                                     presets=[p for p in own["presets"] if p in tax], proposal=None)
            llm.save_settings(settings)
            changed = _reclassify(lib)
            return self._json({"ok": True, "categories": len(tax), "moved": moved, "changed": changed})

        if path == "/api/ai/proposal/dismiss":
            settings = llm.load_settings()
            llm.set_library_settings(settings, lib, proposal=None)
            llm.save_settings(settings)
            return self._json({"ok": True})

        if path == "/api/ai/suggestions/accept":
            n = storage.accept_suggestions(body.get("ids"), body.get("min_confidence"))
            return self._json({"ok": True, "accepted": n})

        if path == "/api/ai/suggestions/dismiss":
            n = storage.dismiss_suggestions(body.get("ids"))
            return self._json({"ok": True, "dismissed": n})

        return self._error("Not found", 404)

def _attachment(name):
    """Content-Disposition for a possibly non-ASCII file name (RFC 6266 / 5987)."""
    from urllib.parse import quote
    ascii_name = name.encode("ascii", "replace").decode().replace("?", "_").replace('"', "'")
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(name)}'

def _clean(value):
    return value.strip() if isinstance(value, str) else value

def _classifier(library=None):
    taxonomy = load_taxonomy(library=library)
    return lambda title, channel: classify_video(title, channel, taxonomy)


def _ytdlp_version():
    try:
        from importlib.metadata import version
        return version("yt-dlp")
    except Exception:
        return "unavailable"

def _probe_job(port, timeout=2.0):
    import urllib.request
    import urllib.error
    url = f"http://127.0.0.1:{port}/api/job"
    req = urllib.request.Request(url, headers={"User-Agent": "YourTube"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.getcode() in (200, 401)
    except urllib.error.HTTPError as err:
        return err.code in (200, 401)
    except Exception:
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description="YourTube local dashboard")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1", help="Address to listen on (only Docker needs 0.0.0.0)")
    parser.add_argument("--no-open", action="store_true", help="Do not open the dashboard in a browser")
    parser.add_argument("--print-url", action="store_true", help="Print the dashboard link (with its access token) and exit")
    args = parser.parse_args(argv)

    applog.setup()
    global TOKEN
    TOKEN = load_token()
    applog.set_secret(TOKEN)
    for config in llm.load_settings().get("providers", {}).values():
        if isinstance(config, dict):
            applog.set_secret(config.get("api_key"))
    for name in {p["env"] for p in llm.PROVIDERS.values() if p.get("env")}:
        applog.set_secret(os.environ.get(name))
    url = f"http://127.0.0.1:{args.port}/?t={TOKEN}"
    if args.print_url:
        print(url)
        return

    import errno
    try:
        if args.host not in ("127.0.0.1", "localhost", "::1"):
            warning = f"Warning: listening on {args.host}; keep the port published to 127.0.0.1 only."
            print(warning)
            applog.get("dashboard").warning(warning)
        server = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as err:
        if err.errno == errno.EADDRINUSE:
            msg = (f"YourTube is already running on port {args.port} (or another program uses it). "
                   f"Open it with: .venv/bin/python dashboard.py --print-url")
            print(msg)
            applog.get("dashboard").warning(msg)
            sys.exit(0 if _probe_job(args.port) else 1)
        raise

    storage.init_db()
    import platform
    applog.get("dashboard").info("Dashboard started: port=%s, python=%s, yt-dlp=%s, platform=%s, db=%s",
                                  args.port, platform.python_version(), _ytdlp_version(), sys.platform,
                                  storage.DB_PATH)
    if sys.stdout.isatty():
        print(f"YourTube dashboard running. Open: {url}  (Ctrl+C to stop)")
    else:
        print(f"YourTube dashboard running on http://127.0.0.1:{args.port}. Get the link with: .venv/bin/python dashboard.py --print-url")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")

if __name__ == "__main__":
    main()
