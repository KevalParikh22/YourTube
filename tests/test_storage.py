import contextlib
import io
import sqlite3
import unittest
from unittest import mock

import embedder
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

    def test_playlist_access_is_saved_and_old_callers_do_not_clear_it(self):
        storage.save_playlist({**playlist("PL-access"), "access": "browser:chrome"}, [video("a")], db_path=self.db)
        storage.save_playlist(playlist("PL-access"), [video("a")], db_path=self.db)
        self.assertEqual(storage.get_playlist("PL-access", db_path=self.db)["access"], "browser:chrome")

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
        self.assertEqual(a["sources"], [{"id": "PL1", "position": 3, "removed": 0, "library": 1}])   # old playlists join library 1
        self.assertIn("access", storage.get_playlist("PL1", db_path=self.db))
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

    def test_no_match_is_not_offered_again_until_categories_change(self):
        before = {v["video_id"] for v in storage.videos_for_ai("unsorted", db_path=self.db)}
        self.assertIn("a", before)
        key = storage.taxonomy_key(["Cooking", "Fitness"])
        storage.mark_no_match(["a"], key, db_path=self.db)
        same = {v["video_id"] for v in storage.videos_for_ai("unsorted", db_path=self.db, no_match_key=key)}
        self.assertEqual(same, before - {"a"})
        changed = storage.taxonomy_key(["Cooking", "Fitness", "Woodworking"])
        self.assertIn("a", {v["video_id"] for v in storage.videos_for_ai("unsorted", db_path=self.db, no_match_key=changed)})
        self.assertEqual(storage.taxonomy_key(["Fitness", "Cooking"]), key)   # order doesn't matter

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


class SearchTest(unittest.TestCase):
    def setUp(self):
        self.db = temp_db()
        storage.save_playlist(playlist(), [video("a", "Graph RAG with Neo4j", "Coder"), video("b", "Cooking pasta", "Chef")], db_path=self.db)
        storage.save_details("a", {"status": "done", "description": "Build a knowledge graph", "chapters": [{"start": 300, "title": "Docker setup"}],
                                   "segments": [{"start": 0, "text": "hello"}, {"start": 10, "text": "we install kubernetes"},
                                                {"start": 95, "text": "now the यह मशीन लर्निंग part"}]}, db_path=self.db)

    def find(self, q, **kw):
        return storage.search(q, db_path=self.db, **kw)

    def test_phrase_first_and_short_words(self):
        storage.save_playlist(playlist("PLP"), [video("p1", "Saying no to people", "X"), video("p2", "Stop saying sorry, there is no need", "Y")],
                              db_path=self.db)
        r = self.find("saying no")["results"]
        self.assertEqual([(x["video_id"], x["exact"]) for x in r], [("p1", True), ("p2", False)])
        self.assertEqual([x["video_id"] for x in self.find('"saying" no')["results"]][:1], ["p1"])   # quotes: words, no phrase rule

    def test_finds_each_kind_with_time(self):
        r = self.find("kube")["results"]
        self.assertEqual([x["video_id"] for x in r], ["a"])
        hit = r[0]["hits"][0]
        self.assertEqual((hit["kind"], hit["t"]), ("transcript", 0))     # first 30 s block starts at 0
        self.assertIn("\x02kube\x03", hit["snippet"].lower())
        self.assertEqual(self.find("docker")["results"][0]["hits"][0]["t"], 300)
        self.assertEqual(self.find("लर्निंग")["results"][0]["hits"][0]["t"], 95)
        self.assertEqual(self.find("pasta")["results"][0]["video_id"], "b")
        self.assertEqual(self.find("knowledge")["results"][0]["hits"][0]["kind"], "description")

    def test_notes_summary_and_updates(self):
        self.assertEqual(self.find("remember")["results"], [])
        storage.update_videos(["b"], notes="remember the sauce", db_path=self.db)
        self.assertEqual(self.find("remember")["results"][0]["hits"][0]["kind"], "notes")
        storage.update_videos(["b"], notes="", db_path=self.db)
        self.assertEqual(self.find("remember")["results"], [])
        storage.save_rating("a", {"score": 8, "verdict": "watch", "tldr": "Explains graphs", "points": [{"t": 42, "text": "Cypher queries"}]}, db_path=self.db)
        hit = self.find("cypher")["results"][0]["hits"][0]
        self.assertEqual((hit["kind"], hit["t"]), ("summary", 42))
        storage.save_details("a", {"status": "done", "segments": [{"start": 5, "text": "only this now"}]}, db_path=self.db)
        self.assertEqual(self.find("kubernetes")["results"], [])         # old transcript left the index

    def test_crop(self):
        text = ("word " * 100) + "\x02match\x03" + (" tail" * 100)
        out = storage._crop(text, width=60)
        self.assertTrue(out.startswith("…") and out.endswith("…"))
        self.assertIn("\x02match\x03", out)
        self.assertLessEqual(len(out), 62)
        self.assertEqual(storage._crop("short \x02hit\x03"), "short \x02hit\x03")
        self.assertEqual(storage._crop("x" * 300 + " \x02hi" + "y" * 300, width=50).count("\x03"), 1)

    def test_query_rules(self):
        self.assertTrue(self.find("ab")["short"])
        self.assertEqual(self.find('"graph rag"')["results"][0]["video_id"], "a")
        self.assertEqual(self.find("graph pasta")["results"], [])         # all terms required
        self.assertEqual(self.find('neo4j" OR "x')["results"][0]["video_id"], "a")  # quotes can't break the query
        self.assertEqual([x["video_id"] for x in self.find("neo4j", kinds=["transcript"])["results"]], [])

    def test_existing_db_gets_index(self):
        import sqlite3
        conn = sqlite3.connect(self.db)
        conn.executescript("DROP TABLE search_index; DROP TABLE search_docs;")
        conn.close()
        storage.init_db(self.db)
        self.assertEqual(self.find("pasta")["results"][0]["video_id"], "b")


