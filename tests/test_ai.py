import json
import io
import re
import os
import tempfile
import unittest
from unittest import mock

import llm
from ai_classifier import (
    FALLBACK_CATEGORY, _same_topic, assign_categories, discover_categories,
    split_category, run_split
)
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

    def test_list_fits_the_context_and_catch_alls_dropped(self):
        videos = [{"video_id": str(i), "title": f"A fairly long video title number {i}", "channel": "Some channel"} for i in range(317)]
        complete = fake('{"categories": [{"name": "Health"}, {"name": "None"}, {"name": "Misc"}]}')
        cats = discover_categories(videos, complete, max_chars=5000)
        shown = re.findall(r"^A fairly long video title number \d+ \| ", complete.calls[0], re.M)
        self.assertTrue(20 < len(shown) < 317 and sum(len(x) for x in shown) <= 5000 + len(shown) * 20)
        self.assertEqual([c["name"] for c in cats], ["Health"])

    def test_near_copies_merge_into_existing_categories(self):
        reply = json.dumps({"categories": [{"name": "Finance & Business Strategy", "keywords": ["mutual funds", "stocks"]},
                                           {"name": "AI & Automation Tools", "keywords": ["agents"]},
                                           {"name": "Productivity & Career Growth", "keywords": ["habits"]}]})
        cats = discover_categories(self.VIDEOS, fake(reply), presets=["Finance & Business"],
                                   taxonomy={"Finance & Business": ["stocks"], "Productivity & Career": ["career"]})
        by = {c["name"]: c for c in cats}
        self.assertEqual(set(by), {"Finance & Business", "AI", "Automation Tools", "Productivity & Career"})   # new: one topic each
        self.assertEqual(by["Finance & Business"]["added"], ["mutual funds"])        # merged as new keywords only
        self.assertEqual(by["Productivity & Career"]["keywords"], ["career", "habits"])
        self.assertEqual(by["Automation Tools"]["added"], [])                        # a genuinely new category

    def test_cut_off_reply_is_explained(self):
        with self.assertRaisesRegex(LLMError, "cut off"):
            discover_categories(self.VIDEOS, fake('{"categories": [{"name": "Health", "descr'))

    def test_empty_library(self):
        with self.assertRaises(LLMError):
            discover_categories([], fake("{}"))

    def test_run_discover_stopped_before_model_call(self):
        import ai_classifier
        with mock.patch("llm.load_settings", return_value={"provider": "ollama"}), \
             mock.patch("llm.for_library", return_value={}), \
             mock.patch("storage.get_library", return_value=[{"library": 1, "status": "new"}]), \
             mock.patch("llm.make_completer") as make_completer:
            self.assertFalse(ai_classifier.run_discover(library=1, should_stop=lambda: True))
            make_completer.assert_not_called()

    def test_run_discover_stopped_during_model_call_discards_proposal(self):
        import ai_classifier
        answers = iter([False, True])                      # not stopped before the call, stopped by the time it returns
        with mock.patch("llm.load_settings", return_value={"provider": "ollama"}), \
             mock.patch("llm.for_library", return_value={}), \
             mock.patch("llm.context_chars", return_value=20_000), \
             mock.patch("storage.get_library", return_value=[{"library": 1, "status": "new"}]), \
             mock.patch("llm.make_completer"), \
             mock.patch("ai_classifier.discover_categories", return_value=[{"name": "Health"}]), \
             mock.patch("llm.save_settings") as save:
            self.assertFalse(ai_classifier.run_discover(library=1, should_stop=lambda: next(answers)))
            save.assert_not_called()


