import json
import re
import os
import tempfile
import unittest

import llm
from ai_classifier import FALLBACK_CATEGORY, assign_categories, discover_categories
from llm import LLMError, parse_json


def fake(reply):
    calls = []
    def complete(system, prompt):
        calls.append(prompt)
        return reply(prompt) if callable(reply) else reply
    complete.calls = calls
    return complete


class ParseJsonTest(unittest.TestCase):
    def test_tolerates_fences_think_and_prose(self):
        self.assertEqual(parse_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(parse_json('<think>{"no": 1}</think>Sure! {"a": [1, 2]} hope that helps'), {"a": [1, 2]})
        self.assertEqual(parse_json('[{"i": 1}]'), [{"i": 1}])

    def test_rejects_non_json(self):
        with self.assertRaises(LLMError):
            parse_json("no json here")


class DiscoverTest(unittest.TestCase):
    VIDEOS = [{"video_id": str(i), "title": f"Video {i}", "channel": "C"} for i in range(5)]

    def test_presets_kept_and_cleaned(self):
        reply = json.dumps({"categories": [
            {"name": "cooking", "description": "Food", "keywords": ["Recipe", "pasta", " "]},
            {"name": "Rust Programming", "description": "Rust", "keywords": ["rust", "cargo"]},
            {"name": "General / Other", "keywords": []},
            {"name": "Rust programming", "keywords": []},   # duplicate, different case
        ]})
        cats = discover_categories(self.VIDEOS, fake(reply), presets=["Cooking", "Fitness"],
                                   taxonomy={"Cooking": ["air fryer"], "Old": ["x"]})
        by = {c["name"]: c for c in cats}
        self.assertEqual(set(by), {"Cooking", "Rust Programming", "Fitness"})
        self.assertTrue(by["Cooking"]["preset"] and by["Fitness"]["preset"])  # dropped preset re-added
        self.assertEqual(by["Cooking"]["keywords"], ["air fryer", "pasta", "recipe"])  # user's keywords kept
        self.assertFalse(by["Rust Programming"]["existing"])

    def test_samples_large_libraries(self):
        videos = [{"video_id": str(i), "title": f"T{i}", "channel": ""} for i in range(2000)]
        complete = fake('{"categories": []}')
        discover_categories(videos, complete)
        self.assertEqual(len(re.findall(r"^T\d+ \| ", complete.calls[0], re.M)), 600)

    def test_empty_library(self):
        with self.assertRaises(LLMError):
            discover_categories([], fake("{}"))


class AssignTest(unittest.TestCase):
    def test_batches_and_validates(self):
        videos = [{"video_id": f"v{i}", "title": f"T{i}", "channel": ""} for i in range(5)]
        def reply(prompt):
            if "1. T0" in prompt:
                return json.dumps({"items": [
                    {"i": 1, "category": "cooking", "confidence": 0.9},     # case-insensitive match
                    {"i": 2, "category": "Astronomy", "confidence": 0.8},   # unknown -> fallback
                    {"i": 3, "category": "Fitness", "confidence": 7},       # clamped
                    {"i": 9, "category": "Fitness", "confidence": 0.5},     # out of range: ignored
                ]})
            return "garbage"
        logs = []
        out = assign_categories(videos, ["Cooking", "Fitness"], fake(reply), batch_size=3, log=logs.append)
        got = {s["video_id"]: (s["category"], s["confidence"]) for s in out["suggestions"]}
        self.assertEqual(got, {"v0": ("Cooking", 0.9), "v1": (FALLBACK_CATEGORY, 0.8), "v2": ("Fitness", 1.0)})
        self.assertEqual(out["failed_batches"], 1)

    def test_all_batches_failing_raises(self):
        videos = [{"video_id": "a", "title": "T", "channel": ""}]
        with self.assertRaises(LLMError):
            assign_categories(videos, ["Cooking"], fake("nope"), log=lambda m: None)

    def test_needs_categories(self):
        with self.assertRaises(LLMError):
            assign_categories([], [FALLBACK_CATEGORY], fake("{}"))


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "settings.json")

    def test_keys_never_reach_the_browser(self):
        s = llm.load_settings(self.path)
        s["provider"] = "anthropic"
        s["providers"]["anthropic"]["api_key"] = "sk-secret"
        llm.save_settings(s, self.path)
        pub = llm.public_settings(llm.load_settings(self.path))
        self.assertNotIn("sk-secret", json.dumps(pub))
        self.assertEqual(pub["providers"]["anthropic"]["key_source"], "saved")
        self.assertTrue(pub["ready"])

    def test_configuration_rules(self):
        s = llm.load_settings(self.path)
        self.assertFalse(llm.is_configured(s))           # provider off
        s["provider"] = "gemini"
        self.assertFalse(llm.is_configured(s))           # needs a key
        s["provider"] = "ollama"
        self.assertFalse(llm.is_configured(s))           # needs a model
        s["providers"]["ollama"]["model"] = "llama3.1:8b"
        self.assertTrue(llm.is_configured(s))            # local: no key needed
        with self.assertRaises(LLMError):
            s["provider"] = "gemini"
            llm.make_completer(s)

    def test_env_key(self):
        s = llm.load_settings(self.path)
        s["provider"] = "gemini"
        os.environ["GEMINI_API_KEY"] = "from-env"
        try:
            self.assertTrue(llm.is_configured(s))
            self.assertEqual(llm.public_settings(s)["providers"]["gemini"]["key_source"], "env")
        finally:
            del os.environ["GEMINI_API_KEY"]
