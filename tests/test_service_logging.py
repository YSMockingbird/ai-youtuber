import stat
import tempfile
import unittest
from pathlib import Path

from service_logging import RotatingTextStream


class RotatingTextStreamTest(unittest.TestCase):
    def test_rotates_at_size_limit_and_keeps_three_backups(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            log_path = Path(temporary_directory) / "service.log"
            stream = RotatingTextStream(
                log_path,
                max_bytes=20,
                backup_count=3,
            )
            for index in range(10):
                stream.write(f"line-{index:02d}\n")
            stream.close()

            backups = sorted(log_path.parent.glob("service.log.*"))
            self.assertEqual(len(backups), 3)
            self.assertLessEqual(log_path.stat().st_size, 20)
            self.assertIn("line-09", log_path.read_text(encoding="utf-8"))

    def test_limits_directory_and_file_permissions_to_owner(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            log_directory = Path(temporary_directory) / "logs"
            log_path = log_directory / "service.log"
            stream = RotatingTextStream(log_path)
            stream.write("test\n")
            stream.close()

            directory_mode = stat.S_IMODE(log_directory.stat().st_mode)
            file_mode = stat.S_IMODE(log_path.stat().st_mode)
            self.assertEqual(directory_mode, 0o700)
            self.assertEqual(file_mode, 0o600)


if __name__ == "__main__":
    unittest.main()