class AssignTest(unittest.TestCase):
    def test_stops_before_first_batch(self):
        complete = fake('{"items": []}')
        out = assign_categories([{"video_id": "a", "title": "A"}], ["Cooking"], complete,
                               should_stop=lambda: True, log=lambda m: None)
        self.assertEqual(complete.calls, [])
        self.assertTrue(out["stopped"])
        self.assertEqual(out["suggestions"], [])

    def test_stops_after_second_batch(self):
        videos = [{"video_id": f"v{i}", "title": f"T{i}"} for i in range(11)]
        def reply(prompt):
            return '{"items": [{"i": 1, "category": "Cooking", "confidence": 1}]}'
        complete = fake(reply)
        out = assign_categories(videos, ["Cooking"], complete, batch_size=1,
                               should_stop=lambda: len(complete.calls) >= 2, log=lambda m: None)
        self.assertEqual(len(complete.calls), 2)
        self.assertEqual([s["video_id"] for s in out["suggestions"]], ["v0", "v1"])
        self.assertTrue(out["stopped"])

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

    def test_public_settings_has_media_tasks_helpers_and_providers(self):
        s = llm.load_settings(self.path)
        s["providers"]["groq"]["api_key"] = "g-secret"
        s["providers"]["openai"]["api_key"] = "o-secret"
        s["helpers"]["rate"] = "groq"
        pub = llm.public_settings(s)
        self.assertNotIn("g-secret", json.dumps(pub))
        self.assertNotIn("o-secret", json.dumps(pub))
        self.assertIn("groq", pub["providers"])
        self.assertIn("openai", pub["providers"])
        self.assertIn("media_tasks", pub)
        for t in ("transcribe", "slides", "embed"):
            self.assertIn(t, pub["media_tasks"])
            self.assertIn("label", pub["media_tasks"][t])
            self.assertIn("providers", pub["media_tasks"][t])
            self.assertIn("uses", pub["media_tasks"][t])
        self.assertIn("helpers", pub)
        for t in ("rate", "summarize", "transcribe", "slides"):
            self.assertIn(t, pub["helpers"])
        self.assertEqual(pub["helpers"]["rate"], "groq")

    def test_openai_compat_env_rename_keeps_saved_keys(self):
        self.assertEqual(llm.PROVIDERS["openai_compat"]["env"], "OPENAI_COMPAT_API_KEY")
        self.assertEqual(llm.PROVIDERS["openai"]["env"], "OPENAI_API_KEY")
        s = llm.load_settings(self.path)
        s["providers"]["openai_compat"]["api_key"] = "compat-saved-key"
        llm.save_settings(s, self.path)
        reloaded = llm.load_settings(self.path)
        self.assertEqual(reloaded["providers"]["openai_compat"]["api_key"], "compat-saved-key")
        os.environ["OPENAI_API_KEY"] = "openai-env-key"
        try:
            self.assertEqual(llm._api_key("openai_compat", reloaded["providers"]["openai_compat"]), "compat-saved-key")
            self.assertEqual(llm._api_key("openai", reloaded["providers"]["openai"]), "openai-env-key")
        finally:
            del os.environ["OPENAI_API_KEY"]


