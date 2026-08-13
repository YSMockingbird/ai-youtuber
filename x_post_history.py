import hashlib
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_DB_PATH = Path(__file__).resolve().parent / "data" / "x_posts.db"


class XPostHistoryRepository:
    def __init__(self, path=None):
        configured_path = path or os.getenv("X_POST_HISTORY_DB_PATH", "").strip()
        self.path = Path(configured_path) if configured_path else DEFAULT_DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        return sqlite3.connect(str(self.path))

    def _initialize(self):
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS x_post_history (
                        history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        text_hash TEXT NOT NULL,
                        text TEXT NOT NULL,
                        status TEXT NOT NULL,
                        x_post_id TEXT,
                        error_message TEXT,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS
                    x_post_history_active_hash
                    ON x_post_history(text_hash)
                    WHERE status IN ('posting', 'posted')
                    """
                )
        except sqlite3.Error as exc:
            raise RuntimeError(
                f"X投稿履歴DBを初期化できませんでした: {self.path}"
            ) from exc

    def has_posted(self, text):
        text_hash = _text_hash(text)
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT 1 FROM x_post_history
                    WHERE text_hash = ? AND status = 'posted'
                    LIMIT 1
                    """,
                    (text_hash,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise RuntimeError("X投稿履歴を確認できませんでした。") from exc
        return row is not None

    def reserve(self, text):
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO x_post_history (
                        text_hash, text, status, created_at
                    ) VALUES (?, ?, 'posting', ?)
                    """,
                    (
                        _text_hash(text),
                        text,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                return cursor.lastrowid
        except sqlite3.IntegrityError as exc:
            raise RuntimeError(
                "同じ本文は投稿済み、または現在投稿処理中です。"
            ) from exc
        except sqlite3.Error as exc:
            raise RuntimeError("X投稿を予約できませんでした。") from exc

    def record_posted(self, history_id, post_id):
        self._update(history_id, "posted", post_id=post_id)

    def record_failed(self, history_id, error_message):
        self._update(
            history_id,
            "failed",
            error_message=str(error_message)[:500],
        )

    def _update(self, history_id, status, post_id=None, error_message=None):
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    UPDATE x_post_history
                    SET status = ?, x_post_id = ?, error_message = ?
                    WHERE history_id = ? AND status = 'posting'
                    """,
                    (
                        status,
                        post_id,
                        error_message,
                        history_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError(
                        "X投稿履歴の対象が見つからないか、すでに更新されています。"
                    )
        except sqlite3.Error as exc:
            raise RuntimeError("X投稿履歴を保存できませんでした。") from exc


def _text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
