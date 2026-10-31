import contextlib
import io
import os
import tempfile
import unittest

import local_asr
import storage
from tests.helpers import playlist, temp_db, video

DEVANAGARI = "फाइंड ट्यूनिंग हैज़ बिकम वन ऑफ़ द मोस्ट इंपोर्टेंट स्किल्स"


class WrongScriptTest(unittest.TestCase):
    def test_detection(self):
        f = storage.captions_look_wrong
        self.assertTrue(f(DEVANAGARI, "LLM Fine-Tuning Explained: The Complete Guide", "", "auto"))
        self.assertFalse(f(DEVANAGARI, "LLM Fine-Tuning Explained", "", "manual"))       # human captions: trust them
        self.assertFalse(f(DEVANAGARI, "फाइन ट्यूनिंग क्या है", "", "auto"))               # Hindi title: probably Hindi speech
        self.assertFalse(f("so today we fine tune a model", "Fine tuning", "", "auto"))   # Latin captions
        self.assertFalse(f("", "Anything", "", "auto"))


class SourcesTest(unittest.TestCase):
    def setUp(self):
        self.db = temp_db()
        storage.save_playlist(playlist(), [video("cap", "Talk with captions"), video("none", "Silent demo"),
                                           video("wrong", "English talk"), video("ok", "Good captions")], db_path=self.db)
        storage.save_details("cap", {"status": "done", "caption_kind": "auto", "segments": [{"start": 0, "text": "youtube words"}]}, db_path=self.db)
        storage.save_details("none", {"status": "no_captions"}, db_path=self.db)
        storage.save_details("wrong", {"status": "done", "caption_kind": "auto", "segments": [{"start": 0, "text": DEVANAGARI}]}, db_path=self.db)
        storage.save_details("ok", {"status": "done", "caption_kind": "auto", "segments": [{"start": 0, "text": "fine english"}]}, db_path=self.db)

    def reasons(self, ids=None):
        return {v["video_id"]: v["reason"] for v in storage.videos_needing_asr(ids, db_path=self.db)}

    def test_candidates(self):
        self.assertEqual(self.reasons(), {"none": "no_captions", "wrong": "wrong_script"})
        self.assertEqual(self.reasons(["cap"]), {"cap": None})     # explicit request is allowed
        for _ in range(3):
            storage.set_job("none", "asr", status="failed", error="x", db_path=self.db)
        self.assertEqual(self.reasons(), {"wrong": "wrong_script"})  # gave up after 3 tries

    def test_local_transcript_without_captions_becomes_active(self):
        storage.save_local_transcript("none", [{"start": 1.5, "dur": 2, "text": "whisper heard this"}], "en", "small", db_path=self.db)
        d = storage.get_details("none", db_path=self.db)
        self.assertEqual((d["transcript"], d["sources"], d["asr_language"]), ("local-asr", ["local-asr"], "en"))
        self.assertEqual(storage.get_segments("none", db_path=self.db)[0]["text"], "whisper heard this")
        self.assertEqual(storage.search("whisper heard", db_path=self.db)["results"][0]["hits"][0]["t"], 1.5)
        self.assertNotIn("none", self.reasons())                      # done: not a candidate any more

    def test_both_kept_and_user_chooses(self):
        storage.save_local_transcript("wrong", [{"start": 0, "text": "fine tuning has become important"}], "en", "turbo", db_path=self.db)
        d = storage.get_details("wrong", db_path=self.db)
        # Wrong-script captions: the local transcript is used automatically (decided 2026-10-25), captions are kept
        self.assertEqual((d["transcript"], d["sources"]), ("local-asr", ["captions", "local-asr"]))
        self.assertEqual(storage.search("important", db_path=self.db)["results"][0]["video_id"], "wrong")
        storage.set_transcript_source("wrong", "captions", db_path=self.db)               # the user's choice wins
        self.assertEqual(storage.get_details("wrong", db_path=self.db)["transcript"], "captions")
        self.assertEqual(storage.search("important", db_path=self.db)["results"], [])
        storage.set_transcript_source("wrong", "local-asr", db_path=self.db)
        self.assertEqual(storage.get_segments("wrong", db_path=self.db)[0]["text"], "fine tuning has become important")
        self.assertEqual(storage.search("important", db_path=self.db)["results"][0]["video_id"], "wrong")
        self.assertIn("fine tuning", storage.ai_context(["wrong"], db_path=self.db)["wrong"]["excerpt"])
        # Re-fetching captions must not delete the local transcript or the user's choice
        storage.save_details("wrong", {"status": "done", "caption_kind": "auto", "segments": [{"start": 0, "text": DEVANAGARI}]}, db_path=self.db)
        self.assertEqual(storage.get_details("wrong", db_path=self.db)["transcript"], "local-asr")
        lib = {v["video_id"]: v for v in storage.get_library(db_path=self.db)}
        self.assertEqual((lib["wrong"]["details"]["sources"], lib["wrong"]["details"]["transcript"]), (["captions", "local-asr"], "local-asr"))
        with self.assertRaises(ValueError):
            storage.set_transcript_source("ok", "local-asr", db_path=self.db)   # it has no local transcript
        with self.assertRaises(ValueError):
            storage.set_transcript_source("ok", "whisper", db_path=self.db)

    def test_jobs(self):
        storage.set_job("none", "asr", status="downloading", db_path=self.db)
        storage.set_job("none", "asr", status="done", progress=1.0, timings={"audio_s": 60}, db_path=self.db)
        lib = {v["video_id"]: v for v in storage.get_library(db_path=self.db)}
        self.assertEqual(lib["none"]["details"]["asr"]["status"], "done")
        with self.assertRaises(ValueError):
            storage.set_job("none", "asr", db_path=self.db, bogus=1)


