import unittest

from metrics import calculate_category_metrics, calculate_speed_duration, format_seconds, format_hms


class MetricsTest(unittest.TestCase):
    def test_formatting(self):
        self.assertEqual(format_seconds(0), "0s")
        self.assertEqual(format_seconds(45), "45s")
        self.assertEqual(format_seconds(125), "2m 5s")
        self.assertEqual(format_seconds(3 * 3600 + 120), "3h 2m")
        self.assertEqual(format_hms(3725), "01:02:05")

    def test_speed(self):
        self.assertEqual(calculate_speed_duration(600, 2.0), 300)
        with self.assertRaises(ValueError):
            calculate_speed_duration(600, 0)

    def test_remaining_excludes_completed(self):
        videos = [
            {"duration_seconds": 600, "status": "unwatched"},
            {"duration_seconds": 1200, "status": "in_progress"},
            {"duration_seconds": 1800, "status": "completed"},
        ]
        m = calculate_category_metrics(videos)
        self.assertEqual(m["total_videos"], 3)
        self.assertEqual(m["completed_videos"], 1)
        self.assertEqual(m["durations"]["1.0x"]["seconds"], 3600)
        self.assertEqual(m["durations"]["1.5x"]["seconds"], 2400)
        self.assertEqual(m["remaining"]["1.0x"]["seconds"], 1800)
        self.assertEqual(m["remaining"]["2.0x"]["seconds"], 900)
        self.assertEqual(m["time_saved_2x"]["seconds"], 1800)
        self.assertEqual(m["completion_percentage"], 33.3)

    def test_empty(self):
        m = calculate_category_metrics([])
        self.assertEqual(m["total_videos"], 0)
        self.assertEqual(m["completion_percentage"], 0.0)
