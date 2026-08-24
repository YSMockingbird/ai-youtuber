import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow


PROJECT_ROOT = Path(__file__).resolve().parent
YOUTUBE_MANAGE_SCOPE = "https://www.googleapis.com/auth/youtube"
YOUTUBE_LIVE_BROADCASTS_API_URL = (
    "https://www.googleapis.com/youtube/v3/liveBroadcasts"
)
YOUTUBE_LIVE_STREAMS_API_URL = (
    "https://www.googleapis.com/youtube/v3/liveStreams"
)
YOUTUBE_VIDEOS_API_URL = "https://www.googleapis.com/youtube/v3/videos"


class NoActiveYouTubeBroadcastError(RuntimeError):
    """認証は成功したが、現在ライブ中の配信がまだないことを表します。"""


class NoUpcomingYouTubeBroadcastError(RuntimeError):
    """認証は成功したが、開始可能な配信枠がないことを表します。"""


def get_youtube_oauth_paths():
    client_path = os.getenv(
        "YOUTUBE_OAUTH_CLIENT_FILE",
        ".secrets/youtube_oauth_client.json",
    ).strip()
    token_path = os.getenv(
        "YOUTUBE_OAUTH_TOKEN_FILE",
        ".secrets/youtube_oauth_token.json",
    ).strip()
    if not client_path:
        raise RuntimeError("YOUTUBE_OAUTH_CLIENT_FILEが空です。")
    if not token_path:
        raise RuntimeError("YOUTUBE_OAUTH_TOKEN_FILEが空です。")
    client_file = _resolve_project_path(client_path)
    token_file = _resolve_project_path(token_path)
    return client_file, token_file


def _resolve_project_path(configured_path):
    path = Path(configured_path).expanduser()
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def get_youtube_credentials():
    # 初回だけブラウザ認証し、以後は保存済みトークンを更新して利用します。
    client_file, token_file = get_youtube_oauth_paths()
    if not client_file.is_file():
        raise RuntimeError(
            "YouTube OAuthクライアントファイルが見つかりません。"
            f" path={client_file}"
        )

    credentials = _load_saved_credentials(token_file)
    if (
        credentials is not None
        and not credentials.has_scopes([YOUTUBE_MANAGE_SCOPE])
    ):
        print(
            "保存済みYouTube認証は読み取り専用のため、"
            "配信管理権限を追加して再認証します。"
        )
        credentials = None
    if credentials is not None and credentials.expired:
        if credentials.refresh_token:
            try:
                credentials.refresh(Request())
                _save_credentials(credentials, token_file)
            except RefreshError as exc:
                print(
                    "保存済みYouTube認証の更新に失敗したため、"
                    "ブラウザで再認証します。"
                )
                credentials = None
        else:
            credentials = None

    if credentials is None or not credentials.valid:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(client_file),
            scopes=[YOUTUBE_MANAGE_SCOPE],
        )
        try:
            credentials = flow.run_local_server(
                host="localhost",
                port=0,
                open_browser=True,
                timeout_seconds=180,
                authorization_prompt_message=(
                    "ブラウザでYouTubeの読み取りを許可してください: {url}"
                ),
                success_message=(
                    "YouTube認証が完了しました。"
                    "このブラウザタブを閉じてください。"
                ),
                access_type="offline",
                prompt="consent",
            )
        except (OSError, TimeoutError) as exc:
            raise RuntimeError(
                "YouTube OAuth認証を完了できませんでした。"
                "ブラウザの認証画面を確認してください。"
            ) from exc
        _save_credentials(credentials, token_file)

    return credentials


