import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from time_context import get_current_datetime_context


class TimeContextTest(unittest.TestCase):
    @patch.dict("os.environ", {"AITUBER_TIMEZONE": "Asia/Tokyo"})
    def test_utc_time_is_converted_to_japan_time(self):
        result = get_current_datetime_context(
            datetime(2026, 8, 15, 13, 30, tzinfo=timezone.utc)
        )

        self.assertIn("2026-08-15(土) 22:30 Asia/Tokyo", result)
        self.assertIn("関連する場合だけ", result)

    @patch.dict("os.environ", {"AITUBER_TIMEZONE": "invalid/timezone"})
    def test_invalid_timezone_has_meaningful_error(self):
        with self.assertRaisesRegex(RuntimeError, "AITUBER_TIMEZONE"):
            get_current_datetime_context()


if __name__ == "__main__":
    unittest.main()
