import html
import os
import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

import requests


DEFAULT_NEWS_RSS_URL = "https://www.digital.go.jp/rss/news.xml"
SENSITIVE_KEYWORDS = {
    "事故",
    "死亡",
    "死去",
    "殺人",
    "逮捕",
    "災害",
    "地震",
    "津波",
    "台風",
    "戦争",
    "紛争",
    "選挙",
}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def get_news_rss_url():
    rss_url = os.getenv("NEWS_RSS_URL", DEFAULT_NEWS_RSS_URL).strip()
    if not rss_url:
        raise RuntimeError("NEWS_RSS_URLが空です。.envにニュースRSSのURLを設定してください。")
    if not rss_url.startswith(("https://", "http://")):
        raise RuntimeError("NEWS_RSS_URLはhttp://またはhttps://で始まるURLにしてください。")
    return rss_url


def get_news_timeout_seconds():
    raw_timeout = os.getenv("NEWS_TIMEOUT_SECONDS", "10").strip()
    try:
        timeout_seconds = int(raw_timeout)
    except ValueError as exc:
        raise RuntimeError("NEWS_TIMEOUT_SECONDSは整数で設定してください。") from exc
    if not 1 <= timeout_seconds <= 60:
        raise RuntimeError("NEWS_TIMEOUT_SECONDSは1〜60秒で設定してください。")
    return timeout_seconds


def fetch_news_articles(rss_url=None):
    # RSSから見出し、概要、リンク、公開日時を取得します。
    target_url = rss_url or get_news_rss_url()
    timeout_seconds = get_news_timeout_seconds()

    try:
        response = requests.get(
            target_url,
            timeout=timeout_seconds,
            headers={"User-Agent": "ai-youtuber-rss-reader/1.0"},
        )
        response.raise_for_status()
    except requests.exceptions.Timeout as exc:
        raise RuntimeError("ニュースRSSの取得がタイムアウトしました。") from exc
    except requests.exceptions.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else "unknown"
        raise RuntimeError(
            f"ニュースRSSの取得でHTTPエラーが発生しました。status_code={status_code}"
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise RuntimeError("ニュースRSSへの接続に失敗しました。") from exc

    return parse_news_feed(response.content, target_url)


def parse_news_feed(xml_content, source_url):
    # RSS 1.0、RSS 2.0、Atomの基本的な項目を同じ形式へ変換します。
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as exc:
        raise RuntimeError("ニュースRSSをXMLとして読み取れませんでした。") from exc

    source_name = _find_feed_title(root) or source_url
    item_elements = [
        element
        for element in root.iter()
        if _local_name(element.tag) in {"item", "entry"}
    ]
    articles = []

    for item in item_elements:
        title = _element_text(item, {"title"})
        link = _find_link(item)
        summary = _element_text(item, {"description", "summary", "content"})
        published_at = _element_text(
            item,
            {"pubDate", "published", "updated", "date"},
        )

        title = _clean_text(title, 200)
        summary = _clean_text(summary, 500)
        link = link.strip()
        published_at = _clean_text(published_at, 100)

        if not title or not link:
            continue

        articles.append(
            {
                "title": title,
                "summary": summary,
                "link": link,
                "published_at": published_at,
                "source_name": source_name,
                "source_url": source_url,
            }
        )

    if not articles:
        raise RuntimeError("ニュースRSSに利用可能な記事が見つかりませんでした。")

    return articles


def select_news_article(articles, used_links=None):
    # 使用済み記事と慎重な扱いが必要な話題を避けます。
    used_links = used_links or set()
    for article in articles:
        if article["link"] in used_links:
            continue
        if any(keyword in article["title"] for keyword in SENSITIVE_KEYWORDS):
            continue
        return article
    return None


def _local_name(tag):
    return tag.rsplit("}", 1)[-1]


def _find_feed_title(root):
    for element in root.iter():
        if _local_name(element.tag) == "item":
            break
        if _local_name(element.tag) == "title" and element.text:
            return _clean_text(element.text, 100)
    return ""


def _element_text(parent, names):
    for element in parent.iter():
        if element is parent:
            continue
        if _local_name(element.tag) in names and element.text:
            return element.text
    return ""


def _find_link(item):
    for element in item.iter():
        if _local_name(element.tag) != "link":
            continue
        href = element.attrib.get("href", "").strip()
        if href:
            return href
        if element.text:
            return element.text.strip()
    return ""


def _clean_text(value, max_length):
    extractor = _TextExtractor()
    extractor.feed(html.unescape(value or ""))
    text = " ".join(extractor.parts)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_length]
