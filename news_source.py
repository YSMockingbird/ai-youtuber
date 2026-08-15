import html
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import quote_plus, urlparse

import requests


LEGACY_DEFAULT_NEWS_RSS_URL = "https://www.digital.go.jp/rss/news.xml"
DEFAULT_NEWS_MAX_AGE_HOURS = 168
DEFAULT_NEWS_CACHE_SECONDS = 600
_NEWS_CACHE = {}
_NEWS_CACHE_LOCK = threading.Lock()
DEFAULT_NEWS_FEED_QUERIES = (
    (
        "vtuber",
        "VTuber OR ホロライブ OR にじさんじ OR ぶいすぽ OR AI VTuber when:3d",
    ),
    (
        "anime",
        "アニメ OR 漫画 OR 声優 OR アニソン when:3d",
    ),
    (
        "game",
        "ゲーム OR eスポーツ OR ストリーマー OR Twitch when:3d",
    ),
    (
        "internet",
        "YouTube OR 配信者 OR ネットミーム OR 生成AI when:3d",
    ),
    (
        "gossip",
        "(VTuber OR ホロライブ OR にじさんじ OR 配信者) "
        "(炎上 OR 物議 OR 謝罪 OR 卒業 OR 活動休止 OR 噂) when:7d",
    ),
)

AUDIENCE_KEYWORDS = {
    "vtuber": (
        "vtuber", "ホロライブ", "にじさんじ", "ぶいすぽ", "ネオポルテ",
        "vshojo", "ライバー", "配信者", "卒業", "活動休止",
    ),
    "anime": (
        "アニメ", "漫画", "マンガ", "声優", "アニソン", "コミケ", "ラノベ",
    ),
    "game": (
        "ゲーム", "任天堂", "playstation", "steam", "eスポーツ", "twitch",
        "ポケモン", "ソシャゲ", "実況",
    ),
    "internet": (
        "youtube", "ネット", "ミーム", "生成ai", "人工知能", "shorts",
        "切り抜き", "アルゴリズム",
    ),
}
GOSSIP_KEYWORDS = {
    "炎上", "物議", "賛否", "批判", "謝罪", "騒動", "卒業", "活動休止",
    "契約解除", "声明", "トラブル", "不仲", "リーク", "暴露", "疑惑", "噂",
}
UNVERIFIED_KEYWORDS = {"噂", "疑惑", "リーク", "暴露", "憶測", "可能性"}
OFFICIAL_BASIS_KEYWORDS = {
    "決算", "業績", "公式発表", "公式声明", "声明を発表", "謝罪文", "会見",
    "プレスリリース", "運営が発表", "本人が発表",
}
HIGH_RISK_KEYWORDS = {
    "事故", "死亡", "死去", "自殺", "殺人", "逮捕", "性犯罪", "児童", "住所特定",
    "個人情報流出", "病気を告白", "闘病", "災害", "地震", "津波", "戦争",
}
CATEGORY_WEIGHTS = {
    "vtuber": 4.0,
    "gossip": 3.6,
    "anime": 3.0,
    "game": 2.8,
    "internet": 2.0,
    "custom": 1.0,
}
TRUSTED_MEDIA_FRAGMENTS = {
    "kai-you", "panora", "mogura", "game*spark", "gamespark", "inside",
    "インサイド", "ファミ通", "4gamer", "電ファミニコゲーマー", "オタク総研",
    "アニメ！アニメ！", "アニメイトタイムズ", "コミックナタリー", "mantanweb",
    "oricon", "real sound", "リアルサウンド", "j-cast", "ねとらぼ",
    "週刊女性prime", "女性自身", "smart flash", "スポニチ", "日刊スポーツ",
    "スポーツ報知", "モデルプレス", "yahoo", "itmedia", "av watch",
    "pr times",
}
LOW_VALUE_SOURCE_FRAGMENTS = {"youtube", "t.co", "x.com"}
LOW_VALUE_TITLE_PATTERNS = (
    r"画像\s*\d+\s*/\s*\d+",
    r"#shorts\b",
    r"反応集",
    r"ネタバレ注意",
    r"プレスリリース",
    r"\d+枚目の写真",
)
STORY_EVENT_KEYWORDS = {
    "訴訟", "提訴", "告訴", "和解", "判決", "起訴", "敗訴", "勝訴",
    "虚偽", "謝罪", "契約解除", "活動休止", "卒業", "引退", "復帰",
    "解散", "延期", "中止", "サービス終了", "配信停止", "受賞", "優勝",
}
GENERIC_STORY_ANCHOR_FRAGMENTS = {
    "vtuber", "youtube", "にじさんじ", "ホロライブ", "ぶいすぽ",
    "ニュース", "に関する", "について", "をめぐる", "所属", "ライバー",
    "配信者", "事務所", "運営", "公式", "大会", "発表",
}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def _google_news_search_url(query):
    return (
        "https://news.google.com/rss/search?q="
        f"{quote_plus(query)}&hl=ja&gl=JP&ceid=JP:ja"
    )


