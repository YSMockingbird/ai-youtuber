import unittest

from news_source import parse_news_feed, select_news_article


class NewsSourceTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
