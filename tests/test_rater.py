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


class DescriptionSettingTest(unittest.TestCase):
    def test_description_left_out_when_off(self):
        v = {"title": "T", "channel": "C", "duration_seconds": 60, "description": "Sponsor links everywhere"}
        on, _ = rater.build_prompt(v, [], {"language": "English"})
        off, _ = rater.build_prompt(v, [], {"language": "English", "use_descriptions": False})
        self.assertIn("Sponsor links", on)
        self.assertNotIn("Sponsor links", off)


class ExplainableRatingTest(unittest.TestCase):
    FULL = {"depth": {"score": 8, "why": "Shows the full LoRA math"}, "fit": {"score": 6, "why": "Partly matches"},
            "value": {"score": 7, "why": "Dense"}, "freshness": {"score": 4, "why": "Uses an old library version"}}

    def test_score_is_quality_adjusted_by_interest(self):
        r = rater.validate({"breakdown": self.FULL, "score": 2, "verdict": "skip"}, 600)
        # quality 8×.45 + 7×.40 + 4×.15 = 7.0; fit 6 is one point under 7: -2 × 1/4 = -0.5; the model's 2 is ignored
        self.assertEqual((r["score"], r["calc"]["quality"], r["calc"]["interest"]), (6.5, 7.0, -0.5))
        self.assertEqual(r["verdict"], "skim")              # skip is two steps from 6.5, so the score decides
        self.assertEqual(r["breakdown"]["freshness"]["why"], "Uses an old library version")
        self.assertEqual(r["reason"], "Strongest: depth and quality (8). Weakest: freshness (4).")   # model gave none

    def test_partial_breakdown_falls_back_to_the_models_score(self):
        r = rater.validate({"breakdown": {"depth": {"score": 9}}, "score": 5, "verdict": "skim"}, 600)
        self.assertEqual((r["score"], list(r["breakdown"])), (5.0, ["depth"]))
        only = rater.validate({"breakdown": {"depth": 9, "fit": "x"}, "verdict": "watch"}, 600)
        self.assertEqual(only["score"], 9.0)                # no model score: the factors that exist

    def test_verdict_follows_the_score_away_from_boundaries(self):
        low = {k: {"score": 2} for k in rater.WEIGHTS}
        self.assertEqual(rater.validate({"breakdown": low, "verdict": "watch"}, 600)["verdict"], "skip")
        edge = {k: {"score": 6.5} for k in rater.WEIGHTS}
        self.assertEqual(rater.validate({"breakdown": edge, "verdict": "watch"}, 600)["verdict"], "watch")   # 6.5 is near 7
        self.assertEqual(rater.validate({"breakdown": edge, "verdict": "skip"}, 600)["verdict"], "skim")

    def test_minutes_and_takeaways(self):
        minutes = [{"t": "5:00", "topic": "Second", "notes": ["b"]}, {"t": "0:30", "topic": "First", "notes": ["a", "", 3]},
                   {"t": "99:00", "topic": "Past the end", "notes": ["x"]}, {"t": "nope", "topic": "Bad time"}]
        minutes += [{"t": f"{i}:10", "topic": f"T{i}", "notes": ["n"] * 5} for i in range(6, 9)]
        r = rater.validate({"score": 6, "verdict": "skim", "minutes": minutes, "takeaways": [f"k{i}" for i in range(8)]}, 600)
        self.assertEqual([m["topic"] for m in r["minutes"]], ["First", "Second", "T6", "T7", "T8"])
        self.assertEqual(r["minutes"][0], {"t": 30, "topic": "First", "notes": ["a"]})
        self.assertEqual(len(r["minutes"][2]["notes"]), 3)
        self.assertEqual(len(r["takeaways"]), 5)
        self.assertEqual(r["points"][0], {"t": 30, "text": "First"})   # older readers still get points


