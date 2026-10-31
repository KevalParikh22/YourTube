"""Tests for tools.py and the /api/tools/length endpoint."""
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

import dashboard
import extractor
import storage
import tools
from tests.helpers import playlist, video


class ToolsUnitTest(unittest.TestCase):
    def test_clean_error(self):
        self.assertEqual(
            tools._clean_error("ERROR: [youtube] 12345: Private video"),
            "Private video",
        )
        self.assertEqual(
            tools._clean_error(
                "Could not extract info from playlist URL: https://youtube.com/playlist?list=PL1 (YouTube said: The playlist does not exist.)"
            ),
            "YouTube said: The playlist does not exist.",
        )
        self.assertEqual(
            tools._clean_error(
                "Could not extract info from playlist URL: https://youtube.com/playlist?list=PL1 (ERROR: [youtube:tab] PL1: YouTube said: Private playlist)"
            ),
            "YouTube said: Private playlist",
        )
        self.assertEqual(
            tools._clean_error(ValueError("Custom failure reason")),
            "Custom failure reason",
        )

    @mock.patch("extractor.extract_video")
    def test_read_video_link(self, mock_extract):
        mock_extract.return_value = {
            "video_id": "dQw4w9WgXcQ",
            "title": "Never Gonna Give You Up",
            "channel": "Rick Astley",
            "duration_seconds": 213,
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "position": 0,
        }
        res = tools.read_links(["https://www.youtube.com/watch?v=dQw4w9WgXcQ"])
        self.assertEqual(len(res), 1)
        item = res[0]
        self.assertEqual(item["kind"], "video")
        self.assertEqual(item["id"], "dQw4w9WgXcQ")
        self.assertEqual(item["title"], "Never Gonna Give You Up")
        self.assertEqual(item["unavailable"], 0)
        self.assertEqual(len(item["videos"]), 1)
        self.assertEqual(item["videos"][0]["video_id"], "dQw4w9WgXcQ")
        self.assertEqual(item["videos"][0]["duration_seconds"], 213)
        mock_extract.assert_called_once_with("dQw4w9WgXcQ")

    @mock.patch("extractor.extract_playlist_metadata")
    def test_read_playlist_link(self, mock_meta):
        mock_meta.return_value = {
            "id": "PLtest123",
            "title": "Study Playlist",
            "url": "https://www.youtube.com/playlist?list=PLtest123",
            "skipped_unavailable": 2,
            "videos": [
                {
                    "video_id": "v1",
                    "title": "Part 1",
                    "channel": "Prof",
                    "duration_seconds": 600,
                    "position": 0,
                    "url": "https://www.youtube.com/watch?v=v1",
                },
                {
                    "video_id": "v2",
                    "title": "Part 2",
                    "channel": "Prof",
                    "duration_seconds": 1200,
                    "position": 1,
                    "url": "https://www.youtube.com/watch?v=v2",
                },
            ],
        }
        res = tools.read_links(["https://www.youtube.com/playlist?list=PLtest123"], browser="firefox")
        self.assertEqual(len(res), 1)
        item = res[0]
        self.assertEqual(item["kind"], "playlist")
        self.assertEqual(item["id"], "PLtest123")
        self.assertEqual(item["title"], "Study Playlist")
        self.assertEqual(item["unavailable"], 2)
        self.assertEqual(len(item["videos"]), 2)
        mock_meta.assert_called_once_with("https://www.youtube.com/playlist?list=PLtest123", browser_cookies="firefox")

    @mock.patch("extractor.extract_playlist_metadata")
    @mock.patch("extractor.extract_video")
    def test_video_with_list_param_is_playlist(self, mock_extract_video, mock_extract_meta):
        mock_extract_meta.return_value = {
            "id": "PLxyz",
            "title": "List",
            "url": "https://www.youtube.com/watch?v=vid1&list=PLxyz",
            "skipped_unavailable": 0,
            "videos": [],
        }
        link = "https://www.youtube.com/watch?v=vid1&list=PLxyz"
        tools.read_links([link])
        mock_extract_video.assert_not_called()
        mock_extract_meta.assert_called_once_with(link, browser_cookies=None)

    @mock.patch("extractor.extract_playlist_metadata")
    @mock.patch("extractor.extract_video")
    def test_mixed_links_give_items_plus_errors(self, mock_extract_video, mock_extract_meta):
        mock_extract_video.return_value = {
            "video_id": "vid01234567",
            "title": "Video 1",
            "channel": "Chan 1",
            "duration_seconds": 100,
            "url": "https://www.youtube.com/watch?v=vid01234567",
            "position": 0,
        }
        def meta_side_effect(url, browser_cookies=None):
            if "bad" in url:
                raise ValueError("YouTube said: The playlist does not exist.")
            return {
                "id": "PLgood",
                "title": "Good Playlist",
                "url": url,
                "skipped_unavailable": 0,
                "videos": [
                    {"video_id": "v2", "title": "Vid 2", "channel": "Chan 2", "duration_seconds": 200, "position": 0, "url": "https://www.youtube.com/watch?v=v2"}
                ],
            }
        mock_extract_meta.side_effect = meta_side_effect

        links = [
            "https://www.youtube.com/watch?v=vid01234567",
            "https://www.youtube.com/playlist?list=PLgood",
            "https://www.youtube.com/playlist?list=PLbad",
        ]
        res = tools.read_links(links)
        self.assertEqual(len(res), 3)

        # Item 1: video
        self.assertEqual(res[0]["kind"], "video")
        self.assertEqual(res[0]["id"], "vid01234567")

        # Item 2: playlist
        self.assertEqual(res[1]["kind"], "playlist")
        self.assertEqual(res[1]["id"], "PLgood")

        # Item 3: error
        self.assertEqual(res[2]["link"], "https://www.youtube.com/playlist?list=PLbad")
        self.assertEqual(res[2]["error"], "YouTube said: The playlist does not exist.")

    @mock.patch("extractor.extract_playlist_metadata")
    def test_ten_link_cap_and_whitespace(self, mock_extract_meta):
        mock_extract_meta.return_value = {
            "id": "PL1",
            "title": "P",
            "url": "u",
            "skipped_unavailable": 0,
            "videos": [],
        }
        raw = ["  PL1  ", "", "   ", "PL2", "PL3", "PL4", "PL5", "PL6", "PL7", "PL8", "PL9", "PL10", "PL11", "PL12"]
        res = tools.read_links(raw)
        self.assertEqual(len(res), 10)
        self.assertEqual(mock_extract_meta.call_count, 10)

    @mock.patch("extractor.extract_playlist_metadata")
    def test_private_playlist_only_with_browser(self, mock_extract_meta):
        def meta_side_effect(url, browser_cookies=None):
            if not browser_cookies:
                raise ValueError("Could not extract info from playlist URL: https://... (This playlist is private)")
            return {
                "id": "PLprivate",
                "title": "Private List",
                "url": url,
                "skipped_unavailable": 0,
                "videos": [{"video_id": "v1", "title": "Secret", "channel": "Me", "duration_seconds": 50, "position": 0, "url": "https://www.youtube.com/watch?v=v1"}],
            }
        mock_extract_meta.side_effect = meta_side_effect

        private_url = "https://www.youtube.com/playlist?list=PLprivate"

        # Without browser: fails with error
        res_no_browser = tools.read_links([private_url], browser=None)
        self.assertEqual(len(res_no_browser), 1)
        self.assertIn("error", res_no_browser[0])
        self.assertEqual(res_no_browser[0]["error"], "This playlist is private")

        # With browser="auto": succeeds
        res_with_browser = tools.read_links([private_url], browser="auto")
        self.assertEqual(len(res_with_browser), 1)
        self.assertEqual(res_with_browser[0]["kind"], "playlist")
        self.assertEqual(res_with_browser[0]["id"], "PLprivate")


