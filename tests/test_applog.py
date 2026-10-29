"""Coverage for redacted and best-effort application logging."""
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
                        "Error: Cookie: session-cookie\nsk-ant-abcdefghijklmno\nAIza12345678901234567890\nregistered-secret-value")
        log = self.read_log()
        self.assertIn("ERROR", log)
        self.assertIn("Task failed: Sync https://youtube.test/?t=[hidden]", log)
        self.assertIn("kind=sync, 3.1 s", log)
        self.assertIn("Last output:", log)
        for secret in (token, "session-cookie", "sk-ant-abcdefghijklmno", "AIza12345678901234567890",
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