class VisionRequestTest(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "settings.json")

    def test_see_builds_provider_image_requests(self):
        fixtures = {
            "anthropic": {"content": [{"type": "text", "text": '{"content":true}'}]},
            "gemini": {"candidates": [{"content": {"parts": [{"text": '{"content":true}'}]}}]},
            "openai": {"choices": [{"message": {"content": '{"content":true}'}}]},
        }
        for provider, response in fixtures.items():
            with self.subTest(provider=provider):
                settings = llm.default_settings()
                settings["providers"][provider]["api_key"] = "test-key"
                settings["providers"][provider]["model"] = "vision-test"
                if provider == "openai":
                    settings["providers"][provider]["base_url"] = "https://api.example/v1"
                reply = mock.MagicMock()
                reply.__enter__.return_value = io.BytesIO(json.dumps(response).encode())
                with mock.patch("urllib.request.urlopen", return_value=reply) as opened:
                    self.assertEqual(llm.see(settings, provider, b"jpeg-bytes", "Read it"), '{"content":true}')
                req = opened.call_args.args[0]
                body = json.loads(req.data)
                encoded = "anBlZy1ieXRlcw=="
                if provider == "anthropic":
                    img = body["messages"][0]["content"][0]
                    self.assertEqual((img["type"], img["source"]["media_type"], img["source"]["data"]),
                                     ("image", "image/jpeg", encoded))
                elif provider == "gemini":
                    self.assertEqual(body["contents"][0]["parts"][0]["inline_data"],
                                     {"mime_type": "image/jpeg", "data": encoded})
                else:
                    content = body["messages"][0]["content"]
                    self.assertEqual(content[1]["image_url"]["url"], f"data:image/jpeg;base64,{encoded}")
                    self.assertEqual(body["response_format"], {"type": "json_object"})

    def test_media_provider_fallbacks(self):
        s = llm.load_settings(self.path)
        self.assertEqual(llm.media_provider(s, "transcribe"), "local")
        self.assertEqual(llm.media_provider(s, "unknown_task"), "local")

        # Not allowed provider falls back to local
        s["tasks"]["transcribe"] = "anthropic"
        self.assertEqual(llm.media_provider(s, "transcribe"), "local")
        s["tasks"]["embed"] = "groq"
        self.assertEqual(llm.media_provider(s, "embed"), "local")

        # Unconfigured provider (no key) falls back to local
        s["tasks"]["transcribe"] = "groq"
        self.assertEqual(llm.media_provider(s, "transcribe"), "local")

        # Configured provider succeeds
        s["providers"]["groq"]["api_key"] = "groq-key"
        self.assertEqual(llm.media_provider(s, "transcribe"), "groq")

        # Blocked by local_only falls back to local
        s["local_only"] = True
        self.assertEqual(llm.media_provider(s, "transcribe"), "local")

    def test_helper_for_rules(self):
        s = llm.load_settings(self.path)
        s["providers"]["ollama"]["model"] = "llama3.1:8b"
        s["providers"]["groq"]["api_key"] = "groq-key"
        s["providers"]["anthropic"]["api_key"] = "claude-key"
        s["providers"]["gemini"]["api_key"] = "gemini-key"

        # Local main + cloud helper works
        s["tasks"]["rate"] = "ollama"
        s["helpers"]["rate"] = "groq"
        self.assertEqual(llm.helper_for(s, "rate"), "groq")

        # Cloud main + local helper works
        s["tasks"]["rate"] = "anthropic"
        s["helpers"]["rate"] = "ollama"
        self.assertEqual(llm.helper_for(s, "rate"), "ollama")

        # Both local -> None
        s["tasks"]["rate"] = "ollama"
        s["helpers"]["rate"] = "lmstudio"
        self.assertIsNone(llm.helper_for(s, "rate"))

        # Both cloud -> None
        s["tasks"]["rate"] = "anthropic"
        s["helpers"]["rate"] = "gemini"
        self.assertIsNone(llm.helper_for(s, "rate"))

        # Same provider -> None
        s["helpers"]["rate"] = "anthropic"
        self.assertIsNone(llm.helper_for(s, "rate"))

        # Blocked by local_only -> None
        s["helpers"]["rate"] = "ollama"
        s["local_only"] = True
        self.assertIsNone(llm.helper_for(s, "rate"))
        s["local_only"] = False

        # Media task: local main + cloud helper
        s["tasks"]["transcribe"] = "local"
        s["helpers"]["transcribe"] = "groq"
        self.assertEqual(llm.helper_for(s, "transcribe"), "groq")

        # Media task: cloud main + local helper
        s["tasks"]["transcribe"] = "groq"
        s["helpers"]["transcribe"] = "local"
        self.assertEqual(llm.helper_for(s, "transcribe"), "local")

        # Media task: disallowed helper provider
        s["helpers"]["transcribe"] = "anthropic"
        self.assertIsNone(llm.helper_for(s, "transcribe"))

        # Unconfigured helper -> None
        s["providers"]["groq"]["api_key"] = ""
        s["tasks"]["transcribe"] = "local"
        s["helpers"]["transcribe"] = "groq"
        self.assertIsNone(llm.helper_for(s, "transcribe"))

        # Non-helper task -> None
        self.assertIsNone(llm.helper_for(s, "categorize"))


