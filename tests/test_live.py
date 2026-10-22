"""
Live checks against real cloud AI providers. Skipped unless YTKB_LIVE=1, and per provider unless its key is set.
Keys come only from environment variables (tests/__init__.py keeps them in memory for this file only); settings.json is
never read or written, and the settings used here are never saved.

    YTKB_LIVE=1 ANTHROPIC_API_KEY=... GEMINI_API_KEY=... .venv/bin/python -m unittest tests.test_live -v

Optional: YTKB_LIVE_ANTHROPIC_MODEL, YTKB_LIVE_GEMINI_MODEL (defaults: the cheapest listed models);
OPENAI_API_KEY with YTKB_LIVE_OPENAI_MODEL (and YTKB_LIVE_OPENAI_BASE, default OpenRouter) for open-weight hosts.
Each provider makes 4 small calls (a few thousand tokens, well under $0.01 on the default models).
"""
import json
import os
import unittest

import ai_classifier
import asker
import llm
import rater
from tests import LIVE_KEYS

LIVE = os.environ.get("YTKB_LIVE") == "1"
CHEAP = {"anthropic": "claude-haiku-5-5", "gemini": "gemini-2.5-flash-lite"}

VIDEOS = [
    {"video_id": "l1", "title": "Pasta carbonara in 10 minutes", "channel": "Home Kitchen", "duration_seconds": 600},
    {"video_id": "l2", "title": "LoRA and QLoRA explained: fine-tune LLMs on one GPU", "channel": "AI Lab", "duration_seconds": 900},
    {"video_id": "l3", "title": "Docker networking for beginners", "channel": "DevOps Daily", "duration_seconds": 1200},
]
PIECES = [
    {"id": 1, "video_id": "l2", "title": "LoRA and QLoRA explained", "kind": "transcript", "t": 254,
     "text": "LoRA freezes the base model and trains two small low-rank matrices, so only a tiny fraction of weights change."},
    {"id": 2, "video_id": "l3", "title": "Docker networking for beginners", "kind": "transcript", "t": 75,
     "text": "A bridge network lets containers on the same host talk to each other by name."},
    {"id": 3, "video_id": "l2", "title": "LoRA and QLoRA explained", "kind": "transcript", "t": 291,
     "text": "QLoRA quantizes the frozen base model to 4-bit before applying LoRA, so a 70B model fits on a single GPU."},
]


def configured():
    """(provider, settings) for every cloud provider whose key is in the environment."""
    out = []
    for name, p in llm.PROVIDERS.items():
        key = LIVE_KEYS.get(p.get("env") or "")
        if p.get("local") or not key:
            continue
        s = llm.default_settings()          # in memory only: never saved
        s["provider"] = name
        s["providers"][name]["api_key"] = key
        model = os.environ.get(f"YTKB_LIVE_{name.split('_')[0].upper()}_MODEL") or CHEAP.get(name) or p["model"]
        s["providers"][name]["model"] = model
        if name == "openai_compat":
            s["providers"][name]["base_url"] = os.environ.get("YTKB_LIVE_OPENAI_BASE") or p["base_url"]
        if model:
            out.append((name, s))
    return out


@unittest.skipUnless(LIVE, "live provider checks: set YTKB_LIVE=1 and an API key")
class LiveProviderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.providers = configured()
        if not cls.providers:
            raise unittest.SkipTest("no cloud API key in the environment")

    def each(self):
        for name, s in self.providers:
            with self.subTest(provider=name, model=s["providers"][name]["model"]):
                yield name, s

    def test_connection_and_json(self):
        for name, s in self.each():
            reply = llm.make_completer(s)("Reply as JSON only.", 'Return {"ok": true, "n": 3}')
            self.assertEqual(llm.parse_json(reply).get("n"), 3)

    def test_categories(self):
        for name, s in self.each():
            r = ai_classifier.assign_categories(VIDEOS, ["Cooking", "AI & Machine Learning", "DevOps"], llm.make_completer(s, "categorize"),
                                                log=lambda m: None)
            got = {x["video_id"]: x["category"] for x in r["suggestions"]}
            self.assertEqual(got, {"l1": "Cooking", "l2": "AI & Machine Learning", "l3": "DevOps"})

    def test_rating(self):
        for name, s in self.each():
            segs = [{"start": p["t"], "text": p["text"]} for p in PIECES if p["video_id"] == "l2"]
            r = rater.rate_video({**VIDEOS[1], "category": "AI & Machine Learning"}, segs,
                                 {**s, "interests": "Fine-tuning LLMs", "_categories": ["AI & Machine Learning"]}, llm.make_completer(s, "rate"))
            self.assertIn(r["verdict"], ("watch", "skim", "skip"))
            self.assertTrue(0 <= r["score"] <= 10 and r["reason"])

    def test_answer_with_citations(self):
        for name, s in self.each():
            r = asker.ask("How can I fine-tune a 70B model on one GPU?", settings=s, complete=llm.make_completer(s, "ask"),
                          retrieve_fn=lambda q, k, filters: {"mode": "hybrid", "pieces": PIECES}, sim_fn=None, save=False)
            self.assertTrue(r["cited"], r["answer"])
            self.assertIn(3, r["cited"], json.dumps(r, ensure_ascii=False)[:600])   # the QLoRA excerpt
            self.assertEqual(r["checked"]["dropped"], 0)


if __name__ == "__main__":
    unittest.main()