def get_news_feed_specs():
    # NEWS_RSS_URLSがあれば完全に置き換える。旧デジタル庁の既定値だけは移行時に無視する。
    raw_urls = os.getenv("NEWS_RSS_URLS", "").strip()
    legacy_url = os.getenv("NEWS_RSS_URL", "").strip()
    if raw_urls:
        urls = [url.strip() for url in raw_urls.split(",") if url.strip()]
        return [
            {"url": _validate_feed_url(url), "category": "custom"}
            for url in urls
        ]
    if legacy_url and legacy_url != LEGACY_DEFAULT_NEWS_RSS_URL:
        return [
            {"url": _validate_feed_url(legacy_url), "category": "custom"}
        ]
    return [
        {"url": _google_news_search_url(query), "category": category}
        for category, query in DEFAULT_NEWS_FEED_QUERIES
    ]


def get_news_timeout_seconds():
    raw_timeout = os.getenv("NEWS_TIMEOUT_SECONDS", "10").strip()
    try:
        timeout_seconds = int(raw_timeout)
    except ValueError as exc:
        raise RuntimeError("NEWS_TIMEOUT_SECONDSは整数で設定してください。") from exc
    if not 1 <= timeout_seconds <= 60:
        raise RuntimeError("NEWS_TIMEOUT_SECONDSは1〜60秒で設定してください。")
    return timeout_seconds


def get_news_max_age_hours():
    raw_hours = os.getenv(
        "NEWS_MAX_AGE_HOURS",
        str(DEFAULT_NEWS_MAX_AGE_HOURS),
    ).strip()
    try:
        hours = int(raw_hours)
    except ValueError as exc:
        raise RuntimeError("NEWS_MAX_AGE_HOURSは整数で設定してください。") from exc
    if not 6 <= hours <= 24 * 30:
        raise RuntimeError("NEWS_MAX_AGE_HOURSは6〜720で設定してください。")
    return hours


def get_news_cache_seconds():
    raw_seconds = os.getenv(
        "NEWS_CACHE_SECONDS",
        str(DEFAULT_NEWS_CACHE_SECONDS),
    ).strip()
    try:
        seconds = int(raw_seconds)
    except ValueError as exc:
        raise RuntimeError("NEWS_CACHE_SECONDSは整数で設定してください。") from exc
    if not 0 <= seconds <= 3600:
        raise RuntimeError("NEWS_CACHE_SECONDSは0〜3600で設定してください。")
    return seconds


def clear_news_cache():
    # テストや設定変更後に、プロセス内のRSSキャッシュを明示的に破棄します。
    with _NEWS_CACHE_LOCK:
        _NEWS_CACHE.clear()


