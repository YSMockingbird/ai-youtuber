import os
import threading
from pathlib import Path


MAX_LOG_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 3


class RotatingTextStream:
    def __init__(self, path, max_bytes=MAX_LOG_BYTES, backup_count=LOG_BACKUP_COUNT):
        self.path = Path(path)
        self.max_bytes = int(max_bytes)
        self.backup_count = int(backup_count)
        if self.max_bytes <= 0:
            raise ValueError("ログの容量上限は1バイト以上にしてください。")
        if self.backup_count < 0:
            raise ValueError("ログの保持世代数は0以上にしてください。")
        self._lock = threading.Lock()
        self._file = None
        self._size_bytes = 0
        self._open()

    def write(self, value):
        text = str(value)
        if not text:
            return 0
        encoded_size = len(text.encode("utf-8"))
        with self._lock:
            if self._size_bytes and self._size_bytes + encoded_size > self.max_bytes:
                self._rotate()
            self._file.write(text)
            self._file.flush()
            self._size_bytes += encoded_size
        return len(text)

    def flush(self):
        with self._lock:
            if self._file is not None and not self._file.closed:
                self._file.flush()

    def close(self):
        with self._lock:
            if self._file is not None and not self._file.closed:
                self._file.flush()
                self._file.close()

    def isatty(self):
        return False

    def fileno(self):
        return self._file.fileno()

    @property
    def encoding(self):
        return "utf-8"

    @property
    def closed(self):
        return self._file is None or self._file.closed

    def _open(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self.path.parent, 0o700)
            self._file = self.path.open("a", encoding="utf-8", buffering=1)
            os.chmod(self.path, 0o600)
            self._size_bytes = self.path.stat().st_size
        except OSError as exc:
            raise RuntimeError(
                f"常駐サービスのログを開けません: {self.path}"
            ) from exc

    def _rotate(self):
        self._file.close()
        try:
            if self.backup_count > 0:
                oldest = self.path.with_name(
                    f"{self.path.name}.{self.backup_count}"
                )
                oldest.unlink(missing_ok=True)
                for generation in range(self.backup_count - 1, 0, -1):
                    source = self.path.with_name(f"{self.path.name}.{generation}")
                    destination = self.path.with_name(
                        f"{self.path.name}.{generation + 1}"
                    )
                    if source.exists():
                        source.replace(destination)
                if self.path.exists():
                    self.path.replace(self.path.with_name(f"{self.path.name}.1"))
            else:
                self.path.unlink(missing_ok=True)
        except OSError as exc:
            raise RuntimeError(
                f"常駐サービスのログを切り替えられません: {self.path}"
            ) from exc
        self._open()


def configure_service_logging(log_directory):
    log_dir = Path(log_directory)
    return (
        RotatingTextStream(log_dir / "admin-service.log"),
        RotatingTextStream(log_dir / "admin-service-error.log"),
    )
