import json
import unittest
from unittest import mock

import knowledge
import storage
from llm import LLMError
from tests.helpers import playlist, video

CAT = "KnowledgeTestCat"


def summary(tldr, topic):
    return {"tldr": tldr, "minutes": [{"t": 60, "topic": topic, "notes": ["note about " + topic]}],
            "claims": [{"t": 70, "kind": "claim", "text": topic + " claim"}], "quiz": [], "takeaways": [topic + " takeaway"],
            "learn_more": {}}


class ValidateTest(unittest.TestCase):
    def test_keeps_only_points_with_valid_citations(self):
        raw = {"overview": "LoRA and QLoRA.", "open_questions": ["Which rank?", ""],
               "sections": [{"heading": "Adapters", "points": [{"text": "LoRA trains adapters [1]", "cites": [1, 1, 9]},
                                                              {"text": "Uncited point", "cites": []},
                                                              {"text": "Cited only in text [2]"}]},
                            {"heading": "Empty", "points": [{"text": "x", "cites": [7]}]}]}
        page = knowledge.validate(raw, 3)
        self.assertEqual(page["sections"], [{"heading": "Adapters", "points": [{"text": "LoRA trains adapters", "cites": [1]},
                                                                               {"text": "Cited only in text", "cites": [2]}]}])
        self.assertEqual(page["open_questions"], ["Which rank?"])
        with self.assertRaises(LLMError):
            knowledge.validate({"sections": [{"heading": "h", "points": [{"text": "t", "cites": [5]}]}]}, 2)

    def test_sources_shorten_older_videos_to_fit(self):
        old = ({"video_id": "o", "title": "Old"}, {**summary("old tldr", "old"), "created_at": "2026-01-01"})
        new = ({"video_id": "n", "title": "New"}, {**summary("new tldr", "new"), "created_at": "2026-10-01"})
        items, short = knowledge.sources([old, new], 10_000)
        self.assertFalse(short)
        self.assertEqual([i["n"] for i in items], list(range(1, len(items) + 1)))
        self.assertIn("old claim", [i["text"] for i in items])
        items, short = knowledge.sources([old, new], 200)
        self.assertTrue(short)
        texts = [i["text"] for i in items]
        self.assertNotIn("old claim", texts)          # the older video keeps only TL;DR and takeaways
        self.assertIn("old takeaway", texts)


class PageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        storage.save_playlist(playlist("PLK"), [video("k1", "LoRA talk", category=CAT), video("k2", "QLoRA talk", category=CAT),
                                                video("k3", "Unsummarised", category=CAT)])
        storage.save_summary("k1", summary("LoRA freezes weights.", "LoRA"))
        storage.save_summary("k2", summary("QLoRA quantises to 4-bit.", "QLoRA"))

    def test_build_status_staleness_and_export(self):
        reply = json.dumps({"overview": "Two ways to fine-tune cheaply.",
                            "sections": [{"heading": "Methods", "points": [{"text": "LoRA trains adapters.", "cites": [1]},
                                                                           {"text": "QLoRA adds 4-bit.", "cites": [2, 99]}]}]})
        page = knowledge.build_page(1, CAT, complete=lambda s, p: reply)
        self.assertEqual(page["sections"][0]["points"][1]["cites"], [2])
        row = next(c for c in knowledge.status(1) if c["category"] == CAT)
        self.assertEqual((row["videos"], row["summarised"], row["page"]), (3, 2, "ready"))
        storage.save_summary("k3", summary("Now summarised.", "Extra"))           # the category changed
        self.assertEqual(next(c for c in knowledge.status(1) if c["category"] == CAT)["page"], "stale")
        found = storage.get_page(1, CAT)
        md = knowledge.page_markdown(CAT, found["page"], found["sources"])
        self.assertIn(f"# {CAT}", md)
        self.assertIn("- LoRA trains adapters. [\\[1\\]](https://www.youtube.com/watch?v=", md)
        self.assertIn("## Sources", md)

    def test_channel_scope_has_its_own_pages(self):
        cid = "UC" + "k" * 22
        storage.save_playlist({"id": storage.channel_source(cid), "title": "Teacher", "url": ""}, [video("k1", "LoRA talk", category=CAT)],
                              partial=True)
        reply = json.dumps({"sections": [{"heading": "Only LoRA", "points": [{"text": "LoRA trains adapters.", "cites": [1]}]}]})
        page = knowledge.build_page(1, CAT, complete=lambda s, p: reply, channel=cid)
        found = storage.get_page(1, knowledge.page_key(CAT, cid))
        self.assertEqual({i["video_id"] for i in found["sources"]}, {"k1"})        # only the channel's video
        self.assertEqual(page["sections"][0]["heading"], "Only LoRA")
        [row] = knowledge.status(1, cid)
        self.assertEqual((row["category"], row["videos"], row["summarised"], row["page"]), (CAT, 1, 1, "ready"))
        self.assertNotIn(knowledge.page_key(CAT, cid), [c["category"] for c in knowledge.status(1)])   # not among the library's

    def test_nothing_summarised(self):
        with self.assertRaises(LLMError):
            knowledge.build_page(1, "NoSuchCategory", complete=lambda s, p: "{}")


