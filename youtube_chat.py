import os
import queue
import threading
import time
from collections import deque

import requests


YOUTUBE_VIDEO_API_URL = "https://www.googleapis.com/youtube/v3/videos"
YOUTUBE_LIVE_CHAT_API_URL = "https://www.googleapis.com/youtube/v3/liveChat/messages"
RETRYABLE_HTTP_STATUS_CODES = {408, 429, 500, 502, 503, 504}


class YouTubeLiveNotStartedError(RuntimeError):
    """YouTube配信またはライブチャットがまだ開始されていない状態です。"""


class YouTubeLiveEndedError(RuntimeError):
    """YouTube配信とライブチャットが終了した状態です。"""


class YouTubeTransientChatError(RuntimeError):
    """再接続で回復する可能性があるコメント取得エラーです。"""


def _get_youtube_error_reasons(response):
    # HTTPステータスだけでは配信終了と権限エラーを区別できないため、reasonを確認します。
    try:
        data = response.json()
    except (TypeError, ValueError):
        return []
    return [
        error.get("reason", "")
        for error in data.get("error", {}).get("errors", [])
        if isinstance(error, dict)
    ]


def resolve_youtube_video_id():
    # 手動IDを優先し、未設定の場合だけOAuthで現在ライブ中の配信を検索します。
    configured_video_id = os.getenv("YOUTUBE_VIDEO_ID", "").strip()
    if configured_video_id and "your_" not in configured_video_id:
        print(f"設定済みのYouTube動画IDを使用します：{configured_video_id}")
        return configured_video_id

    # 手動ID利用時はOAuthライブラリを読み込まず、従来どおり動作させます。
    from youtube_oauth import (
        find_active_youtube_broadcast,
        NoActiveYouTubeBroadcastError,
    )

    try:
        broadcast = find_active_youtube_broadcast()
    except NoActiveYouTubeBroadcastError as exc:
        raise YouTubeLiveNotStartedError(str(exc)) from exc
    print(
        "現在ライブ中の配信を自動取得しました："
        f"{broadcast['title']} / video_id={broadcast['video_id']}"
    )
    return broadcast["video_id"]


def get_live_chat_id(return_video_id=False):
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
        actual_start_time = live_streaming_details.get("actualStartTime")
        actual_end_time = live_streaming_details.get("actualEndTime")
        if not actual_start_time and not actual_end_time:
            raise YouTubeLiveNotStartedError(
                "指定したYouTube配信はまだライブ開始前です。"
            )
        raise RuntimeError(
            "ライブ中の配信からliveChatIdを取得できませんでした。"
            "YouTube Studioでライブチャットが有効か確認してください。"
        )

    if return_video_id:
        return live_chat_id, video_id
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
        raise YouTubeTransientChatError(
            "YouTubeコメント取得がタイムアウトしました。"
        ) from exc
    except requests.exceptions.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else "unknown"
        error_reasons = (
            _get_youtube_error_reasons(exc.response)
            if exc.response is not None
            else []
        )
        if "liveChatEnded" in error_reasons:
            raise YouTubeLiveEndedError(
                "YouTubeライブチャットが終了しました。"
            ) from exc
        if status_code in RETRYABLE_HTTP_STATUS_CODES:
            raise YouTubeTransientChatError(
                "YouTubeコメント取得で一時的なHTTPエラーが発生しました。"
                f"status_code={status_code}"
            ) from exc
        raise RuntimeError(f"YouTubeコメント取得でHTTPエラーが発生しました。status_code={status_code}") from exc
    except requests.exceptions.RequestException as exc:
        raise YouTubeTransientChatError(
            "YouTubeコメント取得の通信が一時的に切断されました。"
        ) from exc

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


def _wait_for_chat_retry(
    wait_seconds,
    wait_callback=None,
    wait_step_seconds=0.25,
    stop_event=None,
):
    # 待機中も管理命令と停止指示へ応答できるよう、短い間隔に分けて待ちます。
    deadline = time.monotonic() + float(wait_seconds)
    while time.monotonic() < deadline:
        if stop_event is not None and stop_event.is_set():
            return False
        if wait_callback is not None:
            wait_callback()
        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            break
        sleep_seconds = min(float(wait_step_seconds), remaining_seconds)
        if stop_event is not None:
            if stop_event.wait(timeout=sleep_seconds):
                return False
        else:
            time.sleep(sleep_seconds)
    return True


