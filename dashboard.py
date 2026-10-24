"""
Local web dashboard for YourTube - Phase 0.
Serves web/index.html plus a small JSON API over the local SQLite database.
Standard library only; binds to 127.0.0.1 so nothing is exposed to the network.

    python dashboard.py [--port 8765] [--no-open]
"""
import os
import io
import re
import sys
import json
import argparse
import threading
import webbrowser
import contextlib
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import storage
import llm
from classifier import load_taxonomy, save_taxonomy, classify_video, FALLBACK_CATEGORY
from exporter import videos_to_csv, video_markdown, markdown_zip, safe_filename

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(ROOT_DIR, "web")
# The only files /download/ serves (name -> path, content type); nothing else on disk is reachable
DOWNLOADS = {
    "setup.sh": (os.path.join(ROOT_DIR, "scripts", "setup.sh"), "text/x-shellscript; charset=utf-8"),
    "setup.ps1": (os.path.join(ROOT_DIR, "scripts", "setup.ps1"), "text/plain; charset=utf-8"),
    "SETUP.md": (os.path.join(ROOT_DIR, "docs", "SETUP.md"), "text/markdown; charset=utf-8"),
}
FRAMES_DIR = os.path.join(ROOT_DIR, "frames")
_FRAME_PATH = re.compile(r"^/frames/([\w-]{1,32})/(\d{1,6})\.jpg$")   # only frames/<video id>/<second>.jpg
ALLOWED_BROWSERS = {"auto", "chrome", "edge", "firefox", "brave", "opera", "vivaldi", "chromium", "safari"}
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}

class Job:
    """
    Background tasks (syncs, details, AI runs, transcription, downloads) in a queue: one runs at a time and
    the rest wait their turn, so starting a task while another runs queues it instead of refusing it.
    The same task (kind + label) can't be queued twice. Console output is captured per task.
    """
    HISTORY = 5   # finished tasks kept for the status panel

    def __init__(self):
        self.lock = threading.Lock()
        self.state = {"id": 0, "running": False, "ok": None, "log": "", "kind": None, "label": None,
                      "started_at": None, "finished_at": None}
        self.pending = []    # [{'id', 'kind', 'label', 'fn', 'queued_at'}]
        self.done = []       # recent finished tasks, newest first (without logs)
        self._next = 1
        self._buf = io.StringIO()
        self._stop = threading.Event()
        self._worker = None

    def stop_requested(self):
        return self._stop.is_set()

    def request_stop(self, task_id=None):
        """Stops the running task, or (with the id of a waiting one) takes it off the queue."""
        with self.lock:
            if task_id is not None and any(t["id"] == task_id for t in self.pending):
                self.pending = [t for t in self.pending if t["id"] != task_id]
                return True
            if self.state["running"] and task_id in (None, self.state["id"]):
                self._stop.set()
                return True
        return False

    def snapshot(self):
        with self.lock:
            snap = {k: v for k, v in self.state.items() if k != "key"}
            snap["stopping"] = bool(snap["running"] and self._stop.is_set())
            if snap["running"]:
                snap["log"] = self._buf.getvalue()
            snap["queue"] = [{k: t[k] for k in ("id", "kind", "label", "queued_at")} for t in self.pending]
            snap["recent"] = list(self.done)
        return snap

    def start(self, kind, label, fn, key=None):
        """
        Queues a task. Returns {'id', 'position'} (0 = started now), or None when the same task (kind + key, default
        the label) is already waiting or running.
        """
        key = json.dumps([kind, key if key is not None else label], sort_keys=True, default=str)
        with self.lock:
            if (self.state["running"] and self.state.get("key") == key) or any(t["key"] == key for t in self.pending):
                return None
            task = {"id": self._next, "kind": kind, "label": label, "fn": fn, "key": key,
                    "queued_at": datetime.now().isoformat(timespec="seconds")}
            self._next += 1
            self.pending.append(task)
            position = len(self.pending) - (0 if self.state["running"] else 1)
            if not (self._worker and self._worker.is_alive()):
                self._worker = threading.Thread(target=self._work, daemon=True)
                self._worker.start()
        return {"id": task["id"], "position": position}

    def _work(self):
        while True:
            with self.lock:
                if not self.pending:
                    self._worker = None
                    return
                task = self.pending.pop(0)
                self._buf = io.StringIO()
                self._stop.clear()
                self.state = {"id": task["id"], "key": task["key"], "running": True, "ok": None, "log": "", "kind": task["kind"], "label": task["label"],
                              "started_at": datetime.now().isoformat(timespec="seconds"), "finished_at": None}
            self._run(task["fn"])

    def _run(self, fn):
        ok = False
        try:
            with contextlib.redirect_stdout(self._buf):
                ok = fn()
                # Keep the meaning index current after anything that adds or changes searchable text
                if self.state["kind"] in AUTO_EMBED_KINDS and not self._stop.is_set():
                    import embedder
                    try:
                        print()
                        embedder.run_embed(should_stop=self.stop_requested, quiet=True)
                    except Exception as e:  # the main job already finished; just report
                        print(f"Warning: Meaning index not updated: {e}")
        except Exception as e:  # surface anything unexpected in the job log
            self._buf.write(f"\nUnexpected error: {e}\n")
        with self.lock:
            stopped = self._stop.is_set()
            self.state.update(running=False, ok=bool(ok), stopped=stopped, log=self._buf.getvalue(),
                              finished_at=datetime.now().isoformat(timespec="seconds"))
            self.done.insert(0, {k: self.state.get(k) for k in ("id", "kind", "label", "ok", "stopped", "started_at", "finished_at")})
            del self.done[self.HISTORY:]

