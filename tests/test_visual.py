import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

import local_visual
import rater
import storage
from tests.helpers import playlist, temp_db, video


def make_jpg(path, color=(255, 255, 255), stripe=None):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (320, 180), color)
    if stripe is not None:
        ImageDraw.Draw(img).rectangle([stripe, 0, stripe + 60, 180], fill=(0, 0, 0))
    img.save(path, "JPEG")
    return path


class FrameSelectionTest(unittest.TestCase):
    def test_gap_duplicates_and_limit(self):
        A, B, C, D = 0x0, 0xFFFF, 0x1, 0xAAAA                        # C is 1 bit from A; D is 8 bits from both A and B
        cands = [(0, A), (5, B), (12, B), (40, C), (70, D), (100, B)]
        self.assertEqual(local_visual.select_frames(cands), [0, 2, 4])  # 5 s too close; C and the last B are duplicates
        self.assertEqual(local_visual.select_frames(cands, limit=1), [0])

    def test_dhash_tells_slides_apart(self):
        d = tempfile.mkdtemp()
        a = local_visual.dhash(make_jpg(os.path.join(d, "a.jpg"), stripe=20))
        a2 = local_visual.dhash(make_jpg(os.path.join(d, "a2.jpg"), stripe=22))
        b = local_visual.dhash(make_jpg(os.path.join(d, "b.jpg"), stripe=220))
        self.assertLessEqual(bin(a ^ a2).count("1"), local_visual.HAMMING)
        self.assertGreater(bin(a ^ b).count("1"), local_visual.HAMMING)


class VisualStorageTest(unittest.TestCase):
    def setUp(self):
        self.db = temp_db()
        storage.save_playlist(playlist(), [video("m", "Marked talk", category="Talks"), video("c", "Category video", category="Lectures"),
                                           video("no", "Opted out", category="Lectures"), video("gone", "Gone", category="Lectures"),
                                           video("w", "System Design Interview Prep", category="Talks")], db_path=self.db)
        storage.set_has_visuals(["m"], True, db_path=self.db)
        storage.set_has_visuals(["no"], False, db_path=self.db)
        storage.save_details("gone", {"status": "unavailable"}, db_path=self.db)

    def need(self, ids=None, cats=("Lectures",)):
        return [v["video_id"] for v in storage.videos_needing_visuals(ids, cats, db_path=self.db)]

    def test_candidates(self):
        self.assertEqual(self.need(), ["m", "c"])                    # marked, or category on; opt-out and unavailable skipped
        self.assertEqual(self.need(cats=()), ["m"])
        self.assertEqual(self.need(ids=["no"]), ["no"])              # explicit request
        storage.save_visuals("m", [{"t": 5, "text": "Slide one", "visual": "", "thumb": "00005.jpg"}], db_path=self.db)
        self.assertEqual(self.need(), ["c"])                         # read already
        for _ in range(3):
            storage.set_job("c", "visual", status="failed", error="x", db_path=self.db)
        self.assertEqual(self.need(), [])

    def test_slides_flow_into_details_search_and_library(self):
        storage.save_visuals("m", [{"t": 75.0, "text": "Graph RAG pipeline\nextract entities", "visual": "A flow diagram", "thumb": "00075.jpg"}], db_path=self.db)
        d = storage.get_details("m", db_path=self.db)                  # no details row: still returns the slides
        self.assertEqual((d["status"], d["visuals"][0]["t"], d["visuals"][0]["thumb"]), (None, 75.0, "00075.jpg"))
        hit = storage.search("entities", db_path=self.db)["results"][0]["hits"][0]
        self.assertEqual((hit["kind"], hit["t"]), ("slide", 75.0))
        self.assertEqual(storage.search("flow diagram", kinds=["slide"], db_path=self.db)["results"][0]["video_id"], "m")
        lib = {v["video_id"]: v for v in storage.get_library(db_path=self.db)}
        self.assertEqual((lib["m"]["visual"]["slides"], lib["m"]["has_visuals"]), (1, 1))
        storage.save_visuals("m", [], db_path=self.db)                # read again, nothing found
        self.assertEqual(storage.search("entities", db_path=self.db)["results"], [])
        self.assertIsNone(storage.get_details("m", db_path=self.db))

    def test_hints(self):
        storage.save_details("c", {"status": "done", "segments": [{"start": 0, "text": "As you can see on the screen"},
                                                                  {"start": 9, "text": "and on this slide we have"}]}, db_path=self.db)
        hints = storage.visual_hints(db_path=self.db)
        self.assertIn("refers to the screen 2 times", hints["c"])
        self.assertIn("System Design", hints["w"])
        self.assertNotIn("m", hints)        # the user already decided
        self.assertNotIn("gone", hints)     # unavailable