class ContextTest(unittest.TestCase):
    def test_prompt_carries_context(self):
        videos = [{"video_id": "a", "title": "Episode 12", "channel": "Show", "summary": "How to tune LoRA adapters"},
                  {"video_id": "b", "title": "Untitled", "channel": "", "description": "A recipe\n\nfor  pasta " + "x " * 400,
                   "excerpt": "Today we boil water"},
                  {"video_id": "c", "title": "Bare", "channel": "Ch"}]
        complete = fake('{"items": []}')
        assign_categories(videos, ["AI", "Cooking"], complete, log=lambda m: None)
        p = complete.calls[0]
        self.assertIn("1. Episode 12 | Show\n   Summary: How to tune LoRA adapters", p)
        self.assertIn("   Description: A recipe for pasta", p)
        self.assertIn("   Opening words: Today we boil water", p)
        self.assertIn("3. Bare | Ch\n", p)
        self.assertLess(p.count("x "), 160)   # description cut to ~300 characters

    def test_batches_fit_the_budget(self):
        import ai_classifier
        videos = [{"video_id": str(i), "title": f"T{i}", "channel": "", "excerpt": "word " * 200} for i in range(10)]
        batches = ai_classifier._pack(videos, max_items=50, max_chars=3000)
        self.assertEqual(sum(len(b) for b in batches), 10)
        self.assertTrue(all(len(b) <= 3 for b in batches))
        self.assertEqual([len(b) for b in ai_classifier._pack(videos, max_items=4, max_chars=10**6)], [4, 4, 2])

    def test_storage_context(self):
        import storage
        from tests.helpers import playlist, temp_db, video
        db = temp_db()
        storage.save_playlist(playlist(), [video("a"), video("b")], db_path=db)
        storage.save_details("a", {"status": "done", "description": "desc",
                                   "segments": [{"start": i, "text": "abcdefghi"} for i in range(200)]}, db_path=db)
        storage.save_rating("a", {"score": 5, "verdict": "skim", "tldr": "short"}, db_path=db)
        ctx = storage.ai_context(["a", "b"], excerpt_chars=50, db_path=db)
        self.assertEqual((ctx["a"]["description"], ctx["a"]["summary"], len(ctx["a"]["excerpt"])), ("desc", "short", 50))
        self.assertEqual(ctx["b"], {"description": "", "summary": "", "excerpt": ""})


class SameTopicTest(unittest.TestCase):
    KNOWN = ["AI & Machine Learning", "Productivity & Career", "Finance & Business", "Business, Tech Trends & Podcasts",
             "Software Engineering & Coding"]

    def test_near_copies_merge_into_the_closest_existing_category(self):
        for proposed, existing in [("Productivity and Career Growth", "Productivity & Career"),
                                   ("Finance and Business", "Finance & Business"),
                                   ("Business Strategy & Finance", "Finance & Business")]:
            self.assertEqual(_same_topic(proposed, self.KNOWN), existing, proposed)

    def test_new_topics_stay_new(self):
        for proposed in ("Cooking", "Woodworking Projects", "Travel Vlogs", "AI Agents", "AI Tools", "Career Growth"):
            self.assertIsNone(_same_topic(proposed, self.KNOWN), proposed)

    def test_one_topic_never_folds_into_a_combined_category(self):
        known = self.KNOWN + ["Health, Fitness & Wellness", "Philosophy & Self-Improvement", "Tech Trends & Podcasts"]
        for proposed in ("Fitness", "Health", "Coding", "Finance", "Business", "Podcasts", "Tech Trends", "Self-Improvement",
                         "Philosophy", "Mental Health"):
            self.assertIsNone(_same_topic(proposed, known), proposed)

    def test_discover_prompt_leads_with_interests_and_one_topic_per_category(self):
        seen = {}
        def complete(system, prompt):
            seen["prompt"] = prompt
            return json.dumps({"categories": [{"name": "Communication Skills", "description": "d", "keywords": ["speaking"]},
                                              {"name": "Fitness", "description": "d", "keywords": ["workout"]}]})
        cats = discover_categories([{"video_id": "v1", "title": "How to speak", "channel": "c"}], complete,
                                   presets=["Health, Fitness & Wellness"], interests="Learn: Communication skills, Physical Health")
        self.assertIn("Learn: Communication skills", seen["prompt"])
        self.assertIn("Never join two topics", seen["prompt"])
        self.assertEqual({c["name"] for c in cats}, {"Communication Skills", "Fitness", "Health, Fitness & Wellness"})