class SingleVideoTest(unittest.TestCase):
    def test_videos_added_one_by_one_are_never_flagged_removed(self):
        db = temp_db()
        storage.save_playlist(storage.SINGLE_SOURCE, [video("s1", "First", duration=100)], db_path=db, partial=True)
        counts = storage.save_playlist(storage.SINGLE_SOURCE, [video("s2", "Second", duration=50)], db_path=db, partial=True)
        self.assertEqual(counts["removed"], 0)
        lib = {v["video_id"]: v for v in storage.get_library(db_path=db)}
        self.assertTrue(lib["s1"]["in_source"] and lib["s2"]["in_source"])
        pl = next(p for p in storage.get_playlists(db_path=db) if p["id"] == "_single")
        self.assertEqual((pl["active_videos"], pl["duration_seconds"], pl["removed_videos"]), (2, 150, 0))


class IndexVersionTest(unittest.TestCase):
    def test_changes_when_text_is_replaced_with_the_same_number_of_pieces(self):
        db = temp_db()
        storage.save_playlist(playlist(), [video("iv", "Plain talk")], db_path=db)
        storage.save_details("iv", {"status": "done", "segments": [{"start": 0, "text": "this slide shows"}, {"start": 40, "text": "this slide again"}]}, db_path=db)
        before = storage.index_version(db)
        self.assertIn("iv", storage.visual_hints(db_path=db))
        storage.save_details("iv", {"status": "done", "segments": [{"start": 0, "text": "plain talk"}, {"start": 40, "text": "more plain talk"}]}, db_path=db)
        self.assertNotEqual(storage.index_version(db), before)            # row ids were reused; the counter still moved
        self.assertNotIn("iv", storage.visual_hints(db_path=db))          # so the cached phrase scan was refreshed