def fetch_news_articles(rss_url=None, now=None):
    specs = (
        [{"url": _validate_feed_url(rss_url), "category": "custom"}]
        if rss_url
        else get_news_feed_specs()
    )
    cache_key = tuple((spec["url"], spec["category"]) for spec in specs)
    current_time = time.monotonic() if now is None else float(now)
    cache_seconds = get_news_cache_seconds()
    with _NEWS_CACHE_LOCK:
        cached = _NEWS_CACHE.get(cache_key)
    if (
        cached is not None
        and current_time - cached["fetched_at"] < cache_seconds
    ):
        return _copy_articles(cached["articles"])

    timeout_seconds = get_news_timeout_seconds()
    articles = []
    errors = []
    for spec in specs:
        try:
            response = requests.get(
                spec["url"],
                timeout=timeout_seconds,
                headers={"User-Agent": "ganna-topic-reader/2.0"},
            )
            response.raise_for_status()
            parsed = parse_news_feed(response.content, spec["url"])
            for article in parsed:
                article["audience_category"] = spec["category"]
            articles.extend(parsed)
        except (RuntimeError, requests.exceptions.RequestException) as exc:
            errors.append(f"{spec['category']}: {exc}")

    if not articles:
        detail = "; ".join(errors) or "取得対象がありません。"
        if cached is not None:
            print(
                "ニュースフィードの更新に失敗したため、"
                f"期限切れキャッシュを使用します: {detail}"
            )
            return _copy_articles(cached["articles"])
        raise RuntimeError(f"ニュースフィードを一件も取得できませんでした: {detail}")
    if errors:
        print("一部のニュースフィードを取得できませんでした: " + "; ".join(errors))
    merged = _merge_duplicate_articles(articles)
    with _NEWS_CACHE_LOCK:
        _NEWS_CACHE[cache_key] = {
            "articles": _copy_articles(merged),
            "fetched_at": current_time,
        }
    return _copy_articles(merged)


def fetch_news_articles_for_query(query, now=None):
    # 指定配信ではGoogle News検索を絞り、無関係な一般ニュースを混ぜません。
    normalized_query = " ".join(str(query or "").split())
    if not 2 <= len(normalized_query) <= 50:
        raise ValueError("ニュース検索テーマは2〜50文字にしてください。")
    return fetch_news_articles(
        rss_url=_google_news_search_url(normalized_query),
        now=now,
    )


def _copy_articles(articles):
    return [dict(article) for article in articles]


def parse_news_feed(xml_content, source_url):
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as exc:
        raise RuntimeError("ニュースRSSをXMLとして読み取れませんでした。") from exc

    feed_name = _find_feed_title(root) or source_url
    item_elements = [
        element
        for element in root.iter()
        if _local_name(element.tag) in {"item", "entry"}
    ]
    articles = []
    for item in item_elements:
        title = _clean_text(_element_text(item, {"title"}), 200)
        link = _find_link(item).strip()
        summary = _clean_text(
            _element_text(item, {"description", "summary", "content"}),
            500,
        )
        published_at = _clean_text(
            _element_text(item, {"pubDate", "published", "updated", "date"}),
            100,
        )
        item_source = _clean_text(_element_text(item, {"source"}), 100)
        if not title or not link:
            continue
        source_name = item_source or feed_name
        articles.append(
            {
                "title": title,
                "summary": summary,
                "link": link,
                "published_at": published_at,
                "published_datetime": _parse_published_datetime(published_at),
                "source_name": source_name,
                "source_url": source_url,
                "source_count": 1,
            }
        )
    if not articles:
        raise RuntimeError("ニュースRSSに利用可能な記事が見つかりませんでした。")
    return articles