class RunVisualTest(unittest.TestCase):
    def setUp(self):
        storage.save_playlist(playlist("PLV"), [video("v1x", "Slide talk"), video("v2x", "Another")])
        storage.set_has_visuals(["v1x", "v2x"], True)
        self.tmp = tempfile.mkdtemp()

    def fake_download(self, vid):
        path = os.path.join(self.tmp, f"{vid}.video.mp4")
        open(path, "wb").write(b"video")
        return path

    def fake_extract(self, path, work):
        os.makedirs(work, exist_ok=True)
        return [(0.0, make_jpg(os.path.join(work, "f0.jpg"))), (30.0, make_jpg(os.path.join(work, "f1.jpg"), stripe=100))]

    def run_quiet(self, **kw):
        kw.setdefault("download", self.fake_download)
        kw.setdefault("extract", self.fake_extract)
        kw.setdefault("read", lambda p: {"content": p.endswith("f1.jpg"), "text": "LoRA ranks" if p.endswith("f1.jpg") else "", "visual": ""})
        kw.setdefault("thumb", lambda src, vid, t: f"{int(t):05d}.jpg")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ok = local_visual.run_visual(["v1x"], sleep=lambda s: None, **kw)
        return ok, out.getvalue()

    def test_keeps_content_frames_and_deletes_video(self):
        ok, log = self.run_quiet()
        self.assertTrue(ok)
        self.assertEqual([(x["t"], x["text"]) for x in storage.get_visuals("v1x")], [(30.0, "LoRA ranks")])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "v1x.video.mp4")))
        self.assertIn("1 of 2 frames had content", log)

    def test_failure_recorded(self):
        def broken(path, work):
            raise RuntimeError("ffmpeg died")
        ok, log = self.run_quiet(extract=broken)
        lib = {v["video_id"]: v for v in storage.get_library()}
        self.assertEqual(lib["v1x"]["visual"]["job"]["status"], "failed")
        self.assertIn("ffmpeg died", log)

    def test_needs_a_vision_model(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch("local_visual.model_sees_images", return_value=False):
            self.assertFalse(local_visual.run_visual(["v1x"], download=self.fake_download, sleep=lambda s: None))
        self.assertIn("can't read images", out.getvalue())


class ReadFrameTest(unittest.TestCase):
    def test_parses_model_reply(self):
        path = make_jpg(os.path.join(tempfile.mkdtemp(), "f.jpg"))
        reply = {"message": {"content": json.dumps({"content": "true", "text": " Slide\ntext ", "visual": "a  chart"})}}
        resp = mock.MagicMock()
        resp.__enter__.return_value = io.BytesIO(json.dumps(reply).encode())
        with mock.patch("urllib.request.urlopen", return_value=resp) as u:
            r = local_visual.read_frame(path, "qwen3.5:4b", "http://localhost:11434")
        self.assertEqual(r, {"content": True, "text": "Slide\ntext", "visual": "a chart"})
        body = json.loads(u.call_args[0][0].data)
        self.assertEqual((body["model"], len(body["messages"][0]["images"]) , body["format"]), ("qwen3.5:4b", 1, "json"))


class RaterSlidesTest(unittest.TestCase):
    def test_prompt_includes_slides(self):
        v = {"title": "T", "channel": "C", "duration_seconds": 600, "category": "AI",
             "slides": [{"t": 75, "text": "Step 1\nExtract", "visual": "A pipeline diagram"}]}
        prompt, _ = rater.build_prompt(v, [], {})
        self.assertIn("On-screen text (slides, code, whiteboard)", prompt)
        self.assertIn("[1:15] Step 1 Extract · A pipeline diagram", prompt)