class BuildAllTest(unittest.TestCase):
    def test_builds_stale_and_eligible_unbuilt_pages(self):
        rows = [{"category": "stale", "page": "stale", "summarised": 0},
                {"category": "eligible", "page": "none", "summarised": 3},
                {"category": "ready", "page": "ready", "summarised": 8},
                {"category": "few", "page": "none", "summarised": 2}]
        with mock.patch.object(knowledge, "status", return_value=rows), \
                mock.patch.object(knowledge, "run_build", return_value=True) as build:
            self.assertTrue(knowledge.run_build_all(1))
        self.assertEqual([call.args for call in build.call_args_list], [(1, "stale", None), (1, "eligible", None)])

    def test_returns_false_if_any_page_fails(self):
        rows = [{"category": "one", "page": "stale", "summarised": 1},
                {"category": "two", "page": "stale", "summarised": 1}]
        with mock.patch.object(knowledge, "status", return_value=rows), \
                mock.patch.object(knowledge, "run_build", side_effect=[True, False]):
            self.assertFalse(knowledge.run_build_all(1))


class EndpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import ThreadingHTTPServer
        import dashboard
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def call(self, path, body=None):
        import urllib.error
        import urllib.request
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"X-YTKB": "1", "Content-Type": "application/json"} if body is not None else {},
                                     method="GET" if body is None else "POST")
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read() or b"{}")

    def test_knowledge_endpoints(self):
        self.assertEqual(self.call("/api/knowledge?library=1")[0], 200)
        self.assertEqual(self.call("/api/knowledge/page?library=1&category=Nope"), (200, {"page": None}))
        self.assertEqual(self.call("/api/export/knowledge?library=1&category=Nope")[0], 404)
        self.assertEqual(self.call("/api/knowledge/build", {"category": ""})[0], 400)
        with mock.patch("llm.is_configured", return_value=True), mock.patch("dashboard.JOB.start", return_value={"id": 3, "position": 0}) as start:
            self.assertEqual(self.call("/api/knowledge/build", {"category": CAT, "library": 1})[0], 200)
        self.assertEqual(start.call_args[0][0], "knowledge")

    def test_build_all_endpoint(self):
        with mock.patch("llm.is_configured", return_value=True), mock.patch("dashboard.JOB.start", return_value={"id": 4, "position": 0}) as start:
            code, body = self.call("/api/knowledge/build-all", {"library": 1})
        self.assertEqual(code, 200)
        self.assertEqual(body["id"], 4)
        self.assertIn("position", body)
        self.assertEqual(start.call_args.args[:2], ("knowledge", "Knowledge pages: build all"))
        with mock.patch("llm.is_configured", return_value=False):
            self.assertEqual(self.call("/api/knowledge/build-all", {"library": 1})[0], 400)


if __name__ == "__main__":
    unittest.main()