class ArchivePlaylistTest(unittest.TestCase):
    def setUp(self):
        self.db = temp_db()
        storage.save_playlist(playlist("DONE"), [video("solo", "Lonely talk"), video("both", "Shared talk")], db_path=self.db)
        storage.save_playlist(playlist("OPEN"), [video("both", "Shared talk"), video("other", "Other talk")], db_path=self.db)
        for vid in ("solo", "both"):
            storage.save_details(vid, {"status": "done", "description": f"about {vid}",
                                       "segments": [{"start": 0, "text": f"spoken words of {vid} " * 400}]}, db_path=self.db)
            storage.save_rating(vid, {"score": 7, "verdict": "watch", "reason": "good"}, db_path=self.db)
            storage.save_summary(vid, {"tldr": f"summary of {vid}"}, db_path=self.db)
        storage.save_embeddings("m", [(h, 256, b"\0" * 1024) for (h,) in sqlite3.connect(self.db).execute("SELECT hash FROM search_docs")],
                                db_path=self.db)
        storage.update_videos(["solo"], status="completed", notes="my thoughts", db_path=self.db)

    def rows(self, table, vid):
        with sqlite3.connect(self.db) as c:
            return c.execute(f"SELECT COUNT(*) FROM {table} WHERE video_id = ?", (vid,)).fetchone()[0]

    def test_refused_until_every_video_is_done(self):
        self.assertFalse(storage.archive_plan("DONE", db_path=self.db)["ready"])
        with self.assertRaises(ValueError):
            storage.archive_playlist("DONE", db_path=self.db)
        self.assertEqual(self.rows("transcript_segments", "solo"), 1)

    def test_frees_heavy_data_keeps_what_matters_and_shared_videos(self):
        storage.update_videos(["both"], status="skipped", db_path=self.db)
        plan = storage.archive_plan("DONE", db_path=self.db)
        self.assertEqual((plan["ready"], plan["videos"], plan["shared"]), (True, 1, 1))
        self.assertGreater(plan["frees_bytes"], 0)
        storage.archive_playlist("DONE", db_path=self.db)
        for table in ("transcript_segments", "media_jobs"):
            self.assertEqual(self.rows(table, "solo"), 0, table)
        for table in ("video_details", "video_ratings", "video_summaries"):
            self.assertEqual(self.rows(table, "solo"), 1, table)
        self.assertEqual(storage.search("spoken words of solo", db_path=self.db)["results"], [])
        self.assertEqual([r["video_id"] for r in storage.search("my thoughts", db_path=self.db)["results"]], ["solo"])
        self.assertEqual(self.rows("transcript_segments", "both"), 1)   # still in the unfinished OPEN playlist
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM embedding_cache WHERE hash NOT IN (SELECT hash FROM search_docs)").fetchone()[0], 0)
        archived = {p["id"]: p["archived_at"] for p in storage.get_playlists(db_path=self.db)}
        self.assertTrue(archived["DONE"])
        self.assertIsNone(archived["OPEN"])
        self.assertGreater(storage.vacuum(self.db), 0)

    def test_archived_videos_are_not_fetched_again_until_unwatched(self):
        storage.update_videos(["both"], status="completed", db_path=self.db)
        storage.archive_playlist("DONE", db_path=self.db)
        self.assertNotIn("solo", [v["video_id"] for v in storage.videos_needing_details(db_path=self.db)])
        self.assertNotIn("solo", [v["video_id"] for v in storage.videos_needing_asr(db_path=self.db)])
        storage.update_videos(["solo"], status="unwatched", db_path=self.db)
        self.assertIn("solo", [v["video_id"] for v in storage.videos_needing_details(db_path=self.db)])
        self.assertEqual(self.rows("video_summaries", "solo"), 1)