def iter_chat_messages(
    live_chat_id,
    max_loops=None,
    wait_callback=None,
    wait_step_seconds=0.25,
    stop_event=None,
    retry_initial_seconds=2,
    retry_max_seconds=30,
):
    # pollingIntervalMillisに従って、YouTube Liveコメントを継続取得します。
    if float(wait_step_seconds) <= 0:
        raise ValueError("wait_step_secondsは0より大きくしてください。")
    if float(retry_initial_seconds) <= 0:
        raise ValueError("コメント取得の初回再試行間隔は0より大きくしてください。")
    if float(retry_max_seconds) < float(retry_initial_seconds):
        raise ValueError(
            "コメント取得の最大再試行間隔は初回再試行間隔以上にしてください。"
        )
    page_token = None
    loop_count = 0
    previous_fetch_at = None
    retry_seconds = float(retry_initial_seconds)

    while True:
        if stop_event is not None and stop_event.is_set():
            return
        fetch_started_at = time.monotonic()
        try:
            result = fetch_chat_messages(live_chat_id, page_token)
        except YouTubeTransientChatError as exc:
            print(
                "YouTubeコメント取得の一時エラー："
                f"{exc} {retry_seconds:g}秒後に同じ位置から再試行します。"
            )
            if not _wait_for_chat_retry(
                retry_seconds,
                wait_callback=wait_callback,
                wait_step_seconds=wait_step_seconds,
                stop_event=stop_event,
            ):
                return
            retry_seconds = min(
                retry_seconds * 2,
                float(retry_max_seconds),
            )
            continue
        retry_seconds = float(retry_initial_seconds)
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

        if not _wait_for_chat_retry(
            recommended_seconds,
            wait_callback=wait_callback,
            wait_step_seconds=wait_step_seconds,
            stop_event=stop_event,
        ):
            return


class YouTubeChatPoller:
    """YouTubeコメント取得とOBS表示を返信生成から分離します。"""

    def __init__(
        self,
        live_chat_id,
        max_loops=None,
        message_callback=None,
        live_status_callback=None,
        live_status_interval_seconds=15,
        max_seen_message_ids=2000,
    ):
        self.live_chat_id = live_chat_id
        self.max_loops = max_loops
        self.message_callback = message_callback
        self.live_status_callback = live_status_callback
        self.live_status_interval_seconds = float(live_status_interval_seconds)
        if self.live_status_interval_seconds <= 0:
            raise ValueError("配信状態の確認間隔は0より大きくしてください。")
        if isinstance(max_seen_message_ids, bool) or not isinstance(
            max_seen_message_ids, int
        ) or max_seen_message_ids <= 0:
            raise ValueError("処理済みコメントIDの保持数は1以上の整数にしてください。")
        self.max_seen_message_ids = max_seen_message_ids
        self._result_queue = queue.Queue(maxsize=256)
        self._stop_event = threading.Event()
        self._finished_event = threading.Event()
        self._error = None
        self._seen_message_ids = set()
        self._seen_message_id_order = deque()
        self._thread = None

    def _remember_message_id(self, message_id):
        if not message_id or message_id in self._seen_message_ids:
            return False
        self._seen_message_ids.add(message_id)
        self._seen_message_id_order.append(message_id)
        while len(self._seen_message_id_order) > self.max_seen_message_ids:
            expired_message_id = self._seen_message_id_order.popleft()
            self._seen_message_ids.discard(expired_message_id)
        return True

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
        next_status_check_at = (
            time.monotonic() + self.live_status_interval_seconds
        )
        try:
            for result in iter_chat_messages(
                self.live_chat_id,
                max_loops=self.max_loops,
                stop_event=self._stop_event,
            ):
                messages = []
                for message in result.get("messages", []):
                    message_id = message.get("message_id", "")
                    if message_id and not self._remember_message_id(message_id):
                        continue
                    messages.append(message)

                published_result = dict(result)
                published_result["messages"] = messages
                if messages and self.message_callback is not None:
                    self.message_callback(messages)

                if (
                    self.live_status_callback is not None
                    and time.monotonic() >= next_status_check_at
                ):
                    if not self.live_status_callback():
                        raise YouTubeLiveEndedError(
                            "YouTubeライブ配信の終了状態を確認しました。"
                        )
                    next_status_check_at = (
                        time.monotonic() + self.live_status_interval_seconds
                    )

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
                # 配信終了時は古い待機結果を処理せず、終了を最優先でメインへ通知します。
                if self._finished_event.is_set() and isinstance(
                    self._error,
                    YouTubeLiveEndedError,
                ):
                    self._raise_if_failed()
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
            if isinstance(self._error, YouTubeLiveEndedError):
                raise self._error
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
