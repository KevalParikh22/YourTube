import unittest
from types import SimpleNamespace
from unittest import mock

from exporter import generate_youtube_watch_queue_urls
from extractor import normalize_playlist_url


class ExtractorTest(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_playlist_url("WL"), "https://www.youtube.com/playlist?list=WL")
        self.assertEqual(normalize_playlist_url(" watch later "), "https://www.youtube.com/playlist?list=WL")
        self.assertEqual(normalize_playlist_url("PLabc"), "https://www.youtube.com/playlist?list=PLabc")
        url = "https://www.youtube.com/playlist?list=PLabc"
        self.assertEqual(normalize_playlist_url(url), url)


def _fake_ydl(results):
    """
    A YoutubeDL stand-in: results maps browser -> info dict, an error message to log, ('rotated', message) to
    also log yt-dlp's rotated-cookies warning, or a list of those consumed one read at a time.
    """
    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def extract_info(self, url, download=False):
            result = results[(self.opts.get('cookiesfrombrowser') or (None,))[0]]
            if isinstance(result, list):
                result = result.pop(0)
            if isinstance(result, tuple):
                self.opts['logger'].warning("[youtube:tab] The provided YouTube account cookies are no longer valid.")
                result = result[1]
            if isinstance(result, str):
                self.opts['logger'].error(f"ERROR: [youtube:tab] PLx: {result}")
                return None
            return result
    return FakeYDL


class PlaylistReadTest(unittest.TestCase):
    FOUND = {'id': 'PLx', 'title': 'Mine', 'entries': [{'id': 'abcdefghijk', 'title': 'A video', 'duration': 60}]}

    MISSING = 'YouTube said: The playlist does not exist.'

    def read(self, results, browsers=('firefox', 'chrome'), **kw):
        from extractor import extract_playlist_metadata
        with mock.patch('extractor.signed_in_browsers', return_value=list(browsers)), \
             mock.patch('extractor.yt_dlp.YoutubeDL', _fake_ydl(results)), \
             mock.patch('extractor.time.sleep') as self.sleep:
            return extract_playlist_metadata('PLx', **kw)

    def test_rotated_sign_in_is_read_once_more(self):
        attempts = [('rotated', self.MISSING), self.FOUND]
        data = self.read({'chrome': attempts}, browser_cookies='chrome')
        self.assertEqual(len(data['videos']), 1)
        self.assertEqual((attempts, self.sleep.call_count), ([], 1))

    def test_rotation_that_persists_says_what_to_do(self):
        attempts = [('rotated', self.MISSING), ('rotated', self.MISSING), self.FOUND]
        with self.assertRaises(ValueError) as cm:
            self.read({'chrome': attempts}, browser_cookies='chrome')
        self.assertIn('rotated the chrome sign-in', str(cm.exception))
        self.assertIn(self.MISSING, str(cm.exception))
        self.assertEqual(len(attempts), 1)   # tried twice, not three times

    def test_failure_without_rotation_is_not_retried(self):
        attempts = [self.MISSING, self.FOUND]
        with self.assertRaises(ValueError):
            self.read({'chrome': attempts}, browser_cookies='chrome')
        self.assertEqual((len(attempts), self.sleep.call_count), (1, 0))

    def test_signed_in_needs_login_info(self):
        from extractor import signed_in_browsers
        jars = {'firefox': ['SID', '__Secure-3PSID', '__Secure-3PAPISID'],     # yt-dlp would read it signed out
                'chrome': ['SID', 'LOGIN_INFO', 'SAPISID']}
        def fake_extract(browser, logger=None):
            if browser not in jars:
                raise FileNotFoundError(browser)
            return [SimpleNamespace(name=name, domain='.youtube.com', value='x') for name in jars[browser]]
        with mock.patch('yt_dlp.cookies.extract_cookies_from_browser', side_effect=fake_extract):
            self.assertEqual(signed_in_browsers(), ['chrome'])

    def test_auto_tries_the_next_signed_in_browser(self):
        data = self.read({'firefox': 'YouTube said: The playlist does not exist.', 'chrome': self.FOUND},
                         browser_cookies='auto')
        self.assertEqual(data['browser'], 'chrome')
        self.assertEqual([v['video_id'] for v in data['videos']], ['abcdefghijk'])

    def test_auto_prefers_a_browser_that_returns_videos(self):
        empty = {'id': 'WL', 'title': 'Watch later', 'entries': []}
        data = self.read({'firefox': empty, 'chrome': self.FOUND}, browser_cookies='auto')
        self.assertEqual(data['browser'], 'chrome')
        data = self.read({'firefox': empty, 'chrome': 'YouTube said: The playlist does not exist.'},
                         browser_cookies='auto')
        self.assertEqual((data['browser'], data['videos']), ('firefox', []))

    def test_auto_reports_each_browsers_reason(self):
        missing = 'YouTube said: The playlist does not exist.'
        with self.assertRaises(ValueError) as cm:
            self.read({'firefox': missing, 'chrome': missing}, browser_cookies='auto')
        msg = str(cm.exception)
        self.assertIn(f'firefox: {missing}', msg)
        self.assertIn(f'chrome: {missing}', msg)
        self.assertNotIn('[youtube:tab]', msg)

    def test_auto_without_any_sign_in(self):
        with self.assertRaisesRegex(ValueError, 'No browser signed in'):
            self.read({}, browsers=(), browser_cookies='auto')

    def test_named_browser_shows_youtubes_reason(self):
        with self.assertRaises(ValueError) as cm:
            self.read({'firefox': 'YouTube said: The playlist does not exist.'}, browser_cookies='firefox')
        self.assertIn('(YouTube said: The playlist does not exist.)', str(cm.exception))


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

    def test_summary_sections_are_exported_and_escaped(self):
        from exporter import video_markdown
        summary = {
            "tldr": "A <summary>",
            "minutes": [{"t": 65, "topic": "Topic", "notes": ["minute note"]}],
            "claims": [{"t": 70, "kind": "quote", "text": "Claim *text*"}],
            "quiz": [{"t": 80, "q": "Question?", "a": "Answer"}],
            "takeaways": ["Takeaway"],
            "learn_more": {"related": [{"video_id": "xyz", "relation": "deeper", "why": "More"}],
                            "searches": [{"query": "safe <query>", "why": "Gap"}]},
        }
        md = video_markdown({**self.V, "rating": None}, summary=summary)
        for needle in ("## Summary", "A \\<summary\\>", "## Minutes", "## Key claims and quotes",
                       "## Check yourself", "## Takeaways", "## Learn more", "Claim \\*text\\*",
                       "https://www.youtube.com/watch?v=abc&t=65s", "safe \\<query\\>"):
            self.assertIn(needle, md)


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
