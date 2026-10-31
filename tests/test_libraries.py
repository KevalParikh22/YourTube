"""Separate libraries: storage, per-library categories and settings, and the API."""
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import classifier
import dashboard
import llm
import rater
import storage
from tests.helpers import playlist, temp_db, video


class LibraryStorageTest(unittest.TestCase):
    def test_libraries_playlists_and_video_library(self):
        db = temp_db()
        self.assertEqual([l["name"] for l in storage.get_libraries(db_path=db)], [storage.DEFAULT_LIBRARY_NAME])
        health = storage.create_library("Health", db_path=db)
        with self.assertRaisesRegex(ValueError, "already exists"):
            storage.create_library(" health ".title(), db_path=db)
        storage.save_playlist(playlist("PLT", "Tech"), [video("t1"), video("both")], db_path=db)
        storage.save_playlist(playlist("PLH", "Fit"), [video("h1"), video("both")], db_path=db, library_id=health)
        lib = {v["video_id"]: v for v in storage.get_library(db_path=db)}
        self.assertEqual((lib["t1"]["library"], lib["h1"]["library"]), (1, health))
        self.assertEqual((lib["both"]["library"], lib["both"]["libraries"]), (1, [1, health]))   # first playlist wins
        storage.save_playlist(playlist("PLH", "Fit"), [video("h1")], db_path=db)                # a re-sync keeps its library
        self.assertEqual(storage.playlist_library("PLH", db_path=db), health)
        with self.assertRaisesRegex(ValueError, "Move this library"):
            storage.delete_library(health, db_path=db)
        storage.set_playlist_library("PLH", 1, db_path=db)
        storage.delete_library(health, db_path=db)
        with self.assertRaises(ValueError):
            storage.delete_library(1, db_path=db)
        storage.rename_library(1, "Tech", db_path=db)
        self.assertEqual(storage.get_libraries(db_path=db)[0]["name"], "Tech")

    def test_rename_and_release_limited_to_some_videos(self):
        db = temp_db()
        storage.save_playlist(playlist(), [video("a", category="Food"), video("b", category="Food")], db_path=db)
        self.assertEqual(storage.rename_category("Food", "Cooking", db_path=db, video_ids=["a"]), 1)
        cats = {v["video_id"]: v["category"] for v in storage.get_library(db_path=db)}
        self.assertEqual(cats, {"a": "Cooking", "b": "Food"})
        self.assertEqual(storage.release_category("Food", db_path=db, video_ids=["a"]), 0)


class LibrarySettingsTest(unittest.TestCase):
    def test_library_one_keeps_top_level_keys(self):
        s = llm.default_settings()
        s["interests"] = "AI agents"
        llm.set_library_settings(s, 2, interests="Fitness", presets=["Nutrition"])
        self.assertEqual(llm.library_settings(s, 1)["interests"], "AI agents")
        self.assertEqual(llm.library_settings(s, 2), {"presets": ["Nutrition"], "category_notes": {}, "proposal": None,
                                                      "interests": "Fitness"})
        self.assertEqual(s["libraries"]["2"]["interests"], "Fitness")
        self.assertEqual(llm.public_settings(s, 2)["interests"], "Fitness")
        self.assertEqual(llm.library_settings(s, 3)["interests"], "AI agents")   # no interests of its own: library 1's

    def test_taxonomy_per_library(self):
        self.assertEqual(classifier.load_taxonomy(library=77), {})                 # a new library starts empty
        classifier.save_taxonomy({"Fitness": ["workout"]}, library=77)
        self.assertEqual(classifier.load_taxonomy(library=77), {"Fitness": ["workout"]})
        self.assertNotEqual(classifier.load_taxonomy(), {"Fitness": ["workout"]})
        self.assertEqual(classifier.classify_video("Full body workout", "", {}), classifier.FALLBACK_CATEGORY)

    def test_rating_uses_the_videos_library_interests(self):
        s = {**llm.default_settings(), "interests": "AI agents"}
        llm.set_library_settings(s, 5, interests="Fitness and sleep")
        prompt, _ = rater.build_prompt({"title": "T", "duration_seconds": 60}, [], rater._context(s, 5))
        self.assertIn("Fitness and sleep", prompt)
        self.assertNotIn("AI agents", prompt)


class LibraryApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def call(self, path, body=None):
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"X-YTKB": "1", "Content-Type": "application/json"} if body is not None else {},
                                     method="GET" if body is None else "POST")
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read() or b"{}")

    def test_library_flow(self):
        code, r = self.call("/api/libraries", {"action": "create", "name": "Wellbeing"})
        self.assertEqual(code, 200)
        lib = r["id"]
        storage.save_playlist(playlist("PLW", "Wellbeing list"), [video("w1", "Morning workout routine")], library_id=lib)
        self.assertEqual(self.call("/api/taxonomy", {"taxonomy": {"Fitness": ["workout"]}, "library": lib})[0], 200)
        self.call("/api/reclassify", {"library": lib})
        code, st = self.call(f"/api/state?library={lib}")
        self.assertEqual((st["library"], st["taxonomy"]), (lib, {"Fitness": ["workout"]}))
        self.assertEqual(next(v for v in st["videos"] if v["video_id"] == "w1")["category"], "Fitness")
        self.assertNotIn("Fitness", self.call("/api/state")[1]["taxonomy"])        # library 1 is untouched
        self.call("/api/ai/settings", {"interests": "Sleep better", "library": lib})
        self.assertEqual(self.call(f"/api/state?library={lib}")[1]["ai"]["interests"], "Sleep better")
        self.assertEqual(self.call("/api/state?library=0")[1]["library"], 0)       # all libraries
        self.assertEqual(self.call("/api/taxonomy", {"taxonomy": {}, "library": 0})[0], 400)
        self.assertEqual(self.call("/api/libraries", {"action": "delete", "id": lib})[0], 400)   # not empty
        code, r = self.call("/api/playlists/library", {"id": "PLW", "library": 1})
        self.assertEqual(code, 200)
        moved = next(v for v in self.call("/api/state")[1]["videos"] if v["video_id"] == "w1")
        self.assertNotEqual(moved["category"], "Fitness")                          # re-sorted with library 1's categories
        self.assertEqual(self.call("/api/libraries", {"action": "delete", "id": lib})[0], 200)


if __name__ == "__main__":
    unittest.main()
