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


class MarkdownTest(unittest.TestCase):
    V = {"video_id": "abc", "title": "Graph | RAG # [demo]", "channel": "Ch", "category": "AI & ML", "status": "unwatched",
         "duration_seconds": 3725, "notes": "my **own** notes",
         "rating": {"score": 8.0, "verdict": "skim", "reason": "good <b>stuff</b>", "tldr": "TLDR",
                    "breakdown": {"depth": {"score": 8, "why": "deep *work*"}, "fit": {"score": 5, "why": "some fit"},
                                  "value": {"score": 7, "why": "good value"}, "freshness": {"score": 6, "why": "new"}},
                    "calc": {"score": 7.1, "quality": 7.2, "interest": -0.1,
                             "formula": {"depth": .45, "value": .4, "freshness": .15}},
                    "minutes": [{"t": 65, "topic": "Topic <one>", "notes": ["note *one*"]}],
                    "takeaways": ["Do [this]"],
                    "points": [{"t": 65, "text": "point one"}, {"t": None, "text": "untimed"}],
                    "skim": [{"start": 60, "end": 3700, "why": "core"}]}}

    def test_note(self):
        from exporter import video_markdown
        md = video_markdown(self.V, {"upload_date": "20240105", "chapters": [{"start": 0, "title": "Intro"}]})
        self.assertTrue(md.startswith('---\ntitle: "Graph | RAG # [demo]"\n'))
        for needle in ("uploaded: 2024-01-05", "duration_minutes: 62", "verdict: skim", "tags: [yourtube, ai-ml]",
                       "# Graph \\| RAG \\# \\[demo\\]", "· 1h 2m",
                       "- [1:05](https://www.youtube.com/watch?v=abc&t=65s) point one", "- untimed",
                       "- [1:00–1:01:40](https://www.youtube.com/watch?v=abc&t=60s) core",
                       "- [0:00](https://www.youtube.com/watch?v=abc&t=0s) Intro",
                       "good \\<b\\>stuff\\</b\\>", "my **own** notes"):
            self.assertIn(needle, md)
        for needle in ("## Minutes", "[1:05](https://www.youtube.com/watch?v=abc&t=65s) Topic \\<one\\>",
                       "note \\*one\\*", "## Takeaways", "Do \\[this\\]", "## Why this score",
                       "Depth: 8** — deep \\*work\\*", "Score: 7.1 = quality 7.2"):
            self.assertIn(needle, md)

    def test_zip(self):
        import io, zipfile
        from exporter import markdown_zip, safe_filename
        self.assertEqual(safe_filename('a/b:c*?"<>|d. '), "a b c d")
        two = [self.V, {**self.V, "video_id": "xyz", "rating": None}]
        z = zipfile.ZipFile(io.BytesIO(markdown_zip(two, {})))
        names = sorted(z.namelist())
        self.assertEqual(names, ["YourTube/AI & ML/Graph RAG # [demo] (xyz).md", "YourTube/AI & ML/Graph RAG # [demo].md",
                                 "YourTube/AI & ML/_AI & ML.md"])
        self.assertIn("[[Graph RAG # [demo] (xyz)]]", z.read("YourTube/AI & ML/_AI & ML.md").decode())


class VideoLinkTest(unittest.TestCase):
    def test_video_link_or_playlist(self):
        from extractor import video_id_from_url as vid
        cases = {"https://www.youtube.com/watch?v=b6LDbTTqMj0": "b6LDbTTqMj0",
                 "https://youtu.be/b6LDbTTqMj0?si=abc": "b6LDbTTqMj0",
                 "https://youtube.com/shorts/b6LDbTTqMj0": "b6LDbTTqMj0",
                 "https://m.youtube.com/watch?feature=share&v=b6LDbTTqMj0": "b6LDbTTqMj0",
                 "https://www.youtube.com/watch?v=b6LDbTTqMj0&list=PLx": None,   # a playlist link syncs the playlist
                 "PLui3EUkuMTPg": None, "WL": None, "": None}
        for link, expected in cases.items():
            self.assertEqual(vid(link), expected, link)