class RemovePlaylistTest(unittest.TestCase):
    def setUp(self):
        self.db = temp_db()
        storage.save_playlist(playlist("GONE"), [video("solo", "Lonely talk"), video("noted", "Noted talk"), video("both", "Shared talk")],
                              db_path=self.db)
        storage.save_playlist(playlist("STAY"), [video("both", "Shared talk"), video("other", "Other talk")], db_path=self.db)
        storage.update_videos(["noted"], status="completed", notes="my noted thoughts", db_path=self.db)
        for vid in ("solo", "noted", "both"):
            storage.save_details(vid, {"status": "done", "description": f"about {vid}",
                                       "segments": [{"start": 0, "text": f"spoken words of {vid}"}]}, db_path=self.db)
            storage.save_rating(vid, {"score": 7, "verdict": "watch", "reason": "good"}, db_path=self.db)
            storage.save_summary(vid, {"tldr": f"summary of {vid}"}, db_path=self.db)
            storage.save_parts(vid, [{"start": 0, "end": 60, "title": "t"}], db_path=self.db)
        storage.save_embeddings("m", [(h, 1, b"\0\0\0\0") for (h,) in sqlite3.connect(self.db).execute("SELECT hash FROM search_docs")],
                                db_path=self.db)

    def rows(self, table, vid):
        with sqlite3.connect(self.db) as c:
            return c.execute(f"SELECT COUNT(*) FROM {table} WHERE video_id = ?", (vid,)).fetchone()[0]

    def test_plan_counts_match_the_removal_and_change_nothing(self):
        plan = storage.remove_playlist_plan("GONE", db_path=self.db)
        self.assertEqual(plan, {"removed": 1, "kept_notes": 1, "shared": 1})
        self.assertEqual(self.rows("video_details", "solo"), 1)
        done = storage.remove_playlist("GONE", db_path=self.db)
        self.assertEqual({k: done[k] for k in plan}, plan)
        self.assertEqual(sorted(done["cleared"]), ["noted", "solo"])

    def test_only_this_playlists_videos_lose_their_data(self):
        storage.remove_playlist("GONE", db_path=self.db)
        lib = {v["video_id"]: v for v in storage.get_library(db_path=self.db)}
        self.assertNotIn("solo", lib)
        for table in storage.VIDEO_DATA_TABLES + ("search_docs", "playlist_items"):
            self.assertEqual(self.rows(table, "solo"), 0, table)
        # notes, status and the notes search survive; transcripts, ratings and summaries don't
        self.assertEqual((lib["noted"]["notes"], lib["noted"]["status"], lib["noted"]["sources"]), ("my noted thoughts", "completed", []))
        for table in ("video_details", "transcript_segments", "video_ratings", "video_summaries"):
            self.assertEqual(self.rows(table, "noted"), 0, table)
        self.assertEqual([r["video_id"] for r in storage.search("noted thoughts", db_path=self.db)["results"]], ["noted"])
        self.assertEqual(storage.search("spoken words of noted", db_path=self.db)["results"], [])
        # a video also in another playlist keeps everything
        self.assertEqual([s["id"] for s in lib["both"]["sources"]], ["STAY"])
        for table in ("video_details", "transcript_segments", "video_ratings", "video_summaries"):
            self.assertEqual(self.rows(table, "both"), 1, table)
        self.assertEqual([p["id"] for p in storage.get_playlists(db_path=self.db)], ["STAY"])

    def test_removal_survives_a_restart_and_orphan_vectors_go(self):
        storage.remove_playlist("GONE", db_path=self.db)
        storage.init_db(self.db)   # the legacy playlist_id backfill must not bring the items back
        self.assertEqual(self.rows("playlist_items", "noted"), 0)
        with sqlite3.connect(self.db) as c:
            orphans = c.execute("SELECT COUNT(*) FROM embedding_cache WHERE hash NOT IN (SELECT hash FROM search_docs)").fetchone()[0]
        self.assertEqual(orphans, 0)

    def test_kept_videos_are_not_fetched_or_rated_again(self):
        storage.update_videos(["noted"], status="unwatched", db_path=self.db)
        storage.remove_playlist("GONE", db_path=self.db)
        self.assertNotIn("noted", [v["video_id"] for v in storage.videos_needing_details(db_path=self.db)])
        self.assertNotIn("noted", [v["video_id"] for v in storage.videos_for_rating(db_path=self.db)])

    def test_unknown_playlist(self):
        with self.assertRaises(ValueError):
            storage.remove_playlist_plan("NOPE", db_path=self.db)


