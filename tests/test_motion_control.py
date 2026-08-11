import unittest
from unittest.mock import patch

from motion_control import MotionRateLimiter, get_motion_cooldown_seconds


class MotionRateLimiterTest(unittest.TestCase):
    def test_body_motion_is_suppressed_during_cooldown_but_head_remains(self):
        limiter = MotionRateLimiter(cooldown_seconds=10)
        motion = {
            "name": "greeting",
            "speed": 1.0,
            "intensity": 0.8,
            "head": "nod",
        }

        first = limiter.filter(motion, now=100)
        second = limiter.filter(motion, now=105)

        self.assertEqual(first["name"], "greeting")
        self.assertIsNone(second["name"])
        self.assertEqual(second["head"], "nod")

    def test_empty_motion_is_removed_after_cooldown_filter(self):
        limiter = MotionRateLimiter(cooldown_seconds=10)
        motion = {
            "name": "peace_sign",
            "speed": 1.0,
            "intensity": 0.8,
            "head": "none",
        }

        limiter.filter(motion, now=100)

        self.assertIsNone(limiter.filter(motion, now=101))

    @patch.dict("os.environ", {"MOTION_COOLDOWN_SECONDS": "8"})
    def test_cooldown_is_read_from_environment(self):
        self.assertEqual(get_motion_cooldown_seconds(), 8)

    @patch.dict("os.environ", {"MOTION_COOLDOWN_SECONDS": "121"})
    def test_invalid_cooldown_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "0〜120秒"):
            get_motion_cooldown_seconds()


if __name__ == "__main__":
    unittest.main()