def _library_name(library):
    return next((l["name"] for l in storage.get_libraries() if l["id"] == library), "all libraries")

def _sync(url, browser, cookies_file, library=1):
    from main import run_pipeline  # deferred so the dashboard starts even if yt-dlp is missing
    settings = llm.load_settings()
    ok = run_pipeline(url, browser_cookies=browser, cookies_file=cookies_file, open_browser=False, library=library)
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

AUTO_EMBED_KINDS = {"sync", "details", "asr", "visual", "rate"}
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
    return {
        "library": library or 0,
        "libraries": storage.get_libraries(),
        "playlists": storage.get_playlists(),
        "videos": storage.get_library(),
        "taxonomy": taxonomy,
        "fallback_category": FALLBACK_CATEGORY,
        "job": JOB.snapshot(),
        "ai": llm.public_settings(settings, library or 1),
        "transcripts": _transcript_categories(settings),
        "visual_categories": _visual_categories(settings),
        "details_after_sync": bool(settings.get("details_after_sync")),
        "use_descriptions": settings.get("use_descriptions", True) is not False,
        "platform": sys.platform,
    }

class Handler(BaseHTTPRequestHandler):
    server_version = "YourTube/0.3"

    def log_message(self, fmt, *args):
        pass  # keep the console quiet; errors are returned to the UI

    # --- helpers -------------------------------------------------------------
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
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, default=str))

    def _error(self, msg, code=400):
        self._json({"error": msg}, code)

    def _queued(self, started):
        """Reply for a task handed to JOB: where it is in the queue, or 409 when it is already there."""
        if started is None:
            return self._error("That task is already running or waiting in the queue", 409)
        snap = JOB.snapshot()
        ahead = ([snap["label"]] if snap["running"] and snap["id"] != started["id"] else []) + \
                [t["label"] for t in snap["queue"] if t["id"] < started["id"]]
        return self._json({"ok": True, "id": started["id"], "position": started["position"], "after": ahead})

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw or b"{}")

    # --- GET -------------------------------------------------------------------
    def do_GET(self):
        if not self._host_ok():
            return self._error("Forbidden host", 403)
        url = urlparse(self.path)
        qs = parse_qs(url.query)
        playlist = (qs.get("playlist") or ["all"])[0]

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
            with open(path, "rb") as f:
                data = f.read()
            headers = {"Content-Disposition": f'attachment; filename="{name}"'} if url.path.startswith("/download/") else None
            return self._send(200, data, ctype, extra_headers=headers)
        if url.path == "/api/state":
            return self._json(build_state(_library((qs.get("library") or ["1"])[0])))
        if url.path == "/api/job":
            return self._json(JOB.snapshot())
        if url.path == "/api/export/markdown":
            vid = (qs.get("id") or [""])[0]
            v = next((x for x in storage.get_library() if x["video_id"] == vid), None)
            if v is None:
                return self._error("Unknown video", 404)
            name = safe_filename(v["title"]) + ".md"
            return self._send(200, video_markdown(v, storage.get_details(vid)), "text/markdown; charset=utf-8",
                              extra_headers={"Content-Disposition": _attachment(name)})
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
        if url.path == "/api/embed/status":
            import embedder
            settings = llm.load_settings()
            model = embedder.model_name(settings)
            return self._json({"model": model, "available": embedder.available(settings), **storage.embedding_status(model)})
        if url.path == "/api/visuals/plan":
            import local_visual
            settings = llm.load_settings()
            cands = storage.videos_needing_visuals(None, local_visual.visual_categories(settings))
            secs = sum(v.get("duration_seconds") or 0 for v in cands)
            model = local_visual.vision_model(settings)
            base = settings["providers"]["ollama"].get("base_url")
            return self._json({"available": local_visual.available(), "model": model,
                               "model_ok": bool(model) and local_visual.model_sees_images(base, model),
                               "videos": len(cands), "seconds": secs,
                               "minutes": round(local_visual.estimate_minutes(secs) + len(cands) * 15 / 60, 1)})
        if url.path == "/api/asr/plan":
            import local_asr
            cands = storage.videos_needing_asr()
            dev = local_asr.device() if local_asr.available() else "cpu"
            audio = sum(v.get("duration_seconds") or 0 for v in cands)
            return self._json({
                "available": local_asr.available(), "device": dev,
                "model": local_asr.choose_model(llm.load_settings(), dev)[0],
                "videos": len(cands), "no_captions": sum(v["reason"] == "no_captions" for v in cands),
                "wrong_script": sum(v["reason"] == "wrong_script" for v in cands),
                "audio_s": audio, "minutes": round(local_asr.estimate_minutes(audio, dev) + len(cands) * 10 / 60, 1)})
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
    def do_POST(self):
        # Custom header + JSON content type force a CORS preflight, which we never answer,
        # so other websites open in the browser cannot drive this API.
        if not self._host_ok() or self.headers.get("X-YTKB") != "1":
            return self._error("Forbidden", 403)
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            return self._error("Expected JSON", 415)
        try:
            body = self._body()
        except json.JSONDecodeError:
            return self._error("Invalid JSON")

        path = urlparse(self.path).path
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
                        "/api/presets", "/api/ai/discover", "/api/ai/proposal/apply", "/api/ai/proposal/dismiss") and lib is None:
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

            if path == "/api/reclassify":
                changed = _reclassify(lib)
                return self._json({"ok": True, "changed": changed})

            if path == "/api/ingest":
                url = _clean(body.get("url"))
                if not url:
                    return self._error("Playlist URL or ID is required")
                from extractor import video_id_from_url
                vid = video_id_from_url(url)
                if vid:   # a link to one video: added on its own, read without the sign-in
                    return self._queued(JOB.start("sync", f"Add video {vid}", lambda: _add_video(vid, lib or 1), key=vid))
                access = body.get("access") or "public"
                browser = cookies = None
                if access == "browser":
                    browser = (body.get("browser") or "").lower()
                    if browser not in ALLOWED_BROWSERS:
                        return self._error("Unsupported browser")
                elif access == "cookies":
                    cookies = _clean(body.get("cookies_file"))
                    if not cookies or not os.path.isfile(cookies):
                        return self._error("cookies.txt file not found")
                return self._queued(JOB.start("sync", f"Sync {url}", lambda: _sync(url, browser, cookies, lib or 1)))

            if path.startswith("/api/ai/") or path == "/api/presets":
                return self._ai(path, body, lib)

            if path == "/api/export/markdown":
                ids = body.get("ids")
                if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
                    return self._error("ids must be a non-empty list of video IDs")
                wanted = set(ids)
                videos = [v for v in storage.get_library() if v["video_id"] in wanted]
                data = markdown_zip(videos, {v["video_id"]: storage.get_details(v["video_id"]) for v in videos})
                stamp = datetime.now().strftime("%Y%m%d")
                return self._send(200, data, "application/zip",
                                  extra_headers={"Content-Disposition": f'attachment; filename="yourtube_notes_{stamp}.zip"'})

            if path == "/api/job/cancel":
                task_id = body.get("id")
                if task_id is not None and (not isinstance(task_id, int) or isinstance(task_id, bool)):
                    return self._error("id must be a number")
                ok = JOB.request_stop(task_id)
                return self._json({"ok": ok, "stopping": JOB.stop_requested() if ok else False})

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

            if path == "/api/asr":
                import local_asr
                ids = body.get("ids")
                if ids is not None and (not isinstance(ids, list) or not all(isinstance(i, str) for i in ids)):
                    return self._error("ids must be a list of video IDs")
                if not local_asr.available():
                    return self._error("Local transcription needs faster-whisper and ffmpeg: run bash scripts/setup.sh --local-ai")
                return self._queued(JOB.start("asr", _label("Transcribe locally", ids),
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
                    if task in llm.TASKS and (provider is None or provider in llm.PROVIDERS):
                        settings["tasks"][task] = provider
            if "auto_after_sync" in body:
                settings["auto_after_sync"] = bool(body["auto_after_sync"])
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
            if isinstance(body.get("interests"), str):
                llm.set_library_settings(settings, lib or 1, interests=body["interests"].strip()[:2000])
            if isinstance(body.get("language"), str) and body["language"].strip():
                settings["language"] = body["language"].strip()[:40]
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

def main():
    parser = argparse.ArgumentParser(description="YourTube local dashboard")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true", help="Do not open the dashboard in a browser")
    args = parser.parse_args()

    storage.init_db()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"YourTube dashboard running at {url}  (Ctrl+C to stop)")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")

if __name__ == "__main__":
    main()
