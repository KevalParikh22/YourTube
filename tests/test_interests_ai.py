"""Tests for AI rewrite into interests (interests_ai.py, llm task, dashboard endpoint)."""
import json
import unittest
from unittest import mock

import dashboard
import interests_ai
import llm
import storage


def fake_completer(reply):
    calls = []

    def complete(system, prompt):
        calls.append({"system": system, "prompt": prompt})
        return reply(prompt) if callable(reply) else reply

    complete.calls = calls
    return complete


class InterestsPromptTest(unittest.TestCase):
    def test_prompt_includes_text_current_interests_and_categories(self):
        text = "I want to learn distributed systems and Raft consensus in Go"
        current = "Learn: Basic Python\nGoal: Web dev"
        context = {
            "library": "Backend Studies",
            "categories": ["Distributed Systems", "Go", "Databases"],
            "language": "English",
        }
        complete = fake_completer("Learn: Raft in Go\nLevel: Advanced\nSkip: Python\nGoal: Distributed systems")
        interests_ai.draft_interests(text, current, context, complete)

        self.assertEqual(len(complete.calls), 1)
        prompt = complete.calls[0]["prompt"]
        self.assertIn(text, prompt)
        self.assertIn(current, prompt)
        self.assertIn("Distributed Systems", prompt)
        self.assertIn("Go", prompt)
        self.assertIn("Databases", prompt)
        self.assertIn("Backend Studies", prompt)

    def test_prompt_handles_empty_current_and_dict_categories(self):
        text = "Learn Kubernetes basics"
        complete = fake_completer("Learn: Kubernetes\nLevel: Beginner\nSkip: nothing specific\nGoal: DevOps")
        interests_ai.draft_interests(text, "", {"categories": {"DevOps": ["k8s"]}}, complete)
        prompt = complete.calls[0]["prompt"]
        self.assertIn(text, prompt)
        self.assertIn("DevOps", prompt)
        self.assertIn("Current interests:", prompt)


class CleanAndCutReplyTest(unittest.TestCase):
    def test_strips_code_fences_and_surrounding_quotes(self):
        cases = [
            ("```\nLearn: Rust\nLevel: Intermediate\nSkip: C++\nGoal: Systems\n```",
             "Learn: Rust\nLevel: Intermediate\nSkip: C++\nGoal: Systems"),
            ("```text\n\"Learn: Rust\nLevel: Intermediate\nSkip: C++\nGoal: Systems\"\n```",
             "Learn: Rust\nLevel: Intermediate\nSkip: C++\nGoal: Systems"),
            ("'''\nLearn: Rust\nLevel: Intermediate\nSkip: C++\nGoal: Systems\n'''",
             "Learn: Rust\nLevel: Intermediate\nSkip: C++\nGoal: Systems"),
            ('"""\nLearn: Rust\nLevel: Intermediate\nSkip: C++\nGoal: Systems\n"""',
             "Learn: Rust\nLevel: Intermediate\nSkip: C++\nGoal: Systems"),
            ("“Learn: Rust\nLevel: Intermediate\nSkip: C++\nGoal: Systems”",
             "Learn: Rust\nLevel: Intermediate\nSkip: C++\nGoal: Systems"),
        ]
        for raw, expected in cases:
            self.assertEqual(interests_ai.draft_interests("learn rust", "", {}, fake_completer(raw)), expected)

    def test_collapses_three_or_more_newlines(self):
        raw = "Learn: Rust\n\n\n\nLevel: Intermediate\n\n\nSkip: none\n\nGoal: Systems"
        expected = "Learn: Rust\n\nLevel: Intermediate\n\nSkip: none\n\nGoal: Systems"
        self.assertEqual(interests_ai.draft_interests("learn rust", "", {}, fake_completer(raw)), expected)

    def test_cut_at_2000_on_line_boundary(self):
        # 25 lines of 100 characters each = ~2500 chars
        line = "Learn: " + ("a" * 90)
        lines = [f"{line} #{i}" for i in range(25)]
        raw = "\n".join(lines)
        res = interests_ai.draft_interests("learn big topics", "", {}, fake_completer(raw))

        self.assertLessEqual(len(res), 2000)
        self.assertTrue(res.endswith(lines[res.count("\n")]))
        # Must not cut in the middle of a line
        self.assertTrue(all(l in lines for l in res.split("\n")))

    def test_short_reply_not_cut(self):
        raw = "Learn: Go\nLevel: Beginner\nSkip: none\nGoal: backend"
        res = interests_ai.draft_interests("learn go", "", {}, fake_completer(raw))
        self.assertEqual(res, raw)


    def test_strips_think_tags(self):
        raw = "<think>Let me see what the user wants.</think>\nLearn: Go\nLevel: Beginner\nSkip: none\nGoal: backend"
        res = interests_ai.draft_interests("learn go", "", {}, fake_completer(raw))
        self.assertEqual(res, "Learn: Go\nLevel: Beginner\nSkip: none\nGoal: backend")


class ValidationErrorsTest(unittest.TestCase):
    def test_short_and_invalid_input(self):
        complete = fake_completer("Learn: A\nLevel: B\nSkip: C\nGoal: D")
        with self.assertRaises(ValueError):
            interests_ai.draft_interests("", "", {}, complete)
        with self.assertRaises(ValueError):
            interests_ai.draft_interests("   ", "", {}, complete)
        with self.assertRaises(ValueError):
            interests_ai.draft_interests("hi", "", {}, complete)
        with self.assertRaises(ValueError):
            interests_ai.draft_interests(None, "", {}, complete)

    def test_oversized_input(self):
        complete = fake_completer("Learn: A\nLevel: B\nSkip: C\nGoal: D")
        with self.assertRaises(ValueError):
            interests_ai.draft_interests("x" * 4001, "", {}, complete)

    def test_empty_and_under_three_words_reply(self):
        for bad_reply in ["", "   ", "```\n```", "one", "one two"]:
            with self.assertRaisesRegex(ValueError, "The AI returned nothing useful"):
                interests_ai.draft_interests("learn rust programming", "", {}, fake_completer(bad_reply))


