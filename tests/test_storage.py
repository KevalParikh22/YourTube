import sqlite3
import unittest

import storage
from tests.helpers import playlist, temp_db, video


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.db = temp_db()

    def save(self, videos, pid="PL1"):
        return storage.save_playlist(playlist(pid), videos, db_path=self.db)

    def lib(self):
        return {v["video_id"]: v for v in storage.get_library(db_path=self.db)}

    def test_add_then_resync_counts(self):
        self.assertEqual(self.save([video("a"), video("b")]), {"added": 2, "updated": 0, "removed": 0})
        self.assertEqual(self.save([video("a"), video("b"), video("c")]), {"added": 1, "updated": 2, "removed": 0})

    def test_resync_keeps_status_notes_and_locked_category(self):
        self.save([video("a", category="Auto"), video("b", category="Auto")])
        storage.update_videos(["a"], status="completed", notes="great", category="Mine", db_path=self.db)
        self.save([video("a", category="Auto2"), video("b", category="Auto2")])
        lib = self.lib()
        self.assertEqual((lib["a"]["status"], lib["a"]["notes"], lib["a"]["category"]), ("completed", "great", "Mine"))
        self.assertEqual(lib["a"]["category_source"], "manual")
        self.assertEqual(lib["b"]["category"], "Auto2")  # unlocked follows the classifier

    def test_missing_video_is_flagged_not_deleted(self):
        self.save([video("a"), video("b")])
        self.assertEqual(self.save([video("a")])["removed"], 1)
        lib = self.lib()
        self.assertIn("b", lib)
        self.assertFalse(lib["b"]["in_source"])
        self.assertEqual(lib["b"]["sources"][0]["removed"], 1)
        self.save([video("a"), video("b")])  # comes back
        self.assertTrue(self.lib()["b"]["in_source"])

    def test_empty_extraction_never_flags_removals(self):
        self.save([video("a")])
        self.assertEqual(self.save([])["removed"], 0)
        self.assertTrue(self.lib()["a"]["in_source"])

    def test_video_in_two_playlists_is_one_row(self):
        self.save([video("a")], "PL1")
        self.save([video("a")], "PL2")
        lib = self.lib()
        self.assertEqual(len(lib), 1)
        self.assertEqual({s["id"] for s in lib["a"]["sources"]}, {"PL1", "PL2"})

    def test_skipped_status(self):
        self.save([video("a")])
        storage.update_videos(["a"], status="skipped", db_path=self.db)
        self.assertEqual(self.lib()["a"]["status"], "skipped")
        self.save([video("a")])  # re-sync keeps the decision
        self.assertEqual(self.lib()["a"]["status"], "skipped")

    def test_rebuilds_outdated_status_check(self):
        conn = sqlite3.connect(self.db)
        conn.executescript("""
            CREATE TABLE playlists (id TEXT PRIMARY KEY, title TEXT NOT NULL, original_url TEXT,
                total_videos INTEGER DEFAULT 0, total_duration_seconds INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE videos (video_id TEXT PRIMARY KEY, playlist_id TEXT, title TEXT NOT NULL, channel TEXT,
                duration_seconds INTEGER DEFAULT 0, url TEXT NOT NULL, category TEXT DEFAULT 'Uncategorized',
                status TEXT CHECK(status IN ('unwatched', 'in_progress', 'completed')) DEFAULT 'unwatched',
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, category_locked INTEGER NOT NULL DEFAULT 0,
                notes TEXT DEFAULT '', status_updated_at TIMESTAMP,
                category_source TEXT NOT NULL DEFAULT 'keywords');
            CREATE TABLE playlist_items (playlist_id TEXT NOT NULL, video_id TEXT NOT NULL, position INTEGER DEFAULT 0,
                removed INTEGER NOT NULL DEFAULT 0, first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(playlist_id, video_id));
            INSERT INTO playlists (id, title) VALUES ('PL1', 'Old');
            INSERT INTO videos (video_id, playlist_id, title, url, status, notes, category, category_locked, category_source)
                VALUES ('a', 'PL1', 'Old video', 'u', 'completed', 'my notes', 'Mine', 1, 'manual');
            INSERT INTO playlist_items (playlist_id, video_id, position) VALUES ('PL1', 'a', 3);
        """)
        conn.commit()
        conn.close()
        storage.init_db(self.db)
        a = self.lib()["a"]
        self.assertEqual((a["status"], a["notes"], a["category"], a["category_locked"], a["category_source"]),
                         ("completed", "my notes", "Mine", 1, "manual"))
        self.assertEqual(a["sources"], [{"id": "PL1", "position": 3, "removed": 0}])
        storage.update_videos(["a"], status="skipped", db_path=self.db)
        self.assertEqual(self.lib()["a"]["status"], "skipped")
        storage.init_db(self.db)  # second run is a no-op
        self.assertEqual(self.lib()["a"]["status"], "skipped")

    def test_invalid_status_rejected(self):
        self.save([video("a")])
        with self.assertRaises(ValueError):
            storage.update_videos(["a"], status="done", db_path=self.db)

    def test_reclassify_skips_locked_and_reset_unlocks(self):
        self.save([video("a", category="X"), video("b", category="X")])
        storage.update_videos(["a"], category="Mine", db_path=self.db)
        changed = storage.reclassify_videos(lambda t, c: "Y", db_path=self.db)
        self.assertEqual(changed, 1)
        self.assertEqual(self.lib()["a"]["category"], "Mine")
        storage.reclassify_videos(lambda t, c: "Y", ["a"], unlock=True, db_path=self.db)
        a = self.lib()["a"]
        self.assertEqual((a["category"], a["category_locked"], a["category_source"]), ("Y", 0, "keywords"))

    def test_release_category_unlocks(self):
        self.save([video("a")])
        storage.update_videos(["a"], category="Gone", db_path=self.db)
        self.assertEqual(storage.release_category("Gone", db_path=self.db), 1)
        a = self.lib()["a"]
        self.assertEqual((a["category"], a["category_locked"]), (storage.FALLBACK_CATEGORY, 0))

    def test_migrates_legacy_database(self):
        conn = sqlite3.connect(self.db)
        conn.executescript("""
            CREATE TABLE playlists (id TEXT PRIMARY KEY, title TEXT NOT NULL, original_url TEXT,
                total_videos INTEGER DEFAULT 0, total_duration_seconds INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE videos (video_id TEXT PRIMARY KEY, playlist_id TEXT, title TEXT NOT NULL, channel TEXT,
                duration_seconds INTEGER DEFAULT 0, url TEXT NOT NULL, category TEXT DEFAULT 'Uncategorized',
                status TEXT DEFAULT 'unwatched', added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            INSERT INTO playlists (id, title) VALUES ('PL1', 'Old');
            INSERT INTO videos (video_id, playlist_id, title, url, status) VALUES ('a', 'PL1', 'Old video', 'u', 'completed');
        """)
        conn.commit()
        conn.close()
        storage.init_db(self.db)
        a = self.lib()["a"]
        self.assertEqual(a["status"], "completed")
        self.assertEqual(a["category_source"], "keywords")
        self.assertEqual(a["sources"][0]["id"], "PL1")
        self.assertIsNone(a["suggestion"])


