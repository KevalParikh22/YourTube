import contextlib
import io
import json
import sqlite3
import unittest
from unittest import mock

import storage
import summarizer
from llm import LLMError
from tests.helpers import playlist, video, temp_db
from tests import test_summarizer
from tests.test_summarizer import GOOD as _GOOD

GOOD = {**_GOOD, "claims": []}   # keeps test_summarizer's search for its claim unambiguous

HOUR = 3600
PART = {"title": "Setting up", "summary": "Installs the tools.", "points": [{"t": "1:00", "text": "Install Python"}, {"t": "9:59:00", "text": "Out of range"}],
        "actions": ["Install it yourself", "", 3]}

PART_WITH_QUIZ = {**PART, "quiz": [
    {"q": "What do you install?", "options": ["Python", "Java", "Ruby"], "answer": 0, "t": "1:00"},
    {"q": "Out of range time?", "options": ["Yes", "No"], "answer": 1, "t": "9:59:00"},  # t far outside -> None
    {"q": "Good question?", "options": ["A", "B", "C"], "answer": 2, "t": "0:30"},
]}


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
        self.assertEqual(p["quiz"], [])   # PART has no quiz key
        for bad in ([], {"title": "x"}):
            with self.assertRaises(LLMError):
                summarizer.validate_part(bad, 0, 1800)

    def test_validate_part_keeps_good_quiz_items(self):
        # start=0, end=1800; "1:00" = 60 s is in range; "9:59:00" = 35940 s is far outside → t=None
        p = summarizer.validate_part(PART_WITH_QUIZ, 0, 1800)
        self.assertEqual(len(p["quiz"]), 3)   # at most 3 kept
        q0 = p["quiz"][0]
        self.assertEqual(q0["q"], "What do you install?")
        self.assertEqual(q0["options"], ["Python", "Java", "Ruby"])
        self.assertEqual(q0["answer"], 0)
        self.assertEqual(q0["t"], 60)   # "1:00" is within [0-60, 1800+60]
        q1 = p["quiz"][1]
        self.assertIsNone(q1["t"])   # "9:59:00" is far outside the part → t stripped to None
        q2 = p["quiz"][2]
        self.assertEqual(q2["t"], 30)   # "0:30" within range

    def test_validate_part_drops_bad_quiz_items(self):
        bad_quiz = [
            # answer out of range
            {"q": "Q1", "options": ["A", "B"], "answer": 5, "t": "0:30"},
            # answer is True (bool, not int)
            {"q": "Q2", "options": ["A", "B"], "answer": True, "t": "0:30"},
            # only 1 option
            {"q": "Q3", "options": ["A"], "answer": 0, "t": "0:30"},
            # empty q
            {"q": "", "options": ["A", "B"], "answer": 0, "t": "0:30"},
            # good item
            {"q": "What is it?", "options": ["A", "B", "C"], "answer": 1, "t": "0:30"},
        ]
        p = summarizer.validate_part({**PART, "quiz": bad_quiz}, 0, 1800)
        self.assertEqual(len(p["quiz"]), 1)
        self.assertEqual(p["quiz"][0]["q"], "What is it?")

    def test_validate_part_missing_quiz_gives_empty_list(self):
        p = summarizer.validate_part(PART, 0, 1800)
        self.assertEqual(p["quiz"], [])

    def test_validate_part_bad_quiz_key_is_not_error(self):
        # entirely missing quiz key → [] (not an error)
        raw = {**PART}
        raw.pop("quiz", None)
        p = summarizer.validate_part(raw, 0, 1800)
        self.assertEqual(p["quiz"], [])

    def test_validate_part_skips_bad_quiz_containers(self):
        # "quiz" is not a list → empty quiz, not a TypeError
        p = summarizer.validate_part({**PART, "quiz": 5}, 0, 1800)
        self.assertEqual(p["quiz"], [])
        # an item whose "options" is not a list is dropped, not a TypeError
        raw = {**PART, "quiz": [{"q": "What is it?", "options": 3, "answer": 0, "t": "0:30"}]}
        p = summarizer.validate_part(raw, 0, 1800)
        self.assertEqual(p["quiz"], [])

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


