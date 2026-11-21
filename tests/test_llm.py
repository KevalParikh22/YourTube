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


class UsageTest(unittest.TestCase):
    def setUp(self):
        import storage
        self.storage = storage
        storage.init_db()
        with storage.get_connection() as conn:
            conn.execute("DELETE FROM ai_usage")
            conn.commit()

    def _settings(self, provider, model, **cfg):
        s = llm.default_settings()
        s["provider"] = provider
        s["providers"][provider].update(model=model, api_key="test-key", **cfg)
        return s

    def rows(self):
        return self.storage.usage_summary("2000-01-01 00:00:00")

    def test_daily_cap_and_cost_since(self):
        self.assertIsNone(llm.default_settings()["daily_cap"])
        self.storage.record_usage("rate", "openai", "m", 1, 1, 1.2)
        self.assertAlmostEqual(self.storage.cost_since("2000-01-01 00:00:00"), 1.2)
        self.assertEqual(self.storage.cost_since("2999-01-01 00:00:00"), 0)
        with mock.patch.object(llm, "_today_start", return_value="2000-01-01 00:00:00"):
            with self.assertRaises(llm.QuotaError):
                llm.check_cap({"daily_cap": 1.0})
            with mock.patch.object(self.storage, "cost_since", return_value=0.5):
                self.assertIsNone(llm.check_cap({"daily_cap": 1.0}))
            self.assertIsNone(llm.check_cap({"daily_cap": None}))

    def test_open_checks_only_cloud_hosts(self):
        ok = mock.MagicMock()
        ok.__enter__.return_value.read.return_value = b'{}'
        with mock.patch.object(llm, "check_cap") as check, mock.patch("urllib.request.urlopen", return_value=ok):
            llm._get_json("http://127.0.0.1:11434/api/chat", {})
            check.assert_not_called()
            llm._get_json("https://api.example/v1/chat", {})
            check.assert_called_once()

    def test_anthropic_checks_daily_budget_before_import_or_call(self):
        with mock.patch.object(llm, "check_cap", side_effect=llm.QuotaError("cap reached")) as check:
            with mock.patch.dict("sys.modules", {"anthropic": None}):
                with self.assertRaises(llm.QuotaError):
                    llm._anthropic({"model": "m"}, "k", "s", "p")
            check.assert_called_once()

    def test_anthropic_with_no_cap_proceeds_to_sdk(self):
        fake_resp = mock.MagicMock(usage=None, stop_reason="end_turn", content=[mock.MagicMock(type="text", text="ok")])
        fake_client = mock.MagicMock()
        fake_client.messages.create.return_value = fake_resp
        with mock.patch("anthropic.Anthropic", return_value=fake_client), \
                mock.patch.object(llm, "load_settings", return_value={"daily_cap": None}):
            res = llm._anthropic({"model": "m"}, "k", "s", "p")
            self.assertEqual(res, "ok")

    def test_cloud_call_is_saved_with_the_reported_tokens_and_price(self):
        reply = {"candidates": [{"content": {"parts": [{"text": "{}"}]}}],
                 "usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 200, "thoughtsTokenCount": 50}}
        with mock.patch.object(llm, "_post_json", return_value=reply):
            llm.make_completer(self._settings("gemini", "gemini-2.5-flash"), "rate")("system", "prompt")
        [row] = self.rows()
        self.assertEqual((row["task"], row["provider"], row["model"], row["calls"]), ("rate", "gemini", "gemini-2.5-flash", 1))
        self.assertEqual((row["tokens_in"], row["tokens_out"], row["estimated"], row["unpriced"]), (1000, 250, 0, 0))
        self.assertAlmostEqual(row["cost"], 0.0009, places=4)   # 1000 * 0.30/M + 250 * 2.50/M

    def test_reply_without_usage_is_estimated_and_unknown_model_unpriced(self):
        reply = {"choices": [{"message": {"content": "x" * 40}}]}
        s = self._settings("openai", "some-new-model", base_url="https://api.openai.test/v1")
        with mock.patch.object(llm, "_post_json", return_value=reply):
            llm.make_completer(s, "summarize")("s" * 100, "p" * 300)
        [row] = self.rows()
        self.assertEqual((row["tokens_in"], row["tokens_out"], row["estimated"], row["unpriced"]), (100, 10, 1, 1))

    def test_billed_reply_that_cannot_be_read_is_still_saved(self):
        reply = {"promptFeedback": {"blockReason": "SAFETY"}, "usageMetadata": {"promptTokenCount": 70}}
        with mock.patch.object(llm, "_post_json", return_value=reply), self.assertRaises(llm.LLMError):
            llm.make_completer(self._settings("gemini", "gemini-2.5-flash"), "ask")("s", "p")
        self.assertEqual(self.rows()[0]["tokens_in"], 70)

    def test_models_on_this_computer_are_not_saved(self):
        with mock.patch.object(llm, "_post_json", return_value={"message": {"content": "{}"}}):
            llm.make_completer(self._settings("ollama", "qwen3:8b"), "rate")("s", "p")
        self.assertEqual(self.rows(), [])

    def test_task_totals_include_helper_threads(self):
        import contextvars
        import threading
        self.addCleanup(llm._RUN.set, None)
        run = llm.start_run()
        threads = [threading.Thread(target=contextvars.copy_context().run,
                                    args=(llm.record_usage, "rate", "gemini", "gemini-2.5-flash", 1000, 100)) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        llm.record_usage("rate", "gemini", "unpriced-model", 500, 0, exact=False)
        self.assertEqual((run["calls"], run["tokens_in"], run["tokens_out"]), (5, 4500, 400))
        line = llm.run_usage_line(run)
        self.assertIn("AI use: 5 cloud calls, about 4.5k tokens in, about 400 out", line)
        self.assertIn("(some calls unpriced)", line)
        self.assertEqual(llm.run_usage_line(llm.start_run()), "")   # nothing sent: no line
