"""End-to-end checks of the dashboard API against a temporary database."""
import json
import threading
import unittest
import urllib.error
import urllib.request
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
        self.assertEqual(ai["tasks"], {"categorize": None, "rate": "ollama", "ask": None})
        self.assertEqual((ai["interests"], ai["language"]), ("RAG, agents", "Hindi"))
        self.assertEqual(ai["ready_for"], {"categorize": True, "rate": True, "ask": True})
        self.assertIn("Ollama", ai["uses"]["rate"])
        self.assertIn("Ollama", ai["uses"]["ask"])          # questions follow the ratings choice
        self.assertIn("Claude", ai["uses"]["categorize"])
        self.call("/api/ai/settings", {"provider": "none", "tasks": {"rate": None}, "edit": "anthropic",
                                       "config": {"api_key": "__clear__"}, "language": "English", "interests": ""})

    def test_ai_requires_provider(self):
        self.call("/api/ai/settings", {"provider": "none"})
        code, r = self.call("/api/ai/suggest", {"scope": "unsorted"})
        self.assertEqual(code, 400)

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

    def test_cancel_without_job(self):
        self.assertEqual(self.call("/api/job/cancel", {})[1], {"ok": False, "stopping": False})

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