class CourseStorageTest(unittest.TestCase):
    def test_init_db_adds_daily_minutes_to_an_existing_courses_table(self):
        path = temp_db()
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE courses (video_id TEXT PRIMARY KEY, parts TEXT NOT NULL DEFAULT '[]', "
                         "per_week INTEGER, progress TEXT NOT NULL DEFAULT '{}', model TEXT, "
                         "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        storage.init_db(path)
        with sqlite3.connect(path) as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(courses)")}
        self.assertIn("daily_minutes", cols)
        storage.save_parts("old-course", [{"start": 0, "end": 1800}], db_path=path)
        self.assertIsNone(storage.get_course("old-course", db_path=path)["daily_minutes"])

    def test_set_course_daily_round_trip_and_false_without_a_course(self):
        path = temp_db()
        storage.save_parts("daily-storage", [{"start": 0, "end": 1800}], db_path=path)
        self.assertTrue(storage.set_course_daily("daily-storage", 30, db_path=path))
        self.assertEqual(storage.get_course("daily-storage", db_path=path)["daily_minutes"], 30)
        self.assertTrue(storage.set_course_daily("daily-storage", None, db_path=path))
        self.assertIsNone(storage.get_course("daily-storage", db_path=path)["daily_minutes"])
        self.assertFalse(storage.set_course_daily("no-course", 30, db_path=path))


class CourseRunTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        storage.save_playlist(playlist("PLC"), [video("c4", "Four hour course", duration=4 * HOUR), video("c0", "No transcript", duration=HOUR)])
        storage.save_details("c4", {"status": "done", "segments": segments(4 * HOUR, step=60)})

    def test_course_saves_each_part_and_resumes_pending_parts(self):
        storage.save_playlist(playlist("PLC2"), [video("c-resume", "Resumable", duration=4 * HOUR)])
        storage.save_details("c-resume", {"status": "done", "segments": segments(4 * HOUR, step=60)})
        calls = []
        def partial(system, prompt):
            calls.append(prompt)
            if len(calls) == 3:
                from llm import QuotaError
                raise QuotaError("quota")
            return json.dumps(PART)
        with mock.patch("llm.make_completer", return_value=partial), mock.patch("llm.context_chars", return_value=5000), \
             mock.patch("llm.model_for", return_value="m"), contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(summarizer.run_course(["c-resume"], 2))
        course = storage.get_course("c-resume")
        self.assertEqual(course["per_week"], 2)
        self.assertEqual(course["model"], "m")
        self.assertEqual(sum(not p.get("pending") for p in course["parts"]), 2)
        self.assertEqual(sum(bool(p.get("pending")) for p in course["parts"]), len(course["parts"]) - 2)
        storage.set_course_progress("c-resume", "p0", True)
        remaining = sum(bool(p.get("pending")) for p in course["parts"])
        resumed = []
        def working(system, prompt):
            resumed.append(prompt)
            return json.dumps(PART)
        with mock.patch("llm.make_completer", return_value=working), mock.patch("llm.context_chars", return_value=5000), \
             mock.patch("llm.model_for", return_value="m"), contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(summarizer.run_course(["c-resume"], 2))
        self.assertEqual(len(resumed), remaining)
        self.assertTrue(storage.get_course("c-resume")["progress"]["p0"])
        self.assertFalse(any(p.get("pending") for p in storage.get_course("c-resume")["parts"]))
        storage.set_course_pace("c-resume", None)

    def test_run_course_sets_daily_minutes_only_when_given(self):
        storage.save_playlist(playlist("PLD"), [video("d-run", "Daily course", duration=HOUR)])
        storage.save_parts("d-run", [summarizer.validate_part(PART, 0, 1800), summarizer.validate_part(PART, 1800, 3600)])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(summarizer.run_course(["d-run"], 2, daily_minutes=45))
        self.assertEqual(storage.get_course("d-run")["daily_minutes"], 45)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(summarizer.run_course(["d-run"], 2))
        self.assertEqual(storage.get_course("d-run")["daily_minutes"], 45)   # not cleared when omitted
        storage.set_course_pace("d-run", None)

    def test_summary_ignores_pending_saved_parts(self):
        storage.save_playlist(playlist("PLC3"), [video("c-half-summary", "Half course", duration=HOUR)])
        storage.save_details("c-half-summary", {"status": "done", "segments": segments(HOUR)})
        storage.save_parts("c-half-summary", [{"start": 0, "end": HOUR, "pending": True}])
        prompts = []
        def complete(system, prompt):
            prompts.append(prompt)
            return json.dumps(PART if "Transcript of this part" in prompt else GOOD)
        v = next(v for v in summarizer._videos(["c-half-summary"]))
        with mock.patch("summarizer.candidates", return_value=[]):
            result = summarizer._summarize_one(v, complete, 5000, "fake", {})
        self.assertIsNotNone(result)
        self.assertNotIn("Part 1: None", prompts[-1])
        self.assertIn("Video", prompts[-1])

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
        self.assertEqual(storage.list_courses(), [{"video_id": "c4", "per_week": 2, "daily_minutes": None, "parts": n, "done": 1}])
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


class CourseEndpointTest(test_summarizer.EndpointTest):
    def test_summarize_endpoints(self):   # inherited test belongs to test_summarizer
        pass

    def test_course_endpoints(self):
        storage.save_playlist(playlist("PLE"), [video("e1", "Course API", duration=HOUR), video("e2", "Not split yet", duration=HOUR)])
        storage.save_parts("e1", [summarizer.validate_part(PART, 0, 1800), summarizer.validate_part(PART, 1800, 3600)])
        self.assertEqual(self.call("/api/course?id=nope"), (200, {"course": None}))
        for bad in ({"id": "e1", "per_week": 0}, {"id": "e1", "per_week": "2"}, {"per_week": 2}):
            self.assertEqual(self.call("/api/course", bad)[0], 400, bad)
        storage.save_parts("e3", [{"start": 0, "end": 1800, "pending": True}])
        storage.set_course_pace("e3", 1)
        with mock.patch("llm.is_configured", return_value=True), mock.patch("dashboard.JOB.start", return_value={"id": 10, "position": 0}) as start:
            code, r = self.call("/api/course", {"id": "e3", "per_week": 1})
        self.assertEqual((code, start.call_args[0][0]), (200, "course"))
        with mock.patch("llm.is_configured", return_value=False), mock.patch("dashboard.JOB.start") as start:
            code, r = self.call("/api/course", {"id": "e3", "per_week": 3})
        self.assertEqual(code, 400)
        self.assertEqual(r.get("error"), "Choose an AI for summaries in Settings → AI first")
        self.assertEqual(storage.get_course("e3")["per_week"], 1)
        self.assertEqual(start.call_count, 0)
        storage.set_course_pace("e3", None)
        with mock.patch("dashboard.JOB.start") as start:
            code, r = self.call("/api/course", {"id": "e1", "per_week": 1})    # parts exist: no task
        self.assertEqual((code, r["course"]["per_week"], start.call_count), (200, 1, 0))
        with mock.patch("llm.is_configured", return_value=True), mock.patch("dashboard.JOB.start", return_value={"id": 9, "position": 0}) as start:
            code, r = self.call("/api/course", {"id": "e2", "per_week": 2})
        self.assertEqual((code, start.call_args[0][0]), (200, "course"))
        self.assertEqual(self.call("/api/course/progress", {"id": "e1", "key": "x1", "done": True})[0], 400)
        self.assertEqual(self.call("/api/course/progress", {"id": "e2", "key": "p0", "done": True})[0], 404)
        self.assertEqual(self.call("/api/course/progress", {"id": "e1", "key": "p1", "done": True}), (200, {"ok": True, "progress": {"p1": True}}))
        # q<part>.<question> key is accepted
        self.assertEqual(self.call("/api/course/progress", {"id": "e1", "key": "q0.1", "done": True})[0], 200)
        self.assertEqual(self.call("/api/course?id=e1")[1]["course"]["progress"], {"p1": True, "q0.1": True})
        import urllib.request
        with urllib.request.urlopen(self.base + "/api/course/markdown?id=e1") as r:
            self.assertIn("## Week 2", r.read().decode())
        code, r = self.call("/api/course", {"id": "e1", "per_week": None})       # end the course, keep the parts
        self.assertEqual((code, r["course"]["per_week"], len(r["course"]["parts"])), (200, None, 2))

    def test_course_daily_pace_on_an_existing_course(self):
        storage.save_playlist(playlist("PLDAILY"), [video("e-daily", "Daily API", duration=HOUR)])
        storage.save_parts("e-daily", [summarizer.validate_part(PART, 0, 1800)])
        storage.set_course_pace("e-daily", 2)
        code, r = self.call("/api/course", {"id": "e-daily", "per_week": 2, "daily_minutes": 30})
        self.assertEqual(code, 200)
        self.assertEqual(r["course"]["daily_minutes"], 30)
        self.assertEqual(storage.get_course("e-daily")["daily_minutes"], 30)
        for good in (15, 240):      # boundaries of the allowed range
            code, r = self.call("/api/course", {"id": "e-daily", "per_week": 2, "daily_minutes": good})
            self.assertEqual((code, r["course"]["daily_minutes"]), (200, good))
        code, r = self.call("/api/course", {"id": "e-daily", "per_week": 2, "daily_minutes": None})
        self.assertEqual(code, 200)
        self.assertIsNone(r["course"]["daily_minutes"])
        self.assertIsNone(storage.get_course("e-daily")["daily_minutes"])
        for bad in (5, 300, True, "30"):
            self.assertEqual(self.call("/api/course", {"id": "e-daily", "per_week": 2, "daily_minutes": bad})[0], 400, bad)
        storage.set_course_pace("e-daily", None)

    def test_new_course_passes_daily_minutes_to_its_task(self):
        storage.save_playlist(playlist("PLNEWDAILY"), [video("e-new-daily", "New daily course", duration=HOUR)])
        with mock.patch("llm.is_configured", return_value=True), \
             mock.patch("dashboard.JOB.start", return_value={"id": 12, "position": 0}) as start, \
             mock.patch("summarizer.run_course") as run:
            code, r = self.call("/api/course", {"id": "e-new-daily", "per_week": 2, "daily_minutes": 30})
            self.assertEqual((code, start.call_args[0][0]), (200, "course"))
            start.call_args[0][2]()     # the queued task
            self.assertEqual(run.call_args[0], (["e-new-daily"], 2))
            self.assertEqual(run.call_args.kwargs["daily_minutes"], 30)

    def test_course_markdown_daily_groups_parts_into_days(self):
        from exporter import course_markdown
        v = {"video_id": "md-daily", "title": "Daily", "channel": "ch", "duration_seconds": 90 * 60}
        course = {"parts": [{"start": i * 1800, "end": (i + 1) * 1800, "title": f"Part {i + 1}"} for i in range(3)],
                  "per_week": 2, "daily_minutes": 30, "progress": {}}
        md = course_markdown(v, course)
        self.assertIn("30 min a day", md)
        self.assertNotIn("min a day at", md)   # no speed in the header at 1x
        self.assertNotIn("a week", md)
        self.assertEqual([l for l in md.splitlines() if l.startswith("## Day")],
                         ["## Day 1", "## Day 2", "## Day 3"])
        self.assertEqual(md.split("## Day 1")[1].split("## Day 2")[0].count("### Part"), 1)
        self.assertEqual(course_markdown(v, course, 1).splitlines()[2], md.splitlines()[2])
        fast = course_markdown(v, course, 2)
        self.assertIn("30 min a day at 2×", fast)
        self.assertEqual([l for l in fast.splitlines() if l.startswith("## Day")], ["## Day 1", "## Day 2"])
        self.assertEqual(fast.split("## Day 1")[1].split("## Day 2")[0].count("### Part"), 2)
        self.assertEqual(fast.split("## Day 2")[1].count("### Part"), 1)
        self.assertNotIn("## Day 3", fast)
        self.assertNotIn("a week", fast)
        self.assertNotIn("## Day", course_markdown(v, {**course, "parts": []}))

    def test_course_markdown_endpoint_accepts_only_speeds_from_half_to_four(self):
        import urllib.request
        storage.save_playlist(playlist("PLMDSPEED"), [video("e-md-speed", "MD Speed", duration=90 * 60)])
        storage.save_parts("e-md-speed", [{"start": i * 1800, "end": (i + 1) * 1800, "title": f"Part {i + 1}"} for i in range(3)])
        storage.set_course_pace("e-md-speed", 2)
        storage.set_course_daily("e-md-speed", 30)

        def export(query=""):
            with urllib.request.urlopen(f"{self.base}/api/course/markdown?id=e-md-speed{query}") as r:
                return r.read().decode()

        def days(text):
            return [l for l in text.splitlines() if l.startswith("## Day")]

        normal = export()
        self.assertIn("30 min a day", normal)
        self.assertNotIn("min a day at", normal)
        self.assertEqual(days(normal), ["## Day 1", "## Day 2", "## Day 3"])
        fast = export("&speed=2")
        self.assertIn("30 min a day at 2×", fast)
        self.assertEqual(days(fast), ["## Day 1", "## Day 2"])
        self.assertEqual(fast.split("## Day 1")[1].split("## Day 2")[0].count("### Part"), 2)
        for good in ("0.5", "4", "1.25"):
            self.assertIn(f"at {good}×", export(f"&speed={good}"))
        for bad in ("", "abc", "0", "0.49", "4.01", "nan", "inf", "-1"):
            text = export(f"&speed={bad}")
            self.assertNotIn("min a day at", text, bad)
            self.assertEqual(days(text), ["## Day 1", "## Day 2", "## Day 3"], bad)
        storage.set_course_pace("e-md-speed", None)

    def test_course_markdown_pending_part(self):
        from exporter import course_markdown
        v = {"video_id": "pending-md", "title": "Pending", "channel": "ch", "duration_seconds": HOUR}
        course = {"parts": [{"start": 0, "end": 1800, "pending": True}], "per_week": 1, "progress": {}}
        md = course_markdown(v, course)
        self.assertIn("### Part 1 (not written yet)", md)
        self.assertIn("[Watch 0:00–30:00]", md)
        self.assertNotIn("Untitled", md)

    def test_course_markdown_quiz(self):
        """course_markdown shows Check yourself and right option when quiz present."""
        from exporter import course_markdown
        part_with_quiz = summarizer.validate_part(PART_WITH_QUIZ, 0, 1800)
        fake_v = {"video_id": "qv_md_only", "title": "Quiz Video", "channel": "ch", "duration_seconds": HOUR}
        fake_course = {"parts": [part_with_quiz], "per_week": 1, "progress": {}}
        md = course_markdown(fake_v, fake_course)
        self.assertIn("Check yourself:", md)
        self.assertIn("What do you install?", md)
        self.assertIn("→ Python", md)   # right option for answer=0


if __name__ == "__main__":
    unittest.main()
