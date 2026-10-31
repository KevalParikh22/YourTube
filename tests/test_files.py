"""Storage file accounting, backup, clear and saved export checks."""
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import applog
import classifier
import files
import storage


class FilesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name
        self.db = os.path.join(self.root, "library.db")
        self.frames = os.path.join(self.root, "frames")
        self.logs = os.path.join(self.root, "logs")
        self.taxonomy = os.path.join(self.root, "taxonomy.json")
        with mock.patch.object(files, "DATA_DIR", self.root), \
             mock.patch.object(storage, "DB_PATH", self.db), \
             mock.patch.object(files, "FRAMES_DIR", self.frames), \
             mock.patch.object(applog, "LOG_DIR", self.logs), \
             mock.patch.object(classifier, "TAXONOMY_PATH", self.taxonomy):
            storage.init_db(self.db)
            with storage.get_connection(self.db) as conn:
                conn.execute("INSERT INTO videos(video_id,title,url) VALUES('v1','Video','https://youtu.be/v1')")
                conn.execute("INSERT INTO transcript_segments(video_id,start_s,dur_s,text) VALUES('v1',0,1,'text')")
                conn.execute("INSERT INTO video_summaries(video_id,tldr) VALUES('v1','summary')")
                conn.execute("INSERT INTO visual_segments(video_id,t_s,text) VALUES('v1',0,'slide')")
            os.makedirs(self.frames)
            with open(os.path.join(self.frames, "slide.jpg"), "wb") as f:
                f.write(b"12345")
            os.makedirs(os.path.join(self.root, "media_cache"))
            with open(os.path.join(self.root, "media_cache", "audio"), "wb") as f:
                f.write(b"1234567")

    def patched(self):
        original = storage.get_connection
        return mock.patch.multiple(files, DATA_DIR=self.root), \
            mock.patch.object(storage, "DB_PATH", self.db), \
            mock.patch.object(storage, "get_connection", side_effect=lambda db_path=None: original(
                self.db if db_path is None or db_path == original.__defaults__[0] else db_path)), \
            mock.patch.object(files, "FRAMES_DIR", self.frames), \
            mock.patch.object(applog, "LOG_DIR", self.logs), \
            mock.patch.object(classifier, "TAXONOMY_PATH", self.taxonomy)

    def test_usage_sizes_and_counts(self):
        with self.patched()[0], self.patched()[1], self.patched()[2], self.patched()[3], self.patched()[4], self.patched()[5]:
            result = files.usage()
        by_key = {a["key"]: a for a in result["areas"]}
        self.assertEqual(by_key["frames"]["bytes"], 5)
        self.assertEqual(by_key["media"]["bytes"], 7)
        self.assertTrue(by_key["frames"]["clearable"])
        self.assertEqual(result["counts"], {"videos": 1, "transcripts": 1, "summaries": 1, "slides": 1})

    def test_backup_is_valid_and_does_not_copy_settings(self):
        with self.patched()[0], self.patched()[1], self.patched()[2], self.patched()[3], self.patched()[4], self.patched()[5]:
            with open(os.path.join(self.root, "settings.json"), "w") as f:
                f.write('{"api_key":"secret"}')
            with open(self.taxonomy, "w") as f:
                f.write('{"topics": []}')
            for other in ("taxonomy-2.json", "taxonomy-old.json"):
                with open(os.path.join(self.root, other), "w") as f:
                    f.write("{}")
            name = files.backup()
            backup_path = os.path.join(self.root, "backups", name)
            with sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0], 1)
            folder = os.path.splitext(backup_path)[0]
            self.assertEqual(sorted(os.listdir(folder)), ["taxonomy-2.json", "taxonomy.json"])
            self.assertNotIn("settings.json", os.listdir(folder))

    def test_pruning_keeps_newest_ten(self):
        with self.patched()[0]:
            folder = files._backup_dir()
            for i in range(12):
                path = os.path.join(folder, f"yourtube-20260101-{i:06d}.db")
                with open(path, "wb") as f:
                    f.write(b"db")
                os.utime(path, (i + 100, i + 100))
            files._prune_backups()
            names = sorted(os.listdir(folder))
        self.assertEqual(len(names), 10)
        self.assertNotIn("yourtube-20260101-000000.db", names)

    def test_clear_rejects_unknown_and_outside_paths(self):
        with self.patched()[0], self.patched()[1], self.patched()[2], self.patched()[3], self.patched()[4], self.patched()[5]:
            with self.assertRaises(ValueError):
                files.clear("unknown")
            outside = os.path.join(self.root, "outside")
            os.makedirs(outside)
            linked = os.path.join(self.root, "linked")
            os.symlink(outside, linked)
            with mock.patch.object(files, "FRAMES_DIR", linked):
                with self.assertRaises(ValueError):
                    files.clear("frames")
            self.assertEqual(os.listdir(outside), [])

    def test_export_persistence_and_name_rules(self):
        with mock.patch.object(files, "DATA_DIR", self.root):
            body = b'{"export":"same bytes"}'
            name = files.save_export("yourtube.json", body)
            saved = files.list_exports()
            self.assertEqual(len(saved), 1)
            with open(os.path.join(self.root, "exports", name), "rb") as f:
                self.assertEqual(f.read(), body)
            self.assertIsNone(files.get_saved("exports", "../settings.json"))
            self.assertIsNone(files.get_saved("exports", "a/b"))
            with self.assertRaises(ValueError):
                files.delete("exports", "../settings.json")