class OneTopicTest(unittest.TestCase):
    def test_combined_proposals_become_one_category_per_topic(self):
        reply = json.dumps({"categories": [
            {"name": "Fitness & Body Transformation", "description": "d", "keywords": ["workout", "body fat", "gym"]},
            {"name": "Health, Fitness & Wellness", "description": "kept: protected", "keywords": ["health"]}]})
        cats = discover_categories([{"video_id": "v", "title": "t", "channel": "c"}], fake(reply), presets=["Health, Fitness & Wellness"])
        names = {c["name"]: c["keywords"] for c in cats}
        self.assertEqual(set(names), {"Fitness", "Body Transformation", "Health, Fitness & Wellness"})
        self.assertEqual(names["Body Transformation"], ["body fat"])           # the keywords that mention it

    def test_splitting_a_combined_category_gives_its_topics(self):
        reply = json.dumps({"categories": [
            {"name": "Fitness & Body Transformation", "description": "Training", "keywords": ["workout", "gym"]},
            {"name": "Home Remedies", "description": "Natural cures", "keywords": ["remedy"]}]})
        videos = [{"video_id": f"h{i}", "title": f"Video {i}", "channel": "c"} for i in range(8)]
        cats = split_category("Health, Fitness & Wellness", videos, ["Philosophy & Self-Improvement"], fake(reply), max_new=5)
        self.assertEqual([c["name"] for c in cats][:3], ["Health", "Fitness", "Wellness"])
        self.assertEqual(next(c for c in cats if c["name"] == "Fitness")["keywords"], ["gym", "workout"])
        self.assertIn("Home Remedies", [c["name"] for c in cats])


