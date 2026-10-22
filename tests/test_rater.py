import contextlib
import datetime as dt
import io
import json
import unittest
from unittest import mock

import llm
import rater
import storage
from llm import LLMError
from tests.helpers import playlist, temp_db, video

VIDEO = {"video_id": "r1", "title": "Graph RAG Explained", "channel": "Coder", "duration_seconds": 1200,
         "category": "AI", "description": "Build a project", "upload_date": "20240105",
         "chapters": [{"start": 0, "title": "Intro"}, {"start": 300, "title": "Build"}]}
SEGS = [{"start": float(i * 10), "text": f"line {i}"} for i in range(12)]


class PromptTest(unittest.TestCase):
    def test_blocks_and_sampling(self):
        blocks = rater.transcript_blocks(SEGS, block=30)
        self.assertEqual([b["start"] for b in blocks], [0, 30, 60, 90])
        self.assertEqual(blocks[0]["text"], "line 0 line 1 line 2")
        text, sampled = rater.fit_transcript(blocks, budget=10_000)
        self.assertFalse(sampled)
        self.assertTrue(text.startswith("[0:00] line 0"))
        text, sampled = rater.fit_transcript(blocks, budget=60)
        self.assertTrue(sampled)
        self.assertLess(text.count("\n") + 1, 4)

    def test_prompt_contents(self):
        settings = {"language": "Hindi", "interests": "RAG and agents", "presets": ["AI"], "_categories": ["AI", "Music"]}
        prompt, basis = rater.build_prompt(VIDEO, SEGS, settings, today=dt.date(2026, 10, 20))
        self.assertEqual(basis, "transcript")
        for needle in ("Today is 2026-10-20", "RAG and agents", "AI (key interest), Music", "Uploaded: 2024-01-05",
                       "Length: 20 min", "[5:00] Build", "[0:30] line 3", "in Hindi"):
            self.assertIn(needle, prompt)
        prompt, basis = rater.build_prompt({**VIDEO, "upload_date": None, "chapters": []}, [], {})
        self.assertEqual(basis, "metadata")
        self.assertIn("Uploaded: unknown", prompt)
        self.assertIn("No transcript is available", prompt)


class ValidateTest(unittest.TestCase):
    def test_clock_strings(self):
        self.assertEqual([rater.parse_time(x) for x in ("4:05", "1:02:30", "[12:00]", "95", 95, "0:61", "soon", None)],
                         [245, 3750, 720, 95, 95, None, None, None])
        r = rater.validate({"score": 6, "verdict": "skim", "points": [{"t": "1:02:30", "text": "late point"}],
                            "skim": [{"start": "20:00", "end": "38:15", "why": "core"}]}, duration=7560)
        self.assertEqual((r["points"][0]["t"], r["skim"][0]["start"], r["skim"][0]["end"]), (3750, 1200, 2295))

    def test_clamps_and_filters(self):
        r = rater.validate({"score": 14, "verdict": "WATCH", "reason": " good  stuff ",
                            "points": [{"t": 95, "text": "a"}, {"t": 99999, "text": "b"}, {"text": " "}],
                            "skim": [{"start": 300, "end": 600, "why": "build"}, {"start": 50, "end": 40},
                                     {"start": 10, "end": 5000}]}, duration=1200)
        self.assertEqual((r["score"], r["verdict"], r["reason"]), (10.0, "watch", "good stuff"))
        self.assertEqual(r["points"], [{"t": 95, "text": "a"}, {"t": None, "text": "b"}])
        self.assertEqual(r["skim"], [{"start": 300, "end": 600, "why": "build"}])

    def test_verdict_from_score_and_back(self):
        self.assertEqual(rater.validate({"score": 3}, 0)["verdict"], "skip")
        self.assertEqual(rater.validate({"score": "7"}, 0)["verdict"], "watch")
        self.assertEqual(rater.validate({"verdict": "skim"}, 0)["score"], 5.5)
        with self.assertRaises(LLMError):
            rater.validate({"verdict": "maybe"}, 0)
        with self.assertRaises(LLMError):
            rater.validate([], 0)


