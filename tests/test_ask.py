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

    def test_citation_checks_use_same_embedder_as_index(self):
        import numpy as np
        s_gem = llm.default_settings()
        s_gem["tasks"] = {"embed": "gemini"}
        s_gem["providers"]["gemini"]["api_key"] = "test-key"

        mock_emb = mock.MagicMock()
        mock_emb.model = "gemini:gemini-embedding-001"
        mock_emb.queries.return_value = [[0.0, 1.0]]

        pieces = [{"id": 1, "video_id": "v1", "text": "text1"}]
        mock_rows = [{"id": 1}]
        mock_mat = np.array([[0.0, 1.0]], dtype=np.float32)

        with mock.patch("embedder.embedder_for", return_value=mock_emb), \
             mock.patch("retrieval._matrix", return_value=(mock_rows, mock_mat)):
            sim_fn = asker._embedding_similarity(s_gem, pieces)
            self.assertIsNotNone(sim_fn)
            scores = sim_fn(["sentence one"])
            mock_emb.queries.assert_called_once_with(["sentence one"])
            self.assertAlmostEqual(scores[0][0], 1.0, places=4)


class FollowUpTest(unittest.TestCase):
    def test_follow_up_is_rewritten_and_keeps_filters(self):
        first = asker.ask("How does LoRA work?", filters={"video_ids": ["q_ft"]}, settings=llm.load_settings(),
                          complete=lambda s, p: json.dumps({"answer": "It trains adapters [1]."}), retrieve_fn=found, sim_fn=None)
        self.addCleanup(storage.delete_ask, first["id"])
        calls, seen = [], {}

        def complete(system, prompt):
            calls.append(system)
            if system == asker.REWRITE_SYSTEM:
                seen["rewrite"] = prompt
                return json.dumps({"question": "Where does the LoRA video show the code?"})
            return json.dumps({"answer": "At 4:14 [1]."})

        def retrieve(question, k, filters):
            seen["retrieve"] = (question, filters)
            return found(question, k, filters)
        r = asker.ask("and where is the code?", filters={"categories": ["ignored"]}, settings=llm.load_settings(),
                      complete=complete, retrieve_fn=retrieve, sim_fn=None, follow_up_of=first["id"])
        self.assertEqual(calls, [asker.REWRITE_SYSTEM, asker.SYSTEM])
        self.assertIn("Q: How does LoRA work?\nA: It trains adapters.", seen["rewrite"])       # marks stripped
        self.assertEqual(seen["retrieve"], ("Where does the LoRA video show the code?", {"video_ids": ["q_ft"]}))
        self.assertEqual((r["question"], r["standalone"], r["thread_id"]),
                         ("and where is the code?", "Where does the LoRA video show the code?", first["id"]))
        self.assertEqual([x["id"] for x in storage.get_thread(first["id"])], [first["id"], r["id"]])

    def test_bad_rewrite_falls_back_to_the_question(self):
        thread = [{"question": "q1", "answer": "a1"}]
        self.assertEqual(asker.standalone_question("and that?", thread, lambda s, p: "no json"), "and that?")
        self.assertEqual(asker.standalone_question("and that?", thread, lambda s, p: '{"question": ""}'), "and that?")

    def test_missing_conversation(self):
        with self.assertRaises(ValueError):
            asker.ask("x", follow_up_of=999999, complete=lambda s, p: "", retrieve_fn=found, save=False)


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
        self.assertEqual(storage.delete_ask(a), 1)
        self.assertIsNone(storage.get_ask(a))
        self.assertEqual(storage.delete_ask(a), 0)

    def test_conversations(self):
        root = storage.save_ask({"question": "Thread root zq", "answer": "A."})
        f1 = storage.save_ask({"question": "and then?", "answer": "B zq-follow.", "standalone": "And then, root?"}, thread_id=root)
        lone = storage.save_ask({"question": "Lone zq", "answer": "C."})
        rows = {x["id"]: x for x in storage.list_asks("zq")}
        self.assertEqual(set(rows), {root, lone})                          # follow-ups aren't listed on their own
        self.assertEqual((rows[root]["follow_ups"], rows[lone]["follow_ups"]), (1, 0))
        self.assertEqual([x["id"] for x in storage.list_asks("zq-follow")], [root])   # found through its follow-up
        self.assertEqual([x["id"] for x in storage.list_asks("zq")][0], lone)        # newest activity first
        thread = storage.get_thread(f1)
        self.assertEqual([x["id"] for x in thread], [root, f1])
        md = ask_markdown(thread)
        self.assertIn("# Thread root zq", md)
        self.assertIn("## and then?", md)
        self.assertIn("Searched for: And then, root?", md)
        self.assertEqual(storage.delete_ask(f1), 2)                         # deleting removes the conversation
        self.assertEqual(storage.get_thread(root), [])
        storage.delete_ask(lone)


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
        self.assertEqual((code, "Settings" in r["error"]), (400, True))
        with mock.patch("llm.is_configured", return_value=True), \
                mock.patch("asker.ask", side_effect=lambda q, filters, follow_up_of: {"question": q, "filters": filters,
                                                                                    "follow": follow_up_of}) as fake:
            code, r = self.call("/api/ask", {"question": "lora?", "filters": {"categories": ["AI", 3], "include_done": False}})
            self.assertEqual(self.call("/api/ask", {"question": "more?", "follow_up_of": 7})[1]["follow"], 7)
            self.assertEqual(self.call("/api/ask", {"question": "more?", "follow_up_of": "7"})[0], 400)
        self.assertEqual(code, 200)
        self.assertEqual(r["filters"], {"categories": ["AI"], "playlists": [], "video_ids": [], "include_done": False})
        self.assertEqual(fake.call_count, 2)

        ask_id = storage.save_ask({"question": "Saved q", "answer": "A [1].", "citations": [1],
                                   "sources": [{"n": 1, "video_id": "v", "title": "T", "kind": "transcript", "t": 5, "text": "x", "cited": True}]})
        code, h = self.call("/api/ask/history?q=Saved")
        self.assertEqual((code, h["items"][0]["id"]), (200, ask_id))
        code, item = self.call(f"/api/ask/item?id={ask_id}")
        self.assertEqual((code, item["thread"][0]["cited"], item["thread"][0]["sources"][0]["t"]), (200, [1], 5))
        code, md = self.call(f"/api/export/ask?id={ask_id}")
        self.assertEqual(code, 200)
        self.assertIn("# Saved q", md)
        self.assertEqual(self.call("/api/ask/item?id=nope")[0], 404)
        self.assertEqual(self.call("/api/ask/delete", {"id": "1"})[0], 400)
        self.assertEqual(self.call("/api/ask/delete", {"id": ask_id}), (200, {"ok": True, "deleted": 1}))
        self.assertEqual(self.call(f"/api/ask/item?id={ask_id}")[0], 404)

    def test_search_by_meaning_endpoint(self):
        with mock.patch("retrieval.search_by_meaning", return_value={"results": [], "short": False, "mode": "keyword"}) as m:
            code, r = self.call("/api/search?q=lora&kinds=transcript,bogus&meaning=1")
        self.assertEqual((code, r["mode"]), (200, "keyword"))
        m.assert_called_once_with("lora", ["transcript"])


if __name__ == "__main__":
    unittest.main()
