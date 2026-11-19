import contextlib
import io
import unittest
import urllib.error
from unittest import mock

import llm


class RetryTest(unittest.TestCase):
    def setUp(self):   # pacing has its own test; here only the retry waits reach time.sleep
        patcher = mock.patch.object(llm, "_wait_turn", lambda service: None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(llm._PACE.clear)

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
        for err, kind in ((self._error(429, "Quota exceeded for metric: requests per day"), llm.QuotaError),
                          (self._error(400, "bad request"), llm.LLMError)):
            with mock.patch("urllib.request.urlopen", side_effect=[err]), mock.patch("time.sleep") as sleep:
                with self.assertRaises(kind) as caught:
                    llm._get_json("https://x.test/v1", {})
            sleep.assert_not_called()
        self.assertNotIsInstance(caught.exception, llm.QuotaError)   # a 400 is not a used-up quota


class PaceTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(llm._PACE.clear)

    def test_gap_starts_after_a_429_doubles_and_shrinks_while_calls_succeed(self):
        service = "https://pace.test/v1"
        with mock.patch("time.sleep") as sleep, mock.patch("time.monotonic", return_value=100.0), \
                contextlib.redirect_stdout(io.StringIO()):
            llm._wait_turn(service)
            sleep.assert_not_called()                                # no limit seen yet: no waiting
            llm._pace_after(service, True)
            self.assertEqual(llm._PACE[service]["gap"], llm.PACE_MIN)
            llm._pace_after(service, True)
            self.assertEqual(llm._PACE[service]["gap"], llm.PACE_MIN * 2)
            llm._wait_turn(service)                                  # takes the slot at 100, books the next at 108
            llm._wait_turn(service)                                  # a second caller (helper thread) waits its turn
            self.assertEqual(sleep.call_args.args[0], llm.PACE_MIN * 2)
            for _ in range(200):
                llm._pace_after(service, False)
            self.assertEqual(llm._PACE[service]["gap"], 0.0)         # back to full speed after a run of successes
            llm._pace_after(service, True)
            for _ in range(50):
                llm._pace_after(service, True)
            self.assertEqual(llm._PACE[service]["gap"], llm.PACE_MAX)