class RatingStorageTest(unittest.TestCase):
    def test_new_fields_round_trip_and_old_tables_migrate(self):
        import sqlite3
        import storage
        from tests.helpers import playlist, temp_db, video
        db = temp_db()
        storage.save_playlist(playlist(), [video("r1", "LoRA")], db_path=db)
        conn = sqlite3.connect(db)
        conn.executescript("ALTER TABLE video_ratings DROP COLUMN breakdown; ALTER TABLE video_ratings DROP COLUMN minutes; "
                           "ALTER TABLE video_ratings DROP COLUMN takeaways;")
        conn.close()
        storage.init_db(db)
        storage.save_rating("r1", {"score": 6.8, "verdict": "skim", "breakdown": {"depth": {"score": 8, "why": "w"}},
                                   "calc": {"score": 6.8, "quality": 7.3, "interest": -0.5},
                                   "minutes": [{"t": 30, "topic": "Adapters", "notes": ["Only two small matrices train"]}],
                                   "takeaways": ["Start with QLoRA"]}, db_path=db)
        r = next(v for v in storage.get_library(db_path=db) if v["video_id"] == "r1")["rating"]
        self.assertEqual((r["breakdown"]["depth"]["score"], r["minutes"][0]["topic"], r["takeaways"]), (8, "Adapters", ["Start with QLoRA"]))
        self.assertEqual((list(r["breakdown"]), r["calc"]["interest"]), (["depth"], -0.5))     # calc kept apart
        hits = storage.search("small matrices", db_path=db)["results"]
        self.assertEqual(hits[0]["hits"][0]["kind"], "summary")                 # minutes are searchable


class FormulaTest(unittest.TestCase):
    def b(self, depth, fit, value, freshness):
        return {k: {"score": v} for k, v in zip(("depth", "fit", "value", "freshness"), (depth, fit, value, freshness))}

    def test_great_video_outside_your_interests_still_scores_well(self):
        calc = rater.compute_score(self.b(9, 2, 9, 9))
        self.assertEqual((calc["quality"], calc["interest"], calc["score"]), (9.0, -2.0, 7.0))
        self.assertEqual(rater.compute_score(self.b(9, 5, 9, 9))["interest"], -1.0)   # half way: half the penalty
        self.assertEqual(rater.compute_score(self.b(6, 9, 6, 6))["score"], 6.5)       # strong match: +0.5
        self.assertIsNone(rater.compute_score({"depth": {"score": 5}}))

    def test_formula_can_be_configured(self):
        f = rater.formula_of({"rating_formula": {"interest_penalty": 4, "depth": 1, "value": 0, "freshness": 0, "bogus": 3}})
        self.assertEqual((f["interest_penalty"], f["interest_bonus"], f["depth"]), (4.0, 0.5, 1.0))
        self.assertEqual(rater.compute_score(self.b(8, 3, 1, 1), f)["score"], 4.0)    # only depth counts, full penalty 4
        self.assertEqual(rater.formula_of({"rating_formula": {"interest_penalty": 99, "depth": "x"}})["interest_penalty"], 5.0)
        zero = rater.formula_of({"rating_formula": {"depth": 0, "value": 0, "freshness": 0}})
        self.assertEqual(zero["depth"], rater.DEFAULT_FORMULA["depth"])               # all-zero weights fall back


