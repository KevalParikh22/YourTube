import contextvars
import threading
import time
import unittest

import parallel


class ShareTest(unittest.TestCase):
    def test_empty_cases(self):
        self.assertEqual(parallel.share([], {"w1": lambda x: None}), {"w1": 0})
        self.assertEqual(parallel.share([1, 2, 3], {}), {})

    def test_two_workers_share_work_and_both_do_some(self):
        items = list(range(12))
        processed = []
        lock = threading.Lock()

        def fast_worker(x):
            time.sleep(0.005)
            with lock:
                processed.append(x)

        def slow_worker(x):
            time.sleep(0.02)
            with lock:
                processed.append(x)

        workers = {"fast": fast_worker, "slow": slow_worker}
        counts = parallel.share(items, workers)

        self.assertEqual(sorted(processed), items)
        self.assertEqual(sum(counts.values()), 12)
        self.assertGreater(counts["fast"], 0)
        self.assertGreater(counts["slow"], 0)

    def test_stop_halts_both_workers(self):
        items = list(range(100))
        processed = []
        lock = threading.Lock()
        stop_event = threading.Event()

        def worker(x):
            time.sleep(0.01)
            with lock:
                processed.append(x)
                if len(processed) >= 5:
                    stop_event.set()

        workers = {"w1": worker, "w2": worker}
        counts = parallel.share(items, workers, should_stop=stop_event.is_set)

        self.assertTrue(stop_event.is_set())
        self.assertLess(len(processed), 50)
        self.assertEqual(sum(counts.values()), len(processed))

    def test_three_consecutive_errors_stops_worker_and_other_finishes(self):
        items = list(range(10))
        processed = []
        errors = []
        lock = threading.Lock()

        def failing_worker(x):
            time.sleep(0.005)
            raise ValueError(f"fail {x}")

        def healthy_worker(x):
            time.sleep(0.01)
            with lock:
                processed.append(x)

        def on_error(name, item, exc):
            with lock:
                errors.append((name, item, str(exc)))

        workers = {"failing": failing_worker, "healthy": healthy_worker}
        counts = parallel.share(items, workers, on_error=on_error)

        self.assertEqual(counts["failing"], 0)
        self.assertEqual(len(errors), 3)
        self.assertEqual([name for name, _, _ in errors], ["failing", "failing", "failing"])
        # Remaining items were finished by healthy worker
        self.assertEqual(counts["healthy"], len(processed))
        self.assertEqual(len(processed), 7)
        self.assertEqual(len(processed) + len(errors), 10)

    def test_consecutive_errors_reset_on_success(self):
        # Worker fails twice, succeeds once, fails twice, succeeds once... total 4 errors without 3 in a row
        items = list(range(6))
        call_count = 0
        lock = threading.Lock()
        errors = []

        def intermittent_worker(x):
            nonlocal call_count
            with lock:
                call_count += 1
                curr = call_count
            if curr in (1, 2, 4, 5):
                raise RuntimeError("intermittent error")

        def on_error(name, item, exc):
            with lock:
                errors.append(item)

        workers = {"intermittent": intermittent_worker}
        counts = parallel.share(items, workers, on_error=on_error)

        self.assertEqual(len(errors), 4)
        self.assertEqual(counts["intermittent"], 2)

    def test_contextvars_copied_to_worker_threads(self):
        var = contextvars.ContextVar("test_var", default="initial")
        var.set("task_context_value")
        seen_values = []
        lock = threading.Lock()

        def worker(x):
            with lock:
                seen_values.append(var.get())

        parallel.share([1, 2], {"w": worker})
        self.assertEqual(seen_values, ["task_context_value", "task_context_value"])


if __name__ == "__main__":
    unittest.main()


class RetryOtherTest(unittest.TestCase):
    def test_one_item_failed_by_one_worker_goes_to_the_other(self):
        done, errors = [], []
        def quota(x):
            time.sleep(0.05)   # the other worker has found the queue empty by now and must wait
            raise RuntimeError("HTTP 429 quota")
        counts = parallel.share(["v1"], {"cloud": quota, "local": done.append},
                                on_error=lambda n, i, e: errors.append(i), retry_other=True)
        self.assertEqual((done, errors, counts), (["v1"], [], {"cloud": 0, "local": 1}))

    def test_item_failed_by_every_worker_is_reported_once(self):
        errors = []
        def fail(x):
            raise RuntimeError("down")
        counts = parallel.share([1, 2], {"a": fail, "b": fail}, on_error=lambda n, i, e: errors.append(i), retry_other=True)
        self.assertEqual((sorted(errors), counts), ([1, 2], {"a": 0, "b": 0}))

    def test_without_retry_other_errors_are_reported_at_once(self):
        errors, done = [], []
        def fail(x):
            raise RuntimeError("down")
        parallel.share([1], {"a": fail}, on_error=lambda n, i, e: errors.append(i))
        self.assertEqual(errors, [1])
