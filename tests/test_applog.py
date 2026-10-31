"""Coverage for redacted and best-effort application logging."""
import io
import logging
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

import applog
import dashboard


class AppLogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.logger = applog.setup()

    def setUp(self):
        applog.set_secret("registered-secret-value")

    def read_log(self):
        for handler in applog.logging.getLogger("yourtube").handlers:
            handler.flush()
        with open(applog.LOG_FILE, encoding="utf-8") as stream:
            return stream.read()

    def test_redacts_secrets_and_task_failure_is_descriptive(self):
        token = "dashboard-token-123"
        applog.set_secret(token)
        now = datetime.now()
        applog.log_task("sync", f"Sync https://youtube.test/?t={token}", False, False,
                        now.isoformat(), (now + timedelta(seconds=3.1)).isoformat(),
                        "Error: Cookie: session-cookie\nsk-ant-abcdefghijklmno\nAIza12345678901234567890\ngsk_abcdefghijklmnopqrstuv\nregistered-secret-value")
        log = self.read_log()
        self.assertIn("ERROR", log)
        self.assertIn("Task failed: Sync https://youtube.test/?t=[hidden]", log)
        self.assertIn("kind=sync, 3.1 s", log)
        self.assertIn("Last output:", log)
        for secret in (token, "session-cookie", "sk-ant-abcdefghijklmno", "AIza12345678901234567890", "gsk_abcdefghijklmnopqrstuv",
                       "registered-secret-value"):
            self.assertNotIn(secret, log)

    def test_rotation_is_configured(self):
        handler = next(h for h in applog.logging.getLogger("yourtube").handlers
                       if getattr(h, "_yourtube_file_handler", False))
        self.assertEqual(handler.maxBytes, 2_000_000)
        self.assertEqual(handler.backupCount, 5)

    def test_unwritable_log_location_does_not_raise(self):
        logger = applog.logging.getLogger("yourtube")
        handlers = logger.handlers[:]
        logger.handlers.clear()
        with tempfile.NamedTemporaryFile() as not_a_directory, \
                mock.patch.object(applog, "LOG_DIR", not_a_directory.name), \
                mock.patch.object(applog, "LOG_FILE", os.path.join(not_a_directory.name, "yourtube.log")), \
                mock.patch("builtins.print") as warning:
            self.assertIs(applog.setup(), logger)
            warning.assert_called_once()
            applog.get("job").error("logging fallback works")
        logger.handlers.clear()
        logger.handlers.extend(handlers)

    def test_request_exception_returns_500_and_logs_traceback(self):
        handler = object.__new__(dashboard.Handler)
        handler.path = "/api/state?private=query"
        handler._headers_sent = False
        handler._headers_buffer = []
        sent = []
        handler._json = lambda obj, code=200: sent.append((obj, code))
        handler._handle_request(lambda: (_ for _ in ()).throw(RuntimeError("request exploded")), "GET")
        self.assertEqual(sent, [({"error": "Something went wrong. Details are in logs/yourtube.log"}, 500)])
        log = self.read_log()
        self.assertIn("Request failed: GET /api/state", log)
        self.assertNotIn("private=query", log)
        self.assertIn("Traceback (most recent call last)", log)
        self.assertIn("request exploded", log)

    def test_explain_each_row_of_table(self):
        # Row 1: playlist does not exist / This playlist type is unviewable
        cause1 = "The playlist ID is wrong, or the playlist is private and was read without your sign-in"
        fix1 = 'Check the link. For private playlists and Watch Later choose "Use my browser login" or a cookies.txt file'
        self.assertEqual(applog.explain("ERROR: playlist does not exist"), (cause1, fix1))
        self.assertEqual(applog.explain("This playlist type is unviewable"), (cause1, fix1))

        # Rows that contain "playlist does not exist" but happen with a sign-in come first
        rotated = applog.explain("YouTube said: The playlist does not exist. YouTube rotated the chrome sign-in during the read")
        self.assertIn("replaced the browser's sign-in", rotated[0])
        nobody = applog.explain("none of the browsers signed in to YouTube can read this playlist. "
                                "chrome: YouTube said: The playlist does not exist.")
        self.assertIn("account that owns this playlist", nobody[0])

        # Row 2: Sign in to confirm you.?re not a bot
        cause2 = "YouTube asked for a sign-in check"
        fix2 = "Sync with your browser login, wait a few minutes, or update yt-dlp: .venv/bin/python -m pip install -U yt-dlp"
        self.assertEqual(applog.explain("Sign in to confirm you're not a bot"), (cause2, fix2))
        self.assertEqual(applog.explain("Sign in to confirm you’re not a bot"), (cause2, fix2))
        self.assertEqual(applog.explain("Sign in to confirm youre not a bot"), (cause2, fix2))

        # Row 3: HTTP Error 429 / Too Many Requests
        cause3 = "YouTube is rate-limiting this computer"
        fix3 = "Wait 15–30 minutes, then try again"
        self.assertEqual(applog.explain("HTTP Error 429: Too Many Requests"), (cause3, fix3))
        self.assertEqual(applog.explain("HTTP Error 429"), (cause3, fix3))
        self.assertEqual(applog.explain("Too Many Requests"), (cause3, fix3))

        # Row 4: could not find .* cookies database / failed to decrypt / secretstorage
        cause4 = "The browser's cookies could not be read"
        fix4 = "Sign in to YouTube in that browser, close it, and try again; on Linux install secretstorage; or use a cookies.txt export"
        self.assertEqual(applog.explain("could not find chrome cookies database"), (cause4, fix4))
        self.assertEqual(applog.explain("failed to decrypt"), (cause4, fix4))
        self.assertEqual(applog.explain("secretstorage missing"), (cause4, fix4))

        # Row 5: Unsupported URL / is not a valid URL
        cause5 = "The link is not a YouTube playlist or video"
        fix5 = "Paste a link like https://www.youtube.com/playlist?list=…"
        self.assertEqual(applog.explain("Unsupported URL: https://example.com"), (cause5, fix5))
        self.assertEqual(applog.explain("'foo' is not a valid URL"), (cause5, fix5))

        # Row 6: Ollama: Connection refused.*11434 / Ollama + not running
        cause6 = "Ollama is not running"
        fix6 = "Start Ollama (ollama serve)"
        self.assertEqual(applog.explain("Connection refused: 127.0.0.1:11434"), (cause6, fix6))
        self.assertEqual(applog.explain("Ollama is not running"), (cause6, fix6))

        # Row 7: getaddrinfo / Name or service not known / timed out / Connection (refused|reset) / URLError
        cause7 = "No connection to YouTube"
        fix7 = "Check the internet connection and try again"
        self.assertEqual(applog.explain("getaddrinfo failed"), (cause7, fix7))
        self.assertEqual(applog.explain("Name or service not known"), (cause7, fix7))
        self.assertEqual(applog.explain("The read operation timed out"), (cause7, fix7))
        self.assertEqual(applog.explain("Connection refused by server:443"), (cause7, fix7))
        self.assertEqual(applog.explain("Connection reset by peer"), (cause7, fix7))
        self.assertEqual(applog.explain("<urlopen error URLError: disconnected>"), (cause7, fix7))

        # Row 8: nsig extraction failed / Unable to extract / Requested format is not available
        cause8 = "YouTube changed something that yt-dlp does not handle yet"
        fix8 = "Update yt-dlp: .venv/bin/python -m pip install -U yt-dlp"
        self.assertEqual(applog.explain("nsig extraction failed"), (cause8, fix8))
        self.assertEqual(applog.explain("Unable to extract video data"), (cause8, fix8))
        self.assertEqual(applog.explain("Requested format is not available"), (cause8, fix8))

        # Row 9: database is locked
        cause9 = "Another program is using the library database"
        fix9 = "Close other YourTube windows or scripts and try again"
        self.assertEqual(applog.explain("sqlite3.OperationalError: database is locked"), (cause9, fix9))

        # Row 10: No space left on device
        cause10 = "The disk is full"
        fix10 = "Free some space"
        self.assertEqual(applog.explain("OSError: [Errno 28] No space left on device"), (cause10, fix10))

        # Case insensitivity and unknown / empty inputs
        self.assertEqual(applog.explain("PLAYLIST DOES NOT EXIST")[0], cause1)
        self.assertEqual(applog.explain("DATABASE IS LOCKED")[0], cause9)
        self.assertIsNone(applog.explain(None))
        self.assertIsNone(applog.explain(""))
        self.assertIsNone(applog.explain("Unrecognized custom error"))

    def test_log_task_uses_explain_when_matched(self):
        now = datetime.now()
        applog.log_task("sync", "Sync WL", False, False,
                        now.isoformat(), (now + timedelta(seconds=1.5)).isoformat(),
                        "Extracting...\nERROR: playlist does not exist")
        log = self.read_log()
        self.assertIn("Task failed: Sync WL (kind=sync, 1.5 s)", log)
        self.assertIn("Cause: The playlist ID is wrong, or the playlist is private and was read without your sign-in", log)
        self.assertIn('Fix: Check the link. For private playlists and Watch Later choose "Use my browser login" or a cookies.txt file', log)

    def test_log_task_fallback_when_explain_does_not_match(self):
        now = datetime.now()
        applog.log_task("sync", "Sync WL", False, False,
                        now.isoformat(), (now + timedelta(seconds=1.0)).isoformat(),
                        "Extracting...\nError: Some unrecognized error occurred")
        log = self.read_log()
        self.assertIn("Cause: Error: Some unrecognized error occurred", log)
        self.assertIn("Fix: See the output below", log)

    def test_run_pipeline_prints_cause_fix_and_logs_with_access_mode(self):
        import main
        with mock.patch("main.extract_playlist_metadata", side_effect=Exception("ERROR: playlist does not exist")), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as fake_stdout:
            res = main.run_pipeline("https://youtube.com/playlist?list=123", browser_cookies="chrome")
            self.assertFalse(res)
            out = fake_stdout.getvalue()
            self.assertIn("Cause: The playlist ID is wrong, or the playlist is private and was read without your sign-in", out)
            self.assertIn('Fix: Check the link. For private playlists and Watch Later choose "Use my browser login" or a cookies.txt file', out)
        log = self.read_log()
        self.assertIn("ERROR", log)
        self.assertIn("yourtube.sync: Sync failed (browser:chrome): ERROR: playlist does not exist", log)

    def test_run_pipeline_logs_cookies_access_without_path(self):
        import main
        with mock.patch("main.extract_playlist_metadata", side_effect=Exception("HTTP Error 429")), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as fake_stdout:
            res = main.run_pipeline("https://youtube.com/playlist?list=123", cookies_file="/secret/path/to/cookies.txt")
            self.assertFalse(res)
            out = fake_stdout.getvalue()
            self.assertIn("Cause: YouTube is rate-limiting this computer", out)
            self.assertIn("Fix: Wait 15–30 minutes, then try again", out)
        log = self.read_log()
        self.assertIn("ERROR", log)
        self.assertIn("yourtube.sync: Sync failed (cookies): HTTP Error 429", log)
        self.assertNotIn("/secret/path/to/cookies.txt", log)

    def test_run_pipeline_public_access_and_unmatched_error(self):
        import main
        with mock.patch("main.extract_playlist_metadata", side_effect=Exception("completely unknown error")), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as fake_stdout:
            res = main.run_pipeline("https://youtube.com/playlist?list=123")
            self.assertFalse(res)
            out = fake_stdout.getvalue()
            self.assertNotIn("Cause:", out)
            self.assertNotIn("Fix:", out)
        log = self.read_log()
        self.assertIn("ERROR", log)
        self.assertIn("yourtube.sync: Sync failed (public): completely unknown error", log)



class ExplainOnlyErrorLinesTest(unittest.TestCase):
    def test_tips_do_not_decide_the_cause(self):
        import logging
        output = ("Error: could not read the playlist: HTTP Error 429: Too Many Requests\n"
                  "3. \"The playlist does not exist\" usually means a wrong ID\n")
        with self.assertLogs("yourtube.job", level="ERROR") as logs:
            applog.log_task("sync", "Sync x", False, False, None, None, output)
        self.assertIn("rate-limiting", logs.output[0])


class YtDlpPluginShadowTest(unittest.TestCase):
    def test_using_yt_dlp_keeps_our_extractor_module(self):
        import sys
        import yt_dlp
        import extractor
        with yt_dlp.YoutubeDL({"quiet": True}):
            pass
        self.assertIs(sys.modules["extractor"], extractor)
        from extractor import video_id_from_url   # failed after the first sync before YTDLP_NO_PLUGINS
        self.assertTrue(callable(video_id_from_url))
