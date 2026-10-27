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

    def test_nothing_summarised(self):
        with self.assertRaises(LLMError):
            knowledge.build_page(1, "NoSuchCategory", complete=lambda s, p: "{}")


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


if __name__ == "__main__":
    unittest.main()
