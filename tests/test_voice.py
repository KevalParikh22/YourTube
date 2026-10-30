"""Voice clip decoding, cleanup, and dashboard endpoint checks."""
import base64
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import dashboard
import voice


class VoiceClipTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir_patch = mock.patch.object(voice, "VOICE_DIR", self.tmp.name)
        self.dir_patch.start()

    def tearDown(self):
        self.dir_patch.stop()
        self.tmp.cleanup()

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is not installed")
    def test_fake_transcriber_decodes_clip_and_removes_temp_file(self):
        clip = os.path.join(self.tmp.name, "clip.wav")
        subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                        "-i", "sine=frequency=440:duration=0.2", clip], check=True)
        with open(clip, "rb") as f:
            data = f.read()
        result = voice.transcribe_clip(data, "audio/mp4", lambda samples: ([{"text": " hello "}], "en"))
        self.assertEqual(result["text"], "hello")
        self.assertEqual(result["language"], "en")
        self.assertFalse(any(name.endswith(".m4a") for name in os.listdir(self.tmp.name)))

    def test_unavailable_with_mlx_whisper_only(self):
        with mock.patch.object(voice.local_asr, "available", return_value=True), \
             mock.patch("importlib.util.find_spec", side_effect=lambda name: None if name == "faster_whisper" else object()):
            self.assertFalse(voice.available())

    def test_temp_file_removed_after_transcriber_failure(self):
        with mock.patch.object(voice.local_asr, "decode", return_value=[0.0] * 16000):
            with self.assertRaisesRegex(RuntimeError, "failed"):
                voice.transcribe_clip(b"clip", "audio/webm", lambda samples: (_ for _ in ()).throw(RuntimeError("failed")))
        self.assertEqual(os.listdir(self.tmp.name), [])

    def test_validation_and_too_long_audio(self):
        with self.assertRaisesRegex(ValueError, "No audio received"):
            voice.transcribe_clip(b"", "audio/webm")
        with self.assertRaisesRegex(ValueError, "too long"):
            voice.transcribe_clip(b"x" * (voice.MAX_BYTES + 1), "audio/webm")
        with self.assertRaisesRegex(ValueError, "Unsupported audio format"):
            voice.transcribe_clip(b"x", "audio/wav")
        with mock.patch.object(voice.local_asr, "decode", return_value=[0.0] * (voice.MAX_SECONDS * 16000 + 1)):
            with self.assertRaisesRegex(ValueError, "3 minutes"):
                voice.transcribe_clip(b"clip", "audio/webm", lambda samples: ([], "en"))
        self.assertEqual(os.listdir(self.tmp.name), [])


class VoiceEndpointTest(unittest.TestCase):
    def call(self, path, data, mime="audio/webm"):
        body = json.dumps({"audio": data, "mime": mime}).encode()

        class Request:
            command = "POST"
            headers = {"Host": "127.0.0.1", "X-YTKB": "1", "Content-Type": "application/json"}
            _headers_sent = False

            def _host_ok(self): return True
            def _token_ok(self): return True
            def _body(self): return json.loads(body)
            def _error(self, message, code=400): self.result = (code, {"error": message})
            def _json(self, result, code=200): self.result = (code, result)

        request = Request()
        request.path = path
        dashboard.Handler._do_post(request)
        return request.result

    def test_state_has_voice_flag(self):
        with mock.patch.object(dashboard, "_VOICE_AVAILABLE", None), mock.patch.object(voice, "available", return_value=False):
            self.assertIs(dashboard.build_state()["voice"], False)

    def test_unavailable_and_bad_base64(self):
        with mock.patch.object(voice, "available", return_value=False):
            self.assertEqual(self.call("/api/voice/transcribe", "AA==")[0], 503)
        with mock.patch.object(voice, "available", return_value=True):
            code, body = self.call("/api/voice/transcribe", "not base64!")
        self.assertEqual((code, body["error"]), (400, "Could not read the recording"))

    def test_transcription_errors_are_400_and_transcript_is_not_logged(self):
        cases = [("", "audio/webm", "No audio received"),
                 (base64.b64encode(b"x" * (voice.MAX_BYTES + 1)).decode(), "audio/webm", "Recording is too long"),
                 (base64.b64encode(b"x").decode(), "audio/wav", "Unsupported audio format")]
        with mock.patch.object(voice, "available", return_value=True):
            for audio, mime, expected in cases:
                with mock.patch.object(voice, "transcribe_clip", side_effect=ValueError(expected)):
                    code, body = self.call("/api/voice/transcribe", audio, mime)
                    self.assertEqual(code, 400)
                    self.assertEqual(body["error"], expected)
        secret = "distinctive transcript content"
        log = mock.Mock()
        with mock.patch.object(voice, "available", return_value=True), \
             mock.patch.object(dashboard.applog, "get", return_value=log), \
             mock.patch.object(voice, "transcribe_clip", return_value={"text": secret, "language": "en", "seconds": 1}):
            self.assertEqual(self.call("/api/voice/transcribe", base64.b64encode(b"x").decode())[0], 200)
        with mock.patch.object(voice, "available", return_value=True), \
             mock.patch.object(dashboard.applog, "get", return_value=log), \
             mock.patch.object(voice, "transcribe_clip", side_effect=RuntimeError(secret)):
            self.assertEqual(self.call("/api/voice/transcribe", base64.b64encode(b"x").decode())[0], 500)
        self.assertNotIn(secret, repr(log.mock_calls))

    def test_oversized_body_rejected_before_read(self):
        class Headers(dict):
            pass

        class Request:
            headers = Headers({"Content-Length": str(dashboard.MAX_BODY + 1)})
            rfile = mock.Mock(side_effect=AssertionError("body should not be read"))

        with self.assertRaises(dashboard.BodyTooLarge):
            dashboard.Handler._body(Request())
        Request.rfile.assert_not_called()

        class PostRequest:
            command = "POST"
            path = "/api/voice/transcribe"
            headers = {"Host": "127.0.0.1", "X-YTKB": "1", "Content-Type": "application/json"}
            _headers_sent = False
            def _host_ok(self): return True
            def _token_ok(self): return True
            def _body(self): raise dashboard.BodyTooLarge()
            def _error(self, message, code=400): self.result = (code, {"error": message})

        request = PostRequest()
        dashboard.Handler._do_post(request)
        self.assertEqual(request.result, (413, {"error": "Request too large"}))