class RemoveVideosTest(unittest.TestCase):
    def setUp(self):
        self.db = temp_db()
        self.list_a = [video("gone", "Talk to drop"), video("kept", "Talk to keep")]
        storage.save_playlist(playlist("A"), self.list_a, db_path=self.db)
        storage.save_playlist(playlist("B"), [video("gone", "Talk to drop")], db_path=self.db)
        storage.update_videos(["gone"], notes="private thoughts", db_path=self.db)
        for vid in ("gone", "kept"):
            storage.save_details(vid, {"status": "done", "segments": [{"start": 0, "text": f"spoken words of {vid}"}]}, db_path=self.db)
            storage.save_rating(vid, {"score": 7, "verdict": "watch", "reason": "good"}, db_path=self.db)
            storage.save_summary(vid, {"tldr": f"summary of {vid}"}, db_path=self.db)
            storage.save_parts(vid, [{"start": 0, "end": 60, "title": "t"}], db_path=self.db)
        storage.save_embeddings("m", [(h, 1, b"\0\0\0\0") for (h,) in sqlite3.connect(self.db).execute("SELECT hash FROM search_docs")],
                                db_path=self.db)

    def rows(self, table, vid):
        with sqlite3.connect(self.db) as c:
            return c.execute(f"SELECT COUNT(*) FROM {table} WHERE video_id = ?", (vid,)).fetchone()[0]

    def test_removes_the_video_everywhere_and_leaves_the_others(self):
        self.assertEqual(storage.remove_videos(["gone", "never-existed"], db_path=self.db), ["gone"])
        for table in storage.VIDEO_DATA_TABLES + ("search_docs", "playlist_items", "videos"):
            self.assertEqual(self.rows(table, "gone"), 0, table)
            if table not in ("category_suggestions", "visual_segments", "media_jobs"):
                self.assertGreater(self.rows(table, "kept"), 0, table)
        self.assertEqual(storage.search("private thoughts", db_path=self.db)["results"], [])
        with sqlite3.connect(self.db) as c:
            orphans = c.execute("SELECT COUNT(*) FROM embedding_cache WHERE hash NOT IN (SELECT hash FROM search_docs)").fetchone()[0]
        self.assertEqual(orphans, 0)

    def test_a_sync_does_not_bring_it_back_but_adding_it_by_link_does(self):
        storage.remove_videos(["gone"], db_path=self.db)
        storage.init_db(self.db)
        storage.save_playlist(playlist("A"), self.list_a, db_path=self.db)          # the playlist still has it on YouTube
        self.assertEqual([v["video_id"] for v in storage.get_library(db_path=self.db)], ["kept"])
        storage.save_playlist(storage.SINGLE_SOURCE, [video("gone", "Talk to drop")], db_path=self.db, partial=True)
        storage.save_playlist(playlist("A"), self.list_a, db_path=self.db)
        self.assertEqual(self.rows("playlist_items", "gone"), 2)                     # back, and in A again