def select_news_article(
    articles,
    used_links=None,
    theme_text="",
    now=None,
    max_age_hours=None,
    excluded_story_keys=None,
):
    used_links = used_links or set()
    excluded_story_keys = set(excluded_story_keys or ())
    excluded_story_map = {key: True for key in excluded_story_keys}
    current_time = _normalize_now(now)
    maximum_age = max_age_hours or get_news_max_age_hours()
    ranked = []
    for index, article in enumerate(articles):
        if article.get("link") in used_links:
            continue
        story_key = create_news_story_key(article.get("title", ""))
        if story_key in excluded_story_keys or _find_similar_story_key(
            story_key,
            excluded_story_map,
        ) is not None:
            continue
        combined_text = " ".join(
            (str(article.get("title", "")), str(article.get("summary", "")))
        ).lower()
        if _is_low_value_article(article, combined_text):
            continue
        if (
            "news.google.com" in str(article.get("source_url", "")).lower()
            and not _is_trusted_media(article)
        ):
            continue
        if (
            article.get("audience_category") == "gossip"
            and not _is_trusted_media(article)
        ):
            continue
        if any(keyword.lower() in combined_text for keyword in HIGH_RISK_KEYWORDS):
            continue
        age_hours = _article_age_hours(article, current_time)
        if age_hours is not None and age_hours > maximum_age:
            continue
        score = _score_article(article, combined_text, theme_text, age_hours)
        ranked.append((score, -index, article))
    if not ranked:
        return None
    _, _, selected = max(ranked, key=lambda item: item[:2])
    selected = dict(selected)
    selected["information_status"] = _classify_information_status(selected)
    selected["is_gossip"] = _contains_any(
        f"{selected.get('title', '')} {selected.get('summary', '')}",
        GOSSIP_KEYWORDS,
    )
    return selected


def _score_article(article, combined_text, theme_text, age_hours):
    category = article.get("audience_category", "custom")
    score = CATEGORY_WEIGHTS.get(category, CATEGORY_WEIGHTS["custom"])
    for keywords in AUDIENCE_KEYWORDS.values():
        score += min(sum(keyword in combined_text for keyword in keywords), 3) * 0.8
    if _contains_any(combined_text, GOSSIP_KEYWORDS):
        score += 1.4
    source_count = max(int(article.get("source_count", 1)), 1)
    score += min(source_count - 1, 3) * 0.2
    if _is_trusted_media(article):
        score += 1.2
    if "pr times" in _source_identity(article):
        score -= 2.0
    normalized_theme = str(theme_text or "").lower()
    if normalized_theme:
        overlap = _bigram_overlap(normalized_theme, combined_text)
        score += min(overlap, 5) * 0.45
    if age_hours is None:
        score -= 0.8
    elif age_hours <= 24:
        score += 3.0
    elif age_hours <= 72:
        score += 1.8
    else:
        score += 0.5
    return score


def _classify_information_status(article):
    text = f"{article.get('title', '')} {article.get('summary', '')}".lower()
    if _contains_any(text, UNVERIFIED_KEYWORDS):
        return "unverified"
    if _contains_any(text, OFFICIAL_BASIS_KEYWORDS):
        return "official_basis"
    # 同じ見出しの転載数は独立した裏取りとみなさない。
    if int(article.get("independent_report_count", 1)) >= 2:
        return "multiple_reports"
    return "single_report"


def _merge_duplicate_articles(articles):
    merged = {}
    order = []
    for article in articles:
        key = _normalized_story_key(article.get("title", ""))
        if not key:
            key = article.get("link", "")
        similar_key = _find_similar_story_key(key, merged)
        if similar_key is not None:
            key = similar_key
        if key not in merged:
            merged[key] = dict(article)
            merged[key]["reporting_sources"] = [article["source_name"]]
            order.append(key)
            continue
        existing = merged[key]
        sources = set(existing.get("reporting_sources", [existing["source_name"]]))
        sources.add(article["source_name"])
        existing["reporting_sources"] = sorted(sources)
        existing["source_count"] = len(sources)
    return [merged[key] for key in order]


def _find_similar_story_key(candidate_key, existing):
    if len(candidate_key) < 20:
        return None
    candidate_terms = {
        candidate_key[index : index + 2]
        for index in range(len(candidate_key) - 1)
    }
    for existing_key in existing:
        if len(existing_key) < 20:
            continue
        existing_terms = {
            existing_key[index : index + 2]
            for index in range(len(existing_key) - 1)
        }
        union = candidate_terms | existing_terms
        if union and len(candidate_terms & existing_terms) / len(union) >= 0.62:
            return existing_key
        if _has_matching_event_signature(candidate_key, existing_key):
            return existing_key
    return None