class RunRateTest(unittest.TestCase):
    def setUp(self):
        storage.save_playlist(playlist("PLR"), [video("ra", "Deep dive"), video("rb", "Hype news"), video("rc", "Done one")])
        storage.update_videos(["rc"], status="completed")
        storage.save_details("ra", {"status": "done", "segments": SEGS, "chapters": [], "upload_date": "20260101"})
        s = llm.load_settings()
        s.update(provider="ollama", tasks={"rate": None})
        s["providers"]["ollama"]["model"] = "m"
        llm.save_settings(s)

    def tearDown(self):
        with storage.get_connection() as conn:
            conn.execute("DELETE FROM video_ratings")
        s = llm.load_settings()
        s["provider"] = "none"
        llm.save_settings(s)

    def test_queue_is_unwatched_and_unrated(self):
        db = temp_db()
        storage.save_playlist(playlist(), [video("x"), video("y"), video("z")], db_path=db)
        storage.update_videos(["z"], status="skipped", db_path=db)
        self.assertEqual([v["video_id"] for v in storage.videos_for_rating(db_path=db)], ["x", "y"])
        storage.save_rating("x", {"score": 5, "verdict": "skim"}, db_path=db)
        self.assertEqual([v["video_id"] for v in storage.videos_for_rating(db_path=db)], ["y"])
        self.assertEqual([v["video_id"] for v in storage.videos_for_rating(["x", "z"], db_path=db)], ["x", "z"])

    def test_rates_with_transcript_or_metadata(self):
        prompts = []
        def fake(system, prompt):
            prompts.append(prompt)
            return json.dumps({"score": 8 if "Deep dive" in prompt else 2, "reason": "r", "tldr": "t"})
        out = io.StringIO()
        with mock.patch("llm.make_completer", return_value=fake), contextlib.redirect_stdout(out):
            self.assertTrue(rater.run_rate(["ra", "rb"]))
        lib = {v["video_id"]: v for v in storage.get_library()}
        self.assertEqual((lib["ra"]["rating"]["verdict"], lib["ra"]["rating"]["basis"]), ("watch", "transcript"))
        self.assertEqual((lib["rb"]["rating"]["verdict"], lib["rb"]["rating"]["basis"]), ("skip", "metadata"))
        self.assertIn("1 have no transcript", out.getvalue())
        self.assertNotIn("ra", [v["video_id"] for v in storage.videos_for_rating()])  # rated: not queued again

    def test_stops_when_everything_fails(self):
        out = io.StringIO()
        with mock.patch("llm.make_completer", return_value=lambda s, p: "nonsense"), contextlib.redirect_stdout(out):
            self.assertTrue(rater.run_rate(["ra", "rb"]))  # 2 videos: reports failures but finishes
        self.assertIn("2 failed", out.getvalue())
        with mock.patch("llm.make_completer", return_value=lambda s, p: "nonsense"), contextlib.redirect_stdout(out):
            self.assertFalse(rater.run_rate(["ra", "rb", "rc"]))  # first three all failed: gives up

    def test_estimate(self):
        est = rater.estimate(["ra", "rb"])
        self.assertEqual((est["videos"], est["with_transcript"], est["cost"]), (2, 1, 0.0))  # local = free
        self.assertGreater(est["tokens_in"], 100)


class CostTest(unittest.TestCase):
    def test_prices(self):
        s = llm.default_settings()
        s["provider"] = "anthropic"
        s["providers"]["anthropic"]["model"] = "claude-haiku-5-5"
        self.assertEqual(llm.estimate_cost(s, "rate", 1_000_000, 1_000_000), 0.6)
        s["providers"]["anthropic"]["model"] = "some-new-model"
        self.assertIsNone(llm.estimate_cost(s, "rate", 1000, 1000))


class SkimAndAgeTest(unittest.TestCase):
    def test_filler_and_whole_video_ranges_are_dropped(self):
        r = rater.validate({"score": 6, "verdict": "skim", "worth_watching": [
            {"start": "0:00", "end": "1:45", "why": "Introductory fluff about discipline"},
            {"start": "2:00", "end": "5:00", "why": "The dopamine reset routine, step by step"},
            {"start": "0:00", "end": "11:50", "why": "Covers the main ideas"}]}, 726)
        self.assertEqual([(x["start"], x["end"]) for x in r["skim"]], [(120, 300)])
        old = rater.validate({"score": 6, "verdict": "skim", "skim": [{"start": 10, "end": 60, "why": "Setup steps"}]}, 726)
        self.assertEqual(len(old["skim"]), 1)                     # older replies used "skim"
        skip = rater.validate({"score": 2, "verdict": "skip", "worth_watching": [{"start": 10, "end": 60, "why": "Tips"}]}, 726)
        self.assertEqual(skip["skim"], [])
        weak = rater.validate({"score": 3, "verdict": "skim", "worth_watching": []}, 726)
        self.assertEqual(weak["verdict"], "skip")                   # nothing worth watching was marked

    def test_age_is_spelled_out(self):
        today = rater.dt.date(2026, 10, 23)
        self.assertEqual(rater._date("20260523", today), "2026-05-23 (5 months ago)")
        self.assertEqual(rater._date("20261010", today), "2026-10-10 (this month)")
        self.assertEqual(rater._date("20230101", today), "2023-01-01 (about 3.8 years ago)")
        self.assertEqual(rater._date(None, today), "unknown")