class HelperRatingTest(unittest.TestCase):
    def setUp(self):
        import time
        self.time = time
        storage.save_playlist(playlist("PLH"), [video(f"hr{i}", f"Video {i}") for i in range(1, 7)])
        for i in range(1, 7):
            storage.save_details(f"hr{i}", {"status": "done", "segments": SEGS, "chapters": [], "upload_date": "20260101"})
        s = llm.load_settings()
        s.update(provider="ollama", tasks={"rate": None}, helpers={"rate": "anthropic"})
        s["providers"]["ollama"]["model"] = "qwen3.5:4b"
        s["providers"]["anthropic"] = {"model": "claude-haiku-5-5", "api_key": "fake-key", "base_url": ""}
        llm.save_settings(s)

    def tearDown(self):
        with storage.get_connection() as conn:
            conn.execute("DELETE FROM video_ratings")
            conn.execute("DELETE FROM videos WHERE video_id LIKE 'hr%'")
        s = llm.load_settings()
        s["provider"] = "none"
        s.get("helpers", {}).pop("rate", None)
        llm.save_settings(s)

    def test_rates_with_two_completers_one_slow_and_counts_line_per_worker(self):
        def fake_make_completer(settings, task=None):
            provider = llm.provider_for(settings, task)
            if provider == "anthropic":
                def slow_call(system, prompt):
                    self.time.sleep(0.04)
                    return json.dumps({"score": 8, "verdict": "watch", "reason": "cloud ok", "tldr": "t"})
                return slow_call
            else:
                def fast_call(system, prompt):
                    self.time.sleep(0.005)
                    return json.dumps({"score": 5, "verdict": "skim", "reason": "local ok", "tldr": "t"})
                return fast_call

        out = io.StringIO()
        with mock.patch("llm.make_completer", side_effect=fake_make_completer), contextlib.redirect_stdout(out):
            ok = rater.run_rate([f"hr{i}" for i in range(1, 7)])
        self.assertTrue(ok)

        # Every video is rated exactly once
        lib = {v["video_id"]: v for v in storage.get_library() if v["video_id"].startswith("hr")}
        self.assertEqual(len(lib), 6)
        models_used = set()
        for i in range(1, 7):
            self.assertIsNotNone(lib[f"hr{i}"]["rating"])
            models_used.add(lib[f"hr{i}"]["rating"]["model"])

        # Both workers do some
        self.assertIn("qwen3.5:4b", models_used)
        self.assertIn("claude-haiku-5-5", models_used)

        # Progress lines carry short worker tags
        output = out.getvalue()
        self.assertIn("· qwen3.5:4b", output)
        self.assertIn("· claude-haiku-5-5", output)

        # Counts line per worker
        self.assertIn("6 rated: ", output)
        self.assertIn("by Local: Ollama · qwen3.5:4b", output)
        self.assertIn("by Claude (Anthropic) · claude-haiku-5-5", output)

    def test_helper_stop_halts_both(self):
        stop_flag = False

        def fake_make_completer(settings, task=None):
            def call(system, prompt):
                nonlocal stop_flag
                stop_flag = True
                self.time.sleep(0.02)
                return json.dumps({"score": 7, "verdict": "watch", "reason": "r", "tldr": "t"})
            return call

        out = io.StringIO()
        with mock.patch("llm.make_completer", side_effect=fake_make_completer), contextlib.redirect_stdout(out):
            rater.run_rate([f"hr{i}" for i in range(1, 7)], should_stop=lambda: stop_flag)

        self.assertIn("Stopped. Ratings so far are saved.", out.getvalue())
        rated = [v for v in storage.get_library() if v["video_id"].startswith("hr") and v.get("rating")]
        self.assertLess(len(rated), 6)

    def test_three_errors_in_a_row_stops_one_worker_and_other_finishes(self):
        def fake_make_completer(settings, task=None):
            provider = llm.provider_for(settings, task)
            if provider == "ollama":
                def failing_call(system, prompt):
                    self.time.sleep(0.005)
                    raise LLMError("ollama quota")
                return failing_call
            else:
                def healthy_call(system, prompt):
                    self.time.sleep(0.01)
                    return json.dumps({"score": 8, "verdict": "watch", "reason": "cloud", "tldr": "t"})
                return healthy_call

        out = io.StringIO()
        with mock.patch("llm.make_completer", side_effect=fake_make_completer), contextlib.redirect_stdout(out):
            ok = rater.run_rate([f"hr{i}" for i in range(1, 6)])

        self.assertTrue(ok)
        output = out.getvalue()
        # 3 errors stopped ollama, claude finished the remaining 2
        self.assertIn("2 rated: 0 by Local: Ollama · qwen3.5:4b, 2 by Claude (Anthropic) · claude-haiku-5-5.", output)
        self.assertIn("3 failed", output)

    def test_estimate_with_helper(self):
        est = rater.estimate(["hr1", "hr2"])
        self.assertEqual(est["videos"], 2)
        self.assertIn("Ollama", est["uses"])
        self.assertIn("Claude", est["uses"])
        self.assertEqual(est["cost_note"], "less if the local model keeps up")
