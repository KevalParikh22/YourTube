import os
import tempfile
import unittest

from classifier import FALLBACK_CATEGORY, classify_playlist_videos, classify_video, load_taxonomy, save_taxonomy

TAX = {"Cooking": ["recipe", "pasta", "air fryer"], "Fitness": ["workout", "yoga"]}


class ClassifierTest(unittest.TestCase):
    def test_single_word_match(self):
        self.assertEqual(classify_video("Easy pasta in 10 minutes", "", TAX), "Cooking")

    def test_phrase_outweighs_single_word(self):
        # "air fryer" (2) beats "workout" (1)
        self.assertEqual(classify_video("Air fryer workout snacks", "", TAX), "Cooking")

    def test_channel_counts(self):
        self.assertEqual(classify_video("Morning routine", "Yoga with Ana", TAX), "Fitness")

    def test_word_boundaries(self):
        # "yoga" must not match inside another word
        self.assertEqual(classify_video("Yogart review", "", TAX), FALLBACK_CATEGORY)

    def test_no_match_falls_back(self):
        self.assertEqual(classify_video("Something unrelated", "", TAX), FALLBACK_CATEGORY)

    def test_existing_category_is_kept(self):
        vids = [{"title": "pasta", "channel": "", "category": "Mine"}, {"title": "pasta", "channel": "", "category": "Uncategorized"}]
        classify_playlist_videos(vids, TAX)
        self.assertEqual([v["category"] for v in vids], ["Mine", "Cooking"])

    def test_taxonomy_roundtrip_and_empty_start(self):
        path = os.path.join(tempfile.mkdtemp(), "tax.json")
        self.assertEqual(load_taxonomy(path), {})   # no built-in categories: the user's interests lead
        save_taxonomy(TAX, path)
        self.assertEqual(load_taxonomy(path), TAX)