class SplitTest(unittest.TestCase):
    VIDEOS = [{"video_id": f"v{i}", "title": f"Video {i}", "channel": "Channel"} for i in range(10)]

    def test_split_category_validation_and_deduplication(self):
        reply = json.dumps({"categories": [
            {"name": "AI Agents", "description": "Autonomous agents", "keywords": ["agent", "llm", "Agent", " "]},
            {"name": "AI Tools", "description": "Productivity tools with AI", "keywords": ["tool", "PROMPT", "workflow"]},
            {"name": "Productivity & Career", "description": "Old name duplicate", "keywords": ["career"]},
            {"name": "Software Engineering & Coding", "description": "Other existing category", "keywords": ["code"]},
            {"name": "AI Agents", "description": "Self duplicate", "keywords": ["agent"]},
            {"name": "General / Other", "description": "Catch all", "keywords": []},
            {"name": "Time Management", "description": "Focus and deep work", "keywords": ["pomodoro", "focus"]},
            {"name": "Goal Setting", "description": "OKRs and planning", "keywords": ["goals"]},
            {"name": "Remote Work", "description": "Working from home", "keywords": ["remote"]},
            {"name": "Job Hunting", "description": "Resumes and interviews", "keywords": ["resume"]},
        ]})
        cats = split_category("Self-Improvement", self.VIDEOS, ["Software Engineering & Coding", "Productivity & Career"],
                              fake(reply), max_new=5)
        self.assertEqual(len(cats), 5)
        names = [c["name"] for c in cats]
        self.assertEqual(names, ["AI Agents", "AI Tools", "Time Management", "Goal Setting", "Remote Work"])
        agents = cats[0]
        self.assertEqual(agents["keywords"], ["agent", "llm"])
        tools = cats[1]
        self.assertEqual(tools["keywords"], ["prompt", "tool", "workflow"])

    def test_split_keeps_narrower_parts_of_the_parent_but_not_its_name(self):
        reply = json.dumps({"categories": [
            {"name": "Productivity and Career", "description": "the parent again", "keywords": ["x"]},
            {"name": "Career", "description": "jobs and growth", "keywords": ["career"]},
            {"name": "Productivity", "description": "getting things done", "keywords": ["focus"]},
        ]})
        cats = split_category("Productivity & Career", self.VIDEOS, [], fake(reply))
        self.assertEqual([c["name"] for c in cats], ["Productivity", "Career"])   # the topics of the name, in its order
        self.assertEqual(cats[1]["description"], "jobs and growth")

    def test_split_category_requires_at_least_two_valid_categories(self):
        reply = json.dumps({"categories": [
            {"name": "AI Agents", "description": "Autonomous agents", "keywords": ["agent"]},
            {"name": "Productivity", "description": "Old duplicate", "keywords": ["career"]},
        ]})
        with self.assertRaises(LLMError) as cm:
            split_category("Productivity", self.VIDEOS, [], fake(reply))
        self.assertIn("could not find distinct topics", str(cm.exception))

    def test_split_category_empty_videos_raises(self):
        with self.assertRaises(LLMError):
            split_category("Productivity & Career", [], [], fake('{"categories": []}'))

    def test_run_split_too_few_videos(self):
        with mock.patch("storage.get_library", return_value=[{"video_id": "v1", "category": "Cooking", "library": 1}]), \
             mock.patch("storage.video_libraries", return_value={"v1": 1}):
            self.assertFalse(run_split("Cooking", library=1))

    def test_run_split_success_and_stored_in_settings(self):
        vids = [{"video_id": f"v{i}", "category": "Work", "library": 1, "title": f"Work {i}", "channel": "C"} for i in range(7)]
        reply = json.dumps({"categories": [
            {"name": "Focus & Flow", "description": "Focus", "keywords": ["focus"]},
            {"name": "Office Politics", "description": "Navigating office", "keywords": ["office"]},
        ]})
        with mock.patch("storage.get_library", return_value=vids), \
             mock.patch("storage.video_libraries", return_value={v["video_id"]: 1 for v in vids}), \
             mock.patch("classifier.load_taxonomy", return_value={"Work": []}), \
             mock.patch("llm.make_completer", return_value=fake(reply)):
            ok = run_split("Work", library=1)
            self.assertTrue(ok)
            s = llm.load_settings()
            split = llm.library_settings(s, 1)["split"]
            self.assertIsNotNone(split)
            self.assertEqual(split["category"], "Work")
            self.assertEqual([c["name"] for c in split["categories"]], ["Focus", "Flow", "Office Politics"])   # one topic each
            self.assertIn("created_at", split)

    def test_category_scope_and_include_locked(self):
        import storage
        from tests.helpers import playlist, temp_db, video
        db = temp_db()
        storage.save_playlist(playlist(), [
            video("v1", "Task 1", category="Work"),
            video("v2", "Task 2", category="Work"),
            video("v3", "Task 3", category="Work"),
            video("v4", "Other 4", category="Cooking"),
        ], db_path=db)
        with storage.get_connection(db) as conn:
            conn.execute("UPDATE videos SET category_locked = 1 WHERE video_id = 'v2'")
            conn.commit()
        storage.save_suggestions([{"video_id": "v3", "category": "Deep Work"}], db_path=db, include_locked=True)

        vids = storage.videos_for_ai("category:Work", db_path=db)
        vid_ids = {v["video_id"] for v in vids}
        self.assertEqual(vid_ids, {"v1", "v2"})

        stored = storage.save_suggestions([{"video_id": "v2", "category": "Office Politics"}], db_path=db, include_locked=False)
        self.assertEqual(stored, 0)

        stored = storage.save_suggestions([{"video_id": "v2", "category": "Office Politics"}], db_path=db, include_locked=True)
        self.assertEqual(stored, 1)
