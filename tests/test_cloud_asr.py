import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import cloud_asr


class CloudASRTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cloud-asr-")
        self.audio = os.path.join(self.tmp, "tone.wav")
        self.cache = os.path.join(self.tmp, "cache")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_tone(self):
        subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                        "-i", "sine=frequency=440:duration=25", self.audio], check=True)

    def settings(self, provider="groq"):
        return {"providers": {provider: {"api_key": "secret-key", "base_url": "https://example.test/v1",
                                         "model": "gemini-test"}}}

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is not installed")
    def test_multipart_retry_offsets_and_chunk_cleanup(self):
        self.make_tone()
        replies = [cloud_asr.CloudASRError("busy", 429),
                   {"language": "en", "segments": [{"start": 1, "end": 2, "text": "hello"}]},
                   {"language": "fr", "segments": [{"start": 2, "end": 3, "text": "world"}]},
                   {"language": "fr", "segments": [{"start": 0, "end": 1, "text": "again"}]}]
        seen = []

        def post(url, headers, body, timeout):
            seen.append((url, headers, body, timeout))
            if len(seen) == 1:
                raise replies.pop(0)
            return replies.pop(0)

        with mock.patch.object(cloud_asr, "MEDIA_CACHE", self.cache), \
             mock.patch.object(cloud_asr, "CHUNK_SECONDS", 10), \
             mock.patch.object(cloud_asr, "_post", side_effect=post), \
             mock.patch.object(cloud_asr.time, "sleep"):
            segments, language = cloud_asr.transcribe(self.audio, "groq", self.settings())
        self.assertEqual(language, "en")
        self.assertEqual([s["start"] for s in segments], [1, 12, 20])
        self.assertEqual(len(seen), 4)
        url, headers, body, timeout = seen[0]
        self.assertEqual(url, "https://example.test/v1/audio/transcriptions")
        self.assertEqual(headers["Authorization"], "Bearer secret-key")
        self.assertIn(b'name="model"\r\n\r\nwhisper-large-v3-turbo', body)
        self.assertIn(b'name="response_format"\r\n\r\nverbose_json', body)
        self.assertIn(b'name="timestamp_granularities[]"\r\n\r\nsegment', body)
        self.assertIn(b'name="file"; filename="', body)
        self.assertTrue(all(not os.path.exists(p) for p in __import__("glob").glob(os.path.join(self.cache, "*.mp3"))))

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is not installed")
    def test_gemini_drops_invalid_rows_and_cleans_chunks_on_error(self):
        normalized = cloud_asr._normalize_gemini([
            {"start": 3, "text": "later"}, {"start": 2, "text": "out of order"},
            {"start": 11, "text": "outside"}, {"start": 4, "text": "  "},
            {"start": 5, "text": "last"}], 10, 20)
        self.assertEqual(normalized, [{"start": 23.0, "dur": 2.0, "text": "later"},
                                     {"start": 25.0, "dur": 5.0, "text": "last"}])
        self.make_tone()
        response = {"candidates": [{"content": {"parts": [{"text": json.dumps({"language": "en", "segments": []})}]}}]}
        with mock.patch.object(cloud_asr, "MEDIA_CACHE", self.cache), \
             mock.patch.object(cloud_asr, "CHUNK_SECONDS", 10), \
             mock.patch.object(cloud_asr, "_post", return_value=response):
            with self.assertRaises(cloud_asr.CloudASRError):
                cloud_asr.transcribe(self.audio, "gemini", self.settings("gemini"))
        self.assertEqual(__import__("glob").glob(os.path.join(self.cache, "*.mp3")), [])

    def test_key_is_not_in_error(self):
        with mock.patch.object(cloud_asr, "MEDIA_CACHE", self.cache), \
             mock.patch.object(cloud_asr.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "ffmpeg")):
            with self.assertRaises(cloud_asr.CloudASRError) as caught:
                cloud_asr.transcribe("audio", "openai", self.settings("openai"))
        self.assertNotIn("secret-key", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
