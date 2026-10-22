import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

import asker
import dashboard
import llm
import storage
from exporter import ask_markdown

PIECES = [
    {"id": 1, "video_id": "q_ft", "title": "Fine tuning talk", "kind": "transcript", "t": 254, "text": "LoRA trains a few adapter weights"},
    {"id": 2, "video_id": "q_db", "title": "Graph project", "kind": "transcript", "t": 3662, "text": "run neo4j in docker"},
    {"id": 3, "video_id": "q_ft", "title": "Fine tuning talk", "kind": "summary", "t": None, "text": "A talk about fine tuning"},
]


def found(question, k, filters):
    return {"mode": "hybrid", "pieces": PIECES}


def rows(*r):
    return lambda sentences: [list(x) for x in r][:len(sentences)]


class CitationCheckTest(unittest.TestCase):
    def test_invalid_numbers_dropped_and_lists_split(self):
        r = asker.check_citations("LoRA trains few weights [1, 7]. Neo4j runs in Docker [2;0].", 3)
        self.assertEqual(r["answer"], "LoRA trains few weights [1]. Neo4j runs in Docker [2].")
        self.assertEqual((r["cited"], r["dropped"], r["moved"]), ([1, 2], 2, 0))

    def test_misattributed_citation_moves_to_best_match(self):
        r = asker.check_citations("Neo4j runs in Docker [1].", 3, rows((0.30, 0.80, 0.20)))
        self.assertEqual((r["answer"], r["moved"]), ("Neo4j runs in Docker [2].", 1))

    def test_close_or_weak_matches_stay(self):
        # The cited excerpt is nearly as good: keep the model's choice
        self.assertEqual(asker.check_citations("A claim [1].", 3, rows((0.70, 0.78, 0.2)))["moved"], 0)
        # Every excerpt matches weakly (e.g. "your videos don't cover this"): nothing to move to
        self.assertEqual(asker.check_citations("A claim [1].", 3, rows((0.20, 0.45, 0.2)))["moved"], 0)

    def test_duplicates_after_moving_collapse(self):
        r = asker.check_citations("Docker setup [1][2].", 3, rows((0.1, 0.9, 0.1)))
        self.assertEqual((r["answer"], r["cited"]), ("Docker setup [2].", [2]))

    def test_marks_on_their_own_line_and_inline_mentions(self):
        r = asker.check_citations("LM Studio serves local models.\n\n[1]\n\nExcerpt [2] shows Docker.", 3)
        self.assertEqual(r["answer"], "LM Studio serves local models. [1]\n\nExcerpt [2] shows Docker.")
        self.assertEqual(r["cited"], [1, 2])

    def test_similarity_failure_still_checks_numbers(self):
        def broken(sentences):
            raise RuntimeError("Ollama is down")
        r = asker.check_citations("A [1] B [9].", 2, broken)
        self.assertEqual((r["answer"], r["dropped"]), ("A [1] B.", 1))


class AskTest(unittest.TestCase):
    def test_answer_is_checked_numbered_and_saved(self):
        reply = json.dumps({"answer": "Use LoRA to train a few weights [2]. Neo4j runs in Docker [2][5]."})
        seen = {}

        def complete(system, prompt):
            seen["prompt"] = prompt
            return reply
        r = asker.ask("How do I fine-tune cheaply?", settings={**llm.load_settings(), "language": "Hindi"}, complete=complete,
                      retrieve_fn=found, sim_fn=rows((0.9, 0.3, 0.5), (0.2, 0.9, 0.1)))
        self.assertIn("[1] Fine tuning talk @ 4:14 (transcript): LoRA", seen["prompt"])
        self.assertIn("[3] Fine tuning talk (AI summary)", seen["prompt"])     # no time for whole-video pieces
        self.assertIn("in Hindi", seen["prompt"])
        self.assertEqual(r["answer"], "Use LoRA to train a few weights [1]. Neo4j runs in Docker [2].")
        self.assertEqual(r["checked"], {"dropped": 1, "moved": 1})
        self.assertEqual([s["cited"] for s in r["sources"]], [True, True, False])
        saved = storage.get_ask(r["id"])
        self.assertEqual((saved["question"], saved["citations"], saved["sources"][1]["video_id"]),
                         ("How do I fine-tune cheaply?", [1, 2], "q_db"))
        self.assertEqual(asker.from_history(saved)["cited"], [1, 2])

    def test_nothing_found_skips_the_model(self):
        def complete(system, prompt):
            raise AssertionError("the model must not be called")
        r = asker.ask("pasta?", complete=complete, retrieve_fn=lambda q, k, filters: {"mode": "keyword", "pieces": []}, save=False)
        self.assertEqual((r["answer"], r["sources"], r["cited"]), (asker.NOTHING, [], []))

    def test_plain_text_reply_is_used(self):
        r = asker.ask("q", complete=lambda s, p: "Not JSON but fine [1].", retrieve_fn=found, sim_fn=None, save=False,
                      settings=llm.load_settings())
        self.assertEqual(r["cited"], [1])

    def test_ask_follows_the_ratings_provider(self):
        s = llm.default_settings()
        s["provider"] = "gemini"
        s["tasks"] = {"rate": "ollama"}
        self.assertEqual(llm.provider_for(s, "ask"), "ollama")
        s["tasks"]["ask"] = "anthropic"
        self.assertEqual(llm.provider_for(s, "ask"), "anthropic")
        self.assertEqual(llm.provider_for({**s, "tasks": {}}, "ask"), "gemini")


