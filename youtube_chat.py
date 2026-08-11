import os
import time

import requests


YOUTUBE_VIDEO_API_URL = "https://www.googleapis.com/youtube/v3/videos"
YOUTUBE_LIVE_CHAT_API_URL = "https://www.googleapis.com/youtube/v3/liveChat/messages"


def get_live_chat_id():
    # .envからYouTube APIキーと配信動画IDを読み込みます。
    api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
    video_id = os.getenv("YOUTUBE_VIDEO_ID", "").strip()

    if not api_key or "your_" in api_key:
        raise RuntimeError("YOUTUBE_API_KEYが未設定です。.envにYouTube Data APIキーを設定してください。")

    if not video_id or "your_" in video_id:
        raise RuntimeError("YOUTUBE_VIDEO_IDが未設定です。.envにYouTube Liveの動画IDを設定してください。")

    params = {
        "part": "liveStreamingDetails",
        "id": video_id,
        "key": api_key,
    }

    try:
        response = requests.get(YOUTUBE_VIDEO_API_URL, params=params, timeout=10)
        response.raise_for_status()
    except requests.exceptions.Timeout as exc:
        raise RuntimeError("YouTube APIへの接続がタイムアウトしました。ネットワーク接続を確認してください。") from exc
    except requests.exceptions.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else "unknown"
        raise RuntimeError(f"YouTube APIでHTTPエラーが発生しました。status_code={status_code}") from exc
    except requests.exceptions.RequestException as exc:
        raise RuntimeError("YouTube APIへの接続に失敗しました。ネットワーク接続を確認してください。") from exc

    data = response.json()
    items = data.get("items", [])

    if not items:
        raise RuntimeError("指定したYOUTUBE_VIDEO_IDの動画が見つかりませんでした。動画IDを確認してください。")

    live_streaming_details = items[0].get("liveStreamingDetails", {})
    live_chat_id = live_streaming_details.get("activeLiveChatId")

    if not live_chat_id:
        raise RuntimeError("liveChatIdを取得できませんでした。配信がライブ中か、ライブチャットが有効か確認してください。")

    return live_chat_id


def fetch_chat_messages(live_chat_id, page_token=None):
    # liveChatIdを使って、YouTube Liveコメントを1回分取得します。
    api_key = os.getenv("YOUTUBE_API_KEY", "").strip()

    if not api_key or "your_" in api_key:
        raise RuntimeError("YOUTUBE_API_KEYが未設定です。.envにYouTube Data APIキーを設定してください。")

    if not live_chat_id:
        raise RuntimeError("live_chat_idが空です。先にliveChatIdを取得してください。")

    params = {
        "part": "snippet,authorDetails",
        "liveChatId": live_chat_id,
        "key": api_key,
    }

    if page_token:
        params["pageToken"] = page_token

    try:
        response = requests.get(YOUTUBE_LIVE_CHAT_API_URL, params=params, timeout=10)
        response.raise_for_status()
    except requests.exceptions.Timeout as exc:
        raise RuntimeError("YouTubeコメント取得がタイムアウトしました。ネットワーク接続を確認してください。") from exc
    except requests.exceptions.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else "unknown"
        raise RuntimeError(f"YouTubeコメント取得でHTTPエラーが発生しました。status_code={status_code}") from exc
    except requests.exceptions.RequestException as exc:
        raise RuntimeError("YouTubeコメント取得に失敗しました。ネットワーク接続を確認してください。") from exc

    data = response.json()
    messages = []

    for item in data.get("items", []):
        snippet = item.get("snippet", {})
        author_details = item.get("authorDetails", {})

        # Phase 1では通常のテキストコメントだけを扱います。
        if snippet.get("type") != "textMessageEvent":
            continue

        messages.append(
            {
                "message_id": item.get("id", ""),
                "user_id": author_details.get("channelId", ""),
                "user_name": author_details.get("displayName", "unknown"),
                "comment": snippet.get("displayMessage", ""),
                "published_at": snippet.get("publishedAt", ""),
            }
        )

    return {
        "messages": messages,
        "next_page_token": data.get("nextPageToken"),
        "polling_interval_millis": data.get("pollingIntervalMillis", 5000),
    }


def iter_chat_messages(live_chat_id, max_loops=None):
    # pollingIntervalMillisに従って、YouTube Liveコメントを継続取得します。
    page_token = None
    loop_count = 0

    while True:
        result = fetch_chat_messages(live_chat_id, page_token)
        page_token = result["next_page_token"]
        loop_count += 1

        yield result

        if max_loops is not None and loop_count >= max_loops:
            break

        wait_seconds = result["polling_interval_millis"] / 1000
        time.sleep(wait_seconds)