class SuggestionTest(unittest.TestCase):
    def setUp(self):
        self.db = temp_db()
        fb = storage.FALLBACK_CATEGORY
        storage.save_playlist(playlist(), [video("a", category=fb), video("b", category=fb),
                                           video("c", category="Cooking"), video("d", category=fb)], db_path=self.db)
        storage.update_videos(["d"], category="Mine", db_path=self.db)

    def lib(self):
        return {v["video_id"]: v for v in storage.get_library(db_path=self.db)}

    def test_scopes_skip_locked(self):
        ids = lambda scope: sorted(v["video_id"] for v in storage.videos_for_ai(scope, db_path=self.db))
        self.assertEqual(ids("unsorted"), ["a", "b"])
        self.assertEqual(ids("unlocked"), ["a", "b", "c"])
        with self.assertRaises(ValueError):
            storage.videos_for_ai("everything", db_path=self.db)

    def test_suggestions_wait_for_review(self):
        n = storage.save_suggestions([
            {"video_id": "a", "category": "Cooking", "confidence": 0.9},
            {"video_id": "b", "category": "Fitness", "confidence": 0.5},
            {"video_id": "c", "category": "Cooking", "confidence": 0.9},   # same as current: not stored
            {"video_id": "d", "category": "Cooking", "confidence": 0.9},   # locked: not stored
        ], model="m", db_path=self.db)
        self.assertEqual(n, 2)
        lib = self.lib()
        self.assertEqual(lib["a"]["category"], storage.FALLBACK_CATEGORY)  # unchanged until accepted
        self.assertEqual(lib["a"]["suggestion"], {"category": "Cooking", "confidence": 0.9})
        self.assertIsNone(lib["d"]["suggestion"])
        # pending suggestions are not offered again
        self.assertEqual(storage.videos_for_ai("unsorted", db_path=self.db), [])

    def test_accept_by_confidence_then_dismiss(self):
        storage.save_suggestions([{"video_id": "a", "category": "Cooking", "confidence": 0.9},
                                  {"video_id": "b", "category": "Fitness", "confidence": 0.5}], db_path=self.db)
        self.assertEqual(storage.accept_suggestions(min_confidence=0.8, db_path=self.db), 1)
        a = self.lib()["a"]
        self.assertEqual((a["category"], a["category_locked"], a["category_source"], a["suggestion"]), ("Cooking", 1, "ai", None))
        # accepted AI categories survive a re-sync like manual ones
        storage.save_playlist(playlist(), [video("a", category="Other"), video("b"), video("c"), video("d")], db_path=self.db)
        self.assertEqual(self.lib()["a"]["category"], "Cooking")
        self.assertEqual(storage.dismiss_suggestions(["b"], db_path=self.db), 1)
        self.assertIsNone(self.lib()["b"]["suggestion"])

    def test_manual_choice_clears_suggestion(self):
        storage.save_suggestions([{"video_id": "a", "category": "Cooking", "confidence": 0.9}], db_path=self.db)
        storage.update_videos(["a"], category="Mine", db_path=self.db)
        self.assertIsNone(self.lib()["a"]["suggestion"])
        self.assertEqual(storage.accept_suggestions(db_path=self.db), 0)

    def test_rename_and_delete_follow_suggestions(self):
        storage.save_suggestions([{"video_id": "a", "category": "Cooking", "confidence": 0.9}], db_path=self.db)
        storage.rename_category("Cooking", "Food", db_path=self.db)
        self.assertEqual(self.lib()["a"]["suggestion"]["category"], "Food")
        storage.release_category("Food", db_path=self.db)
        self.assertIsNone(self.lib()["a"]["suggestion"])