class RestoreTest(FilesTest):
    def titles(self):
        with storage.get_connection(self.db) as conn:
            return [r[0] for r in conn.execute("SELECT title FROM videos ORDER BY video_id")]

    def test_round_trip_keeps_a_safety_backup_and_settings(self):
        settings = os.path.join(self.root, "settings.json")
        with open(settings, "w") as f:
            f.write('{"api_key":"secret"}')
        with open(self.taxonomy, "w") as f:
            f.write('{"Old": []}')
        with mock.patch.object(files, "DATA_DIR", self.root), mock.patch.object(storage, "DB_PATH", self.db), \
                mock.patch.object(classifier, "TAXONOMY_PATH", self.taxonomy):
            name = files.backup()
            with storage.get_connection(self.db) as conn:
                conn.execute("UPDATE videos SET title = 'Changed'")
                conn.execute("INSERT INTO videos(video_id,title,url) VALUES('v2','New','https://youtu.be/v2')")
            with open(self.taxonomy, "w") as f:
                f.write('{"New": []}')
            before = storage.index_version(self.db)
            safety = files.restore(name)
            self.assertEqual(self.titles(), ["Video"])
            with open(self.taxonomy) as f:
                self.assertEqual(f.read(), '{"Old": []}')
            self.assertNotEqual(storage.index_version(self.db), before)
            # the safety backup holds the library as it was just before
            with sqlite3.connect(f"file:{os.path.join(self.root, 'backups', safety)}?mode=ro", uri=True) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0], 2)
            with self.assertRaises(FileNotFoundError):
                files.restore("../settings.json")
        with open(settings) as f:
            self.assertEqual(f.read(), '{"api_key":"secret"}')

    def test_older_backup_is_migrated(self):
        with mock.patch.object(files, "DATA_DIR", self.root), mock.patch.object(storage, "DB_PATH", self.db), \
                mock.patch.object(classifier, "TAXONOMY_PATH", self.taxonomy):
            name = files.backup()
            with sqlite3.connect(os.path.join(self.root, "backups", name)) as conn:
                conn.execute("DROP TABLE ask_history")
            files.restore(name)
            with storage.get_connection(self.db) as conn:
                self.assertTrue(conn.execute("SELECT name FROM sqlite_master WHERE name = 'ask_history'").fetchone())
