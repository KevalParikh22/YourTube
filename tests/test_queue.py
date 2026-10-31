import threading
import time
import unittest
from unittest import mock

import dashboard


def wait_until(cond, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


class QueueTest(unittest.TestCase):
    def setUp(self):
        # Tasks of these kinds update the meaning index afterwards; keep the real model out of tests
        for patcher in (mock.patch("embedder.run_embed", return_value=True), mock.patch("embedder.available", return_value=False),
                        mock.patch("dashboard.lane_for", return_value="youtube")):   # the old one-lane tests share a lane
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_tasks_run_in_order_one_at_a_time(self):
        job, gate, order = dashboard.Job(), threading.Event(), []

        def task(name, block=False):
            def fn():
                order.append(name)
                if block:
                    gate.wait(5)
                print(f"did {name}")
                return name != "b"
            return fn
        first = job.start("details", "A", task("a", block=True))
        self.assertEqual(first["position"], 0)
        self.assertTrue(wait_until(lambda: job.snapshot()["running"]))
        second = job.start("rate", "B", task("b"))
        third = job.start("rate", "C", task("c"))
        self.assertEqual((second["position"], third["position"]), (1, 2))
        self.assertIsNone(job.start("rate", "B", task("b")))                     # the same task isn't queued twice
        self.assertEqual([t["label"] for t in job.snapshot()["queue"]], ["B", "C"])
        self.assertEqual(order, ["a"])                                            # nothing else starts meanwhile
        gate.set()
        self.assertTrue(wait_until(lambda: len(job.snapshot()["recent"]) == 3))
        snap = job.snapshot()
        self.assertEqual(order, ["a", "b", "c"])
        self.assertEqual([(t["label"], t["ok"]) for t in snap["recent"]], [("C", True), ("B", False), ("A", True)])
        self.assertIn("did c", snap["log"])                                       # the last task's own output
        self.assertNotIn("did a", snap["log"])

    def test_keys_tell_per_video_tasks_apart_and_queued_ones_can_be_removed(self):
        job, gate = dashboard.Job(), threading.Event()
        job.start("asr", "Transcribe", lambda: gate.wait(5))
        self.assertTrue(wait_until(lambda: job.snapshot()["running"]))
        one = job.start("rate", "Rate", lambda: True, key=["v1"])
        two = job.start("rate", "Rate", lambda: True, key=["v2"])
        self.assertIsNotNone(two)
        self.assertIsNone(job.start("rate", "Rate", lambda: True, key=["v1"]))
        self.assertTrue(job.request_stop(one["id"]))                              # removed before it started
        self.assertEqual([t["id"] for t in job.snapshot()["queue"]], [two["id"]])
        self.assertTrue(job.request_stop())                                       # stops the running one
        self.assertFalse(job.stop_requested())                                    # outside a task: never "stopping"
        self.assertTrue(job.snapshot()["stopping"])
        gate.set()
        self.assertTrue(wait_until(lambda: not job.snapshot()["running"] and not job.snapshot()["queue"]))
        self.assertTrue(job.snapshot()["recent"][-1]["stopped"])


class LaneTest(unittest.TestCase):
    def setUp(self):
        for patcher in (mock.patch("embedder.run_embed", return_value=True), mock.patch("embedder.available", return_value=True),
                        mock.patch("llm.media_provider", create=True, return_value="local")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_local_and_cloud_tasks_run_together_and_keep_their_own_logs(self):
        job, gate, both = dashboard.Job(), threading.Event(), threading.Barrier(2, timeout=5)

        def task(name):
            def fn():
                print(f"start {name}")
                both.wait()                      # only passes when the other lane's task runs at the same time
                if name == "cloud":              # a helper thread inside the task prints into the same log
                    import contextvars
                    t = threading.Thread(target=contextvars.copy_context().run, args=(print, "helper line"))
                    t.start(); t.join()
                gate.wait(5)
                print(f"end {name}")
                return True
            return fn
        a = job.start("asr", "Transcribe", task("local"), lane="local")
        b = job.start("rate", "Rate", task("cloud"), lane="cloud")
        c = job.start("pull", "Download", lambda: True, lane="local")
        self.assertEqual((a["position"], b["position"], c["position"]), (0, 0, 1))
        self.assertTrue(wait_until(lambda: sum(l["running"] for l in job.snapshot()["lanes"]) == 2))
        snap = job.snapshot()
        self.assertEqual([t["lane"] for t in snap["queue"]], ["local"])         # the second local task waits
        logs = {l["lane"]: l["log"] for l in snap["lanes"]}
        self.assertTrue(wait_until(lambda: "helper line" in {l["lane"]: l["log"] for l in job.snapshot()["lanes"]}["cloud"]))
        self.assertNotIn("cloud", logs["local"])
        self.assertNotIn("local", logs["cloud"])
        gate.set()
        self.assertTrue(wait_until(lambda: len([t for t in job.snapshot()["recent"] if t["kind"] != "embed"]) == 3))

    def test_stop_one_lane_or_all(self):
        job, gate = dashboard.Job(), threading.Event()
        seen = {}

        def fn(name):
            def run():
                gate.wait(5)
                seen[name] = job.stop_requested()
                return True
            return run
        one = job.start("asr", "A", fn("a"), lane="local")
        job.start("rate", "B", fn("b"), lane="cloud")
        self.assertTrue(wait_until(lambda: sum(l["running"] for l in job.snapshot()["lanes"]) == 2))
        self.assertTrue(job.request_stop(one["id"]))
        self.assertEqual({l["lane"]: l["stopping"] for l in job.snapshot()["lanes"]}, {"youtube": False, "local": True, "cloud": False})
        gate.set()
        self.assertTrue(wait_until(lambda: len(seen) == 2))
        self.assertEqual(seen, {"a": True, "b": False})
        gate.clear()
        job.start("asr", "C", lambda: gate.wait(5) and False, lane="local")
        job.start("rate", "D", lambda: gate.wait(5) and False, lane="cloud")
        self.assertTrue(wait_until(lambda: sum(l["running"] for l in job.snapshot()["lanes"]) == 2))
        self.assertTrue(job.request_stop())
        self.assertTrue(all(l["stopping"] for l in job.snapshot()["lanes"] if l["running"]))
        gate.set()

    def test_auto_embed_is_queued_once(self):
        job, gate = dashboard.Job(), threading.Event()
        job.start("embed", "Busy index", lambda: gate.wait(5), lane="local", key="other")
        self.assertTrue(wait_until(lambda: job.snapshot()["running"]))
        job.start("rate", "R1", lambda: True, lane="cloud")
        job.start("sync", "S1", lambda: True, lane="youtube")
        self.assertTrue(wait_until(lambda: len(job.snapshot()["recent"]) == 2))
        # each finished task queues the update just after it shows as finished, so wait for both to have tried
        time.sleep(0.2)
        self.assertEqual([t["label"] for t in job.snapshot()["queue"]], ["Update meaning index"])
        gate.set()
        self.assertTrue(wait_until(lambda: not any(l["running"] for l in job.snapshot()["lanes"]) and not job.snapshot()["queue"]))

    def test_lane_for(self):
        s = {"tasks": {}}
        with mock.patch("llm.media_provider", create=True, side_effect=lambda st, task: {"transcribe": "groq"}.get(task, "local")), \
             mock.patch("llm.is_local", side_effect=lambda st, task: task == "rate"):
            got = {k: dashboard.lane_for(k, s) for k in ("sync", "details", "remove", "pull", "asr", "visual", "embed", "rate",
                                                          "knowledge", "summarize", "suggest", "discover", "weird")}
        self.assertEqual(got, {"sync": "youtube", "details": "youtube", "remove": "youtube", "pull": "local", "asr": "cloud",
                               "visual": "local", "embed": "local", "rate": "local", "knowledge": "local", "summarize": "cloud",
                               "suggest": "cloud", "discover": "cloud", "weird": "youtube"})


class ConcurrentWritesTest(unittest.TestCase):
    def test_a_second_writer_waits_instead_of_failing(self):
        import storage
        from tests.helpers import temp_db, playlist, video
        db = temp_db()
        storage.save_playlist(playlist(), [video("w1")], db_path=db)
        errors = []

        def hold():
            with storage.get_connection(db) as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("UPDATE videos SET notes = 'a' WHERE video_id = 'w1'")
                time.sleep(1)
                conn.commit()
        t = threading.Thread(target=hold)
        t.start()
        time.sleep(0.2)
        try:
            storage.update_videos(["w1"], notes="b", db_path=db)
        except Exception as e:
            errors.append(e)
        t.join()
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