class VideoActivityTest(unittest.TestCase):
    def setUp(self):
        self.db = temp_db()
        storage.save_playlist(playlist("PL1"), [video("a"), video("b")], db_path=self.db)

    def acts(self, vid="a"):
        return storage.get_activity(vid, db_path=self.db)

    def test_step_records_its_cloud_ai_use_and_failures(self):
        storage.record_usage("rate", "claude", "m1", 5, 5, 0.5, db_path=self.db)   # outside any step: not on a video
        with storage.video_step("a", "rate", db_path=self.db):
            storage.record_usage("rate", "claude", "m1", 100, 20, 0.01, db_path=self.db)
            storage.record_usage("rate", "claude", "m1", 50, 10, None, db_path=self.db)
        with self.assertRaises(ValueError):
            with storage.video_step("a", "summarize", "local-model", db_path=self.db):
                raise ValueError("bad reply")
        fail, ok = self.acts()
        self.assertEqual((ok["kind"], ok["ok"], ok["model"], ok["tokens_in"], ok["tokens_out"], ok["cost"]),
                         ("rate", 1, "m1", 150, 30, 0.01))
        self.assertEqual((fail["kind"], fail["ok"], fail["model"], fail["note"], fail["tokens_in"]),
                         ("summarize", 0, "local-model", "bad reply", None))
        self.assertEqual(self.acts("b"), [])

    def test_media_jobs_and_details_are_recorded(self):
        storage.set_job("a", "asr", status="queued", db_path=self.db)
        storage.set_job("a", "asr", status="downloading", db_path=self.db)
        storage.record_usage("transcribe", "openai", "whisper-1", 0, 0, 0.02, db_path=self.db)
        storage.set_job("a", "asr", status="done", model="openai:whisper-1", db_path=self.db)
        storage.set_job("b", "visual", status="downloading", db_path=self.db)
        storage.set_job("b", "visual", status="failed", error="no video", db_path=self.db)
        storage.save_details("a", {"status": "no_captions"}, db_path=self.db)
        details, asr = self.acts()
        self.assertEqual((asr["kind"], asr["ok"], asr["model"], asr["cost"]), ("asr", 1, "openai:whisper-1", 0.02))
        self.assertEqual((details["kind"], details["ok"], details["note"]), ("details", 1, "no_captions"))
        [vis] = self.acts("b")
        self.assertEqual((vis["ok"], vis["note"], vis["tokens_in"]), (0, "no video", None))

    def test_existing_work_is_filled_in_once_and_removed_with_the_video(self):
        conn = sqlite3.connect(self.db)
        conn.execute("DROP TABLE video_activity")
        conn.execute("INSERT INTO video_summaries (video_id, model) VALUES ('a', 'm2')")
        conn.commit()
        conn.close()
        storage.init_db(self.db)
        storage.init_db(self.db)
        self.assertEqual([(r["kind"], r["model"]) for r in self.acts()], [("summarize", "m2")])
        storage.remove_videos(["a"], db_path=self.db)
        self.assertEqual(self.acts(), [])


