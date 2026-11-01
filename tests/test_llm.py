import contextlib
import io
import unittest
import urllib.error
from unittest import mock

import llm


class RetryTest(unittest.TestCase):
    def _error(self, code, body, headers=None):
        import email.message
        msg = email.message.Message()
        for k, v in (headers or {}).items():
            msg[k] = v
        return urllib.error.HTTPError("https://x.test/v1", code, "busy", msg, io.BytesIO(body.encode()))

    def test_rate_limit_waits_and_tries_again(self):
        ok = mock.MagicMock()
        ok.__enter__.return_value.read.return_value = b'{"fine": true}'
        calls = [self._error(429, '{"retryDelay": "7s"}'), self._error(503, "overloaded", {"Retry-After": "3"}), ok]
        with mock.patch("urllib.request.urlopen", side_effect=calls), mock.patch("time.sleep") as sleep, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(llm._get_json("https://x.test/v1", {}), {"fine": True})
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [8, 4])

    def test_used_up_daily_quota_and_other_errors_fail_at_once(self):
        for err in (self._error(429, "Quota exceeded for metric: requests per day"), self._error(400, "bad request")):
            with mock.patch("urllib.request.urlopen", side_effect=[err]), mock.patch("time.sleep") as sleep:
                with self.assertRaises(llm.LLMError):
                    llm._get_json("https://x.test/v1", {})
            sleep.assert_not_called()