def find_active_youtube_broadcast(credentials=None):
    # 認証したYouTubeアカウントが所有する、現在ライブ中の配信だけを取得します。
    credentials = credentials or get_youtube_credentials()
    headers = {"Authorization": f"Bearer {credentials.token}"}
    params = {
        "part": "id,snippet,status",
        "mine": "true",
        "broadcastType": "all",
        "maxResults": 50,
    }
    try:
        response = requests.get(
            YOUTUBE_LIVE_BROADCASTS_API_URL,
            headers=headers,
            params=params,
            timeout=10,
        )
        response.raise_for_status()
    except requests.exceptions.Timeout as exc:
        raise RuntimeError(
            "YouTubeライブ配信の自動検索がタイムアウトしました。"
        ) from exc
    except requests.exceptions.HTTPError as exc:
        status_code = (
            exc.response.status_code
            if exc.response is not None
            else "unknown"
        )
        detail = (
            exc.response.text[:300]
            if exc.response is not None
            else ""
        )
        raise RuntimeError(
            "YouTubeライブ配信の自動検索でHTTPエラーが発生しました。"
            f" status_code={status_code} detail={detail}"
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(
            "YouTubeライブ配信の自動検索に接続できませんでした。"
        ) from exc

    try:
        data = response.json()
    except requests.exceptions.JSONDecodeError as exc:
        raise RuntimeError(
            "YouTubeライブ配信の検索結果をJSONとして読み取れませんでした。"
        ) from exc
    items = data.get("items", [])
    if not isinstance(items, list):
        raise RuntimeError(
            "YouTubeライブ配信の検索結果が期待する形式ではありません。"
        )
    # mineとbroadcastStatusは同時指定できないため、自分の配信を取得後に絞ります。
    active_items = [
        item
        for item in items
        if item.get("status", {}).get("lifeCycleStatus") == "live"
    ]
    if not active_items:
        raise NoActiveYouTubeBroadcastError(
            "認証したYouTubeアカウントに、現在ライブ中の配信がありません。"
            "YouTube Studioで配信が「ライブ」になっているか確認してください。"
        )
    if len(active_items) > 1:
        candidates = ", ".join(
            f"{item.get('id', 'ID不明')}:"
            f"{item.get('snippet', {}).get('title', 'タイトル不明')}"
            for item in active_items
        )
        raise RuntimeError(
            "現在ライブ中の配信が複数見つかったため自動選択できません。"
            f" candidates={candidates}"
        )

    item = active_items[0]
    video_id = str(item.get("id", "")).strip()
    if not video_id:
        raise RuntimeError(
            "ライブ配信の検索結果に動画IDが含まれていません。"
        )
    return {
        "video_id": video_id,
        "title": str(
            item.get("snippet", {}).get("title", "タイトル不明")
        ),
    }


def find_upcoming_youtube_broadcast(credentials=None):
    # 自動開始対象を誤選択しないよう、開始可能な予約配信が一件の場合だけ返します。
    credentials = credentials or get_youtube_credentials()
    headers = {"Authorization": f"Bearer {credentials.token}"}
    params = {
        "part": "id,snippet,status,contentDetails",
        "mine": "true",
        "broadcastType": "all",
        "maxResults": 50,
    }
    try:
        response = requests.get(
            YOUTUBE_LIVE_BROADCASTS_API_URL,
            headers=headers,
            params=params,
            timeout=10,
        )
        response.raise_for_status()
    except requests.exceptions.Timeout as exc:
        raise RuntimeError(
            "YouTube予約配信の検索がタイムアウトしました。"
        ) from exc
    except requests.exceptions.HTTPError as exc:
        status_code = (
            exc.response.status_code
            if exc.response is not None
            else "unknown"
        )
        raise RuntimeError(
            "YouTube予約配信の検索でHTTPエラーが発生しました。"
            f" status_code={status_code}"
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(
            "YouTube予約配信の検索に接続できませんでした。"
        ) from exc

    try:
        data = response.json()
    except requests.exceptions.JSONDecodeError as exc:
        raise RuntimeError(
            "YouTube予約配信の検索結果をJSONとして読み取れませんでした。"
        ) from exc
    items = data.get("items", [])
    if not isinstance(items, list):
        raise RuntimeError(
            "YouTube予約配信の検索結果が期待する形式ではありません。"
        )
    ready_items = [
        item
        for item in items
        if item.get("status", {}).get("lifeCycleStatus") == "ready"
    ]
    if not ready_items:
        raise NoUpcomingYouTubeBroadcastError(
            "開始可能なYouTube予約配信がありません。"
            "YouTube Studioで予約配信を作成してください。"
        )
    if len(ready_items) > 1:
        candidates = ", ".join(
            f"{item.get('id', 'ID不明')}:"
            f"{item.get('snippet', {}).get('title', 'タイトル不明')}"
            for item in ready_items
        )
        raise RuntimeError(
            "開始可能なYouTube予約配信が複数あるため自動選択できません。"
            f" candidates={candidates}"
        )

    item = ready_items[0]
    content_details = item.get("contentDetails", {})
    bound_stream_id = str(content_details.get("boundStreamId", "")).strip()
    if not bound_stream_id:
        raise RuntimeError(
            "YouTube予約配信にストリームが接続されていません。"
            "YouTube Studioで配信ストリームを選択してください。"
        )
    return {
        "video_id": str(item.get("id", "")).strip(),
        "title": str(item.get("snippet", {}).get("title", "タイトル不明")),
        "scheduled_start_time": str(
            item.get("snippet", {}).get("scheduledStartTime", "")
        ),
        "privacy_status": str(
            item.get("status", {}).get("privacyStatus", "不明")
        ),
        "bound_stream_id": bound_stream_id,
        "enable_auto_start": bool(content_details.get("enableAutoStart", False)),
        "enable_auto_stop": bool(content_details.get("enableAutoStop", False)),
    }


def get_youtube_broadcast(video_id, credentials=None):
    # 予定に保存した動画IDから、開始対象の配信枠を一意に取得します。
    normalized_video_id = str(video_id or "").strip()
    if not normalized_video_id:
        raise ValueError("取得するYouTube配信の動画IDが空です。")
    credentials = credentials or get_youtube_credentials()
    headers = {"Authorization": f"Bearer {credentials.token}"}
    try:
        response = requests.get(
            YOUTUBE_LIVE_BROADCASTS_API_URL,
            headers=headers,
            params={
                "part": "id,snippet,status,contentDetails",
                "id": normalized_video_id,
            },
            timeout=10,
        )
        response.raise_for_status()
        items = response.json().get("items", [])
    except (requests.exceptions.RequestException, ValueError) as exc:
        raise RuntimeError(
            "保存済みのYouTube配信枠を取得できませんでした。"
            f" video_id={normalized_video_id}"
        ) from exc
    if not items:
        raise RuntimeError(
            "保存済みのYouTube配信枠が見つかりませんでした。"
            f" video_id={normalized_video_id}"
        )
    item = items[0]
    snippet = item.get("snippet", {})
    status = item.get("status", {})
    content_details = item.get("contentDetails", {})
    return {
        "video_id": normalized_video_id,
        "title": str(snippet.get("title", "タイトル不明")),
        "description": str(snippet.get("description", "")),
        "scheduled_start_time": str(snippet.get("scheduledStartTime", "")),
        "privacy_status": str(status.get("privacyStatus", "不明")),
        "life_cycle_status": str(status.get("lifeCycleStatus", "不明")),
        "bound_stream_id": str(content_details.get("boundStreamId", "")),
        "enable_auto_start": bool(content_details.get("enableAutoStart", False)),
        "enable_auto_stop": bool(content_details.get("enableAutoStop", False)),
    }


def update_youtube_scheduled_broadcast(
    video_id,
    *,
    title,
    description="",
    privacy_status="unlisted",
    scheduled_start_time,
    credentials=None,
):
    # 管理画面の予定変更を、まだ開始していないYouTube配信枠へ同期します。
    normalized_video_id = str(video_id or "").strip()
    normalized_title = str(title or "").strip()
    normalized_description = str(description or "").strip()
    normalized_privacy = str(privacy_status or "").strip()
    if not normalized_video_id:
        raise ValueError("更新するYouTube配信の動画IDが空です。")
    if not 1 <= len(normalized_title) <= 100:
        raise ValueError("YouTube配信タイトルは1〜100文字にしてください。")
    if len(normalized_description) > 5000:
        raise ValueError("YouTube配信説明は5000文字以内にしてください。")
    if normalized_privacy not in {"private", "unlisted", "public"}:
        raise ValueError("公開設定はprivate、unlisted、publicのいずれかです。")
    start_time = _normalize_scheduled_start_time(scheduled_start_time)
    credentials = credentials or get_youtube_credentials()
    current = get_youtube_broadcast(
        normalized_video_id,
        credentials=credentials,
    )
    if current["life_cycle_status"] != "ready":
        raise RuntimeError(
            "開始済みまたは終了済みのYouTube配信枠は予定変更できません。"
            f" status={current['life_cycle_status']}"
        )
    headers = {
        "Authorization": f"Bearer {credentials.token}",
        "Content-Type": "application/json",
    }
    try:
        response = requests.put(
            YOUTUBE_LIVE_BROADCASTS_API_URL,
            headers=headers,
            params={"part": "id,snippet,status"},
            json={
                "id": normalized_video_id,
                "snippet": {
                    "title": normalized_title,
                    "description": normalized_description,
                    "scheduledStartTime": start_time,
                },
                "status": {"privacyStatus": normalized_privacy},
            },
            timeout=10,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(
            "YouTube配信枠へ予定変更を同期できませんでした。"
            f" video_id={normalized_video_id}"
        ) from exc
    return {
        **current,
        "title": normalized_title,
        "description": normalized_description,
        "privacy_status": normalized_privacy,
        "scheduled_start_time": start_time,
    }


def delete_youtube_broadcast(video_id, credentials=None):
    # 管理画面の予定削除に合わせ、未開始のYouTube待機枠だけを削除します。
    normalized_video_id = str(video_id or "").strip()
    if not normalized_video_id:
        raise ValueError("削除するYouTube配信の動画IDが空です。")
    credentials = credentials or get_youtube_credentials()
    current = get_youtube_broadcast(
        normalized_video_id,
        credentials=credentials,
    )
    if current["life_cycle_status"] != "ready":
        raise RuntimeError(
            "開始済みまたは終了済みのYouTube配信は削除できません。"
            f" status={current['life_cycle_status']}"
        )
    try:
        response = requests.delete(
            YOUTUBE_LIVE_BROADCASTS_API_URL,
            headers={"Authorization": f"Bearer {credentials.token}"},
            params={"id": normalized_video_id},
            timeout=10,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(
            "YouTube配信枠を削除できませんでした。"
            f" video_id={normalized_video_id}"
        ) from exc
    return True


def _normalize_scheduled_start_time(value):
    if isinstance(value, datetime):
        parsed = value
    else:
        normalized = str(value or "").strip()
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise ValueError("YouTube配信開始予定時刻を読み取れません。") from exc
    if parsed.tzinfo is None:
        raise ValueError("YouTube配信開始予定時刻にはタイムゾーンが必要です。")
    return (
        parsed.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def update_youtube_broadcast_metadata(
    video_id,
    title,
    description="",
    privacy_status="unlisted",
    credentials=None,
):
    # 常設配信の変更不可項目に触れず、対応する動画のメタデータだけを更新します。
    normalized_video_id = str(video_id).strip()
    normalized_title = str(title).strip()
    normalized_description = str(description).strip()
    normalized_privacy = str(privacy_status).strip()
    if not normalized_video_id:
        raise ValueError("更新するYouTube配信の動画IDが空です。")
    if not 1 <= len(normalized_title) <= 100:
        raise ValueError("YouTube配信タイトルは1〜100文字にしてください。")
    if len(normalized_description) > 5000:
        raise ValueError("YouTube配信説明は5000文字以内にしてください。")
    if normalized_privacy not in {"private", "unlisted", "public"}:
        raise ValueError("公開設定はprivate、unlisted、publicのいずれかです。")

    credentials = credentials or get_youtube_credentials()
    headers = {
        "Authorization": f"Bearer {credentials.token}",
        "Content-Type": "application/json",
    }
    try:
        response = requests.get(
            YOUTUBE_VIDEOS_API_URL,
            headers=headers,
            params={"part": "snippet,status", "id": normalized_video_id},
            timeout=10,
        )
        response.raise_for_status()
        items = response.json().get("items", [])
    except (requests.exceptions.RequestException, ValueError) as exc:
        raise RuntimeError(
            "更新前のYouTube配信情報を取得できませんでした。"
        ) from exc
    if not items:
        raise RuntimeError(
            "更新対象のYouTube配信が見つかりませんでした。"
            f" video_id={normalized_video_id}"
        )

    current = items[0]
    current_snippet = current.get("snippet", {})
    current_status = current.get("status", {})
    category_id = str(current_snippet.get("categoryId", "")).strip()
    if not category_id:
        raise RuntimeError(
            "YouTube配信のカテゴリIDを取得できないため、安全に更新できません。"
        )
    snippet = {
        "title": normalized_title,
        "description": normalized_description,
        "categoryId": category_id,
    }
    for key in ("tags", "defaultLanguage"):
        if key in current_snippet:
            snippet[key] = current_snippet[key]
    status = {
        "privacyStatus": normalized_privacy,
    }
    for key in (
        "embeddable",
        "license",
        "publicStatsViewable",
        "selfDeclaredMadeForKids",
        "containsSyntheticMedia",
    ):
        if key in current_status:
            status[key] = current_status[key]
    try:
        update_response = requests.put(
            YOUTUBE_VIDEOS_API_URL,
            headers=headers,
            params={"part": "snippet,status"},
            json={
                "id": normalized_video_id,
                "snippet": snippet,
                "status": status,
            },
            timeout=10,
        )
        update_response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        status_code = (
            exc.response.status_code
            if isinstance(exc, requests.exceptions.HTTPError)
            and exc.response is not None
            else "unknown"
        )
        detail = (
            exc.response.text[:300]
            if isinstance(exc, requests.exceptions.HTTPError)
            and exc.response is not None
            else ""
        )
        raise RuntimeError(
            "YouTube配信のタイトル・説明・公開設定を更新できませんでした。"
            f" status_code={status_code} detail={detail}"
        ) from exc
    return {
        "video_id": normalized_video_id,
        "title": normalized_title,
        "description": normalized_description,
        "privacy_status": normalized_privacy,
    }


def find_reusable_youtube_stream(credentials=None):
    # 設定済みIDを優先し、未設定時は再利用可能なストリームが一件の場合だけ選びます。
    credentials = credentials or get_youtube_credentials()
    configured_stream_id = os.getenv("YOUTUBE_STREAM_ID", "").strip()
    headers = {"Authorization": f"Bearer {credentials.token}"}
    params = {
        "part": "id,snippet,status,contentDetails",
        "maxResults": 50,
    }
    if configured_stream_id:
        params["id"] = configured_stream_id
    else:
        params["mine"] = "true"
    try:
        response = requests.get(
            YOUTUBE_LIVE_STREAMS_API_URL,
            headers=headers,
            params=params,
            timeout=10,
        )
        response.raise_for_status()
    except requests.exceptions.Timeout as exc:
        raise RuntimeError(
            "YouTube配信ストリームの検索がタイムアウトしました。"
        ) from exc
    except requests.exceptions.HTTPError as exc:
        status_code = (
            exc.response.status_code
            if exc.response is not None
            else "unknown"
        )
        raise RuntimeError(
            "YouTube配信ストリームの検索でHTTPエラーが発生しました。"
            f" status_code={status_code}"
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(
            "YouTube配信ストリームの検索に接続できませんでした。"
        ) from exc

    try:
        data = response.json()
    except requests.exceptions.JSONDecodeError as exc:
        raise RuntimeError(
            "YouTube配信ストリームの検索結果をJSONとして読み取れませんでした。"
        ) from exc
    items = data.get("items", [])
    if not isinstance(items, list):
        raise RuntimeError(
            "YouTube配信ストリームの検索結果が期待する形式ではありません。"
        )
    reusable_items = [
        item
        for item in items
        if item.get("contentDetails", {}).get("isReusable") is True
        and item.get("status", {}).get("streamStatus")
        in {"ready", "inactive", "active"}
    ]
    if not reusable_items:
        raise RuntimeError(
            "利用可能な再利用ストリームがありません。"
            "YouTube Studioで配信ストリームを作成してください。"
        )
    if len(reusable_items) > 1:
        candidates = ", ".join(
            f"{item.get('id', 'ID不明')}:"
            f"{item.get('snippet', {}).get('title', 'タイトル不明')}"
            for item in reusable_items
        )
        raise RuntimeError(
            "再利用可能なYouTube配信ストリームが複数あります。"
            ".envのYOUTUBE_STREAM_IDへ使用するIDを設定してください。"
            f" candidates={candidates}"
        )
    item = reusable_items[0]
    return {
        "stream_id": str(item.get("id", "")).strip(),
        "title": str(item.get("snippet", {}).get("title", "タイトル不明")),
        "stream_status": str(
            item.get("status", {}).get("streamStatus", "不明")
        ),
    }


def find_youtube_autostart_conflicts(stream_id, credentials=None):
    # 同じストリームで自動スタートする別枠があると誤配信になるため検出します。
    credentials = credentials or get_youtube_credentials()
    headers = {"Authorization": f"Bearer {credentials.token}"}
    params = {
        "part": "id,snippet,status,contentDetails",
        "mine": "true",
        "broadcastType": "all",
        "maxResults": 50,
    }
    try:
        response = requests.get(
            YOUTUBE_LIVE_BROADCASTS_API_URL,
            headers=headers,
            params=params,
            timeout=10,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(
            "YouTube自動スタート競合の確認に失敗しました。"
        ) from exc
    items = response.json().get("items", [])
    return [
        {
            "video_id": str(item.get("id", "")).strip(),
            "title": str(
                item.get("snippet", {}).get("title", "タイトル不明")
            ),
        }
        for item in items
        if item.get("status", {}).get("lifeCycleStatus")
        in {"created", "ready", "testing"}
        and item.get("contentDetails", {}).get("boundStreamId") == stream_id
        and item.get("contentDetails", {}).get("enableAutoStart") is True
    ]


def is_youtube_stream_active(stream_id, credentials=None):
    normalized_stream_id = str(stream_id).strip()
    if not normalized_stream_id:
        raise ValueError("YouTube配信ストリームIDが空です。")
    credentials = credentials or get_youtube_credentials()
    try:
        response = requests.get(
            YOUTUBE_LIVE_STREAMS_API_URL,
            headers={"Authorization": f"Bearer {credentials.token}"},
            params={"part": "id,status", "id": normalized_stream_id},
            timeout=10,
        )
        response.raise_for_status()
        items = response.json().get("items", [])
    except (requests.exceptions.RequestException, ValueError) as exc:
        raise RuntimeError(
            "YouTube配信ストリームの受信状態を確認できませんでした。"
        ) from exc
    if not items:
        raise RuntimeError(
            "確認対象のYouTube配信ストリームが見つかりませんでした。"
        )
    return items[0].get("status", {}).get("streamStatus") == "active"


def transition_youtube_broadcast_to_live(video_id, credentials=None):
    normalized_video_id = str(video_id).strip()
    if not normalized_video_id:
        raise ValueError("開始するYouTube配信の動画IDが空です。")
    credentials = credentials or get_youtube_credentials()
    try:
        response = requests.post(
            f"{YOUTUBE_LIVE_BROADCASTS_API_URL}/transition",
            headers={"Authorization": f"Bearer {credentials.token}"},
            params={
                "part": "id,status",
                "id": normalized_video_id,
                "broadcastStatus": "live",
            },
            timeout=10,
        )
        response.raise_for_status()
        status = response.json().get("status", {}).get("lifeCycleStatus")
    except requests.exceptions.RequestException as exc:
        status_code = (
            exc.response.status_code
            if isinstance(exc, requests.exceptions.HTTPError)
            and exc.response is not None
            else "unknown"
        )
        detail = (
            exc.response.text[:300]
            if isinstance(exc, requests.exceptions.HTTPError)
            and exc.response is not None
            else ""
        )
        raise RuntimeError(
            "YouTube配信をライブ状態へ移行できませんでした。"
            f" status_code={status_code} detail={detail}"
        ) from exc
    if status not in {"live", "liveStarting"}:
        raise RuntimeError(
            "YouTube配信のライブ開始を確認できませんでした。"
            f" lifeCycleStatus={status or '不明'}"
        )
    return status


def create_youtube_broadcast(
    title,
    description="",
    privacy_status="unlisted",
    credentials=None,
    scheduled_start_time=None,
):
    # 配信枠を作成し、既存の再利用ストリームへ接続します。
    normalized_title = str(title).strip()
    normalized_description = str(description).strip()
    normalized_privacy = str(privacy_status).strip()
    if not 1 <= len(normalized_title) <= 100:
        raise ValueError("YouTube配信タイトルは1〜100文字にしてください。")
    if len(normalized_description) > 5000:
        raise ValueError("YouTube配信説明は5000文字以内にしてください。")
    if normalized_privacy not in {"private", "unlisted", "public"}:
        raise ValueError("公開設定はprivate、unlisted、publicのいずれかです。")

    credentials = credentials or get_youtube_credentials()
    stream = find_reusable_youtube_stream(credentials=credentials)
    conflicts = find_youtube_autostart_conflicts(
        stream["stream_id"],
        credentials=credentials,
    )
    if conflicts:
        candidates = ", ".join(
            f"{item['video_id']}:{item['title']}" for item in conflicts
        )
        raise RuntimeError(
            "同じストリームに自動スタートONの別配信があるため、"
            "新しい配信枠を安全に開始できません。"
            "YouTube Studioで既存枠の自動スタートをOFFにしてください。"
            f" conflicts={candidates}"
        )
    start_time = scheduled_start_time or (
        datetime.now(timezone.utc) + timedelta(minutes=5)
    )
    scheduled_start = _normalize_scheduled_start_time(start_time)
    headers = {
        "Authorization": f"Bearer {credentials.token}",
        "Content-Type": "application/json",
    }
    body = {
        "snippet": {
            "title": normalized_title,
            "description": normalized_description,
            "scheduledStartTime": scheduled_start,
        },
        "status": {
            "privacyStatus": normalized_privacy,
            "selfDeclaredMadeForKids": False,
        },
        "contentDetails": {
            "enableAutoStart": False,
            "enableAutoStop": True,
            "enableDvr": True,
            "recordFromStart": True,
            "monitorStream": {
                "enableMonitorStream": False,
                "broadcastStreamDelayMs": 0,
            },
        },
    }
    try:
        response = requests.post(
            YOUTUBE_LIVE_BROADCASTS_API_URL,
            headers=headers,
            params={"part": "id,snippet,status,contentDetails"},
            json=body,
            timeout=10,
        )
        response.raise_for_status()
    except requests.exceptions.Timeout as exc:
        raise RuntimeError("YouTube配信枠の作成がタイムアウトしました。") from exc
    except requests.exceptions.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else "unknown"
        detail = exc.response.text[:300] if exc.response is not None else ""
        raise RuntimeError(
            "YouTube配信枠の作成でHTTPエラーが発生しました。"
            f" status_code={status_code} detail={detail}"
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise RuntimeError("YouTube配信枠の作成に接続できませんでした。") from exc

    try:
        created = response.json()
    except requests.exceptions.JSONDecodeError as exc:
        raise RuntimeError("YouTube配信枠の作成結果をJSONとして読み取れませんでした。") from exc
    video_id = str(created.get("id", "")).strip()
    if not video_id:
        raise RuntimeError("作成したYouTube配信枠に動画IDがありません。")

    try:
        bind_response = requests.post(
            f"{YOUTUBE_LIVE_BROADCASTS_API_URL}/bind",
            headers=headers,
            params={
                "part": "id,snippet,status,contentDetails",
                "id": video_id,
                "streamId": stream["stream_id"],
            },
            timeout=10,
        )
        bind_response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        status_code = (
            exc.response.status_code
            if isinstance(exc, requests.exceptions.HTTPError)
            and exc.response is not None
            else "unknown"
        )
        raise RuntimeError(
            "作成したYouTube配信枠をストリームへ接続できませんでした。"
            f" video_id={video_id} status_code={status_code}"
        ) from exc
    return {
        "video_id": video_id,
        "title": normalized_title,
        "description": normalized_description,
        "privacy_status": normalized_privacy,
        "scheduled_start_time": scheduled_start,
        "bound_stream_id": stream["stream_id"],
        "enable_auto_start": False,
        "enable_auto_stop": True,
    }


def is_youtube_broadcast_live(video_id, credentials=None):
    # 開始時に特定した配信だけを確認し、別の配信への切り替わりを誤認しません。
    normalized_video_id = str(video_id).strip()
    if not normalized_video_id:
        raise ValueError("YouTube配信状態確認用の動画IDが空です。")
    credentials = credentials or get_youtube_credentials()
    headers = {"Authorization": f"Bearer {credentials.token}"}
    params = {
        "part": "id,status",
        "id": normalized_video_id,
        "maxResults": 1,
    }
    try:
        response = requests.get(
            YOUTUBE_LIVE_BROADCASTS_API_URL,
            headers=headers,
            params=params,
            timeout=10,
        )
        response.raise_for_status()
    except requests.exceptions.Timeout as exc:
        raise RuntimeError(
            "YouTubeライブ配信の状態確認がタイムアウトしました。"
        ) from exc
    except requests.exceptions.HTTPError as exc:
        status_code = (
            exc.response.status_code
            if exc.response is not None
            else "unknown"
        )
        raise RuntimeError(
            "YouTubeライブ配信の状態確認でHTTPエラーが発生しました。"
            f" status_code={status_code}"
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(
            "YouTubeライブ配信の状態確認に接続できませんでした。"
        ) from exc

    try:
        data = response.json()
    except requests.exceptions.JSONDecodeError as exc:
        raise RuntimeError(
            "YouTubeライブ配信の状態をJSONとして読み取れませんでした。"
        ) from exc
    items = data.get("items", [])
    if not isinstance(items, list):
        raise RuntimeError(
            "YouTubeライブ配信の状態が期待する形式ではありません。"
        )
    if not items:
        raise RuntimeError(
            "状態確認対象のYouTubeライブ配信が見つかりませんでした。"
            f" video_id={normalized_video_id}"
        )
    return items[0].get("status", {}).get("lifeCycleStatus") == "live"


def complete_youtube_broadcast(video_id, credentials=None):
    # 終了挨拶の再生後に、対象のライブ配信だけを終了状態へ遷移させます。
    normalized_video_id = str(video_id).strip()
    if not normalized_video_id:
        raise ValueError("終了するYouTube配信の動画IDが空です。")
    credentials = credentials or get_youtube_credentials()
    headers = {"Authorization": f"Bearer {credentials.token}"}
    params = {
        "part": "id,status",
        "id": normalized_video_id,
        "broadcastStatus": "complete",
    }
    try:
        response = requests.post(
            f"{YOUTUBE_LIVE_BROADCASTS_API_URL}/transition",
            headers=headers,
            params=params,
            timeout=10,
        )
        response.raise_for_status()
    except requests.exceptions.Timeout as exc:
        raise RuntimeError(
            "YouTubeライブ配信の終了操作がタイムアウトしました。"
        ) from exc
    except requests.exceptions.HTTPError as exc:
        status_code = (
            exc.response.status_code
            if exc.response is not None
            else "unknown"
        )
        detail = (
            exc.response.text[:300]
            if exc.response is not None
            else ""
        )
        raise RuntimeError(
            "YouTubeライブ配信の終了操作でHTTPエラーが発生しました。"
            f" status_code={status_code} detail={detail}"
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(
            "YouTubeライブ配信の終了操作に接続できませんでした。"
        ) from exc

    try:
        data = response.json()
    except requests.exceptions.JSONDecodeError as exc:
        raise RuntimeError(
            "YouTubeライブ配信の終了結果をJSONとして読み取れませんでした。"
        ) from exc
    status = data.get("status", {}).get("lifeCycleStatus")
    if status not in {"complete", "liveStopping"}:
        raise RuntimeError(
            "YouTubeライブ配信の終了を確認できませんでした。"
            f" lifeCycleStatus={status or '不明'}"
        )
    return status


def _load_saved_credentials(token_file):
    if not token_file.is_file():
        return None
    try:
        return Credentials.from_authorized_user_file(
            str(token_file),
        )
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            "保存済みYouTube OAuthトークンを読み取れません。"
            f" path={token_file}"
        ) from exc


def _save_credentials(credentials, token_file):
    token_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = token_file.with_suffix(".tmp")
    try:
        temporary_file.write_text(credentials.to_json(), encoding="utf-8")
        os.chmod(temporary_file, 0o600)
        os.replace(temporary_file, token_file)
        os.chmod(token_file, 0o600)
    except OSError as exc:
        raise RuntimeError(
            "YouTube OAuthトークンを安全に保存できませんでした。"
            f" path={token_file}"
        ) from exc
