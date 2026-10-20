"""
Local web dashboard for YT KB - Phase 0.
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
from exporter import videos_to_csv

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(ROOT_DIR, "web")
# The only files /download/ serves (name -> path, content type); nothing else on disk is reachable
DOWNLOADS = {
    "setup.sh": (os.path.join(ROOT_DIR, "scripts", "setup.sh"), "text/x-shellscript; charset=utf-8"),
    "setup.ps1": (os.path.join(ROOT_DIR, "scripts", "setup.ps1"), "text/plain; charset=utf-8"),
    "SETUP.md": (os.path.join(ROOT_DIR, "docs", "SETUP.md"), "text/markdown; charset=utf-8"),
}
ALLOWED_BROWSERS = {"auto", "chrome", "edge", "firefox", "brave", "opera", "vivaldi", "chromium", "safari"}
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}

class Job:
    """Runs one background task at a time (a sync or an AI run), capturing its console output."""
    def __init__(self):
        self.lock = threading.Lock()
        self.state = {"running": False, "ok": None, "log": "", "kind": None, "label": None,
                      "started_at": None, "finished_at": None}
        self._buf = io.StringIO()
        self._stop = threading.Event()

    def stop_requested(self):
        return self._stop.is_set()

    def request_stop(self):
        if self.state["running"]:
            self._stop.set()
            return True
        return False

    def snapshot(self):
        snap = dict(self.state)
        if snap["running"]:
            snap["log"] = self._buf.getvalue()
        return snap

    def start(self, kind, label, fn):
        with self.lock:
            if self.state["running"]:
                return False
            self._buf = io.StringIO()
            self._stop.clear()
            self.state = {"running": True, "ok": None, "log": "", "kind": kind, "label": label,
                          "started_at": datetime.now().isoformat(timespec="seconds"), "finished_at": None}
        threading.Thread(target=self._run, args=(fn,), daemon=True).start()
        return True

    def _run(self, fn):
        ok = False
        try:
            with contextlib.redirect_stdout(self._buf):
                ok = fn()
        except Exception as e:  # surface anything unexpected in the job log
            self._buf.write(f"\n❌ Unexpected error: {e}\n")
        self.state.update(running=False, ok=bool(ok), log=self._buf.getvalue(),
                          finished_at=datetime.now().isoformat(timespec="seconds"))

def _sync(url, browser, cookies_file):
    from main import run_pipeline  # deferred so the dashboard starts even if yt-dlp is missing
    settings = llm.load_settings()
    ok = run_pipeline(url, browser_cookies=browser, cookies_file=cookies_file, open_browser=False,
                      ai_suggest=settings["auto_after_sync"] and llm.is_configured(settings, "categorize"))
    if ok and settings.get("details_after_sync"):
        from enrich import run_enrich
        print()
        run_enrich(limit=AFTER_SYNC_DETAILS_LIMIT, should_stop=JOB.stop_requested)  # a failure here doesn't fail the sync
    return ok

AFTER_SYNC_DETAILS_LIMIT = 50

JOB = Job()

def _transcript_categories(settings):
    from enrich import transcripts_wanted
    names = set(load_taxonomy()) | {v["category"] for v in storage.get_all_videos(include_removed=True)}
    return {n: transcripts_wanted(n, settings) for n in sorted(names)}

def build_state():
    settings = llm.load_settings()
    return {
        "playlists": storage.get_playlists(),
        "videos": storage.get_library(),
        "taxonomy": load_taxonomy(),
        "fallback_category": FALLBACK_CATEGORY,
        "job": JOB.snapshot(),
        "ai": llm.public_settings(settings),
        "transcripts": _transcript_categories(settings),
        "details_after_sync": bool(settings.get("details_after_sync")),
        "platform": sys.platform,
    }

class Handler(BaseHTTPRequestHandler):
    server_version = "YTKB/0.2"

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
            return self._json(build_state())
        if url.path == "/api/job":
            return self._json(JOB.snapshot())
        if url.path == "/api/search":
            q = (qs.get("q") or [""])[0][:200]
            kinds = [k for k in (qs.get("kinds") or [""])[0].split(",") if k] or None
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
                                  extra_headers={"Content-Disposition": f'attachment; filename="yt_kb_{stamp}.json"'})
            return self._send(200, "﻿" + videos_to_csv(videos), "text/csv; charset=utf-8",
                              extra_headers={"Content-Disposition": f'attachment; filename="yt_kb_{stamp}.csv"'})
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
                    changed = storage.reclassify_videos(_classifier(), ids, unlock=True)
                    return self._json({"ok": True, "changed": changed})
                n = storage.update_videos(ids, status=body.get("status"),
                                          category=_clean(body.get("category")), notes=body.get("notes"))
                return self._json({"ok": True, "updated": n})

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
                save_taxonomy(cleaned)
                return self._json({"ok": True, "taxonomy": cleaned})

            if path == "/api/categories/rename":
                old, new = _clean(body.get("old")), _clean(body.get("new"))
                if not old or not new:
                    return self._error("Both old and new names are required")
                tax = load_taxonomy()
                if new in tax and new != old:
                    return self._error(f'Category "{new}" already exists')
                if old in tax:
                    tax = {(new if k == old else k): v for k, v in tax.items()}
                    save_taxonomy(tax)
                n = storage.rename_category(old, new)
                settings = llm.load_settings()
                settings["presets"] = [new if p == old else p for p in settings["presets"]]
                for key in ("category_notes", "transcripts"):
                    if old in settings.get(key, {}):
                        settings[key][new] = settings[key].pop(old)
                llm.save_settings(settings)
                return self._json({"ok": True, "updated": n})

            if path == "/api/categories/delete":
                name = _clean(body.get("name"))
                tax = load_taxonomy()
                tax.pop(name, None)
                save_taxonomy(tax)
                n = storage.release_category(name)
                settings = llm.load_settings()
                settings["presets"] = [p for p in settings["presets"] if p != name]
                settings["category_notes"].pop(name, None)
                settings.get("transcripts", {}).pop(name, None)
                llm.save_settings(settings)
                return self._json({"ok": True, "moved": n})

            if path == "/api/reclassify":
                changed = storage.reclassify_videos(_classifier())
                return self._json({"ok": True, "changed": changed})

            if path == "/api/ingest":
                url = _clean(body.get("url"))
                if not url:
                    return self._error("Playlist URL or ID is required")
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
                if not JOB.start("sync", url, lambda: _sync(url, browser, cookies)):
                    return self._error("Another task is already running", 409)
                return self._json({"ok": True})

            if path.startswith("/api/ai/") or path == "/api/presets":
                return self._ai(path, body)

            if path == "/api/job/cancel":
                return self._json({"ok": JOB.request_stop()})

            if path == "/api/details/fetch":
                from enrich import run_enrich
                ids = body.get("ids")
                if ids is not None and (not isinstance(ids, list) or not all(isinstance(i, str) for i in ids)):
                    return self._error("ids must be a list of video IDs")
                if not JOB.start("details", "Fetch video details",
                                 lambda: run_enrich(video_ids=ids, should_stop=JOB.stop_requested)):
                    return self._error("Another task is already running", 409)
                return self._json({"ok": True})

            if path == "/api/local/pull":
                import local_setup
                model = _clean(body.get("model"))
                if not isinstance(model, str) or not re.fullmatch(r"[\w.\-/]+(:[\w.\-]+)?", model):
                    return self._error("Not a valid model name")
                if not JOB.start("pull", f"Download {model}",
                                 lambda: local_setup.pull_model(model, should_stop=JOB.stop_requested)):
                    return self._error("Another task is already running", 409)
                return self._json({"ok": True})

            if path == "/api/local/use":
                import local_setup
                model = _clean(body.get("model"))
                tasks = [t for t in body.get("tasks") or [] if t in llm.TASKS]
                if not isinstance(model, str) or not model:
                    return self._error("Model is required")
                local_setup.use_model(model, tasks)
                return self._json({"ok": True, "ai": llm.public_settings(llm.load_settings())})

            if path in ("/api/rate", "/api/rate/estimate"):
                import rater
                ids = body.get("ids")
                if ids is not None and (not isinstance(ids, list) or not all(isinstance(i, str) for i in ids)):
                    return self._error("ids must be a list of video IDs")
                if path == "/api/rate/estimate":
                    return self._json(rater.estimate(ids))
                if not llm.is_configured(llm.load_settings(), "rate"):
                    return self._error("Choose an AI for ratings on the AI page first")
                if not JOB.start("rate", "Rate videos", lambda: rater.run_rate(ids, should_stop=JOB.stop_requested)):
                    return self._error("Another task is already running", 409)
                return self._json({"ok": True})

            if path == "/api/details/settings":
                settings = llm.load_settings()
                if "after_sync" in body:
                    settings["details_after_sync"] = bool(body["after_sync"])
                name = _clean(body.get("category"))
                if name:
                    settings.setdefault("transcripts", {})[name] = bool(body.get("on"))
                llm.save_settings(settings)
                return self._json({"ok": True, "transcripts": _transcript_categories(settings),
                                   "details_after_sync": bool(settings.get("details_after_sync"))})
        except ValueError as e:
            return self._error(str(e))
        except llm.LLMError as e:
            return self._error(str(e), 502)
        return self._error("Not found", 404)

    # --- AI ------------------------------------------------------------------------
    def _ai(self, path, body):
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
            if isinstance(body.get("interests"), str):
                settings["interests"] = body["interests"].strip()[:2000]
            if isinstance(body.get("language"), str) and body["language"].strip():
                settings["language"] = body["language"].strip()[:40]
            llm.save_settings(settings)
            return self._json({"ok": True, "ai": llm.public_settings(settings)})

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
            presets = [p for p in settings["presets"] if p != name]
            if body.get("preset"):
                presets.append(name)
            settings["presets"] = presets
            llm.save_settings(settings)
            return self._json({"ok": True, "presets": presets})

        if path in ("/api/ai/discover", "/api/ai/suggest"):
            if not llm.is_configured(llm.load_settings(), "categorize"):
                return self._error("Set up an AI provider on the AI page first")
            if path == "/api/ai/discover":
                started = JOB.start("discover", "Propose categories", run_discover)
            else:
                scope = body.get("scope") or "unsorted"
                if scope not in ("unsorted", "unlocked"):
                    return self._error("Invalid scope")
                started = JOB.start("suggest", "Suggest categories", lambda: run_suggest(scope))
            if not started:
                return self._error("Another task is already running", 409)
            return self._json({"ok": True})

        if path == "/api/ai/proposal/apply":
            cats = body.get("categories")
            if not isinstance(cats, list):
                return self._error("categories must be a list")
            remove = {_clean(n) for n in body.get("remove") or []}
            old = load_taxonomy()
            settings = llm.load_settings()
            tax, notes = {}, dict(settings["category_notes"])
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
            save_taxonomy(tax)
            moved = sum(storage.release_category(n) for n in remove if n not in tax)
            settings["category_notes"] = {k: v for k, v in notes.items() if k in tax}
            settings["presets"] = [p for p in settings["presets"] if p in tax]
            settings["proposal"] = None
            llm.save_settings(settings)
            changed = storage.reclassify_videos(_classifier())
            return self._json({"ok": True, "categories": len(tax), "moved": moved, "changed": changed})

        if path == "/api/ai/proposal/dismiss":
            settings = llm.load_settings()
            settings["proposal"] = None
            llm.save_settings(settings)
            return self._json({"ok": True})

        if path == "/api/ai/suggestions/accept":
            n = storage.accept_suggestions(body.get("ids"), body.get("min_confidence"))
            return self._json({"ok": True, "accepted": n})

        if path == "/api/ai/suggestions/dismiss":
            n = storage.dismiss_suggestions(body.get("ids"))
            return self._json({"ok": True, "dismissed": n})

        return self._error("Not found", 404)

def _clean(value):
    return value.strip() if isinstance(value, str) else value

def _classifier():
    taxonomy = load_taxonomy()
    return lambda title, channel: classify_video(title, channel, taxonomy)

def main():
    parser = argparse.ArgumentParser(description="YT KB local dashboard")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true", help="Do not open the dashboard in a browser")
    args = parser.parse_args()

    storage.init_db()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"⚡ YT KB dashboard running at {url}  (Ctrl+C to stop)")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")

if __name__ == "__main__":
    main()
