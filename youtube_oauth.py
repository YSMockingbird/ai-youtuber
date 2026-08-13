import os
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


class NoActiveYouTubeBroadcastError(RuntimeError):
    """認証は成功したが、現在ライブ中の配信がまだないことを表します。"""


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
