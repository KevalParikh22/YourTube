"""End-to-end checks of the dashboard API against a temporary database."""
import json
import os
import io
import tempfile
import contextlib
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock
from http.server import ThreadingHTTPServer

import dashboard
import llm
import storage
from tests.helpers import playlist, video


class DashboardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        storage.save_playlist(playlist(), [video("a", "Pasta recipe", category="Cooking"),
                                           video("b", "Mystery", category=storage.FALLBACK_CATEGORY)])
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def call(self, path, body=None, headers=None):
        hdrs = {"X-YTKB": "1", "Content-Type": "application/json"} if body is not None else {}
        hdrs.update(headers or {})
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers=hdrs, method="GET" if body is None else "POST")
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read() or b"{}")

    def test_state(self):
        code, state = self.call("/api/state")
        self.assertEqual(code, 200)
        self.assertTrue({"a", "b"} <= {v["video_id"] for v in state["videos"]})   # other tests share this DB
        self.assertIn("ai", state)
        self.assertFalse(state["ai"]["ready"])

    def test_csrf_and_host_checks(self):
        self.assertEqual(self.call("/api/reclassify", {}, {"X-YTKB": "0"})[0], 403)
        self.assertEqual(self.call("/api/state", headers={"Host": "evil.example"})[0], 403)

    def test_ai_settings_hide_key(self):
        code, r = self.call("/api/ai/settings", {"provider": "anthropic", "edit": "anthropic",
                                                  "config": {"model": "claude-opus-5-5", "api_key": "sk-test"}})
        self.assertEqual(code, 200)
        self.assertNotIn("sk-test", json.dumps(r))
        self.assertEqual(r["ai"]["providers"]["anthropic"]["key_source"], "saved")
        code, r = self.call("/api/ai/settings", {"edit": "anthropic", "config": {"api_key": "__clear__"}})
        self.assertIsNone(r["ai"]["providers"]["anthropic"]["key_source"])
        self.call("/api/ai/settings", {"provider": "none"})

    def test_task_routing(self):
        self.call("/api/ai/settings", {"provider": "anthropic", "edit": "anthropic", "config": {"api_key": "sk-x"},
                                       "tasks": {"rate": "ollama", "categorize": None, "bogus": "ollama"}})
        code, r = self.call("/api/ai/settings", {"edit": "ollama", "config": {"model": "llama3.1:8b"},
                                                  "interests": "  RAG, agents  ", "language": "Hindi"})
        ai = r["ai"]
        self.assertEqual(ai["tasks"], {"categorize": None, "rate": "ollama", "summarize": None, "ask": None, "interests": None})
        self.assertEqual((ai["interests"], ai["language"]), ("RAG, agents", "Hindi"))
        self.assertEqual(ai["ready_for"], {"categorize": True, "rate": True, "summarize": True, "ask": True, "interests": True})
        self.assertIn("Ollama", ai["uses"]["rate"])
        self.assertIn("Ollama", ai["uses"]["ask"])          # questions follow the ratings choice
        self.assertIn("Ollama", ai["uses"]["summarize"])    # and so do summaries
        self.assertIn("Ollama", ai["uses"]["interests"])    # and so does about you
        self.assertIn("Claude", ai["uses"]["categorize"])
        self.call("/api/ai/settings", {"provider": "none", "tasks": {"rate": None}, "edit": "anthropic",
                                       "config": {"api_key": "__clear__"}, "language": "English", "interests": ""})

    def test_settings_refuses_unknown_provider_or_task(self):
        # Unknown provider at top level
        code, r = self.call("/api/ai/settings", {"provider": "fake_provider"})
        self.assertEqual(code, 400)
        self.assertEqual(r.get("error"), "Unknown provider")

        # Unknown provider for edit
        code, r = self.call("/api/ai/settings", {"edit": "fake_provider", "config": {}})
        self.assertEqual(code, 400)
        self.assertEqual(r.get("error"), "Unknown provider")

        # Unknown provider for text task in tasks
        code, r = self.call("/api/ai/settings", {"tasks": {"rate": "fake_provider"}})
        self.assertEqual(code, 400)
        self.assertEqual(r.get("error"), "Unknown provider")

        # Unknown provider for media task in tasks
        code, r = self.call("/api/ai/settings", {"tasks": {"transcribe": "anthropic"}})
        self.assertEqual(code, 400)
        self.assertEqual(r.get("error"), "Unknown provider")

        # Unknown task in helpers
        code, r = self.call("/api/ai/settings", {"helpers": {"unknown_job": "ollama"}})
        self.assertEqual(code, 400)
        self.assertEqual(r.get("error"), "Unknown task")

        # Unknown provider for text helper
        code, r = self.call("/api/ai/settings", {"helpers": {"rate": "fake_provider"}})
        self.assertEqual(code, 400)
        self.assertEqual(r.get("error"), "Unknown provider")

        # Disallowed provider for media helper
        code, r = self.call("/api/ai/settings", {"helpers": {"transcribe": "anthropic"}})
        self.assertEqual(code, 400)
        self.assertEqual(r.get("error"), "Unknown provider")

        # Helpers must be an object
        code, r = self.call("/api/ai/settings", {"helpers": "not_a_dict"})
        self.assertEqual(code, 400)
        self.assertEqual(r.get("error"), "Helpers must be an object")

        # Valid media task and helper settings save and return public_settings
        code, r = self.call("/api/ai/settings", {
            "tasks": {"transcribe": "groq", "slides": "gemini"},
            "helpers": {"rate": "groq", "transcribe": "local"},
        })
        self.assertEqual(code, 200)
        ai = r["ai"]
        self.assertEqual(ai["media_tasks"]["transcribe"]["label"], "Transcription")
        self.assertEqual(ai["helpers"]["rate"], "groq")
        self.assertEqual(ai["helpers"]["transcribe"], "local")

        # Cleanup
        self.call("/api/ai/settings", {
            "tasks": {"transcribe": None, "slides": None},
            "helpers": {"rate": None, "transcribe": None},
        })

    def test_about_you_validation_and_language(self):
        # Invalid language -> 400
        code, r = self.call("/api/ai/settings", {"language": "Klingon"})
        self.assertEqual(code, 400)
        self.assertEqual(r.get("error"), "Choose a supported language")

        # Empty language -> 400
        code, r = self.call("/api/ai/settings", {"language": ""})
        self.assertEqual(code, 400)
        self.assertEqual(r.get("error"), "Choose a supported language")

        # A valid language saves
        code, r = self.call("/api/ai/settings", {"language": "Spanish"})
        self.assertEqual(code, 200)
        self.assertEqual(r["ai"]["language"], "Spanish")
        self.assertEqual(llm.load_settings()["language"], "Spanish")
        self.assertEqual(r["ai"]["languages"], llm.LANGUAGES)

        # An older free-text language can be re-sent unchanged, but not switched to
        s = llm.load_settings(); s["language"] = "Hinglish"; llm.save_settings(s)
        self.assertEqual(self.call("/api/ai/settings", {"language": "Hinglish"})[0], 200)
        self.assertEqual(self.call("/api/ai/settings", {"language": "English"})[0], 200)
        self.assertEqual(self.call("/api/ai/settings", {"language": "Hinglish"})[0], 400)

        # Interests length over 2,000 characters -> 400
        code, r = self.call("/api/ai/settings", {"interests": "a" * 2001})
        self.assertEqual(code, 400)
        self.assertIn("2,000", r.get("error", ""))

        # Interests length up to 2,000 characters -> 200
        long_interests = "word " * 400  # 2000 chars exactly
        code, r = self.call("/api/ai/settings", {"interests": long_interests})
        self.assertEqual(code, 200)
        self.assertEqual(r["ai"]["interests"], long_interests.strip())

        # Cleanup: reset language and interests back to default
        self.call("/api/ai/settings", {"language": "English", "interests": ""})

    def test_ai_requires_provider(self):
        self.call("/api/ai/settings", {"provider": "none"})
        code, r = self.call("/api/ai/suggest", {"scope": "unsorted"})
        self.assertEqual(code, 400)

    def test_discover_only_when_library_has_no_categories(self):
        started = []
        with mock.patch("llm.is_configured", return_value=True), \
             mock.patch("dashboard.load_taxonomy", return_value={"Cooking": []}), \
             mock.patch.object(dashboard.JOB, "start", side_effect=lambda *args, **kwargs: started.append(True)):
            code, r = self.call("/api/ai/discover", {})
        self.assertEqual(code, 400)
        self.assertEqual(r["error"], "This library already has categories. Sort unsorted videos instead.")
        self.assertEqual(started, [])
        with mock.patch("llm.is_configured", return_value=True), \
             mock.patch("dashboard.load_taxonomy", return_value={}), \
             mock.patch.object(dashboard.JOB, "start", return_value={"id": 1, "position": 0}) as start:
            code, r = self.call("/api/ai/discover", {})
        self.assertEqual(code, 200)
        self.assertTrue(r["ok"])
        self.assertEqual(start.call_args.args[0], "discover")

    def test_presets_and_proposal_apply(self):
        self.call("/api/presets", {"name": "Cooking", "preset": True})
        s = llm.load_settings()
        s["proposal"] = {"categories": [{"name": "Cooking", "keywords": ["pasta"]}], "model": "test"}
        llm.save_settings(s)
        code, r = self.call("/api/ai/proposal/apply", {
            "categories": [{"name": "Cooking", "keywords": ["pasta"], "description": "Food"},
                           {"name": "Mystery Stories", "keywords": ["mystery"]}],
            "remove": []})
        self.assertEqual(code, 200)
        _, state = self.call("/api/state")
        self.assertIsNone(state["ai"]["proposal"])
        self.assertIn("Mystery Stories", state["taxonomy"])
        self.assertEqual(state["ai"]["category_notes"]["Cooking"], "Food")
        self.assertEqual({v["video_id"]: v["category"] for v in state["videos"]}["b"], "Mystery Stories")

    def test_accept_suggestion_via_api(self):
        storage.save_suggestions([{"video_id": "b", "category": "Cooking", "confidence": 0.7}])
        code, r = self.call("/api/ai/suggestions/accept", {"ids": ["b"]})
        self.assertEqual((code, r["accepted"]), (200, 1))
        _, state = self.call("/api/state")
        b = {v["video_id"]: v for v in state["videos"]}["b"]
        self.assertEqual((b["category"], b["category_source"]), ("Cooking", "ai"))
        self.call("/api/videos/update", {"ids": ["b"], "reset_category": True})

    def test_remove_playlist_endpoints(self):
        storage.save_playlist(playlist("PL_RM"), [video("rm_solo", "Gone soon"), video("a", "Pasta recipe", category="Cooking")])
        self.assertEqual(self.call("/api/playlists/remove?id=PL_RM"), (200, {"removed": 1, "kept_notes": 0, "shared": 1}))
        self.assertEqual(self.call("/api/playlists/remove?id=NOPE")[0], 404)
        self.assertEqual(self.call("/api/playlists/remove", {"id": "NOPE"})[0], 404)
        self.assertEqual(self.call("/api/playlists/remove", {"id": "PL_RM"}, {"X-YTKB": "0"})[0], 403)
        started = []
        with mock.patch.object(dashboard.JOB, "start", side_effect=lambda kind, label, fn, key=None: started.append((kind, fn)) or {"id": 999, "position": 0}):
            self.assertEqual(self.call("/api/playlists/remove", {"id": "PL_RM"})[0], 200)
        kind, fn = started[0]
        self.assertEqual(kind, "remove")
        with tempfile.TemporaryDirectory() as frames, mock.patch.object(dashboard, "FRAMES_DIR", frames):
            os.makedirs(os.path.join(frames, "rm_solo"))
            os.makedirs(os.path.join(frames, "a"))
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertTrue(fn())
            self.assertEqual(sorted(os.listdir(frames)), ["a"])   # only the removed video's thumbnails go
        self.assertIn("Nothing changes on YouTube", out.getvalue())
        ids = {v["video_id"] for v in storage.get_library()}
        self.assertNotIn("rm_solo", ids)
        self.assertIn("a", ids)

    def test_cloud_estimates_and_index_switch(self):
        code, r = self.call("/api/asr/plan")
        self.assertEqual((code, r["engine"], r["cost"]), (200, "local", 0.0))
        self.assertIn("Nothing is sent", r["sends"])
        s = llm.load_settings()
        s["providers"].setdefault("groq", {})["api_key"] = "gsk_test_key_123456789012345"
        s["tasks"]["transcribe"] = "groq"
        llm.save_settings(s)
        try:
            code, r = self.call("/api/asr/plan")
            self.assertEqual(r["engine"], "groq")
            self.assertIn("uploaded to Groq", r["sends"])
            self.assertNotIn("gsk_test", json.dumps(r))
            started = []
            with mock.patch.object(dashboard.JOB, "start", side_effect=lambda kind, label, fn, key=None: started.append(kind) or {"id": 1, "position": 0}):
                code, r = self.call("/api/embed/switch", {"provider": "local"})
            self.assertEqual((code, started), (200, ["embed"]))
            self.assertEqual(self.call("/api/embed/switch", {"provider": "nope"})[0], 400)
        finally:
            s = llm.load_settings()
            s["tasks"].pop("transcribe", None)
            s["tasks"].pop("embed", None)
            s["providers"].get("groq", {}).pop("api_key", None)
            llm.save_settings(s)

    def test_separate_combined_categories(self):
        from classifier import save_taxonomy, load_taxonomy
        before = load_taxonomy(library=1)
        save_taxonomy({**before, "Health & Fitness": ["gym"], "Fitness": ["workout"]}, library=1)
        storage.save_playlist(playlist("PL_SEP"), [video("sep1", "Leg day", category="Health & Fitness")])
        storage.save_suggestions([{"video_id": "sep1", "category": "Health & Fitness", "confidence": 0.9}], include_locked=True)
        try:
            self.assertEqual(self.call("/api/categories/separate", {"names": ["Fitness"]})[0], 400)    # not combined
            code, r = self.call("/api/categories/separate", {"names": ["Health & Fitness"], "preview": True})
            self.assertEqual([(t["name"], t["exists"]) for t in r["topics"]], [("Health", False), ("Fitness", True)])
            self.assertIn("Health & Fitness", load_taxonomy(library=1))                                   # preview changes nothing
            started = []
            with mock.patch("llm.is_configured", return_value=True), \
                 mock.patch.object(dashboard.JOB, "start", side_effect=lambda kind, label, fn, key=None: started.append(key) or {"id": 1, "position": 0}):
                code, r = self.call("/api/categories/separate", {"names": ["Health & Fitness"]})
            self.assertEqual((r["queued"], started), (1, [["category:Health & Fitness", 1]]))   # its videos get suggestions
            self.assertIsNone(next(v for v in storage.get_library() if v["video_id"] == "sep1")["suggestion"])   # the stale one went
            tax = load_taxonomy(library=1)
            self.assertEqual((code, "Health & Fitness" in tax, tax["Health"], tax["Fitness"]), (200, False, ["health"], ["workout"]))
            self.assertIn("Health", llm.load_settings()["presets"])
        finally:
            save_taxonomy(before, library=1)

    def test_video_details_endpoint(self):
        code, _ = self.call("/api/video?id=a")
        self.assertEqual(code, 404)
        storage.save_details("a", {"status": "done", "segments": [{"start": 1, "dur": 1, "text": "<b>hi</b>"}],
                                   "chapters": [], "caption_kind": "auto", "caption_lang": "en"})
        code, r = self.call("/api/video?id=a")
        self.assertEqual((code, r["details"]["segments"][0]["text"]), (200, "<b>hi</b>"))
        _, state = self.call("/api/state")
        self.assertEqual({v["video_id"]: v for v in state["videos"]}["a"]["details"]["status"], "done")

    def test_transcript_switches(self):
        code, r = self.call("/api/details/settings", {"category": "Cooking", "on": False})
        self.assertEqual(code, 200)
        self.assertFalse(r["transcripts"]["Cooking"])
        code, r = self.call("/api/details/settings", {"after_sync": True})
        self.assertTrue(r["details_after_sync"])
        self.call("/api/details/settings", {"category": "Cooking", "on": True, "after_sync": False})

    def test_fetch_rejects_bad_ids(self):
        self.assertEqual(self.call("/api/details/fetch", {"ids": "a"})[0], 400)

    def test_body_must_be_a_json_object(self):
        for raw in (b"[]", b"3", b'"x"'):
            req = urllib.request.Request(self.base + "/api/reclassify", data=raw, method="POST",
                                         headers={"X-YTKB": "1", "Content-Type": "application/json"})
            with self.assertRaises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(req)
            self.assertEqual(e.exception.code, 400)
            e.exception.close()

    def test_no_questions_while_transcribing(self):
        from unittest import mock
        with mock.patch.object(dashboard.JOB, "busy", side_effect=lambda kind, lane: (kind, lane) == ("asr", "local")):
            code, r = self.call("/api/ask", {"question": "lora?"})
        self.assertEqual((code, "Transcribing" in r["error"]), (409, True))

    def test_cancel_without_job(self):
        self.assertEqual(self.call("/api/job/cancel", {})[1], {"ok": False, "stopping": False})

    def test_job_retry_latest_failure_and_dismiss(self):
        from unittest import mock
        job = dashboard.Job()
        from tests.test_queue import wait_until
        idle = lambda: wait_until(lambda: not job.snapshot()["running"] and not job.snapshot()["queue"])
        job.start("test", "failed", lambda: False)   # not an AUTO_EMBED kind: no real embedding call
        idle()
        failed_id = job.snapshot()["id"]
        self.assertTrue(job.dismiss(failed_id))
        self.assertTrue(job.snapshot()["dismissed"])
        self.assertIsNotNone(job.retry(failed_id))
        idle()
        latest_id = job.snapshot()["id"]
        self.assertNotEqual(latest_id, failed_id)
        self.assertIsNone(job.retry(failed_id))
        self.assertIsNotNone(job.retry(latest_id))
        idle()

    def test_logs_level_cap_and_download(self):
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "yourtube.log")
            with open(path, "w", encoding="utf-8") as f:
                f.write("2026-10-29 12:00:00 INFO    yourtube.test: info\n")
                f.write("2026-10-29 12:00:01 WARNING yourtube.test: warning\n  detail\n")
                f.write("2026-10-29 12:00:02 ERROR   yourtube.test: error\n")
            with mock.patch.object(dashboard.applog, "LOG_FILE", path):
                code, result = self.call("/api/logs?level=warning&lines=1")
                self.assertEqual(code, 200)
                self.assertEqual(result["lines"], ["2026-10-29 12:00:02 ERROR   yourtube.test: error"])
                code, headers, body = self.raw("/download/yourtube.log")
                self.assertEqual(code, 200)
                self.assertIn(b"warning", body)

    def test_local_endpoints(self):
        self.assertEqual(self.call("/api/local/pull", {"model": "bad name!"})[0], 400)
        code, r = self.call("/api/ai/settings", {"edit": "ollama", "config": {"num_ctx": 999999, "base_url": "http://localhost:11434/v1"}})
        self.assertEqual((r["ai"]["providers"]["ollama"]["num_ctx"], r["ai"]["providers"]["ollama"]["base_url"]), (131072, "http://localhost:11434"))
        self.assertEqual(self.call("/api/ai/settings", {"edit": "ollama", "config": {"num_ctx": "big"}})[0], 400)
        code, r = self.call("/api/ai/settings", {"local_only": True})
        self.assertTrue(r["ai"]["local_only"])
        self.call("/api/ai/settings", {"local_only": False, "edit": "ollama", "config": {"num_ctx": 8192}})

    def raw(self, path):
        req = urllib.request.Request(self.base + path)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.headers, b""

    def test_downloads_are_whitelisted(self):
        code, headers, body = self.raw("/download/setup.sh")
        self.assertEqual(code, 200)
        self.assertIn('attachment; filename="setup.sh"', headers["Content-Disposition"])
        self.assertTrue(body.startswith(b"#!/usr/bin/env bash"))
        code, headers, body = self.raw("/docs/setup")
        self.assertEqual(code, 200)
        self.assertIsNone(headers["Content-Disposition"])
        self.assertTrue(body.startswith(b"# YourTube setup guide"))
        for bad in ("/download/settings.json", "/download/../llm.py", "/download/%2e%2e/yt_kb.db", "/download/"):
            self.assertEqual(self.raw(bad)[0], 404, bad)

    def test_static_files(self):
        # Allow-list contains only the approved assets with expected types
        self.assertEqual(set(dashboard.STATIC.keys()),
                         {"inter.woff2", "source-serif-4.woff2", "icon.svg", "OFL.txt", "app.css", "app.js"})
        self.assertEqual(dashboard.STATIC["inter.woff2"][1], "font/woff2")
        self.assertEqual(dashboard.STATIC["source-serif-4.woff2"][1], "font/woff2")
        self.assertEqual(dashboard.STATIC["icon.svg"][1], "image/svg+xml")
        self.assertEqual(dashboard.STATIC["OFL.txt"][1], "text/plain")

        # The page loads the split-out styles and script, which are never cached for long
        code, _, body = self.raw("/")
        self.assertIn(b'<link rel="stylesheet" href="/static/app.css">', body)
        self.assertIn(b'<script src="/static/app.js"></script>', body)
        code, headers, body = self.raw("/static/app.js")
        self.assertEqual((code, headers["Cache-Control"]), (200, "no-cache"))
        self.assertTrue(body.startswith(b"'use strict'"))

        # icon.svg placeholder is served with content type and cache header
        code, headers, body = self.raw("/static/icon.svg")
        self.assertEqual(code, 200)
        self.assertEqual(headers["Content-Type"], "image/svg+xml")
        self.assertEqual(headers["Cache-Control"], "public, max-age=604800")
        self.assertTrue(body.startswith(b"<svg"))

        # Every allowed name serves with its type and cache headers when present
        with tempfile.TemporaryDirectory() as tmp:
            test_static = {}
            for name, (_, ctype) in dashboard.STATIC.items():
                p = os.path.join(tmp, name)
                with open(p, "wb") as f:
                    f.write(f"content-{name}".encode())
                test_static[name] = (p, ctype)
            with mock.patch.dict(dashboard.STATIC, test_static):
                for name, (_, ctype) in dashboard.STATIC.items():
                    code, headers, body = self.raw(f"/static/{name}")
                    self.assertEqual(code, 200, name)
                    self.assertEqual(headers["Content-Type"], ctype, name)
                    self.assertEqual(headers["Cache-Control"],
                                     "no-cache" if name in dashboard.NO_CACHE_STATIC else "public, max-age=604800", name)
                    self.assertEqual(body, f"content-{name}".encode(), name)

        # Traversal and unknown names give 404
        for bad in ("/static/../settings.json", "/static/x", "/static/%2e%2e/settings.json",
                    "/static/%2e%2e%2fsettings.json", "/static/", "/static/unknown.woff2",
                    "/static/../web/index.html"):
            self.assertEqual(self.raw(bad)[0], 404, bad)

    def test_favicon_redirect(self):
        import http.client
        conn = http.client.HTTPConnection(self.base.split("//")[1])
        conn.request("GET", "/favicon.ico")
        r = conn.getresponse()
        self.assertEqual(r.status, 302)
        self.assertEqual(r.getheader("Location"), "/static/icon.svg")
        conn.close()


    def test_markdown_export(self):
        code, headers, body = self.raw("/api/export/markdown?id=a")
        self.assertEqual(code, 200)
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertTrue(body.decode().startswith("---\ntitle:"))
        self.assertEqual(self.raw("/api/export/markdown?id=nope")[0], 404)
        self.assertEqual(self.call("/api/export/markdown", {"ids": []})[0], 400)
        req = urllib.request.Request(self.base + "/api/export/markdown", data=json.dumps({"ids": ["a", "b"]}).encode(),
                                     headers={"X-YTKB": "1", "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req) as r:
            self.assertEqual(r.headers["Content-Type"], "application/zip")
            self.assertEqual(r.read()[:2], b"PK")

    def test_asr_endpoints(self):
        code, plan = self.call("/api/asr/plan")
        self.assertEqual(code, 200)
        self.assertIn("videos", plan)
        self.assertIn("cost", plan)
        code, est = self.call("/api/asr/estimate")
        self.assertEqual(code, 200)
        self.assertEqual(est["videos"], plan["videos"])
        code, post_est = self.call("/api/asr/estimate", {"ids": []})
        self.assertEqual(code, 200)
        self.assertEqual(post_est["videos"], 0)
        self.assertEqual(self.call("/api/transcript/source", {"id": "a", "source": "local-asr"})[0], 400)
        self.assertEqual(self.call("/api/asr", {"ids": "a"})[0], 400)
        code, r = self.call("/api/ai/settings", {"asr_model": "small"})
        self.assertEqual(r["ai"]["asr_model"], "small")
        self.call("/api/ai/settings", {"asr_model": "auto"})

    def test_visual_endpoints(self):
        for bad in ("/frames/../yt_kb.db", "/frames/a/../../llm.py", "/frames/a/x.jpg", "/frames/a/00001.png"):
            self.assertEqual(self.raw(bad)[0], 404, bad)
        self.assertEqual(self.raw("/frames/a/00001.jpg")[0], 404)       # valid shape, missing file
        self.assertEqual(self.call("/api/videos/visuals", {"ids": ["a"], "value": "yes"})[0], 400)
        code, r = self.call("/api/videos/visuals", {"ids": ["a"], "value": True})
        self.assertEqual((code, r["updated"]), (200, 1))
        code, r = self.call("/api/visuals/settings", {"category": "Cooking", "on": True})
        self.assertTrue(r["visual_categories"]["Cooking"])
        self.call("/api/visuals/settings", {"category": "Cooking", "on": False})
        code, plan = self.call("/api/visuals/plan")
        self.assertEqual(code, 200)
        self.assertGreaterEqual(plan["videos"], 1)
        self.call("/api/videos/visuals", {"ids": ["a"], "value": None})

    def test_retrieve_endpoints(self):
        self.assertEqual(self.call("/api/retrieve")[0], 400)
        code, r = self.call("/api/retrieve?q=pasta%20recipe&k=3")
        self.assertEqual(code, 200)
        self.assertIn(r["mode"], ("hybrid", "keyword"))
        code, st = self.call("/api/embed/status")
        self.assertEqual(code, 200)
        self.assertIn("embedded", st)
        code, r = self.call("/api/ai/settings", {"embed_model": "qwen3-embedding:4b; x"})
        self.assertEqual(r["ai"]["embed_model"], "qwen3-embedding:0.6b")          # invalid names are ignored
        code, r = self.call("/api/ai/settings", {"embed_model": "qwen3-embedding:4b"})
        self.assertEqual(r["ai"]["embed_model"], "qwen3-embedding:4b")
        self.call("/api/ai/settings", {"embed_model": "qwen3-embedding:0.6b"})


class AddVideoTest(unittest.TestCase):
    def test_add_video_task(self):
        from unittest import mock
        fake = {"video_id": "abcdefghijk", "title": "Pasta recipe in 5 minutes", "channel": "Chef", "duration_seconds": 300,
                "url": "https://www.youtube.com/watch?v=abcdefghijk", "position": 0, "category": "Uncategorized", "status": "unwatched"}
        with mock.patch("extractor.extract_video", return_value=dict(fake)), mock.patch("enrich.run_enrich") as enrich:
            self.assertTrue(dashboard._add_video("abcdefghijk"))
        enrich.assert_called_once()
        v = next(x for x in storage.get_library() if x["video_id"] == "abcdefghijk")
        self.assertEqual([s["id"] for s in v["sources"]], ["_single"])
        with mock.patch("extractor.extract_video", side_effect=ValueError("private")):
            self.assertFalse(dashboard._add_video("zzzzzzzzzzz"))


class AccessTokenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.addCleanup(setattr, dashboard, "TOKEN", None)
        dashboard.TOKEN = "s3cret-token"

    def get(self, path, cookie=None):
        import http.client
        conn = http.client.HTTPConnection(self.base.split("//")[1])
        conn.request("GET", path, headers={"Cookie": cookie} if cookie else {})
        r = conn.getresponse()
        body = r.read()
        conn.close()
        return r.status, r.getheader("Set-Cookie"), r.getheader("Location"), body

    def test_token_link_cookie_and_refusals(self):
        status, _, _, body = self.get("/api/state")
        self.assertEqual(status, 401)
        status, cookie, _, body = self.get("/")
        self.assertEqual((status, cookie), (200, None))
        self.assertIn(b"--print-url --port", body)                              # help page naming this (non-default) port
        self.assertIsNone(self.get("/?t=wrong")[1])                              # a wrong token sets nothing
        status, cookie, location, _ = self.get("/?t=s3cret-token")
        self.assertEqual((status, location), (303, "/"))
        self.assertIn("ytkb_token=s3cret-token", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        status, _, _, body = self.get("/api/state", cookie="ytkb_token=s3cret-token")
        self.assertEqual(status, 200)
        self.assertNotIn(b"s3cret-token", body)                                  # never sent back to the page
        self.assertEqual(self.get("/api/state", cookie="ytkb_token=nope")[0], 401)
        req = urllib.request.Request(self.base + "/api/reclassify", data=b"{}", method="POST",
                                     headers={"X-YTKB": "1", "Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        self.assertEqual(e.exception.code, 401)
        e.exception.close()

    def test_token_created_once_and_kept_out_of_public_settings(self):
        first = dashboard.load_token()
        self.assertEqual(dashboard.load_token(), first)
        self.assertGreaterEqual(len(first), 30)
        self.assertNotIn(first, json.dumps(llm.public_settings(llm.load_settings())))

    def test_static_requires_token(self):
        status, _, _, _ = self.get("/static/icon.svg")
        self.assertEqual(status, 401)
        status, _, _, _ = self.get("/static/inter.woff2")
        self.assertEqual(status, 401)
        status, _, _, _ = self.get("/static/../settings.json")
        self.assertEqual(status, 401)
        status, _, _, body = self.get("/static/icon.svg", cookie=f"{dashboard.TOKEN_COOKIE}={dashboard.TOKEN}")
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b"<svg"))
        status, _, _, _ = self.get("/static/../settings.json", cookie=f"{dashboard.TOKEN_COOKIE}={dashboard.TOKEN}")
        self.assertEqual(status, 404)

    def test_favicon_exempt_from_token(self):
        status, _, location, _ = self.get("/favicon.ico")
        self.assertEqual(status, 302)
        self.assertEqual(location, "/static/icon.svg")


class ServiceAndStartupTest(unittest.TestCase):
    def setUp(self):
        self.orig_token = dashboard.TOKEN
        self.addCleanup(setattr, dashboard, "TOKEN", self.orig_token)

    def test_main_non_tty_start_line_without_token(self):
        buf = io.StringIO()
        fake_server = mock.MagicMock()
        fake_server.serve_forever.side_effect = KeyboardInterrupt
        with mock.patch("dashboard.ThreadingHTTPServer", return_value=fake_server), \
             mock.patch("sys.stdout.isatty", return_value=False), \
             contextlib.redirect_stdout(buf):
            dashboard.main(["--port", "8765", "--no-open"])
        out = buf.getvalue()
        self.assertIn("YourTube dashboard running on http://127.0.0.1:8765. Get the link with: .venv/bin/python dashboard.py --print-url", out)
        self.assertNotIn("?t=", out)
        self.assertNotIn(dashboard.TOKEN, out)

    def test_main_tty_start_line_with_token(self):
        buf = io.StringIO()
        buf.isatty = lambda: True
        fake_server = mock.MagicMock()
        fake_server.serve_forever.side_effect = KeyboardInterrupt
        with mock.patch("dashboard.ThreadingHTTPServer", return_value=fake_server), \
             contextlib.redirect_stdout(buf):
            dashboard.main(["--port", "8765", "--no-open"])
        out = buf.getvalue()
        self.assertIn(f"YourTube dashboard running. Open: http://127.0.0.1:8765/?t={dashboard.TOKEN}", out)

    def test_main_port_in_use_exits_0_when_yourtube_running(self):
        import errno
        buf = io.StringIO()
        err = OSError(errno.EADDRINUSE, "Address in use")
        err.errno = errno.EADDRINUSE
        with mock.patch("dashboard.ThreadingHTTPServer", side_effect=err), \
             mock.patch("dashboard._probe_job", return_value=True), \
             contextlib.redirect_stdout(buf), \
             self.assertRaises(SystemExit) as cm:
            dashboard.main(["--port", "8765", "--no-open"])
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("YourTube is already running on port 8765 (or another program uses it). Open it with: .venv/bin/python dashboard.py --print-url", buf.getvalue())

    def test_main_port_in_use_exits_1_when_other_program(self):
        import errno
        buf = io.StringIO()
        err = OSError(errno.EADDRINUSE, "Address in use")
        err.errno = errno.EADDRINUSE
        with mock.patch("dashboard.ThreadingHTTPServer", side_effect=err), \
             mock.patch("dashboard._probe_job", return_value=False), \
             contextlib.redirect_stdout(buf), \
             self.assertRaises(SystemExit) as cm:
            dashboard.main(["--port", "8765", "--no-open"])
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("YourTube is already running on port 8765 (or another program uses it). Open it with: .venv/bin/python dashboard.py --print-url", buf.getvalue())

    def test_probe_job(self):
        # 200 and 401 return True
        res200 = mock.MagicMock()
        res200.__enter__.return_value = res200
        res200.getcode.return_value = 200
        with mock.patch("urllib.request.urlopen", return_value=res200):
            self.assertTrue(dashboard._probe_job(8765))

        err401 = urllib.error.HTTPError("http://127.0.0.1:8765/api/job", 401, "Unauthorized", {}, io.BytesIO())
        with mock.patch("urllib.request.urlopen", side_effect=err401):
            self.assertTrue(dashboard._probe_job(8765))
        err401.close()

        # 404 returns False
        err404 = urllib.error.HTTPError("http://127.0.0.1:8765/api/job", 404, "Not Found", {}, io.BytesIO())
        with mock.patch("urllib.request.urlopen", side_effect=err404):
            self.assertFalse(dashboard._probe_job(8765))
        err404.close()

        # Network/connection error returns False
        with mock.patch("urllib.request.urlopen", side_effect=ConnectionRefusedError):
            self.assertFalse(dashboard._probe_job(8765))

    def test_service_unit_text_and_script(self):
        import subprocess
        service_sh = os.path.join(dashboard.ROOT_DIR, "scripts", "service.sh")
        try:
            os.chmod(service_sh, 0o755)
        except OSError:
            pass
        # bash -n syntax check
        res = subprocess.run(["bash", "-n", service_sh], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"bash -n failed: {res.stderr}")

        # The unit text quotes paths with spaces
        with open(service_sh, "r", encoding="utf-8") as f:
            script_text = f.read()

        # Check Linux systemd unit quoting in service.sh
        self.assertIn('ExecStartPre=/usr/bin/test -d "$ROOT"', script_text)
        self.assertIn('ExecStart="$ROOT/.venv/bin/python" "$ROOT/dashboard.py" --no-open --port $PORT', script_text)

        # Check macOS launchd plist also exists in service.sh
        self.assertIn("com.yourtube.dashboard.plist", script_text)
        self.assertIn("<key>KeepAlive</key>", script_text)
        self.assertIn("<key>RunAtLoad</key>", script_text)

        # Check service.ps1 exists and has restart settings
        service_ps1 = os.path.join(dashboard.ROOT_DIR, "scripts", "service.ps1")
        self.assertTrue(os.path.exists(service_ps1))
        with open(service_ps1, "r", encoding="utf-8") as f:
            ps1_text = f.read()
        self.assertIn("-RestartCount 3", ps1_text)
        self.assertIn("pythonw.exe", ps1_text)

