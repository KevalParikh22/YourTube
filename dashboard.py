"""
Local web dashboard for YT KB - Phase 0.
Serves web/index.html plus a small JSON API over the local SQLite database.
Standard library only; binds to 127.0.0.1 so nothing is exposed to the network.

    python dashboard.py [--port 8765] [--no-open]
"""
import os
import io
import json
import argparse
import threading
import webbrowser
import contextlib
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import storage
from classifier import load_taxonomy, save_taxonomy, classify_video, FALLBACK_CATEGORY
from exporter import videos_to_csv

WEB_DIR = os.path.join(os.path.dirname(__file__), "web")
ALLOWED_BROWSERS = {"chrome", "edge", "firefox", "brave", "opera", "vivaldi", "chromium", "safari"}
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}

class IngestJob:
    """Runs one pipeline at a time in a background thread, capturing its console output."""
    def __init__(self):
        self.lock = threading.Lock()
        self.state = {"running": False, "ok": None, "log": "", "started_at": None, "finished_at": None, "url": None}
        self._buf = io.StringIO()

    def snapshot(self):
        snap = dict(self.state)
        if snap["running"]:
            snap["log"] = self._buf.getvalue()
        return snap

    def start(self, url, browser=None, cookies_file=None):
        with self.lock:
            if self.state["running"]:
                return False
            self._buf = io.StringIO()
            self.state = {"running": True, "ok": None, "log": "", "url": url,
                          "started_at": datetime.now().isoformat(timespec="seconds"), "finished_at": None}
        threading.Thread(target=self._run, args=(url, browser, cookies_file), daemon=True).start()
        return True

    def _run(self, url, browser, cookies_file):
        from main import run_pipeline  # deferred so the dashboard starts even if yt-dlp is missing
        ok = False
        try:
            with contextlib.redirect_stdout(self._buf):
                ok = run_pipeline(url, browser_cookies=browser, cookies_file=cookies_file, open_browser=False)
        except Exception as e:  # surface anything unexpected in the job log
            self._buf.write(f"\n❌ Unexpected error: {e}\n")
        self.state.update(running=False, ok=bool(ok), log=self._buf.getvalue(),
                          finished_at=datetime.now().isoformat(timespec="seconds"))

JOB = IngestJob()

def build_state():
    return {
        "playlists": storage.get_playlists(),
        "videos": storage.get_library(),
        "taxonomy": load_taxonomy(),
        "fallback_category": FALLBACK_CATEGORY,
        "job": JOB.snapshot(),
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
        if url.path == "/api/state":
            return self._json(build_state())
        if url.path == "/api/job":
            return self._json(JOB.snapshot())
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
                return self._json({"ok": True, "updated": n})

            if path == "/api/categories/delete":
                name = _clean(body.get("name"))
                tax = load_taxonomy()
                tax.pop(name, None)
                save_taxonomy(tax)
                n = storage.release_category(name)
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
                if not JOB.start(url, browser, cookies):
                    return self._error("An ingestion is already running", 409)
                return self._json({"ok": True})
        except ValueError as e:
            return self._error(str(e))
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
