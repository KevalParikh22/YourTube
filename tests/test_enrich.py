import contextlib
import io
import unittest

import enrich
import llm
import storage
from tests.helpers import playlist, temp_db, video


class TrackChoiceTest(unittest.TestCase):
    J = [{"ext": "json3", "url": "u"}, {"ext": "vtt", "url": "v"}]

    def test_manual_in_video_language_first(self):
        info = {"language": "en-US", "subtitles": {"en": self.J, "de": self.J}, "automatic_captions": {"en-orig": self.J}}
        self.assertEqual(enrich.choose_track(info)[:2], ("manual", "en"))

    def test_auto_original_language_over_translation(self):
        info = {"language": "hi", "subtitles": {}, "automatic_captions": {"hi-orig": self.J, "en": self.J}}
        self.assertEqual(enrich.choose_track(info)[:2], ("auto", "hi"))

    def test_unknown_language_uses_any_original(self):
        info = {"language": None, "subtitles": {}, "automatic_captions": {"fr-orig": self.J, "en": self.J}}
        self.assertEqual(enrich.choose_track(info)[:2], ("auto", "fr"))

    def test_english_fallback_and_none(self):
        info = {"language": "ja", "subtitles": {"en": self.J}, "automatic_captions": {}}
        self.assertEqual(enrich.choose_track(info)[:2], ("manual", "en"))
        self.assertIsNone(enrich.choose_track({"language": "ja", "subtitles": {"live_chat": self.J}}))
        self.assertIsNone(enrich.choose_track({"language": "en", "subtitles": {"en": [{"ext": "vtt"}]}}))


class ParseTest(unittest.TestCase):
    def test_json3(self):
        data = {"events": [
            {"tStartMs": 0, "dDurationMs": 1500, "segs": [{"utf8": "Hello "}, {"utf8": "world"}]},
            {"tStartMs": 1500, "segs": [{"utf8": "\n"}]},                       # empty: dropped
            {"tStartMs": 1600, "dDurationMs": 900, "segs": [{"utf8": "Hello world"}]},  # repeat: dropped
            {"tStartMs": 2500, "dDurationMs": 1000, "segs": [{"utf8": "next\nline"}]},
            {"tStartMs": 4000},                                                   # no segs
        ]}
        self.assertEqual(enrich.parse_json3(data), [
            {"start": 0.0, "dur": 1.5, "text": "Hello world"},
            {"start": 2.5, "dur": 1.0, "text": "next line"}])

    def test_error_classes(self):
        self.assertEqual(enrich._classify_error("ERROR: [youtube] x: Private video. Sign in"), "unavailable")
        self.assertEqual(enrich._classify_error("Sign in to confirm you’re not a bot"), "bot")
        self.assertEqual(enrich._classify_error("HTTP Error 429: Too Many Requests"), "bot")
        self.assertEqual(enrich._classify_error("Connection reset"), "failed")


class TranscriptsWantedTest(unittest.TestCase):
    def test_music_names_and_overrides(self):
        self.assertFalse(enrich.transcripts_wanted("2025 Music", {}))
        self.assertFalse(enrich.transcripts_wanted("Bhajan", {}))
        self.assertFalse(enrich.transcripts_wanted("Indie Mix", {}))
        self.assertTrue(enrich.transcripts_wanted("AI & Machine Learning", {}))
        self.assertTrue(enrich.transcripts_wanted("Remix culture", {}))           # "mix" must be a word
        self.assertTrue(enrich.transcripts_wanted("Music Theory", {"transcripts": {"Music Theory": True}}))
        self.assertFalse(enrich.transcripts_wanted("Talks", {"transcripts": {"Talks": False}}))


def done(n=2):
    return {"status": "done", "description": "d", "tags": ["t"], "chapters": [{"start": 0, "title": "Intro"}],
            "language": "en", "yt_categories": ["Education"], "caption_lang": "en", "caption_kind": "auto",
            "segments": [{"start": i, "dur": 1, "text": f"line {i}"} for i in range(n)]}


