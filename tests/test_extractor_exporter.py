import unittest

from exporter import generate_youtube_watch_queue_urls
from extractor import normalize_playlist_url


class ExtractorTest(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_playlist_url("WL"), "https://www.youtube.com/playlist?list=WL")
        self.assertEqual(normalize_playlist_url(" watch later "), "https://www.youtube.com/playlist?list=WL")
        self.assertEqual(normalize_playlist_url("PLabc"), "https://www.youtube.com/playlist?list=PLabc")
        url = "https://www.youtube.com/playlist?list=PLabc"
        self.assertEqual(normalize_playlist_url(url), url)


class ExporterTest(unittest.TestCase):
    def test_queue_urls_are_chunked_at_50(self):
        urls = generate_youtube_watch_queue_urls([f"id{i}" for i in range(120)])
        self.assertEqual(len(urls), 3)
        self.assertEqual(urls[2].split("video_ids=")[1].count(","), 19)