class HistoryTest(unittest.TestCase):
    def test_list_filter_get_delete(self):
        a = storage.save_ask({"question": "What is 100% LoRA?", "answer": "It trains adapters [1].", "citations": [1],
                              "sources": [{"n": 1, "video_id": "v", "title": "T", "kind": "transcript", "t": 75, "text": "x", "cited": True}],
                              "filters": {"categories": ["AI"]}, "mode": "hybrid", "model": "m", "seconds": 1.5})
        b = storage.save_ask({"question": "Neo4j_setup", "answer": "Docker."})
        self.assertEqual([x["id"] for x in storage.list_asks()][:2], [b, a])
        self.assertEqual([x["id"] for x in storage.list_asks("100%")], [a])          # % is literal, not a wildcard
        self.assertEqual([x["id"] for x in storage.list_asks("adapters")], [a])      # answers are searched too
        self.assertNotIn(a, [x["id"] for x in storage.list_asks("o_s")])            # _ is literal too
        item = storage.get_ask(a)
        self.assertEqual(item["filters"], {"categories": ["AI"]})
        md = ask_markdown(item)
        self.assertIn("# What is 100% LoRA?", md)
        self.assertIn("It trains adapters [\\[1\\]](https://www.youtube.com/watch?v=v&t=75s).", md)
        self.assertIn("1. [T @ 1:15](https://www.youtube.com/watch?v=v&t=75s): x", md)
        self.assertTrue(storage.delete_ask(a))
        self.assertIsNone(storage.get_ask(a))
        self.assertFalse(storage.delete_ask(a))


class AskEndpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def call(self, path, body=None):
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"X-YTKB": "1", "Content-Type": "application/json"} if body is not None else {},
                                     method="GET" if body is None else "POST")
        try:
            with urllib.request.urlopen(req) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if r.headers.get_content_type() == "application/json" else raw.decode())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read() or b"{}")

    def test_ask_flow(self):
        self.assertEqual(self.call("/api/ask", {"question": "  "})[0], 400)
        with mock.patch("llm.is_configured", return_value=False):
            code, r = self.call("/api/ask", {"question": "lora?"})
        self.assertEqual((code, "AI page" in r["error"]), (400, True))
        with mock.patch("llm.is_configured", return_value=True), \
                mock.patch("asker.ask", side_effect=lambda q, filters: {"question": q, "filters": filters}) as fake:
            code, r = self.call("/api/ask", {"question": "lora?", "filters": {"categories": ["AI", 3], "include_done": False}})
        self.assertEqual(code, 200)
        self.assertEqual(r["filters"], {"categories": ["AI"], "playlists": [], "video_ids": [], "include_done": False})
        fake.assert_called_once()

        ask_id = storage.save_ask({"question": "Saved q", "answer": "A [1].", "citations": [1],
                                   "sources": [{"n": 1, "video_id": "v", "title": "T", "kind": "transcript", "t": 5, "text": "x", "cited": True}]})
        code, h = self.call("/api/ask/history?q=Saved")
        self.assertEqual((code, h["items"][0]["id"]), (200, ask_id))
        code, item = self.call(f"/api/ask/item?id={ask_id}")
        self.assertEqual((code, item["cited"], item["sources"][0]["t"]), (200, [1], 5))
        code, md = self.call(f"/api/export/ask?id={ask_id}")
        self.assertEqual(code, 200)
        self.assertIn("# Saved q", md)
        self.assertEqual(self.call("/api/ask/item?id=nope")[0], 404)
        self.assertEqual(self.call("/api/ask/delete", {"id": "1"})[0], 400)
        self.assertEqual(self.call("/api/ask/delete", {"id": ask_id}), (200, {"ok": True}))
        self.assertEqual(self.call(f"/api/ask/item?id={ask_id}")[0], 404)


if __name__ == "__main__":
    unittest.main()