class IndexActivityTest(unittest.TestCase):
    def setUp(self):
        self.db = temp_db()
        storage.save_playlist(playlist("PL1"), [
            video("v1", "First video"),
            video("v2", "Second video"),
            video("v3", "Third video"),
        ], db_path=self.db)
        # Give v1 two segments at least 30s apart so it has 3 search_docs rows in total (1 title + 2 transcript blocks)
        storage.save_details("v1", {
            "status": "done",
            "segments": [
                {"start": 0, "text": "v1 segment one"},
                {"start": 60, "text": "v1 segment two"}
            ]
        }, db_path=self.db)
        # Clear the details activity recorded during save_details so activity tests start clean
        with sqlite3.connect(self.db) as c:
            c.execute("DELETE FROM video_activity")

    def hashes_for(self, vid):
        with sqlite3.connect(self.db) as c:
            return [r[0] for r in c.execute("SELECT hash FROM search_docs WHERE video_id = ? AND hash IS NOT NULL ORDER BY id", (vid,))]

    def test_indexed_videos(self):
        h_v1 = self.hashes_for("v1")
        self.assertEqual(len(h_v1), 3)
        h_v2 = self.hashes_for("v2")
        self.assertEqual(len(h_v2), 1)

        # 1. returns [] when embedding_cache is empty
        self.assertEqual(storage.indexed_videos("m", db_path=self.db), [])

        # 2. vector for another model: leaves it out
        storage.save_embeddings("other", [(h_v2[0], b"\0" * 4, 1)], db_path=self.db)
        self.assertEqual(storage.indexed_videos("m", db_path=self.db), [])

        # 3. one of its rows has no vector: leaves it out (v1 has 3 pieces, save 2)
        storage.save_embeddings("m", [(h_v1[0], b"\0" * 4, 1), (h_v1[1], b"\0" * 4, 1)], db_path=self.db)
        self.assertEqual(storage.indexed_videos("m", db_path=self.db), [])

        # 4. returns a video once all its hashed search_docs rows have a vector for "m"
        storage.save_embeddings("m", [(h_v2[0], b"\0" * 4, 1)], db_path=self.db)
        self.assertEqual(storage.indexed_videos("m", db_path=self.db), ["v2"])

        # 5. now complete v1 as well
        storage.save_embeddings("m", [(h_v1[2], b"\0" * 4, 1)], db_path=self.db)
        self.assertEqual(storage.indexed_videos("m", db_path=self.db), ["v1", "v2"])

        # Edge case: video with NULL hash only is not returned
        with sqlite3.connect(self.db) as c:
            c.execute("INSERT INTO search_docs (video_id, kind, t, text, hash) VALUES ('v_null', 'note', 0, 'null hash', NULL)")
        self.assertNotIn("v_null", storage.indexed_videos("m", db_path=self.db))

    def test_record_index_activity(self):
        h_v1 = self.hashes_for("v1")
        h_v2 = self.hashes_for("v2")

        # 1. empty hashes returns 0 and adds no rows
        self.assertEqual(storage.record_index_activity("m", [], db_path=self.db), 0)
        self.assertEqual(storage.get_activity("v1", db_path=self.db), [])
        self.assertEqual(storage.get_activity("v2", db_path=self.db), [])

        # 2. with hashes from two videos it returns 2 and get_activity has kind=="index", ok==1, model=="m", note like "3 pieces" / "1 piece"
        all_hashes = h_v1 + h_v2
        recorded = storage.record_index_activity("m", all_hashes, db_path=self.db)
        self.assertEqual(recorded, 2)

        act1 = storage.get_activity("v1", db_path=self.db)
        self.assertEqual(len(act1), 1)
        self.assertEqual((act1[0]["kind"], act1[0]["ok"], act1[0]["model"], act1[0]["note"]),
                         ("index", 1, "m", "3 pieces"))

        act2 = storage.get_activity("v2", db_path=self.db)
        self.assertEqual(len(act2), 1)
        self.assertEqual((act2[0]["kind"], act2[0]["ok"], act2[0]["model"], act2[0]["note"]),
                         ("index", 1, "m", "1 piece"))

        # Edge case: repeated hashes do not duplicate count or activity
        storage.record_index_activity("m", [h_v2[0], h_v2[0]], db_path=self.db)
        act2_again = storage.get_activity("v2", db_path=self.db)
        self.assertEqual(len(act2_again), 2)
        self.assertEqual(act2_again[0]["note"], "1 piece")

    def patch_db(self):
        orig_gc = storage.get_connection
        orig_db = storage.DB_PATH
        return mock.patch.object(storage, "get_connection",
                                 side_effect=lambda p=None: orig_gc(self.db if p is None or p == orig_db else p))

    def test_embedder_run_embed_records_index_activity(self):
        # acceptance 3: embedder.run_embed(embed_fn=..., settings=...) leaves an "index" activity row on each video whose pieces it embedded
        fake_embed = lambda texts: [[1.0, 0.0] for _ in texts]
        with self.patch_db(), contextlib.redirect_stdout(io.StringIO()):
            embedder.run_embed(embed_fn=fake_embed)

        act1 = storage.get_activity("v1", db_path=self.db)
        self.assertTrue(any(a["kind"] == "index" and a["ok"] == 1 and a["note"] == "3 pieces" for a in act1))

        act2 = storage.get_activity("v2", db_path=self.db)
        self.assertTrue(any(a["kind"] == "index" and a["ok"] == 1 and a["note"] == "1 piece" for a in act2))

    def test_embedder_run_embed_stop_request_records_saved_hashes(self):
        # Stop request calls record_index_activity with hashes saved so far
        fake_embed = lambda texts: [[1.0, 0.0] for _ in texts]
        calls = [0]
        def should_stop():
            calls[0] += 1
            return calls[0] > 1

        with sqlite3.connect(self.db) as c:
            c.execute("DELETE FROM video_activity")

        # Use batch_size=1 so at least one batch completes before stop
        with self.patch_db(), \
             mock.patch.object(embedder, "BATCH", 1), \
             contextlib.redirect_stdout(io.StringIO()):
            res = embedder.run_embed(should_stop=should_stop, embed_fn=fake_embed)
            self.assertFalse(res)

        # At least one activity was recorded
        activities = [storage.get_activity(vid, db_path=self.db) for vid in ("v1", "v2", "v3")]
        total_index_acts = sum(len(acts) for acts in activities)
        self.assertGreater(total_index_acts, 0)
        all_acts = [a for acts in activities for a in acts]
        for a in all_acts:
            self.assertEqual(a["kind"], "index")
            self.assertEqual(a["ok"], 1)