def _has_matching_event_signature(left_key, right_key):
    # 見出しの言い回しが違っても、同じ固有対象と複数の出来事語が一致すれば同一事件とみなします。
    shared_events = {
        keyword
        for keyword in STORY_EVENT_KEYWORDS
        if keyword in left_key and keyword in right_key
    }
    if len(shared_events) < 2:
        return False

    left_anchors = {
        left_key[index : index + 4]
        for index in range(len(left_key) - 3)
    }
    right_anchors = {
        right_key[index : index + 4]
        for index in range(len(right_key) - 3)
    }
    return any(
        _is_informative_story_anchor(anchor)
        for anchor in left_anchors & right_anchors
    )


def _is_informative_story_anchor(anchor):
    if any(
        fragment in anchor or anchor in fragment
        or _shared_bigram_count(anchor, fragment) >= 2
        for fragment in GENERIC_STORY_ANCHOR_FRAGMENTS
    ):
        return False
    if any(
        keyword[index : index + 2] in anchor
        for keyword in STORY_EVENT_KEYWORDS
        for index in range(len(keyword) - 1)
    ):
        return False
    return True


def _shared_bigram_count(left, right):
    left_terms = {
        left[index : index + 2] for index in range(len(left) - 1)
    }
    right_terms = {
        right[index : index + 2] for index in range(len(right) - 1)
    }
    return len(left_terms & right_terms)


def _normalized_story_key(title):
    value = re.sub(r"\s+-\s+[^-]+$", "", str(title or "")).lower()
    return re.sub(r"[^0-9a-zぁ-んァ-ヶ一-龠々ー]", "", value)[:100]


def create_news_story_key(title):
    # URLが変わっても同じ話題を判定できるよう、媒体名や記号を除いたキーを作ります。
    return _normalized_story_key(title)


def _parse_published_datetime(value):
    normalized = str(value or "").strip()
    if not normalized:
        return None
    try:
        parsed = parsedate_to_datetime(normalized)
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_now(now):
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _article_age_hours(article, now):
    published = article.get("published_datetime")
    if isinstance(published, str):
        published = _parse_published_datetime(published)
    if published is None:
        published = _parse_published_datetime(article.get("published_at"))
    if published is None:
        return None
    return max((now - published).total_seconds() / 3600, 0)


def _contains_any(text, keywords):
    normalized = str(text or "").lower()
    return any(str(keyword).lower() in normalized for keyword in keywords)


def _source_identity(article):
    return " ".join(
        (
            str(article.get("source_name", "")),
            str(article.get("source_url", "")),
            str(article.get("link", "")),
        )
    ).lower()


def _is_trusted_media(article):
    identity = _source_identity(article)
    return any(fragment in identity for fragment in TRUSTED_MEDIA_FRAGMENTS)


def _is_low_value_article(article, combined_text):
    identity = _source_identity(article)
    if any(fragment in identity for fragment in LOW_VALUE_SOURCE_FRAGMENTS):
        return True
    title = str(article.get("title", ""))
    if any(re.search(pattern, title, re.IGNORECASE) for pattern in LOW_VALUE_TITLE_PATTERNS):
        return True
    if "漫画" in combined_text and "作品" in combined_text and "炎上したvtuber" in combined_text:
        return True
    return False


def _bigram_overlap(left, right):
    left_terms = {
        left[index : index + 2] for index in range(max(len(left) - 1, 0))
    }
    right_terms = {
        right[index : index + 2] for index in range(max(len(right) - 1, 0))
    }
    return len(left_terms & right_terms)


def _validate_feed_url(url):
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise RuntimeError(
            "ニュースフィードURLはhttp://またはhttps://で指定してください。"
        )
    return parsed.geturl()


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
