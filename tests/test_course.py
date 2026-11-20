import contextlib
import io
import json
import unittest
from unittest import mock

import storage
import summarizer
from llm import LLMError
from tests.helpers import playlist, video
from tests import test_summarizer
from tests.test_summarizer import GOOD as _GOOD

GOOD = {**_GOOD, "claims": []}   # keeps test_summarizer's search for its claim unambiguous

HOUR = 3600
PART = {"title": "Setting up", "summary": "Installs the tools.", "points": [{"t": "1:00", "text": "Install Python"}, {"t": "9:59:00", "text": "Out of range"}],
        "actions": ["Install it yourself", "", 3]}


def segments(seconds, step=10, pause_at=()):
    """One caption line every `step` s, with a 20 s pause before each time in pause_at."""
    out, t = [], 0
    while t < seconds:
        out.append({"start": t, "text": f"words at {t}"})
        t += step + (20 if any(t < p <= t + step for p in pause_at) else 0)
    return out


class SplitTest(unittest.TestCase):
    def test_short_video_is_one_part(self):
        self.assertEqual(summarizer.split_parts(40 * 60, segments(40 * 60), []), [[0, 2400]])

    def test_four_hours_cut_at_pauses_into_20_to_40_min_parts(self):
        pauses = [33 * 60, 61 * 60, 95 * 60]
        parts = summarizer.split_parts(4 * HOUR, segments(4 * HOUR, pause_at=pauses), [])
        self.assertEqual(parts[0][0], 0)
        self.assertEqual(parts[-1][1], 4 * HOUR)
        self.assertTrue(all(a[1] == b[0] for a, b in zip(parts, parts[1:])))                 # no gaps or overlaps
        self.assertTrue(all(20 * 60 <= e - s <= 40 * 60 for s, e in parts), parts)
        self.assertTrue(6 <= len(parts) <= 12)                                                # 3–4 weeks at 2–3 a week
        self.assertTrue(abs(parts[0][1] - 33 * 60) <= 30)                                     # cut at the long pause

    def test_chapters_win_over_pauses(self):
        chapters = [{"start": 0, "title": "a"}, {"start": 25 * 60, "title": "b"}, {"start": 29 * 60, "title": "c"}, {"start": 55 * 60, "title": "d"}]
        parts = summarizer.split_parts(80 * 60, segments(80 * 60, pause_at=[21 * 60]), chapters)
        self.assertEqual(parts, [[0, 29 * 60], [29 * 60, 55 * 60], [55 * 60, 80 * 60]])     # nearest chapter to each 30 min mark


class PartsTest(unittest.TestCase):
    def test_validate_part(self):
        p = summarizer.validate_part(PART, 0, 1800)
        self.assertEqual((p["start"], p["end"], p["title"]), (0, 1800, "Setting up"))
        self.assertEqual(p["points"], [{"t": 60, "text": "Install Python"}])
        self.assertEqual(p["actions"], ["Install it yourself"])
        for bad in ([], {"title": "x"}):
            with self.assertRaises(LLMError):
                summarizer.validate_part(bad, 0, 1800)

    def test_long_transcript_is_summarised_in_parts(self):
        v = {"video_id": "x", "title": "Long", "channel": "c", "duration_seconds": 2 * HOUR}
        prompts = []

        def complete(system, prompt):
            prompts.append(prompt)
            return json.dumps(PART if "Transcript of this part" in prompt else GOOD)
        with contextlib.redirect_stdout(io.StringIO()):
            s = summarizer.summarize_video(v, segments(2 * HOUR), {}, complete, [], budget=5000)
        self.assertEqual(len(s["parts"]), len(prompts) - 1)
        self.assertGreater(len(s["parts"]), 1)
        self.assertIn("Notes on each part", prompts[-1])
        self.assertIn("Part 1: Setting up", prompts[-1])
        # Fits in one call: no parts
        prompts.clear()
        s = summarizer.summarize_video(v, segments(2 * HOUR), {}, complete, [], budget=10 ** 7)
        self.assertNotIn("parts", s)
        self.assertEqual(len(prompts), 1)


class CourseRunTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        storage.save_playlist(playlist("PLC"), [video("c4", "Four hour course", duration=4 * HOUR), video("c0", "No transcript", duration=HOUR)])
        storage.save_details("c4", {"status": "done", "segments": segments(4 * HOUR, step=60)})

    def test_summary_saves_parts_then_course_reuses_them_and_progress(self):
        calls = []

        def complete(system, prompt):
            calls.append(1)
            return json.dumps(PART if "Transcript of this part" in prompt else GOOD)
        with mock.patch("llm.make_completer", return_value=complete), mock.patch("llm.context_chars", return_value=5000), \
             mock.patch("summarizer.candidates", return_value=[]), contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(summarizer.run_summarize(["c4"]))
            n = len(storage.get_course("c4")["parts"])
            self.assertEqual(len(calls), n + 1)
            self.assertIsNone(storage.get_course("c4")["per_week"])
            self.assertEqual(storage.list_courses(), [])
            self.assertTrue(summarizer.run_course(["c4"], 2))                   # parts reused: no AI call
            self.assertEqual(len(calls), n + 1)
            self.assertFalse(summarizer.run_course(["c0"], 2))                  # no transcript
        self.assertTrue(3 <= -(-n // 2) <= 4, n)                                 # 4 h → 3–4 weeks at 2 a week
        self.assertEqual(storage.set_course_progress("c4", "p0", True), {"p0": True})
        self.assertEqual(storage.set_course_progress("c4", "a0.1", True), {"p0": True, "a0.1": True})
        self.assertEqual(storage.set_course_progress("c4", "a0.1", False), {"p0": True})
        self.assertIsNone(storage.set_course_progress("c0", "p0", True))
        self.assertEqual(storage.list_courses(), [{"video_id": "c4", "per_week": 2, "parts": n, "done": 1}])
        from exporter import course_markdown
        md = course_markdown(next(v for v in storage.get_library() if v["video_id"] == "c4"), storage.get_course("c4"))
        self.assertIn("## Week 2", md)
        self.assertIn("### ✓ Part 1: Setting up", md)
        self.assertIn("- [ ] Install it yourself", md)
        self.assertIn("&t=0s", md)


class EstimateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        storage.save_playlist(playlist("EST"), [video("estimate-long", "Long estimate", duration=4 * HOUR),
                                                  video("estimate-short", "Short estimate", duration=30 * 60)])
        storage.save_details("estimate-long", {"status": "done", "segments": segments(4 * HOUR)})
        storage.save_details("estimate-short", {"status": "done", "segments": segments(30 * 60)})

    def test_estimate_counts_parts_and_reuses_saved_parts(self):
        with mock.patch("llm.context_chars", return_value=5000):
            before = summarizer.estimate(["estimate-long", "estimate-short"])
        n_parts = len(summarizer.split_parts(4 * HOUR, segments(4 * HOUR), []))
        self.assertEqual(before["tokens_out"], summarizer.OUTPUT_TOKENS * 2 + n_parts * summarizer.PART_OUTPUT_TOKENS)
        with mock.patch("llm.context_chars", return_value=5000):
            long_before = summarizer.estimate(["estimate-long"])
            short = summarizer.estimate(["estimate-short"])
        self.assertGreater(long_before["tokens_in"], short["tokens_in"])
        storage.save_parts("estimate-long", [summarizer.validate_part(PART, start, end)
                                               for start, end in summarizer.split_parts(4 * HOUR, segments(4 * HOUR), [])])
        with mock.patch("llm.context_chars", return_value=5000):
            after = summarizer.estimate(["estimate-long"])
        self.assertEqual(after["tokens_out"], summarizer.OUTPUT_TOKENS)

    def test_estimate_counts_saved_parts_for_short_video(self):
        storage.save_playlist(playlist("ESV"), [video("estimate-saved-short", "Saved short", duration=30 * 60)])
        storage.save_details("estimate-saved-short", {"status": "done", "segments": segments(30 * 60)})
        with mock.patch("llm.context_chars", return_value=5000):
            without_parts = summarizer.estimate(["estimate-saved-short"])
        storage.save_parts("estimate-saved-short", [summarizer.validate_part(PART, 0, 30 * 60)])
        with mock.patch("llm.context_chars", return_value=5000):
            saved = summarizer.estimate(["estimate-saved-short"])
        self.assertEqual(saved["tokens_out"], summarizer.OUTPUT_TOKENS)
        self.assertLess(saved["tokens_in"], without_parts["tokens_in"])


class CourseEndpointTest(test_summarizer.EndpointTest):
    def test_summarize_endpoints(self):   # inherited test belongs to test_summarizer
        pass

    def test_course_endpoints(self):
        storage.save_playlist(playlist("PLE"), [video("e1", "Course API", duration=HOUR), video("e2", "Not split yet", duration=HOUR)])
        storage.save_parts("e1", [summarizer.validate_part(PART, 0, 1800), summarizer.validate_part(PART, 1800, 3600)])
        self.assertEqual(self.call("/api/course?id=nope"), (200, {"course": None}))
        for bad in ({"id": "e1", "per_week": 0}, {"id": "e1", "per_week": "2"}, {"per_week": 2}):
            self.assertEqual(self.call("/api/course", bad)[0], 400, bad)
        with mock.patch("dashboard.JOB.start") as start:
            code, r = self.call("/api/course", {"id": "e1", "per_week": 1})    # parts exist: no task
        self.assertEqual((code, r["course"]["per_week"], start.call_count), (200, 1, 0))
        with mock.patch("llm.is_configured", return_value=True), mock.patch("dashboard.JOB.start", return_value={"id": 9, "position": 0}) as start:
            code, r = self.call("/api/course", {"id": "e2", "per_week": 2})
        self.assertEqual((code, start.call_args[0][0]), (200, "course"))
        self.assertEqual(self.call("/api/course/progress", {"id": "e1", "key": "x1", "done": True})[0], 400)
        self.assertEqual(self.call("/api/course/progress", {"id": "e2", "key": "p0", "done": True})[0], 404)
        self.assertEqual(self.call("/api/course/progress", {"id": "e1", "key": "p1", "done": True}), (200, {"ok": True, "progress": {"p1": True}}))
        self.assertEqual(self.call("/api/course?id=e1")[1]["course"]["progress"], {"p1": True})
        import urllib.request
        with urllib.request.urlopen(self.base + "/api/course/markdown?id=e1") as r:
            self.assertIn("## Week 2", r.read().decode())
        code, r = self.call("/api/course", {"id": "e1", "per_week": None})       # end the course, keep the parts
        self.assertEqual((code, r["course"]["per_week"], len(r["course"]["parts"])), (200, None, 2))


if __name__ == "__main__":
    unittest.main()