class ToolsAPITest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        storage.save_playlist(playlist("PLtest", "Saved List"), [video("v_init", "Initial Video")])
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def call_post(self, path, body, headers=None):
        hdrs = {"X-YTKB": "1", "Content-Type": "application/json"}
        hdrs.update(headers or {})
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode("utf-8"),
            headers=hdrs,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read() or b"{}")

    def _count_rows(self):
        with storage.get_connection() as conn:
            tables = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%_fts%'"
                ).fetchall()
            ]
            return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}

    @mock.patch("extractor.extract_playlist_metadata")
    @mock.patch("extractor.extract_video")
    def test_api_mixed_links(self, mock_extract_video, mock_extract_meta):
        mock_extract_video.return_value = {
            "video_id": "vid01234567",
            "title": "Video 1",
            "channel": "Chan 1",
            "duration_seconds": 120,
            "url": "https://www.youtube.com/watch?v=vid01234567",
            "position": 0,
        }
        def meta_side_effect(url, browser_cookies=None):
            if "bad" in url:
                raise ValueError("YouTube said: Playlist does not exist")
            return {
                "id": "PL123",
                "title": "Playlist 123",
                "url": url,
                "skipped_unavailable": 1,
                "videos": [{"video_id": "p1", "title": "P1", "channel": "C", "duration_seconds": 300, "position": 0, "url": "https://www.youtube.com/watch?v=p1"}],
            }
        mock_extract_meta.side_effect = meta_side_effect

        payload = {
            "links": [
                "https://www.youtube.com/watch?v=vid01234567",
                "https://www.youtube.com/playlist?list=PL123",
                "https://www.youtube.com/playlist?list=PLbad",
            ]
        }
        status, data = self.call_post("/api/tools/length", payload)
        self.assertEqual(status, 200)
        self.assertIn("items", data)
        items = data["items"]
        self.assertEqual(len(items), 3)

        video_item = next(it for it in items if it.get("kind") == "video")
        playlist_item = next(it for it in items if it.get("kind") == "playlist")
        error_item = next(it for it in items if "error" in it)

        self.assertEqual(video_item["id"], "vid01234567")
        self.assertEqual(playlist_item["id"], "PL123")
        self.assertEqual(playlist_item["unavailable"], 1)
        self.assertEqual(error_item["link"], "https://www.youtube.com/playlist?list=PLbad")
        self.assertIn("Playlist does not exist", error_item["error"])

    def test_api_ten_link_cap(self):
        # 11 links -> 400
        status, data = self.call_post("/api/tools/length", {"links": ["https://youtube.com/playlist?list=PL1"] * 11})
        self.assertEqual(status, 400)
        self.assertIn("At most 10 links", data.get("error", ""))

    def test_api_no_valid_links(self):
        # empty list
        status, data = self.call_post("/api/tools/length", {"links": []})
        self.assertEqual(status, 400)

        # whitespace only
        status, data = self.call_post("/api/tools/length", {"links": ["", "   "]})
        self.assertEqual(status, 400)

        # non-list
        status, data = self.call_post("/api/tools/length", {"links": "not a list"})
        self.assertEqual(status, 400)

        # invalid browser type
        status, data = self.call_post("/api/tools/length", {"links": ["PL1"], "browser": 123})
        self.assertEqual(status, 400)

    @mock.patch("extractor.extract_playlist_metadata")
    @mock.patch("extractor.extract_video")
    def test_api_no_db_writes(self, mock_extract_video, mock_extract_meta):
        mock_extract_video.return_value = {
            "video_id": "new_vid",
            "title": "Brand New Video",
            "channel": "Creator",
            "duration_seconds": 500,
            "url": "https://www.youtube.com/watch?v=new_vid",
            "position": 0,
        }
        mock_extract_meta.return_value = {
            "id": "PLbrandnew",
            "title": "Brand New List",
            "url": "https://www.youtube.com/playlist?list=PLbrandnew",
            "skipped_unavailable": 0,
            "videos": [{"video_id": "v_brandnew", "title": "Vid", "channel": "C", "duration_seconds": 100, "position": 0, "url": "https://www.youtube.com/watch?v=v_brandnew"}],
        }

        counts_before = self._count_rows()
        playlists_before = storage.get_playlists()
        library_before = storage.get_library()

        status, data = self.call_post("/api/tools/length", {
            "links": [
                "https://www.youtube.com/watch?v=new_vid",
                "https://www.youtube.com/playlist?list=PLbrandnew",
            ]
        })
        self.assertEqual(status, 200)

        counts_after = self._count_rows()
        playlists_after = storage.get_playlists()
        library_after = storage.get_library()

        self.assertEqual(counts_before, counts_after)
        self.assertEqual(playlists_before, playlists_after)
        self.assertEqual(library_before, library_after)

    @mock.patch("extractor.extract_playlist_metadata")
    def test_api_private_playlist_browser(self, mock_extract_meta):
        def meta_side_effect(url, browser_cookies=None):
            if not browser_cookies:
                raise ValueError("This playlist is private")
            return {
                "id": "PLprivate_api",
                "title": "Private List API",
                "url": url,
                "skipped_unavailable": 0,
                "videos": [],
            }
        mock_extract_meta.side_effect = meta_side_effect

        link = "https://www.youtube.com/playlist?list=PLprivate_api"

        # Without browser
        status, data = self.call_post("/api/tools/length", {"links": [link]})
        self.assertEqual(status, 200)
        self.assertIn("error", data["items"][0])

        # With browser="auto"
        status, data = self.call_post("/api/tools/length", {"links": [link], "browser": "auto"})
        self.assertEqual(status, 200)
        self.assertEqual(data["items"][0]["kind"], "playlist")
        self.assertEqual(data["items"][0]["id"], "PLprivate_api")


if __name__ == "__main__":
    unittest.main()
