import contextlib
import io
import json
import unittest
from unittest import mock

import embedder
import storage
import summarizer
from llm import LLMError
from tests.helpers import playlist, video

GOOD = {
    "tldr": "LoRA trains small adapters.",
    "minutes": [{"t": "4:05", "topic": "Adapters", "notes": ["Only two small matrices train", "", 3]},
                {"t": "0:30", "topic": "Intro to fine-tuning", "notes": ["Why full tuning is costly"]},
                {"t": "99:00", "topic": "Past the end", "notes": ["x"]}, {"t": "soon", "topic": "No time"}],
    "claims": [{"t": "4:10", "kind": "number", "text": "A 70B model fits on one GPU"}, {"t": "1:00", "kind": "rumour", "text": "Most teams use QLoRA"},
               {"t": "2:00", "kind": "quote", "text": ""}],
    "quiz": [{"q": "What does LoRA freeze?", "a": "The base weights", "t": "4:05"}, {"q": "No answer"}],
    "takeaways": ["Start with QLoRA", 5, ""],
    "learn_more": {"related": [{"video_id": "c1", "relation": "deeper", "why": "More math"},
                               {"video_id": "zz", "relation": "deeper", "why": "not offered"},
                               {"video_id": "c1", "relation": "prerequisite", "why": "duplicate"},
                               {"video_id": "c2", "relation": "odd", "why": "x"}],
                   "searches": [{"query": "LoRA rank choice", "why": "Not covered"}, {"query": ""}]},
}


class ValidateTest(unittest.TestCase):
    def test_cleans_times_kinds_candidates_and_limits(self):
        s = summarizer.validate(GOOD, 600, ["c1", "c2"])
        self.assertEqual([(m["t"], m["topic"]) for m in s["minutes"]], [(30, "Intro to fine-tuning"), (245, "Adapters")])
        self.assertEqual(s["minutes"][1]["notes"], ["Only two small matrices train"])
        self.assertEqual([(c["t"], c["kind"]) for c in s["claims"]], [(60, "claim"), (250, "number")])   # unknown kind → claim
        self.assertEqual(s["quiz"], [{"q": "What does LoRA freeze?", "a": "The base weights", "t": 245}])
        self.assertEqual(s["takeaways"], ["Start with QLoRA"])
        self.assertEqual(s["learn_more"]["related"], [{"video_id": "c1", "relation": "deeper", "why": "More math"},
                                                      {"video_id": "c2", "relation": "same_topic", "why": "x"}])
        self.assertEqual(s["learn_more"]["searches"], [{"query": "LoRA rank choice", "why": "Not covered"}])
        many = {**GOOD, "minutes": [{"t": f"{i}:00", "topic": f"T{i}"} for i in range(1, 15)]}
        self.assertEqual(len(summarizer.validate(many, 3600, [])["minutes"]), summarizer.LIMITS["minutes"])

    def test_no_usable_minutes_is_an_error(self):
        with self.assertRaises(LLMError):
            summarizer.validate({"tldr": "x", "minutes": [{"t": "nope", "topic": "a"}]}, 600, [])
        with self.assertRaises(LLMError):
            summarizer.validate([], 600, [])


class StorageAndRunTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        storage.save_playlist(playlist("PLS"), [video("s1", "LoRA explained", duration=600), video("s2", "Graph databases", duration=600),
                                                video("s3", "No transcript", duration=600)])
        storage.save_details("s1", {"status": "done", "segments": [{"start": 30, "text": "fine tuning is costly"},
                                                                    {"start": 245, "text": "lora trains small adapter matrices"}]})
        storage.save_details("s2", {"status": "done", "segments": [{"start": 10, "text": "small adapter matrices also appear in graph models"}]})

    def test_save_get_and_search(self):
        storage.save_summary("s1", summarizer.validate(GOOD, 600, []), model="m")
        s = storage.get_summary("s1")
        self.assertEqual((s["tldr"], s["minutes"][1]["topic"], s["model"]), ("LoRA trains small adapters.", "Adapters", "m"))
        self.assertIn("s1", storage.summarized_ids())
        hit = storage.search("70B model fits")["results"][0]
        self.assertEqual((hit["video_id"], hit["hits"][0]["kind"], hit["hits"][0]["t"]), ("s1", "summary", 250))
        self.assertIsNone(storage.get_summary("s3"))

    def test_run_summarizes_skips_missing_transcripts_and_stops(self):
        reply = json.dumps({**GOOD, "learn_more": {"related": [{"video_id": "s2", "relation": "deeper", "why": "w"}]}})
        out = io.StringIO()
        with mock.patch("llm.make_completer", return_value=lambda s, p: reply), \
             mock.patch("summarizer.candidates", return_value=[{"video_id": "s2", "title": "Graph databases", "channel": "", "about": ""}]), \
             contextlib.redirect_stdout(out):
            self.assertTrue(summarizer.run_summarize(["s1", "s3"]))
            self.assertIn("no transcript yet", out.getvalue())
            self.assertEqual(storage.get_summary("s1")["learn_more"]["related"][0]["video_id"], "s2")
            calls = []
            with mock.patch("summarizer.summarize_video", side_effect=lambda *a: calls.append(1)):
                summarizer.run_summarize(["s1"], should_stop=lambda: True)
            self.assertEqual(calls, [])

    def test_related_moments_find_other_videos(self):
        groups = [("lora", "adapter", "adapters", "matrices", "small"), ("graph", "database"), ("costly", "fine", "tuning")]
        vec = lambda t: [sum(w in g for w in t.lower().replace(":", " ").split()) + 0.01 for g in groups]
        with contextlib.redirect_stdout(io.StringIO()):
            embedder.run_embed(embed_fn=lambda texts: [vec(t) for t in texts])
        summary = {"minutes": [{"t": 245, "topic": "Adapters", "notes": ["small adapter matrices"]}]}
        with mock.patch("embedder.model_name", return_value=embedder.DEFAULT_MODEL):
            m = summarizer.related_moments("s1", summary, embed_fn=lambda texts: [vec(t) for t in texts])
        found = [(x["video_id"], x["t"]) for x in m[0]]
        self.assertIn(("s2", 10), found)                                                  # the other video's moment
        self.assertNotIn("s1", [v for v, _ in found])                                     # never the video itself
        self.assertLessEqual(len(found), summarizer.MOMENTS_PER_TOPIC)
        self.assertEqual(summarizer.related_moments("s1", {"minutes": []}), {})


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

    def test_summarize_endpoints(self):
        for bad in (None, [], [1], ["a"] * 51):
            self.assertEqual(self.call("/api/summarize", {"ids": bad})[0], 400, bad)
        with mock.patch("llm.is_configured", return_value=True), mock.patch("dashboard.JOB.start", return_value={"id": 9, "position": 0}) as start:
            code, r = self.call("/api/summarize", {"ids": ["s1"]})
        self.assertEqual((code, r["position"], start.call_args[0][0]), (200, 0, "summarize"))
        self.assertEqual(self.call("/api/summary?id=nope")[0], 404)


if __name__ == "__main__":
    unittest.main()
