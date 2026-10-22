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
        patcher = mock.patch("embedder.run_embed", return_value=True)
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
        self.assertTrue(job.stop_requested())
        gate.set()
        self.assertTrue(wait_until(lambda: not job.snapshot()["running"] and not job.snapshot()["queue"]))
        self.assertTrue(job.snapshot()["recent"][-1]["stopped"])


if __name__ == "__main__":
    unittest.main()
