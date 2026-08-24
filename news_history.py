import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from local_storage import secure_sqlite_storage
from llm.config import PROJECT_ROOT
from news_source import create_news_story_key


DEFAULT_NEWS_HISTORY_DAYS = 14


class NewsHistoryRepository:
    def __init__(self, database_path, retention_days=DEFAULT_NEWS_HISTORY_DAYS):
        self.database_path = Path(database_path)
        self.retention_days = int(retention_days)
        if not 1 <= self.retention_days <= 90:
            raise ValueError("ニュース既読保持日数は1〜90日で指定してください。")
        secure_sqlite_storage(self.database_path)
        self._initialize()
        secure_sqlite_storage(self.database_path)

    def _connect(self):
        return sqlite3.connect(str(self.database_path))

    def _initialize(self):
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS news_history (
                        story_key TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        link TEXT NOT NULL,
                        first_used_at TEXT NOT NULL,
                        last_used_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_news_history_last_used "
                    "ON news_history(last_used_at DESC)"
                )
        except sqlite3.Error as exc:
            raise RuntimeError(
                "ニュース既読データベースを初期化できません: "
                f"{self.database_path}"
            ) from exc

    def recent_exclusions(self, now=None):
        current_time = _normalize_datetime(now)
        cutoff = current_time - timedelta(days=self.retention_days)
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT story_key, link
                    FROM news_history
                    WHERE last_used_at >= ?
                    """,
                    (cutoff.isoformat(),),
                ).fetchall()
        except sqlite3.Error as exc:
            raise RuntimeError(
                "ニュース既読履歴をSQLiteから読み込めませんでした。"
            ) from exc
        return {
            "story_keys": {row[0] for row in rows},
            "links": {row[1] for row in rows},
        }

    def record(self, article, now=None):
        title = str(article.get("title", "")).strip()
        link = str(article.get("link", "")).strip()
        story_key = create_news_story_key(title)
        if not title:
            raise ValueError("既読保存するニュースタイトルが空です。")
        if not link:
            raise ValueError("既読保存するニュースURLが空です。")
        if not story_key:
            raise ValueError("ニュースタイトルから既読判定キーを作成できません。")
        used_datetime = _normalize_datetime(now)
        used_at = used_datetime.isoformat()
        cutoff = used_datetime - timedelta(days=self.retention_days)
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO news_history (
                        story_key, title, link, first_used_at, last_used_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(story_key) DO UPDATE SET
                        title = excluded.title,
                        link = excluded.link,
                        last_used_at = excluded.last_used_at
                    """,
                    (story_key, title[:200], link[:1000], used_at, used_at),
                )
                # 保存期間を過ぎた履歴を削除し、長時間運用でもDBを肥大化させません。
                connection.execute(
                    "DELETE FROM news_history WHERE last_used_at < ?",
                    (cutoff.isoformat(),),
                )
        except sqlite3.Error as exc:
            raise RuntimeError(
                "ニュース既読履歴をSQLiteへ保存できませんでした。"
            ) from exc
        secure_sqlite_storage(self.database_path)


def get_news_history_repository():
    database_path = os.getenv(
        "NEWS_HISTORY_DB_PATH",
        str(PROJECT_ROOT / "data" / "news_history.db"),
    ).strip()
    if not database_path:
        raise RuntimeError("NEWS_HISTORY_DB_PATHが空です。")
    database_path = Path(database_path).expanduser()
    if not database_path.is_absolute():
        database_path = PROJECT_ROOT / database_path
    raw_days = os.getenv(
        "NEWS_HISTORY_DAYS",
        str(DEFAULT_NEWS_HISTORY_DAYS),
    ).strip()
    try:
        retention_days = int(raw_days)
    except ValueError as exc:
        raise RuntimeError("NEWS_HISTORY_DAYSは整数で設定してください。") from exc
    return NewsHistoryRepository(database_path, retention_days=retention_days)


def _normalize_datetime(value):
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)
