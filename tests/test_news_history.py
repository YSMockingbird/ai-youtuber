import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from news_history import NewsHistoryRepository
from news_source import create_news_story_key


class NewsHistoryRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "news.db"
        self.repository = NewsHistoryRepository(
            self.database_path,
            retention_days=14,
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_recorded_news_is_excluded_during_retention_period(self):
        article = {
            "title": "VTuber事務所が新企画を発表 - テストニュース",
            "link": "https://example.com/news/1",
        }
        used_at = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)

        self.repository.record(article, now=used_at)
        exclusions = self.repository.recent_exclusions(
            now=datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
        )

        self.assertEqual(exclusions["links"], {article["link"]})
        self.assertEqual(
            exclusions["story_keys"],
            {create_news_story_key(article["title"])},
        )

    def test_expired_history_is_not_excluded_and_is_pruned_on_next_record(self):
        old_article = {
            "title": "古いニュースを紹介",
            "link": "https://example.com/news/old",
        }
        new_article = {
            "title": "新しいニュースを紹介",
            "link": "https://example.com/news/new",
        }
        self.repository.record(
            old_article,
            now=datetime(2026, 7, 1, tzinfo=timezone.utc),
        )

        current_time = datetime(2026, 8, 1, tzinfo=timezone.utc)
        exclusions = self.repository.recent_exclusions(now=current_time)
        self.assertEqual(exclusions, {"story_keys": set(), "links": set()})

        self.repository.record(new_article, now=current_time)
        with sqlite3.connect(str(self.database_path)) as connection:
            rows = connection.execute(
                "SELECT link FROM news_history ORDER BY link"
            ).fetchall()
        self.assertEqual(rows, [(new_article["link"],)])

    def test_invalid_article_has_meaningful_error(self):
        with self.assertRaisesRegex(ValueError, "ニュースタイトルが空"):
            self.repository.record({"title": "", "link": "https://example.com"})


if __name__ == "__main__":
    unittest.main()