class RunAsrTest(unittest.TestCase):
    """run_asr against the package temp DB with fake download/decode/transcriber (no network, no GPU)."""

    def setUp(self):
        storage.save_playlist(playlist("PLA"), [video("a1", "No caps one"), video("a2", "No caps two")])
        for vid in ("a1", "a2"):
            storage.save_details(vid, {"status": "no_captions"})
        self.tmp = tempfile.mkdtemp()
        self.files = []

    def fake_download(self, vid):
        path = os.path.join(self.tmp, f"{vid}.m4a")
        with open(path, "wb") as f:
            f.write(b"audio")
        self.files.append(path)
        return path

    def run_quiet(self, **kw):
        out = io.StringIO()
        kw.setdefault("download", self.fake_download)
        kw.setdefault("decode_audio", lambda p: [0.0] * 16000 * 30)
        kw.setdefault("make_transcriber", lambda n, d, c: (lambda s, progress: ([{"start": 0, "dur": 1, "text": "hello"}], "en")))
        with contextlib.redirect_stdout(out), unittest.mock.patch("local_asr.available", return_value=True):
            ok = local_asr.run_asr(["a1", "a2"], sleep=lambda s: None, **kw)
        return ok, out.getvalue()

    def test_transcribes_and_deletes_audio(self):
        ok, log = self.run_quiet()
        self.assertTrue(ok)
        self.assertEqual(storage.get_segments("a1")[0]["text"], "hello")
        self.assertTrue(all(not os.path.exists(p) for p in self.files))     # audio is temporary
        self.assertIn("2 transcribed", log)

    def test_failure_is_recorded_and_run_continues(self):
        def flaky(vid):
            if vid == "a1":
                raise RuntimeError("network down")
            return self.fake_download(vid)
        ok, log = self.run_quiet(download=flaky)
        self.assertTrue(ok)
        lib = {v["video_id"]: v for v in storage.get_library()}
        self.assertEqual((lib["a1"]["details"]["asr"]["status"], lib["a1"]["details"]["asr"]["tries"]), ("failed", 1))
        self.assertEqual(lib["a2"]["details"]["asr"]["status"], "done")

    def test_bot_check_stops(self):
        def bot(vid):
            raise local_asr.BotCheck("not a bot")
        ok, log = self.run_quiet(download=bot)
        self.assertFalse(ok)
        self.assertIn("not a bot", log)

    def test_choose_model(self):
        self.assertEqual(local_asr.choose_model({}, "cuda"), ("large-v3-turbo", "int8_float16"))
        self.assertEqual(local_asr.choose_model({"asr_model": "auto"}, "cpu"), ("small", "int8"))
        self.assertEqual(local_asr.choose_model({"asr_model": "medium"}, "cpu"), ("medium", "int8"))

    def test_device_selects_mlx_on_apple_silicon(self):
        from unittest import mock
        with mock.patch("local_asr.sys.platform", "darwin"), \
                mock.patch("local_asr.platform.machine", return_value="arm64"), \
                mock.patch("importlib.util.find_spec", return_value=object()):
            self.assertEqual(local_asr.device(), "mlx")

    def test_gpu_temperature_from_amd_sysfs(self):
        import os
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as drm:
            dev = os.path.join(drm, "card0", "device")
            hwmon = os.path.join(dev, "hwmon", "hwmon1")
            os.makedirs(hwmon)
            with open(os.path.join(dev, "vendor"), "w") as f:
                f.write("0x1002")
            with open(os.path.join(hwmon, "temp1_input"), "w") as f:
                f.write("72500")
            with mock.patch("local_asr.shutil.which", return_value=None):
                self.assertEqual(local_asr.gpu_temperature(drm), 72)
        with tempfile.TemporaryDirectory() as drm, mock.patch("local_asr.shutil.which", return_value=None):
            self.assertIsNone(local_asr.gpu_temperature(drm))

    def test_choose_model_mlx(self):
        self.assertEqual(local_asr.choose_model({}, "mlx"), ("mlx-community/whisper-large-v3-turbo", "float16"))
        self.assertEqual(local_asr.choose_model({"asr_model": "large-v3"}, "mlx"),
                         ("mlx-community/whisper-large-v3-turbo", "float16"))
        self.assertEqual(local_asr.choose_model({"asr_model": "mlx-community/custom"}, "mlx"),
                         ("mlx-community/custom", "float16"))

    def test_mlx_transcriber_normalizes_segments(self):
        import sys
        from unittest import mock
        fake = type("Mlx", (), {"transcribe": lambda self, samples, **kw: {
            "language": "en", "segments": [{"start": 1.234, "end": 2.678, "text": " hello "},
                                               {"start": 3, "end": 4, "text": " "}]}})()
        progress = []
        with mock.patch.dict(sys.modules, {"mlx_whisper": fake}):
            transcriber = local_asr.MlxTranscriber("mlx-community/model", "mlx", "float16")
            result, language = transcriber([0], progress.append)
        self.assertEqual(result, [{"start": 1.23, "dur": 1.44, "text": "hello"}])
        self.assertEqual((language, progress), ("en", [1.0]))

    def test_run_asr_uses_mlx_transcriber(self):
        from unittest import mock
        calls = []
        def factory(name, dev, compute):
            calls.append((name, dev, compute))
            return lambda samples, progress: ([{"start": 0, "dur": 1, "text": "mlx"}], "en")
        with mock.patch("local_asr.device", return_value="mlx"), \
                mock.patch("local_asr.MlxTranscriber", side_effect=factory), \
                mock.patch("local_asr.unload_ollama", return_value=[]):
            ok, _ = self.run_quiet(make_transcriber=local_asr.Transcriber)
        self.assertTrue(ok)
        self.assertEqual(calls[0][1], "mlx")

    def test_cloud_transcription_groq(self):
        import cloud_asr
        import llm
        from unittest import mock
        settings = llm.load_settings()
        settings["tasks"]["transcribe"] = "groq"
        settings["providers"]["groq"]["api_key"] = "test-key"

        fake_segments = [{"start": 0.0, "dur": 1.5, "text": "cloud speech"}]
        fake_transcribe = mock.MagicMock(return_value=(fake_segments, "en"))
        mock_unload = mock.MagicMock()

        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
                mock.patch.object(cloud_asr, "transcribe", fake_transcribe), \
                mock.patch("local_asr.unload_ollama", mock_unload), \
                mock.patch("local_asr.available", return_value=False), \
                mock.patch("local_asr.shutil.which", return_value="/usr/bin/ffmpeg"):
            ok = local_asr.run_asr(["a1", "a2"], download=self.fake_download, sleep=lambda s: None, settings=settings)

        self.assertTrue(ok)
        # Segments saved with model groq:...
        segs = storage.get_segments("a1")
        self.assertEqual(segs[0]["text"], "cloud speech")
        details = storage.get_details("a1")
        self.assertEqual(details["asr_model"], "groq:whisper-large-v3-turbo")
        self.assertEqual(details["transcript"], "local-asr")

        # No Ollama unload and no faster-whisper needed
        mock_unload.assert_not_called()

        # The opening line names the engine and cost
        log = out.getvalue()
        lines = [line.strip() for line in log.splitlines() if line.strip()]
        self.assertTrue(lines[0].startswith("Groq whisper-large-v3-turbo"))
        self.assertIn("about $", lines[0])
        self.assertIn("Audio is uploaded to Groq, then deleted here.", lines[0])
        self.assertEqual(fake_transcribe.call_count, 2)

    def test_cloud_transcription_openai_and_gemini(self):
        import cloud_asr
        import llm
        from unittest import mock
        settings = llm.load_settings()
        settings["tasks"]["transcribe"] = "openai"
        settings["providers"]["openai"]["api_key"] = "test-key"

        fake_transcribe = mock.MagicMock(return_value=([{"start": 0.0, "dur": 1.0, "text": "openai audio"}], "en"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
                mock.patch.object(cloud_asr, "transcribe", fake_transcribe), \
                mock.patch("local_asr.available", return_value=False), \
                mock.patch("local_asr.shutil.which", return_value="/usr/bin/ffmpeg"):
            ok = local_asr.run_asr(["a1"], download=self.fake_download, sleep=lambda s: None, settings=settings)
        self.assertTrue(ok)
        self.assertEqual(storage.get_details("a1")["asr_model"], "openai:whisper-1")
        self.assertTrue(out.getvalue().splitlines()[0].startswith("OpenAI whisper-1"))

        # Gemini
        settings["tasks"]["transcribe"] = "gemini"
        settings["providers"]["gemini"]["api_key"] = "test-key"
        settings["providers"]["gemini"]["model"] = "gemini-3.8-flash"
        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
                mock.patch.object(cloud_asr, "transcribe", fake_transcribe), \
                mock.patch("local_asr.available", return_value=False), \
                mock.patch("local_asr.shutil.which", return_value="/usr/bin/ffmpeg"):
            ok = local_asr.run_asr(["a2"], download=self.fake_download, sleep=lambda s: None, settings=settings)
        self.assertTrue(ok)
        self.assertEqual(storage.get_details("a2")["asr_model"], "gemini:gemini-3.8-flash")
        log = out.getvalue()
        self.assertTrue(log.splitlines()[0].startswith("Gemini gemini-3.8-flash"))
        self.assertIn("Timestamps from Gemini are approximate.", log)

    def test_cloud_cost_estimates(self):
        import llm
        settings = llm.load_settings()
        # Local is 0.0
        self.assertEqual(local_asr.estimate_cost(settings, 3600 * 3.1), 0.0)

        # Groq
        settings["tasks"]["transcribe"] = "groq"
        settings["providers"]["groq"]["api_key"] = "test-key"
        self.assertEqual(local_asr.estimate_cost(settings, 3600 * 3.1), 0.12)

        # OpenAI
        settings["tasks"]["transcribe"] = "openai"
        settings["providers"]["openai"]["api_key"] = "test-key"
        self.assertEqual(local_asr.estimate_cost(settings, 3600 * 3.1), 1.12)

        # Estimate dict
        est = local_asr.estimate(video_ids=["a1"], settings=settings)
        self.assertEqual(est["engine"], "openai")
        self.assertEqual(est["device"], "cloud")
        self.assertEqual(est["model"], "whisper-1")
        self.assertIsNotNone(est["cost"])


import unittest.mock  # noqa: E402  (used by RunAsrTest)