class DetailsStorageTest(unittest.TestCase):
    def setUp(self):
        self.db = temp_db()
        storage.save_playlist(playlist(), [video("a"), video("b"), video("c")], db_path=self.db)
        storage.update_videos(["c"], status="completed", db_path=self.db)

    def need(self, **kw):
        return [v["video_id"] for v in storage.videos_needing_details(db_path=self.db, **kw)]

    def test_queue_order_and_states(self):
        self.assertEqual(self.need(), ["a", "b", "c"])   # watched last
        storage.save_details("a", done(), db_path=self.db)
        storage.save_details("b", {"status": "failed", "error": "x"}, db_path=self.db)
        self.assertEqual(self.need(), ["b", "c"])
        for _ in range(2):
            storage.save_details("b", {"status": "failed", "error": "x"}, db_path=self.db)
        self.assertEqual(self.need(), ["c"])              # 3 tries: given up
        self.assertEqual(self.need(video_ids=["a"]), ["a"])  # explicit request always allowed

    def test_roundtrip_and_failed_retry_keeps_good_data(self):
        storage.save_details("a", done(3), db_path=self.db)
        storage.save_details("a", {"status": "failed", "error": "net"}, db_path=self.db)
        d = storage.get_details("a", db_path=self.db)
        self.assertEqual((d["status"], d["error"], len(d["segments"]), d["chapters"][0]["title"]), ("done", "net", 3, "Intro"))
        storage.save_details("a", done(1), db_path=self.db)   # refresh replaces the transcript
        self.assertEqual(len(storage.get_details("a", db_path=self.db)["segments"]), 1)
        lib = {v["video_id"]: v for v in storage.get_library(db_path=self.db)}
        self.assertEqual(lib["a"]["details"]["status"], "done")
        self.assertEqual(lib["a"]["details"]["chapters"], 1)
        self.assertIsNone(lib["b"]["details"])
        self.assertIsNone(storage.get_details("b", db_path=self.db))


class RunEnrichTest(unittest.TestCase):
    """run_enrich against the package's temp DB with a fake fetcher (no network)."""

    def setUp(self):
        storage.save_playlist(playlist("PLE"), [video("e1", category="Talks"), video("e2", category="Talks"),
                                                video("e3", category="Talks"), video("m1", category="My Music")])

    def tearDown(self):
        with storage.get_connection() as conn:
            conn.execute("DELETE FROM video_details")
            conn.execute("DELETE FROM transcript_segments")

    def run_quiet(self, **kw):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ok = enrich.run_enrich(sleep=lambda s: None, **kw)
        return ok, out.getvalue()

    def test_skips_music_categories_without_requests(self):
        calls = []
        ok, log = self.run_quiet(video_ids=["e1", "m1"], fetch=lambda ydl, vid: calls.append(vid) or done())
        self.assertTrue(ok)
        self.assertEqual(calls, ["e1"])
        self.assertIn("1 in no-transcript categories left out", log)

    def test_bot_check_stops_run_and_saves_progress(self):
        def fetch(ydl, vid):
            if vid == "e2":
                raise enrich.BotCheck("bot")
            return done()
        ok, log = self.run_quiet(video_ids=["e1", "e2", "e3"], fetch=fetch)
        self.assertFalse(ok)
        self.assertIn("not a bot", log)
        self.assertEqual(storage.get_details("e1")["status"], "done")
        self.assertIsNone(storage.get_details("e2"))
        self.assertIsNone(storage.get_details("e3"))

    def test_stop_request_and_limit(self):
        calls = []
        ok, _ = self.run_quiet(video_ids=["e1", "e2", "e3"], limit=2, fetch=lambda y, v: calls.append(v) or done())
        self.assertEqual(calls, ["e1", "e2"])
        calls.clear()
        ok, log = self.run_quiet(video_ids=["e1", "e2"], should_stop=lambda: bool(calls),
                                 fetch=lambda y, v: calls.append(v) or done())
        self.assertEqual(calls, ["e1"])
        self.assertIn("Stopped", log)