class TaskFollowsTest(unittest.TestCase):
    def test_task_defined_and_follows_rate(self):
        self.assertEqual(llm.TASKS.get("interests"), "About you")
        self.assertEqual(llm.TASK_FOLLOWS.get("interests"), "rate")

    def test_provider_for_follows_rate_when_unset(self):
        settings = {
            "provider": "ollama",
            "providers": {
                "ollama": {"model": "llama3", "base_url": "http://localhost:11434"},
                "anthropic": {"model": "claude-opus-5-5", "api_key": "sk-test"},
                "gemini": {"model": "gemini-2.5-flash", "api_key": "sk-gemini"},
            },
            "tasks": {"rate": "anthropic"},
        }
        self.assertEqual(llm.provider_for(settings, "interests"), "anthropic")
        self.assertTrue(llm.is_configured(settings, "interests"))

    def test_provider_for_uses_own_setting_when_set(self):
        settings = {
            "provider": "ollama",
            "providers": {
                "ollama": {"model": "llama3", "base_url": "http://localhost:11434"},
                "anthropic": {"model": "claude-opus-5-5", "api_key": "sk-test"},
                "gemini": {"model": "gemini-2.5-flash", "api_key": "sk-gemini"},
            },
            "tasks": {"rate": "anthropic", "interests": "gemini"},
        }
        self.assertEqual(llm.provider_for(settings, "interests"), "gemini")

    def test_provider_for_falls_back_to_default_when_rate_unset(self):
        settings = {
            "provider": "ollama",
            "providers": {
                "ollama": {"model": "llama3", "base_url": "http://localhost:11434"},
            },
            "tasks": {},
        }
        self.assertEqual(llm.provider_for(settings, "interests"), "ollama")


class DashboardEndpointTest(unittest.TestCase):
    def call(self, body_dict):
        body = json.dumps(body_dict).encode()

        class Request:
            command = "POST"
            headers = {"Host": "127.0.0.1", "X-YTKB": "1", "Content-Type": "application/json"}
            _headers_sent = False

            def _host_ok(self): return True
            def _token_ok(self): return True
            def _body(self): return json.loads(body)
            def _error(self, message, code=400): self.result = (code, {"error": message})
            def _json(self, result, code=200): self.result = (code, result)

        req = Request()
        req.path = "/api/interests/draft"
        dashboard.Handler._do_post(req)
        return req.result

    def test_unconfigured_ai_returns_400(self):
        with mock.patch("llm.is_configured", return_value=False):
            code, body = self.call({"text": "learn kubernetes"})
            self.assertEqual(code, 400)
            self.assertEqual(body["error"], "Choose an AI for About you in Settings → AI by task")

    def test_short_input_returns_400(self):
        with mock.patch("llm.is_configured", return_value=True), \
             mock.patch("llm.make_completer", return_value=lambda sys, prompt: "dummy"):
            code, body = self.call({"text": "hi"})
            self.assertEqual(code, 400)
            self.assertIn("Tell us a little more", body["error"])

    def test_empty_ai_reply_returns_400(self):
        with mock.patch("llm.is_configured", return_value=True), \
             mock.patch("llm.make_completer", return_value=lambda sys, prompt: ""):
            code, body = self.call({"text": "learn kubernetes"})
            self.assertEqual(code, 400)
            self.assertEqual(body["error"], "The AI returned nothing useful. Try again or write it yourself")

    def test_ai_error_returns_502(self):
        def fail(sys, prompt):
            raise llm.LLMError("API connection lost")

        with mock.patch("llm.is_configured", return_value=True), \
             mock.patch("llm.make_completer", return_value=fail):
            code, body = self.call({"text": "learn kubernetes"})
            self.assertEqual(code, 502)
            self.assertIn("API connection lost", body["error"])

    def test_successful_draft_returns_200_and_never_saves(self):
        good_reply = "Learn: Docker\nLevel: Beginner\nSkip: none\nGoal: Containers"
        initial_interests = "Original interests unchanged"

        settings = llm.load_settings()
        settings["interests"] = initial_interests
        llm.save_settings(settings)

        with mock.patch("llm.is_configured", return_value=True), \
             mock.patch("llm.make_completer", return_value=lambda sys, prompt: good_reply), \
             mock.patch("storage.update_videos") as mock_update, \
             mock.patch("llm.save_settings") as mock_save:
            code, body = self.call({"text": "learn docker basics", "current": initial_interests})
            self.assertEqual(code, 200)
            self.assertEqual(body["interests"], good_reply)
            mock_save.assert_not_called()
            mock_update.assert_not_called()

        # Confirm settings on disk or in storage were never modified
        current_settings = llm.load_settings()
        self.assertEqual(current_settings["interests"], initial_interests)


class JsonReplyTest(unittest.TestCase):
    def test_json_reply_becomes_labelled_lines(self):
        reply = '```json\n{"Learn": ["Python", "AI agents"], "Level": "intermediate", "Skip": "crypto", "Goal": "not stated"}\n```'
        out = interests_ai.draft_interests("I want AI agents in Python, skip crypto", "", {}, lambda system, prompt: reply)
        self.assertEqual(out, "Learn: Python, AI agents\nLevel: intermediate\nSkip: crypto\nGoal: not stated")
