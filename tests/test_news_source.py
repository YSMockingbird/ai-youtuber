import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import requests

from news_source import (
    _merge_duplicate_articles,
    clear_news_cache,
    fetch_news_articles,
    get_news_feed_specs,
    parse_news_feed,
    select_news_article,
)


class NewsSourceTest(unittest.TestCase):
    def setUp(self):
        clear_news_cache()

    def tearDown(self):
        clear_news_cache()

    @staticmethod
    def rss_response(title="キャッシュ対象ニュース"):
        response = Mock()
        response.content = (
            "<?xml version='1.0' encoding='UTF-8'?>"
            "<rss><channel><title>テストニュース</title><item>"
            f"<title>{title}</title>"
            "<link>https://example.com/cache-news</link>"
            "<description>概要です。</description>"
            "</item></channel></rss>"
        ).encode("utf-8")
        return response

    @patch.dict("os.environ", {"NEWS_CACHE_SECONDS": "600"}, clear=False)
    @patch("news_source.requests.get")
    def test_fetch_reuses_cache_within_ten_minutes(self, request_mock):
        request_mock.return_value = self.rss_response()

        first = fetch_news_articles("https://example.com/rss.xml", now=100)
        second = fetch_news_articles("https://example.com/rss.xml", now=200)

        self.assertEqual(first, second)
        request_mock.assert_called_once()

    @patch.dict("os.environ", {"NEWS_CACHE_SECONDS": "600"}, clear=False)
    @patch("news_source.requests.get")
    def test_fetch_refreshes_cache_after_ten_minutes(self, request_mock):
        request_mock.side_effect = [
            self.rss_response("最初の記事"),
            self.rss_response("更新後の記事"),
        ]

        first = fetch_news_articles("https://example.com/rss.xml", now=100)
        second = fetch_news_articles("https://example.com/rss.xml", now=701)

        self.assertEqual(first[0]["title"], "最初の記事")
        self.assertEqual(second[0]["title"], "更新後の記事")
        self.assertEqual(request_mock.call_count, 2)

    @patch.dict("os.environ", {"NEWS_CACHE_SECONDS": "600"}, clear=False)
    @patch("news_source.requests.get")
    def test_expired_cache_is_used_when_refresh_fails(self, request_mock):
        request_mock.side_effect = [
            self.rss_response(),
            requests.exceptions.ConnectionError("接続失敗"),
        ]

        first = fetch_news_articles("https://example.com/rss.xml", now=100)
        stale = fetch_news_articles("https://example.com/rss.xml", now=701)

        self.assertEqual(stale, first)
        self.assertEqual(request_mock.call_count, 2)

    def test_parse_rss_articles(self):
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
          <channel>
            <title>テストニュース</title>
            <item>
              <title>新しい技術が発表</title>
              <link>https://example.com/news/1</link>
              <description><![CDATA[<p>技術の概要です。</p>]]></description>
              <pubDate>Tue, 11 Aug 2026 10:00:00 +0900</pubDate>
            </item>
          </channel>
        </rss>
        """.encode("utf-8")

        articles = parse_news_feed(xml_content, "https://example.com/rss.xml")

        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["source_name"], "テストニュース")
        self.assertEqual(articles[0]["summary"], "技術の概要です。")
        self.assertIsNotNone(articles[0]["published_datetime"])

    def test_sensitive_and_used_articles_are_skipped(self):
        articles = [
            {"title": "事故に関するニュース", "link": "https://example.com/1"},
            {"title": "使用済みニュース", "link": "https://example.com/2"},
            {"title": "新しい科学ニュース", "link": "https://example.com/3"},
        ]

        selected = select_news_article(
            articles,
            used_links={"https://example.com/2"},
        )

        self.assertEqual(selected["link"], "https://example.com/3")

    @patch.dict(
        "os.environ",
        {"NEWS_RSS_URL": "https://www.digital.go.jp/rss/news.xml"},
        clear=False,
    )
    def test_legacy_digital_agency_default_is_replaced_by_otaku_feeds(self):
        specs = get_news_feed_specs()

        categories = {spec["category"] for spec in specs}
        self.assertIn("vtuber", categories)
        self.assertIn("anime", categories)
        self.assertIn("game", categories)
        self.assertIn("gossip", categories)
        self.assertNotIn(
            "https://www.digital.go.jp/rss/news.xml",
            {spec["url"] for spec in specs},
        )

    @patch.dict(
        "os.environ",
        {"NEWS_RSS_URLS": "https://example.com/a.xml, https://example.com/b.xml"},
        clear=False,
    )
    def test_custom_feed_urls_replace_defaults(self):
        specs = get_news_feed_specs()

        self.assertEqual(
            [spec["url"] for spec in specs],
            ["https://example.com/a.xml", "https://example.com/b.xml"],
        )

    def test_recent_vtuber_gossip_is_prioritized_for_target_audience(self):
        now = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
        articles = [
            {
                "title": "行政手続きの新制度を発表",
                "summary": "行政に関する発表です。",
                "link": "https://example.com/government",
                "published_at": "Wed, 12 Aug 2026 11:00:00 +0000",
                "source_name": "一般ニュース",
                "source_count": 1,
                "audience_category": "custom",
            },
            {
                "title": "人気VTuberの配信発言が物議、本人が説明",
                "summary": "切り抜きをめぐってネット上で賛否が出ている。",
                "link": "https://example.com/vtuber",
                "published_at": "Wed, 12 Aug 2026 10:00:00 +0000",
                "source_name": "KAI-YOU",
                "source_count": 2,
                "independent_report_count": 2,
                "audience_category": "gossip",
            },
        ]

        selected = select_news_article(articles, now=now)

        self.assertEqual(selected["link"], "https://example.com/vtuber")
        self.assertTrue(selected["is_gossip"])
        self.assertEqual(selected["information_status"], "multiple_reports")

    def test_unverified_gossip_is_labeled_and_stale_news_is_skipped(self):
        now = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
        articles = [
            {
                "title": "VTuberの不仲疑惑がネットで噂に",
                "summary": "真偽は確認されていない。",
                "link": "https://example.com/rumor",
                "published_at": "Wed, 12 Aug 2026 09:00:00 +0000",
                "source_name": "J-CASTニュース",
                "source_count": 1,
                "audience_category": "gossip",
            },
            {
                "title": "一年前のアニメニュース",
                "summary": "古い記事です。",
                "link": "https://example.com/old",
                "published_at": "Tue, 12 Aug 2025 09:00:00 +0000",
                "source_name": "アニメニュース",
                "source_count": 3,
                "audience_category": "anime",
            },
        ]

        selected = select_news_article(articles, now=now, max_age_hours=168)

        self.assertEqual(selected["link"], "https://example.com/rumor")
        self.assertEqual(selected["information_status"], "unverified")

    def test_same_story_in_multiple_queries_counts_unique_publishers(self):
        base = {
            "title": "同じVTuberニュース - 媒体A",
            "summary": "同じ内容です。",
            "published_at": "Wed, 12 Aug 2026 09:00:00 +0000",
            "published_datetime": None,
            "source_url": "https://news.example.com/rss",
            "source_count": 1,
        }
        articles = [
            {**base, "link": "https://example.com/1", "source_name": "媒体A"},
            {**base, "link": "https://example.com/1", "source_name": "媒体A"},
        ]

        merged = _merge_duplicate_articles(articles)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["source_count"], 1)

    def test_low_value_video_and_untrusted_gossip_are_skipped(self):
        articles = [
            {
                "title": "ゲーム配信 #shorts #VTuber",
                "summary": "動画です。",
                "link": "https://youtube.com/watch?v=test",
                "source_name": "YouTube",
                "source_count": 1,
                "audience_category": "vtuber",
            },
            {
                "title": "VTuber炎上反応集",
                "summary": "出所不明の記事です。",
                "link": "https://unknown.example/gossip",
                "source_name": "unknown.example",
                "source_count": 1,
                "audience_category": "gossip",
            },
            {
                "title": "新作アニメの放送日が発表",
                "summary": "公式情報を紹介する記事です。",
                "link": "https://animeanime.jp/article/1",
                "source_name": "アニメ！アニメ！",
                "source_count": 1,
                "audience_category": "anime",
            },
        ]

        selected = select_news_article(articles)

        self.assertEqual(selected["link"], "https://animeanime.jp/article/1")

    def test_earnings_article_is_labeled_as_official_basis(self):
        article = {
            "title": "VTuber運営企業が第1四半期決算を発表",
            "summary": "業績について説明した。",
            "link": "https://kai-you.example/earnings",
            "source_name": "KAI-YOU",
            "source_count": 1,
            "audience_category": "vtuber",
        }

        selected = select_news_article([article])

        self.assertEqual(selected["information_status"], "official_basis")

    def test_similar_syndicated_headlines_are_merged(self):
        articles = [
            {
                "title": "ホロライブ運営がネット上の噂に回答する新方針を検討 - インサイド",
                "summary": "方針について説明しました。",
                "link": "https://inside.example/1",
                "published_at": "",
                "published_datetime": None,
                "source_name": "インサイド",
                "source_url": "https://news.google.com/rss",
                "source_count": 1,
            },
            {
                "title": "ホロライブ運営がネット上の噂に回答する新方針を検討（インサイド） - Yahoo!ニュース",
                "summary": "同じ記事の転載です。",
                "link": "https://yahoo.example/1",
                "published_at": "",
                "published_datetime": None,
                "source_name": "Yahoo!ニュース",
                "source_url": "https://news.google.com/rss",
                "source_count": 1,
            },
        ]

        merged = _merge_duplicate_articles(articles)

        self.assertEqual(len(merged), 1)


if __name__ == "__main__":
    unittest.main()
