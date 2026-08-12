import os
import queue
import threading
import time

import requests


YOUTUBE_VIDEO_API_URL = "https://www.googleapis.com/youtube/v3/videos"
YOUTUBE_LIVE_CHAT_API_URL = "https://www.googleapis.com/youtube/v3/liveChat/messages"


def resolve_youtube_video_id():
    # 手動IDを優先し、未設定の場合だけOAuthで現在ライブ中の配信を検索します。
    configured_video_id = os.getenv("YOUTUBE_VIDEO_ID", "").strip()
    if configured_video_id and "your_" not in configured_video_id:
        print(f"設定済みのYouTube動画IDを使用します：{configured_video_id}")
        return configured_video_id

    # 手動ID利用時はOAuthライブラリを読み込まず、従来どおり動作させます。
    from youtube_oauth import find_active_youtube_broadcast

    broadcast = find_active_youtube_broadcast()
    print(
        "現在ライブ中の配信を自動取得しました："
        f"{broadcast['title']} / video_id={broadcast['video_id']}"
    )
    return broadcast["video_id"]


def get_live_chat_id():
    # 手動またはOAuthで動画IDを決定し、ライブチャットIDを取得します。
    api_key = os.getenv("YOUTUBE_API_KEY", "").strip()

    if not api_key or "your_" in api_key:
        raise RuntimeError("YOUTUBE_API_KEYが未設定です。.envにYouTube Data APIキーを設定してください。")

    video_id = resolve_youtube_video_id()

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


def iter_chat_messages(
    live_chat_id,
    max_loops=None,
    wait_callback=None,
    wait_step_seconds=0.25,
    stop_event=None,
):
    # pollingIntervalMillisに従って、YouTube Liveコメントを継続取得します。
    if float(wait_step_seconds) <= 0:
        raise ValueError("wait_step_secondsは0より大きくしてください。")
    page_token = None
    loop_count = 0
    previous_fetch_at = None

    while True:
        if stop_event is not None and stop_event.is_set():
            return
        fetch_started_at = time.monotonic()
        result = fetch_chat_messages(live_chat_id, page_token)
        page_token = result["next_page_token"]
        loop_count += 1
        actual_interval = (
            "初回"
            if previous_fetch_at is None
            else f"{fetch_started_at - previous_fetch_at:.1f}秒"
        )
        previous_fetch_at = fetch_started_at
        recommended_seconds = result["polling_interval_millis"] / 1000
        print(
            "YouTubeコメント取得："
            f"新規候補={len(result['messages'])}件 "
            f"前回取得から={actual_interval} "
            f"次回取得目安={recommended_seconds:.1f}秒"
        )

        yield result

        if max_loops is not None and loop_count >= max_loops:
            break

        wait_seconds = recommended_seconds
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            if stop_event is not None and stop_event.is_set():
                return
            if wait_callback is not None:
                wait_callback()
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                break
            sleep_seconds = min(float(wait_step_seconds), remaining_seconds)
            if stop_event is not None:
                if stop_event.wait(timeout=sleep_seconds):
                    return
            else:
                time.sleep(sleep_seconds)


class YouTubeChatPoller:
    """YouTubeコメント取得とOBS表示を返信生成から分離します。"""

    def __init__(self, live_chat_id, max_loops=None, message_callback=None):
        self.live_chat_id = live_chat_id
        self.max_loops = max_loops
        self.message_callback = message_callback
        self._result_queue = queue.Queue(maxsize=256)
        self._stop_event = threading.Event()
        self._finished_event = threading.Event()
        self._error = None
        self._seen_message_ids = set()
        self._thread = None

    def start(self):
        if self._thread is not None:
            raise RuntimeError("YouTubeコメント取得スレッドはすでに開始されています。")
        self._thread = threading.Thread(
            target=self._run,
            name="youtube-chat-poller",
            daemon=True,
        )
        self._thread.start()
        return self

    def _run(self):
        try:
            for result in iter_chat_messages(
                self.live_chat_id,
                max_loops=self.max_loops,
                stop_event=self._stop_event,
            ):
                messages = []
                for message in result.get("messages", []):
                    message_id = message.get("message_id", "")
                    if message_id and message_id in self._seen_message_ids:
                        continue
                    if message_id:
                        self._seen_message_ids.add(message_id)
                    messages.append(message)

                published_result = dict(result)
                published_result["messages"] = messages
                if messages and self.message_callback is not None:
                    self.message_callback(messages)

                # 返信生成中は空の取得結果を一件だけ残し、コメント用の空きを守ります。
                if not messages and not self._result_queue.empty():
                    continue
                try:
                    self._result_queue.put(published_result, timeout=1)
                except queue.Full:
                    print(
                        "YouTubeコメント返信キューが満杯です。"
                        "OBSには表示済みですが、この取得分は返信候補から除外します。"
                    )
        except (RuntimeError, ValueError) as exc:
            self._error = exc
        except Exception as exc:
            self._error = RuntimeError(
                f"YouTubeコメント取得スレッドで予期しないエラーが発生しました: {exc}"
            )
        finally:
            self._finished_event.set()

    def iter_results(self, wait_callback=None, wait_step_seconds=0.25):
        if float(wait_step_seconds) <= 0:
            raise ValueError("wait_step_secondsは0より大きくしてください。")
        if self._thread is None:
            raise RuntimeError("YouTubeコメント取得スレッドが開始されていません。")

        try:
            while True:
                try:
                    first_result = self._result_queue.get(
                        timeout=float(wait_step_seconds)
                    )
                except queue.Empty:
                    if wait_callback is not None:
                        wait_callback()
                    if self._finished_event.is_set() and self._result_queue.empty():
                        self._raise_if_failed()
                        return
                    continue

                # 返信生成中に到着した複数回分をまとめ、最新候補から一度だけ選別します。
                results = [first_result]
                while True:
                    try:
                        results.append(self._result_queue.get_nowait())
                    except queue.Empty:
                        break

                merged_result = dict(results[-1])
                merged_result["messages"] = [
                    message
                    for result in results
                    for message in result.get("messages", [])
                ]
                yield merged_result

                if self._finished_event.is_set() and self._result_queue.empty():
                    self._raise_if_failed()
                    return
        finally:
            self.stop()

    def _raise_if_failed(self):
        if self._error is not None:
            raise RuntimeError(
                f"YouTubeコメント取得スレッドが停止しました: {self._error}"
            ) from self._error

    def stop(self):
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=11)
            if self._thread.is_alive():
                print(
                    "YouTubeコメント取得スレッドが停止待ち時間内に終了しませんでした。"
                    "通信タイムアウト後に自動終了します。"
                )
