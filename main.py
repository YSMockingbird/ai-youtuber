import argparse
import os
import re
import threading
import time
import unicodedata
from pathlib import Path

from dotenv import load_dotenv

from aivis_speech import AivisSpeechClient, DEFAULT_AIVIS_API_URL
from ai_response import (
    generate_admin_directed_speech,
    generate_ai_response,
    generate_autonomous_speech,
    generate_news_commentary,
)
from autonomous_topics import AutonomousTopicSelector, TOPIC_INSTRUCTIONS
from autonomous_buffer import AutonomousSpeechBuffer
from broadcast_auto_scheduler import BroadcastAutoScheduler, auto_schedule_enabled
from character_memory import get_character_memory_repository
from control_server import ALLOWED_EMOTIONS, ExternalControlServer
from llm.config import load_llm_config
from llm.session import StreamContextManager
from local_services import ensure_live_local_services, wait_for_obs_ready
from live_service import LiveServiceController
from motion_control import MotionRateLimiter
from news_source import fetch_news_articles, select_news_article
from obs_websocket import ObsWebSocketClient
from speech_scheduler import SpeechScheduler
from youtube_chat import (
    fetch_chat_messages,
    get_live_chat_id,
    iter_chat_messages,
    YouTubeLiveEndedError,
    YouTubeLiveNotStartedError,
    YouTubeChatPoller,
)


class YouTubeBroadcastEndCoordinator:
    """終了挨拶の再生完了後にYouTube配信を安全に終了します。"""

    def __init__(
        self,
        video_id,
        runtime,
        complete_callback=None,
        stop_obs_callback=None,
        now=None,
    ):
        self.video_id = video_id
        self.runtime = runtime
        self.complete_callback = complete_callback
        self.stop_obs_callback = stop_obs_callback
        self.now = now or time.monotonic
        self.end_at = None
        self.completed = False

    def schedule(self, duration_ms):
        # ブラウザ側の再生開始遅延を考慮し、WAV終了時刻に0.75秒だけ余裕を持たせます。
        self.end_at = self.now() + float(duration_ms) / 1000 + 0.75
        self.runtime.update_admin_status(
            autonomous_paused=True,
            phase="speaking",
            message="終了挨拶の再生後、YouTube配信を自動終了します。",
            speaking_until_ms=round(time.time() * 1000 + duration_ms),
        )

    def tick(self):
        if self.completed or self.end_at is None or self.now() < self.end_at:
            return False
        self.runtime.update_admin_status(
            autonomous_paused=True,
            phase="ending_youtube",
            message="YouTubeライブを終了しています。",
        )
        try:
            if self.complete_callback is None:
                from youtube_oauth import complete_youtube_broadcast

                complete_callback = complete_youtube_broadcast
            else:
                complete_callback = self.complete_callback
            status = complete_callback(self.video_id)
        except (RuntimeError, ValueError) as exc:
            self.runtime.update_admin_status(
                autonomous_paused=True,
                phase="error",
                message=f"YouTubeライブを自動終了できませんでした: {exc}",
            )
            raise
        self.completed = True
        obs_message = "OBSの配信出力は手動停止が必要です。"
        if self.stop_obs_callback is not None:
            try:
                stopped = self.stop_obs_callback()
                obs_message = (
                    "OBSの配信出力も停止しました。"
                    if stopped
                    else "OBSの配信出力はすでに停止していました。"
                )
            except (RuntimeError, ValueError) as exc:
                obs_message = (
                    "OBSの自動停止に失敗しました。OBSを手動停止してください。"
                )
                print(f"OBS自動停止エラー: {exc}")
        self.runtime.update_admin_status(
            autonomous_paused=True,
            phase="youtube_ended",
            message=f"YouTubeライブを終了しました。{obs_message} Pythonを停止します。",
        )
        self.runtime.update_active_broadcast_schedule("completed")
        print(f"YouTubeライブを自動終了しました：status={status}")
        print(obs_message)
        return True


class AdminRequestedBroadcastEnd(RuntimeError):
    """管理画面からYouTube配信を正常終了したことをメインループへ通知します。"""


def run_openai_test():
    # YouTubeを使わず、固定コメントでOpenAI接続だけ確認します。
    user_name = "テストユーザー"
    comment = "こんにちは！今日の配信楽しみです。"

    ai_response = generate_ai_response(user_name, comment)

    print(f"{user_name}：{comment}")
    print(f"AI：{ai_response['text']}")
    print(f"emotion：{ai_response['emotion']}")
    print(f"motion：{ai_response['motion']}")


def run_obs_websocket_test():
    # ライブを開始せず、OBS WebSocketの接続と配信状態取得だけを確認します。
    client = ObsWebSocketClient.from_env()
    if client is None:
        raise RuntimeError(
            "OBS_WEBSOCKET_ENABLEDがfalseです。.envでtrueにしてください。"
        )
    output_active = client.get_stream_status()
    print("OBS WebSocket接続テストに成功しました。")
    print(f"OBS配信出力：{'稼働中' if output_active else '停止中'}")


def run_youtube_upcoming_test():
    # 公開開始は行わず、自動開始の対象候補と安全設定だけを表示します。
    from youtube_oauth import find_upcoming_youtube_broadcast

    broadcast = find_upcoming_youtube_broadcast()
    print("開始可能なYouTube予約配信を確認しました。")
    print(f"タイトル：{broadcast['title']}")
    print(f"video_id：{broadcast['video_id']}")
    print(f"公開設定：{broadcast['privacy_status']}")
    print(f"開始予定：{broadcast['scheduled_start_time'] or '未設定'}")
    print(f"ストリーム接続：{broadcast['bound_stream_id']}")
    print(
        "YouTube自動スタート："
        f"{'ON' if broadcast['enable_auto_start'] else 'OFF'}"
    )
    print(
        "YouTube自動ストップ："
        f"{'ON' if broadcast['enable_auto_stop'] else 'OFF'}"
    )


def run_x_post_draft(topic=None):
    # Xへは送信せず、確認用の投稿候補だけを表示します。
    try:
        # X機能の依存関係や障害をライブ起動経路から分離します。
        from x_post import generate_x_post_draft

        draft = generate_x_post_draft(topic=topic)
    except (RuntimeError, ValueError) as exc:
        print(f"X投稿案生成エラー: {exc}")
        return False
    print("X投稿案:")
    print(draft["text"])
    print(f"文字数: {len(draft['text'])}")
    return True


def run_x_post(topic=None, confirm=False, input_func=input):
    # 明示確認がない限りXへ送信せず、候補の表示だけで終了します。
    try:
        from x_post import generate_x_post_draft, publish_x_post

        draft = generate_x_post_draft(topic=topic)
        text = draft["text"]
        print("X投稿候補:")
        print(text)
        print(f"文字数: {len(text)}")
        if not confirm:
            print("投稿していません。投稿する場合は--confirmを付けて再実行してください。")
            return False

        answer = input_func("投稿する場合は POST と入力してください: ").strip()
        if answer != "POST":
            print("Xへの投稿をキャンセルしました。")
            return False

        result = publish_x_post(text)
    except (RuntimeError, ValueError) as exc:
        print(f"X投稿エラー: {exc}")
        return False

    print(f"Xへ投稿しました。post_id={result['post_id']}")
    return True


def run_character_memory_list(status="draft"):
    repository = get_character_memory_repository()
    memories = repository.list(status)
    if not memories:
        print(f"キャラクター記憶はありません。status={status}")
        return
    for memory in memories:
        print(
            f"ID: {memory['memory_id']}\n"
            f"状態: {memory['status']} / 種類: {memory['category']} / "
            f"重要度: {memory['importance']:.2f}\n"
            f"出典: {memory['source']} / 作成日時: {memory['created_at']}\n"
            f"内容: {memory['content']}\n"
        )


def run_character_memory_review(memory_id, status):
    if not str(memory_id or "").strip():
        raise RuntimeError(
            "--character-memory-idへ審査するキャラクター記憶IDを指定してください。"
        )
    repository = get_character_memory_repository()
    repository.review(memory_id, status)
    result = "承認" if status == "approved" else "却下"
    print(f"キャラクター記憶を{result}しました。memory_id={memory_id}")


def record_ai_speech_with_character_event(stream_context, ai_response, source):
    candidate = ai_response.get("character_event_candidate")
    if candidate:
        stream_context.record_ai_speech(
            ai_response["text"],
            candidate,
            source=source,
        )
    else:
        stream_context.record_ai_speech(ai_response["text"])


def create_aivis_client():
    api_url = os.getenv("AIVIS_API_URL", DEFAULT_AIVIS_API_URL).strip()
    raw_timeout = os.getenv("AIVIS_TIMEOUT_SECONDS", "180").strip()
    try:
        timeout_seconds = int(raw_timeout)
    except ValueError as exc:
        raise RuntimeError("AIVIS_TIMEOUT_SECONDSは整数で設定してください。") from exc
    if not 10 <= timeout_seconds <= 600:
        raise RuntimeError("AIVIS_TIMEOUT_SECONDSは10〜600秒で設定してください。")
    return AivisSpeechClient(
        base_url=api_url,
        timeout_seconds=timeout_seconds,
    )


def run_aivis_info():
    # AivisSpeech Engineの接続状態と利用可能なスタイルを表示します。
    client = create_aivis_client()
    version = client.get_version()
    styles = client.get_styles()

    print(f"AivisSpeech Engineへ接続しました。version={version}")
    if not styles:
        print(
            "利用可能な音声スタイルがありません。"
            "AIVMモデルを追加してください。"
        )
        return

    print("利用可能な音声スタイル:")
    for style in styles:
        print(
            f"- ID={style['style_id']} / "
            f"{style['speaker_name']} / {style['style_name']}"
        )


def get_aivis_speaker_id(client):
    speaker_id = get_configured_aivis_speaker_id()
    available_style_ids = {
        style["style_id"] for style in client.get_styles()
    }
    if speaker_id not in available_style_ids:
        raise RuntimeError(
            "AIVIS_SPEAKER_IDに対応するスタイルが見つかりません。"
            f"speaker_id={speaker_id}"
        )
    return speaker_id


def get_configured_aivis_speaker_id():
    # 常駐管理画面の起動時はAivisSpeechへ接続せず、設定値だけを検証します。
    raw_speaker_id = os.getenv("AIVIS_SPEAKER_ID", "").strip()
    if not raw_speaker_id:
        raise RuntimeError(
            "AIVIS_SPEAKER_IDが未設定です。"
            ".envへ使用するスタイルIDを設定してください。"
        )

    try:
        speaker_id = int(raw_speaker_id)
    except ValueError as exc:
        raise RuntimeError("AIVIS_SPEAKER_IDは整数で設定してください。") from exc

    return speaker_id


def get_control_server_port():
    raw_port = os.getenv("CONTROL_SERVER_PORT", "8765").strip()
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise RuntimeError("CONTROL_SERVER_PORTは整数で設定してください。") from exc
    if not 1024 <= port <= 65535:
        raise RuntimeError("CONTROL_SERVER_PORTは1024〜65535で設定してください。")
    return port


def get_obs_overlay_wait_seconds():
    raw_seconds = os.getenv("OBS_OVERLAY_WAIT_SECONDS", "120").strip()
    try:
        wait_seconds = float(raw_seconds)
    except ValueError as exc:
        raise RuntimeError(
            "OBS_OVERLAY_WAIT_SECONDSは数値で設定してください。"
        ) from exc
    if not 0 <= wait_seconds <= 600:
        raise RuntimeError(
            "OBS_OVERLAY_WAIT_SECONDSは0〜600秒で設定してください。"
        )
    return wait_seconds


def get_youtube_live_wait_settings():
    raw_interval = os.getenv("YOUTUBE_LIVE_WAIT_INTERVAL_SECONDS", "10").strip()
    raw_timeout = os.getenv("YOUTUBE_LIVE_WAIT_TIMEOUT_SECONDS", "0").strip()
    try:
        interval_seconds = float(raw_interval)
        timeout_seconds = float(raw_timeout)
    except ValueError as exc:
        raise RuntimeError(
            "YouTubeライブ待機時間は数値で設定してください。"
        ) from exc
    if not 5 <= interval_seconds <= 300:
        raise RuntimeError(
            "YOUTUBE_LIVE_WAIT_INTERVAL_SECONDSは5〜300秒で設定してください。"
        )
    if not 0 <= timeout_seconds <= 86400:
        raise RuntimeError(
            "YOUTUBE_LIVE_WAIT_TIMEOUT_SECONDSは0〜86400秒で設定してください。"
        )
    return interval_seconds, timeout_seconds


def wait_for_youtube_stream_active(
    stream_id,
    timeout_seconds=60,
    interval_seconds=2,
    status_checker=None,
    sleep_callback=None,
    now=None,
):
    # OBS開始後、YouTubeが映像を受信してからライブ状態へ移行します。
    normalized_stream_id = str(stream_id or "").strip()
    if not normalized_stream_id:
        raise ValueError("映像到着を確認するYouTubeストリームIDが空です。")
    if timeout_seconds <= 0:
        raise ValueError("YouTube映像到着の待機時間は0より大きくしてください。")
    if interval_seconds <= 0:
        raise ValueError("YouTube映像到着の確認間隔は0より大きくしてください。")
    if status_checker is None:
        from youtube_oauth import is_youtube_stream_active

        status_checker = is_youtube_stream_active
    sleep_callback = sleep_callback or time.sleep
    now = now or time.monotonic
    deadline = now() + timeout_seconds
    while True:
        if status_checker(normalized_stream_id):
            return
        remaining = deadline - now()
        if remaining <= 0:
            raise RuntimeError(
                "OBSを開始しましたが、YouTubeへの映像到着を確認できませんでした。"
                f" stream_id={normalized_stream_id}"
            )
        sleep_callback(min(interval_seconds, remaining))


def start_obs_for_upcoming_youtube_broadcast(
    runtime,
    obs_websocket_client,
    command=None,
):
    # 配信枠を必要に応じて作成または更新し、OBSから映像送信を開始します。
    from youtube_oauth import (
        create_youtube_broadcast,
        find_upcoming_youtube_broadcast,
        NoUpcomingYouTubeBroadcastError,
        transition_youtube_broadcast_to_live,
        update_youtube_broadcast_metadata,
    )

    if obs_websocket_client is None:
        raise RuntimeError(
            "OBS WebSocketが無効なため配信を自動開始できません。"
        )
    requested_stream_plan = None
    if command is not None and command.get("action") == "configure_broadcast":
        schedule_id = command.get("schedule_id")
        draft_id = command.get("draft_id")
        if schedule_id:
            requested_stream_plan = (
                runtime.select_prepared_broadcast_schedule(schedule_id)
            )
        elif draft_id:
            requested_stream_plan = runtime.select_prepared_broadcast_plan(
                draft_id
            )
        else:
            runtime.clear_selected_broadcast_plan()
            requested_stream_plan = command.get("stream_plan")
    elif command is not None and command.get("action") == "start_broadcast":
        runtime.clear_selected_broadcast_plan()

    created_broadcast = False
    try:
        broadcast = find_upcoming_youtube_broadcast()
    except NoUpcomingYouTubeBroadcastError as exc:
        if command is None or command.get("action") != "configure_broadcast":
            raise RuntimeError(
                "開始可能なYouTube配信枠がありません。"
                "先に管理画面でAI配信構成を作成し、"
                "「内容を設定して開始」を押してください。"
            ) from exc
        runtime.update_admin_status(
            phase="configuring_broadcast",
            message="YouTubeに新しい配信枠を作成しています。",
        )
        broadcast = create_youtube_broadcast(
            title=command["title"],
            description=command["description"],
            privacy_status=command["privacy_status"],
        )
        created_broadcast = True
        print(
            "YouTube配信枠を自動作成しました："
            f"{broadcast['title']} / video_id={broadcast['video_id']} "
            f"privacy={broadcast['privacy_status']}"
        )

    if command is not None and command.get("action") == "configure_broadcast":
        runtime.update_admin_status(
            phase="configuring_broadcast",
            message=(
                "YouTube配信枠を作成しました。"
                if created_broadcast
                else "YouTube配信の内容を更新しています。"
            ),
        )
        if not created_broadcast:
            updated = update_youtube_broadcast_metadata(
                video_id=broadcast["video_id"],
                title=command["title"],
                description=command["description"],
                privacy_status=command["privacy_status"],
            )
            broadcast.update(updated)
            print(
                "YouTube配信の内容を更新しました："
                f"{broadcast['title']} / video_id={broadcast['video_id']} "
                f"privacy={broadcast['privacy_status']}"
            )
    runtime.update_admin_status(
        phase="starting_broadcast",
        message=(
            "OBSから映像送信を開始しています。"
            f"対象={broadcast['title']} 公開設定={broadcast['privacy_status']}"
        ),
        starting_video_id=broadcast["video_id"],
        starting_broadcast_title=broadcast["title"],
    )
    started = obs_websocket_client.start_stream()
    print(
        "管理画面：OBS配信出力を開始しました。"
        if started
        else "管理画面：OBS配信出力はすでに稼働中です。"
    )
    print(
        f"YouTube自動開始待機：{broadcast['title']} / "
        f"video_id={broadcast['video_id']} "
        f"privacy={broadcast['privacy_status']}"
    )
    if not broadcast["enable_auto_start"]:
        runtime.update_admin_status(
            phase="starting_broadcast",
            message="YouTubeへの映像到着を確認しています。",
        )
        wait_for_youtube_stream_active(broadcast["bound_stream_id"])
        transition_youtube_broadcast_to_live(broadcast["video_id"])
        print(
            "YouTubeライブの開始を指示しました："
            f"video_id={broadcast['video_id']}"
        )
    runtime.update_admin_status(
        phase="starting_broadcast",
        message="OBSから映像を送信しました。YouTubeのライブ開始を待っています。",
        starting_video_id=broadcast["video_id"],
        starting_broadcast_title=broadcast["title"],
    )
    if command is not None and command.get("schedule_id"):
        runtime.update_active_broadcast_schedule(
            "youtube_scheduled",
            video_id=broadcast["video_id"],
        )
    return broadcast["video_id"], (
        requested_stream_plan
        if command is not None and command.get("action") == "configure_broadcast"
        else None
    )


def wait_for_youtube_live(runtime=None, obs_websocket_client=None):
    # ライブ未開始だけを待機し、認証・通信・APIエラーはそのまま通知します。
    interval_seconds, timeout_seconds = get_youtube_live_wait_settings()
    started_at = time.monotonic()
    attempt = 0
    expected_video_id = None
    requested_stream_plan = None
    while True:
        attempt += 1
        try:
            live_chat_id, video_id = get_live_chat_id(return_video_id=True)
        except YouTubeLiveNotStartedError as exc:
            elapsed_seconds = time.monotonic() - started_at
            if timeout_seconds and elapsed_seconds >= timeout_seconds:
                raise RuntimeError(
                    "YouTubeライブ開始の待機時間を超えました。"
                    f" timeout_seconds={timeout_seconds:g}"
                ) from exc
            message = (
                "YouTubeライブ開始を待っています。"
                f"次回確認={interval_seconds:g}秒後"
            )
            if runtime is not None:
                runtime.update_admin_status(
                    phase="waiting_for_youtube",
                    message=(
                        message
                        if obs_websocket_client is None
                        else "管理画面でAI配信構成を作成し、内容を設定して開始してください。"
                    ),
                    youtube_wait_attempts=attempt,
                )
            print(f"{message} 確認回数={attempt}")
            if runtime is not None and obs_websocket_client is not None:
                command = runtime.get_next_admin_command(
                    timeout_seconds=interval_seconds
                )
                if command is None:
                    continue
                if command.get("action") not in {
                    "start_broadcast",
                    "prepare_broadcast",
                    "configure_broadcast",
                    "stop_live_control",
                }:
                    runtime.update_admin_status(
                        phase="waiting_for_youtube",
                        message=(
                            "配信開始前に利用できるのは、AI配信構成の作成と"
                            "OBS・YouTube配信開始だけです。"
                        ),
                    )
                    continue
                if command.get("action") == "stop_live_control":
                    runtime.update_admin_status(
                        phase="stopping_live_control",
                        message="配信開始前のライブ制御を停止します。",
                    )
                    raise AdminRequestedBroadcastEnd(
                        "管理画面からライブ制御を停止しました。"
                    )
                if command.get("action") == "prepare_broadcast":
                    try:
                        from stream_theme import generate_stream_theme_plan

                        runtime.update_admin_status(
                            phase="preparing_broadcast_draft",
                            message="AIが配信構成表を作成しています。",
                        )
                        instruction = command.get("stream_plan") or None
                        plan = generate_stream_theme_plan(
                            instruction=instruction
                        )
                        schedule_id = command.get("schedule_id")
                        if schedule_id:
                            draft = runtime.store_prepared_broadcast_draft(
                                plan,
                                instruction,
                                command["id"],
                                schedule_id=schedule_id,
                            )
                        else:
                            draft = runtime.store_prepared_broadcast_draft(
                                plan,
                                instruction,
                                command["id"],
                            )
                        runtime.update_admin_status(
                            phase="waiting_for_youtube",
                            message=(
                                "AI配信構成を作成しました。内容を確認して開始してください。"
                                f" 企画={draft['theme']}"
                            ),
                        )
                        print(
                            "AI配信構成を作成しました："
                            f"theme={draft['theme']} "
                            f"news={draft['news_description']}"
                        )
                    except (RuntimeError, ValueError) as prepare_exc:
                        if command.get("schedule_id"):
                            runtime.update_broadcast_schedule_status(
                                command["schedule_id"],
                                "error",
                                error=str(prepare_exc),
                            )
                        runtime.update_admin_status(
                            phase="waiting_for_youtube",
                            message=f"AI配信構成を作成できませんでした: {prepare_exc}",
                        )
                        print(f"AI配信構成作成エラー: {prepare_exc}")
                    continue
                try:
                    (
                        expected_video_id,
                        requested_stream_plan,
                    ) = start_obs_for_upcoming_youtube_broadcast(
                        runtime,
                        obs_websocket_client,
                        command=command,
                    )
                except (RuntimeError, ValueError) as start_exc:
                    if command.get("schedule_id"):
                        runtime.update_broadcast_schedule_status(
                            command["schedule_id"],
                            "error",
                            error=str(start_exc),
                        )
                    runtime.update_admin_status(
                        phase="waiting_for_youtube",
                        message=f"配信を開始できませんでした: {start_exc}",
                    )
                    print(f"配信自動開始エラー: {start_exc}")
                continue
            time.sleep(interval_seconds)
            continue
        if expected_video_id is not None and video_id != expected_video_id:
            raise RuntimeError(
                "開始を指示した配信とは別のYouTubeライブを検出しました。"
                f" expected={expected_video_id} actual={video_id}"
            )
        if runtime is not None:
            runtime.update_active_broadcast_schedule(
                "live",
                video_id=video_id,
            )
            runtime.update_admin_status(
                phase="preparing",
                message="YouTubeライブを確認しました。配信を準備しています。",
                youtube_wait_attempts=attempt,
            )
        return live_chat_id, video_id, requested_stream_plan


def _create_youtube_live_status_callback(video_id):
    # OAuth依存はライブ開始後だけ読み込み、同じ配信の終了状態を追跡します。
    def check_live_status():
        from youtube_oauth import is_youtube_broadcast_live

        return is_youtube_broadcast_live(video_id)

    return check_live_status


def parse_interactive_speech_command(command):
    # 通常入力はneutral、/emotion指定時は指定された感情で発話します。
    if not command.startswith("/emotion "):
        return command, "neutral"

    parts = command.split(maxsplit=2)
    if len(parts) < 3:
        raise ValueError("形式: /emotion surprised 発話する文章")
    emotion = parts[1].strip()
    text = parts[2].strip()
    if emotion not in ALLOWED_EMOTIONS:
        allowed = ", ".join(sorted(ALLOWED_EMOTIONS))
        raise ValueError(f"未対応のemotionです。利用可能={allowed}")
    return text, emotion


def start_external_control_server():
    # 外部制御を使う各モードで共通のサーバーを起動します。
    client = create_aivis_client()
    version = client.get_version()
    speaker_id = get_aivis_speaker_id(client)
    port = get_control_server_port()
    try:
        server = ExternalControlServer(
            aivis_client=client,
            speaker_id=speaker_id,
            port=port,
        )
        server.start()
    except OSError as exc:
        raise RuntimeError(
            f"外部制御サーバーを起動できません。port={port} detail={exc}"
        ) from exc

    return server, version, speaker_id, port


def print_external_control_server_info(version, speaker_id, port):
    print(
        "外部制御サーバーを起動しました。"
        f"AivisSpeech={version} speaker_id={speaker_id}"
    )
    print(f"SSE URL: http://127.0.0.1:{port}/events")
    print(f"OBS字幕URL: http://127.0.0.1:{port}/overlay")
    print(f"OBS話題カードURL: http://127.0.0.1:{port}/topic-overlay")
    print(f"OBSチャットURL: http://127.0.0.1:{port}/chat-overlay")
    project_root = Path(__file__).resolve().parent
    print(f"OBS固定字幕ファイル: {project_root / 'subtitle_overlay.html'}")
    print(f"OBS固定話題カードファイル: {project_root / 'topic_overlay.html'}")
    print(f"OBS固定チャットファイル: {project_root / 'chat_overlay.html'}")
    print(f"配信管理画面: http://127.0.0.1:{port}/admin")
    print(f"記憶管理画面: http://127.0.0.1:{port}/character-memories")
    print(f"確認URL: http://127.0.0.1:{port}/health")


def run_external_control_server():
    # AivisSpeechで生成した音声とSSE命令をAITuber OnAirへ配信します。
    server, version, speaker_id, port = start_external_control_server()

    print_external_control_server_info(version, speaker_id, port)
    print("発話する文章を入力してください。")
    print("感情指定: /emotion surprised びっくりした！")
    print("表情解除: /reset")
    print("終了: /quit")

    try:
        while True:
            try:
                command = input("発話> ").strip()
            except EOFError:
                break
            if not command:
                continue
            if command == "/quit":
                break
            if command == "/reset":
                _, delivered_count = server.runtime.reset()
                print(
                    "リセット命令を送信しました。"
                    f"接続中クライアント数={delivered_count}"
                )
                continue

            try:
                text, emotion = parse_interactive_speech_command(command)
                _, delivered_count = server.runtime.speak(text, emotion)
                print(
                    "音声と発話命令を送信しました。"
                    f"emotion={emotion} 接続中クライアント数={delivered_count}"
                )
            except (RuntimeError, ValueError) as exc:
                print(f"発話エラー: {exc}")
    except KeyboardInterrupt:
        print("\n終了操作を受け付けました。")
    finally:
        server.stop()
        print("外部制御サーバーを停止しました。")


def deliver_generated_response(runtime, ai_response):
    filtered_response, delivered_count, _ = (
        deliver_generated_response_with_command(runtime, ai_response)
    )
    return filtered_response, delivered_count


def deliver_generated_response_with_command(runtime, ai_response):
    prepared_speech = runtime.prepare_speech(
        ai_response["text"],
        ai_response["emotion"],
        ai_response.get("speech_style", "normal"),
    )
    return deliver_prepared_response(
        runtime,
        ai_response,
        prepared_speech,
    )


def deliver_prepared_response(runtime, ai_response, prepared_speech):
    # ランタイムごとにモーション間隔を管理し、発話経路を共通化します。
    runtime_state = vars(runtime)
    motion_limiter = runtime_state.get("_ai_motion_limiter")
    if not isinstance(motion_limiter, MotionRateLimiter):
        motion_limiter = MotionRateLimiter()
        runtime_state["_ai_motion_limiter"] = motion_limiter

    filtered_response = dict(ai_response)
    filtered_response["motion"] = motion_limiter.filter(
        ai_response.get("motion")
    )
    command, delivered_count = runtime.publish_prepared_speech(
        prepared_speech,
        filtered_response["motion"],
        filtered_response.get("view_action"),
    )
    return filtered_response, delivered_count, command


def generate_and_deliver_ai_response(
    runtime,
    user_name,
    comment,
    user_id="",
    stream_context=None,
):
    # LLMの返答結果をそのままAivisSpeechとAITuber OnAirへ渡します。
    if stream_context is None:
        ai_response = generate_ai_response(user_name, comment)
    else:
        ai_response = generate_ai_response(
            user_name,
            comment,
            context_builder=stream_context.context_builder,
            user_id=user_id,
            character_memory_repository=(
                stream_context.character_memory_repository
            ),
        )
        stream_context.record_comment_exchange(
            user_id,
            user_name,
            comment,
            ai_response,
        )
    return deliver_generated_response(runtime, ai_response)


def get_autonomous_speech_interval_seconds():
    raw_interval = os.getenv(
        "AUTONOMOUS_SPEECH_INTERVAL_SECONDS",
        "600",
    ).strip()
    try:
        interval_seconds = int(raw_interval)
    except ValueError as exc:
        raise RuntimeError(
            "AUTONOMOUS_SPEECH_INTERVAL_SECONDSは整数で設定してください。"
        ) from exc
    if not 60 <= interval_seconds <= 3600:
        raise RuntimeError(
            "AUTONOMOUS_SPEECH_INTERVAL_SECONDSは60〜3600秒で設定してください。"
        )
    return interval_seconds


def get_unused_news_article(used_links=None):
    articles = fetch_news_articles()
    article = select_news_article(articles, used_links)
    if article is None:
        raise RuntimeError(
            "未使用かつ雑談に適したニュース記事が見つかりませんでした。"
        )
    return article


def print_news_commentary(article, ai_response, delivered_count=None):
    print(
        "ニュース："
        f"{article['title']} / {article['source_name']} / "
        f"{article['published_at'] or '公開日時不明'}"
    )
    print(f"参照URL：{article['link']}")
    print(f"AI：{ai_response['text']}")
    print(f"emotion：{ai_response['emotion']}")
    if delivered_count is not None:
        print(f"接続中クライアント数：{delivered_count}")


def generate_and_deliver_news_commentary(
    runtime,
    article,
    stream_context=None,
):
    # ニュース雑談を生成し、AivisSpeechとAITuber OnAirへ渡します。
    ai_response = generate_news_commentary(
        article,
        context_builder=(
            stream_context.context_builder if stream_context else None
        ),
        character_memory_repository=(
            stream_context.character_memory_repository
            if stream_context
            else None
        ),
    )
    if stream_context is not None:
        record_ai_speech_with_character_event(
            stream_context,
            ai_response,
            source="news",
        )
    return deliver_generated_response(runtime, ai_response)


def run_news_test():
    # YouTubeや音声合成を使わず、ニュース取得と雑談生成を確認します。
    article = get_unused_news_article()
    ai_response = generate_news_commentary(article)
    print_news_commentary(article, ai_response)


def run_news_voice_test():
    # ニュース雑談を音声、字幕、表情としてAITuber OnAirへ送信します。
    server, version, speaker_id, port = start_external_control_server()
    print_external_control_server_info(version, speaker_id, port)

    try:
        article = get_unused_news_article()
        ai_response, delivered_count = generate_and_deliver_news_commentary(
            server.runtime,
            article,
        )
        print_news_commentary(article, ai_response, delivered_count)
    finally:
        server.stop()
        print("外部制御サーバーを停止しました。")


def get_mock_live_delay_seconds(override=None):
    raw_delay = (
        str(override)
        if override is not None
        else os.getenv("MOCK_LIVE_DELAY_SECONDS", "15").strip()
    )
    try:
        delay_seconds = float(raw_delay)
    except ValueError as exc:
        raise RuntimeError("MOCK_LIVE_DELAY_SECONDSは数値で設定してください。") from exc
    if not 0 <= delay_seconds <= 60:
        raise RuntimeError("MOCK_LIVE_DELAY_SECONDSは0〜60秒で設定してください。")
    return delay_seconds


def deliver_autonomous_speech(
    runtime,
    situation,
    recent_utterances,
    stream_context=None,
):
    ai_response = generate_autonomous_speech(
        situation,
        recent_utterances,
        context_builder=(
            stream_context.context_builder if stream_context else None
        ),
        character_memory_repository=(
            stream_context.character_memory_repository
            if stream_context
            else None
        ),
    )
    recent_utterances.append(ai_response["text"])
    if stream_context is not None:
        record_ai_speech_with_character_event(
            stream_context,
            ai_response,
            source="autonomous_speech",
        )
    return deliver_generated_response(runtime, ai_response)


def run_mock_live(delay_override=None):
    # YouTubeを使わず、配信開始から終了までの主要な発話経路を再現します。
    delay_seconds = get_mock_live_delay_seconds(delay_override)
    server, version, speaker_id, port = start_external_control_server()
    recent_utterances = []
    mock_comments = [
        ("初見さん", "初見です。今日の配信を楽しみにしていました"),
        ("ゲーム好き", "ガン奈はどんなゲームが好き？"),
        ("仕事帰り", "今日も残業で疲れたよ"),
    ]

    print_external_control_server_info(version, speaker_id, port)
    print("模擬ライブを開始します。終了する場合はCtrl+Cを押してください。")
    print(f"発話間隔：{delay_seconds}秒")

    try:
        opening_response, delivered_count = deliver_autonomous_speech(
            server.runtime,
            "配信を開始した直後。視聴者を短く歓迎する",
            recent_utterances,
        )
        print("--- 配信開始 ---")
        print(f"AI：{opening_response['text']}")
        print(f"emotion：{opening_response['emotion']}")
        print(f"接続中クライアント数：{delivered_count}")

        for user_name, comment in mock_comments:
            time.sleep(delay_seconds)
            ai_response, delivered_count = generate_and_deliver_ai_response(
                server.runtime,
                user_name,
                comment,
            )
            recent_utterances.append(ai_response["text"])
            print("--- ダミーコメント ---")
            print(f"{user_name}：{comment}")
            print(f"AI：{ai_response['text']}")
            print(f"emotion：{ai_response['emotion']}")
            print(f"接続中クライアント数：{delivered_count}")

        time.sleep(delay_seconds)
        article = get_unused_news_article()
        news_response, delivered_count = generate_and_deliver_news_commentary(
            server.runtime,
            article,
        )
        recent_utterances.append(news_response["text"])
        print("--- コメントがない時間のニュース雑談 ---")
        print_news_commentary(article, news_response, delivered_count)

        time.sleep(delay_seconds)
        closing_response, delivered_count = deliver_autonomous_speech(
            server.runtime,
            "模擬配信を終了する直前。視聴者へ感謝して、短く別れを告げる",
            recent_utterances,
        )
        print("--- 配信終了 ---")
        print(f"AI：{closing_response['text']}")
        print(f"emotion：{closing_response['emotion']}")
        print(f"接続中クライアント数：{delivered_count}")
        # 最後の音声をクライアントが取得する前にサーバーが終了するのを防ぎます。
        shutdown_grace_seconds = min(max(delay_seconds, 1), 5)
        time.sleep(shutdown_grace_seconds)
    except KeyboardInterrupt:
        print("\n終了操作を受け付けました。")
    finally:
        server.stop()
        print("外部制御サーバーを停止しました。")


def run_interactive_ai():
    # 手入力コメントでOpenAIからVRM発話までの経路を確認します。
    server, version, speaker_id, port = start_external_control_server()
    user_name = "テストユーザー"

    print_external_control_server_info(version, speaker_id, port)
    print("視聴者コメントを入力すると、AIキャラクターが返答します。")
    print("表情解除: /reset")
    print("終了: /quit")

    try:
        while True:
            try:
                comment = input("視聴者コメント> ").strip()
            except EOFError:
                break
            if not comment:
                continue
            if comment == "/quit":
                break
            if comment == "/reset":
                _, delivered_count = server.runtime.reset()
                print(
                    "リセット命令を送信しました。"
                    f"接続中クライアント数={delivered_count}"
                )
                continue

            try:
                ai_response, delivered_count = generate_and_deliver_ai_response(
                    server.runtime,
                    user_name,
                    comment,
                )
                print(f"AI：{ai_response['text']}")
                print(f"emotion：{ai_response['emotion']}")
                print(f"接続中クライアント数：{delivered_count}")
            except (RuntimeError, ValueError) as exc:
                print(f"AI発話エラー: {exc}")
    except KeyboardInterrupt:
        print("\n終了操作を受け付けました。")
    finally:
        server.stop()
        print("外部制御サーバーを停止しました。")


def run_youtube_chat_id_test():
    # 配信動画IDからliveChatIdを取得できるか確認します。
    live_chat_id = get_live_chat_id()

    print("YouTube LiveのliveChatIdを取得しました。")
    print(f"liveChatId：{live_chat_id}")


def run_youtube_messages_test():
    # liveChatIdを取得した後、コメントを1回だけ取得します。
    live_chat_id = get_live_chat_id()
    result = fetch_chat_messages(live_chat_id)

    messages = result["messages"]
    print(f"取得コメント数：{len(messages)}")
    print(f"次回取得用トークン：{result['next_page_token']}")
    print(f"推奨待機時間ミリ秒：{result['polling_interval_millis']}")

    for message in messages:
        print(f"{message['user_name']}：{message['comment']}")


def run_youtube_loop_test(max_loops):
    # YouTube Liveコメントを指定回数だけ継続取得します。
    live_chat_id = get_live_chat_id()
    print("YouTube Liveコメントの取得ループを開始します。")
    print(f"最大取得回数：{max_loops}")

    for index, result in enumerate(iter_chat_messages(live_chat_id, max_loops=max_loops), start=1):
        messages = result["messages"]
        print(f"--- {index}回目 / 取得コメント数：{len(messages)} ---")

        for message in messages:
            print(f"{message['user_name']}：{message['comment']}")


def select_reply_target(messages):
    # Phase 1では、返答しやすい新規コメントの先頭1件に返答します。
    for message in messages:
        if is_reply_candidate(message):
            return message

    return None


def is_reply_candidate(message):
    # 空コメント、長すぎるコメント、意味の薄い文字列は返答対象から外します。
    comment = message.get("comment", "").strip()

    if len(comment) < 2:
        return False

    if len(comment) > 120:
        return False

    if comment.startswith("@"):
        return False

    normalized_comment = unicodedata.normalize("NFKC", comment).lower()
    compact_comment = "".join(
        char for char in normalized_comment if not char.isspace()
    )
    if re.fullmatch(r"(?:https?://|www\.)\S+", compact_comment):
        return False

    meaningful_chars = [char for char in compact_comment if char.isalnum()]
    if not meaningful_chars:
        return False

    meaningful_text = "".join(meaningful_chars)
    if re.fullmatch(r"(?:w{2,}|草+|笑+|8{3,}|っ{3,}[a-z0-9]?)", meaningful_text):
        return False
    if set(meaningful_text) <= {"w", "草", "笑", "8"}:
        return False

    # 「っっっっっf」のように一文字の連打が大半を占める入力を除外します。
    if len(meaningful_chars) >= 4:
        most_common_count = max(
            meaningful_chars.count(char) for char in set(meaningful_chars)
        )
        if most_common_count / len(meaningful_chars) >= 0.75:
            return False

    return True


def process_next_admin_command(
    runtime,
    autonomous_buffer,
    stream_context,
    schedule_broadcast_end=None,
):
    command = runtime.get_next_admin_command()
    if command is None:
        return False

    action = command["action"]
    if action == "stop_live_control":
        autonomous_buffer.pause()
        raise AdminRequestedBroadcastEnd(
            "管理画面からライブ制御を停止しました。"
        )
    try:
        if action == "start_broadcast":
            raise RuntimeError("YouTubeライブはすでに開始しています。")
        if action == "end_broadcast" and schedule_broadcast_end is None:
            raise RuntimeError(
                "YouTube自動終了はai-youtuber-liveモードでのみ利用できます。"
            )
        if action == "pause_autonomous":
            autonomous_buffer.pause()
            runtime.update_admin_status(
                autonomous_paused=True,
                phase="paused",
                message="自発発話を一時停止しました。コメント返信は継続します。",
            )
            print("管理画面：自発発話を一時停止しました。")
            return True

        if action == "resume_autonomous":
            autonomous_buffer.resume()
            runtime.update_admin_status(
                autonomous_paused=False,
                phase="waiting",
                message="自発発話を再開しました。",
            )
            print("管理画面：自発発話を再開しました。")
            return True

        if action == "cancel_next":
            autonomous_buffer.cancel_next()
            runtime.update_admin_status(
                phase=("paused" if autonomous_buffer.paused else "waiting"),
                message="先読み済みの次の自発発話をキャンセルしました。",
            )
            print("管理画面：次の自発発話をキャンセルしました。")
            return True

        if action == "change_stream_plan":
            runtime.update_admin_status(
                phase="processing",
                message="新しい配信構成を作成しています。",
            )
            description = autonomous_buffer.replace_program(command["text"])
            runtime.update_admin_status(
                **autonomous_buffer.theme_manager.status(),
                phase="waiting",
                message="配信企画と話題構成を変更しました。",
            )
            print("--- 管理画面から配信構成を変更 ---")
            print(description)
            return True

        autonomous_buffer.cancel_next()
        runtime.update_admin_status(
            phase="processing",
            message=(
                "終了挨拶を生成しています。"
                if action in {"closing_greeting", "end_broadcast"}
                else "管理者の発話指示を処理しています。"
            ),
        )

        if action == "direct_speech":
            generated_response = {
                "text": command["text"],
                "emotion": command["emotion"],
                "speech_style": command["speech_style"],
                "motion": command.get("motion"),
                "view_action": None,
            }
        else:
            instruction = (
                "その配信らしい自然な終了挨拶をしてください。"
                if action in {"closing_greeting", "end_broadcast"}
                else command["text"]
            )
            generated_response = generate_admin_directed_speech(
                instruction,
                context_builder=stream_context.context_builder,
                closing_greeting=(
                    action in {"closing_greeting", "end_broadcast"}
                ),
            )

        if action == "direct_speech":
            speech_command, delivered_count = runtime.speak(
                generated_response["text"],
                generated_response["emotion"],
                generated_response.get("motion"),
                generated_response.get("view_action"),
                generated_response.get("speech_style", "normal"),
            )
            ai_response = generated_response
        else:
            ai_response, delivered_count, speech_command = (
                deliver_generated_response_with_command(
                    runtime,
                    generated_response,
                )
            )
        record_ai_speech_with_character_event(
            stream_context,
            ai_response,
            source="admin_instruction",
        )
        if action in {"closing_greeting", "end_broadcast"}:
            autonomous_buffer.pause()
        else:
            autonomous_buffer.schedule_after_external_speech(
                speech_command["duration_ms"]
            )
        runtime.update_admin_status(
            autonomous_paused=(
                True
                if action in {"closing_greeting", "end_broadcast"}
                else autonomous_buffer.paused
            ),
            phase="speaking",
            message=(
                (
                    "終了挨拶を再生しています。再生後にYouTubeを終了します。"
                    if action == "end_broadcast"
                    else "終了挨拶を再生しています。自発発話は停止しました。"
                    "OBSとYouTubeは停止しません。"
                )
                if action in {"closing_greeting", "end_broadcast"}
                else "管理者指定の発話を再生しています。"
            ),
            speaking_until_ms=round(
                time.time() * 1000 + speech_command["duration_ms"]
            ),
        )
        if action == "end_broadcast":
            schedule_broadcast_end(speech_command["duration_ms"])
        print(f"--- 管理画面からの発話 / action={action} ---")
        print(f"AI：{ai_response['text']}")
        print(f"emotion：{ai_response['emotion']}")
        print(f"接続中クライアント数：{delivered_count}")
        return True
    except (RuntimeError, ValueError) as exc:
        runtime.update_admin_status(
            phase="error",
            message=f"管理命令の処理に失敗しました: {exc}",
        )
        print(f"管理命令エラー: action={action} detail={exc}")
        return True


def run_ai_youtuber_once():
    # コメントを1回取得し、最初の1件にAIキャラクターとして返答します。
    live_chat_id = get_live_chat_id()
    result = fetch_chat_messages(live_chat_id)
    target_message = select_reply_target(result["messages"])

    if target_message is None:
        print("返答対象のコメントはありませんでした。")
        return

    ai_response = generate_ai_response(target_message["user_name"], target_message["comment"])

    print(f"{target_message['user_name']}：{target_message['comment']}")
    print(f"AI：{ai_response['text']}")
    print(f"emotion：{ai_response['emotion']}")


def run_ai_youtuber_loop(
    max_loops,
    runtime=None,
    stream_topic=None,
    stream_plan=None,
    obs_websocket_client=None,
    news_history_repository=None,
):
    # コメントを優先し、音声終了後の無言時間が続いたら自発発話します。
    prepared_theme_plan = None
    if runtime is not None:
        # OBSブラウザが保持している前回配信の字幕・話題・コメントを先に消去します。
        runtime.clear_overlays()
        overlay_wait_seconds = get_obs_overlay_wait_seconds()
        runtime.update_admin_status(
            available=True,
            phase="waiting_for_obs",
            message="OBSの字幕・話題カード・コメント接続を待っています。",
        )
        print(
            "OBS接続待機：字幕・話題カード・コメントの接続を待っています。"
            f"最大={overlay_wait_seconds:g}秒"
        )
        if not runtime.wait_for_obs_overlays(overlay_wait_seconds):
            overlay_status = runtime.get_obs_overlay_status()
            raise RuntimeError(
                "OBSの字幕・話題カード・コメント接続を確認できませんでした。"
                f" subtitle_connected={overlay_status['subtitle_connected']}"
                f" topic_connected={overlay_status['topic_connected']}"
                f" chat_connected={overlay_status['chat_connected']} "
                "OBSのローカルHTML設定を確認してください。"
            )
        print("OBS接続確認：字幕・話題カード・コメントすべて接続済みです。")
        live_chat_id, video_id, requested_stream_plan = wait_for_youtube_live(
            runtime,
            obs_websocket_client=obs_websocket_client,
        )
        if requested_stream_plan:
            stream_plan = requested_stream_plan
        selected_broadcast_plan = runtime.consume_selected_broadcast_plan()
        if (
            isinstance(selected_broadcast_plan, dict)
            and "plan" in selected_broadcast_plan
        ):
            prepared_theme_plan = selected_broadcast_plan["plan"]
            stream_plan = selected_broadcast_plan["instruction"] or None
    else:
        live_chat_id = get_live_chat_id()
        video_id = None
    processed_message_ids = set()
    used_news_links = set()
    llm_config = load_llm_config()
    stream_context = StreamContextManager(config=llm_config)
    speech_scheduler = SpeechScheduler.from_config(llm_config)
    topic_selector = AutonomousTopicSelector(llm_config)
    autonomous_buffer = None
    if runtime is not None:
        autonomous_buffer = AutonomousSpeechBuffer(
            runtime=runtime,
            stream_context=stream_context,
            config=llm_config,
            publish_callback=deliver_prepared_response,
            stream_topic=stream_topic,
            stream_instruction=stream_plan,
            prepared_theme_plan=prepared_theme_plan,
            news_history_repository=news_history_repository,
        )
    recent_utterances = []
    previous_autonomous_topic = None
    has_received_comment = False
    broadcast_end_coordinator = (
        YouTubeBroadcastEndCoordinator(video_id, runtime)
        if runtime is not None and video_id is not None
        else None
    )
    if broadcast_end_coordinator is not None and obs_websocket_client is not None:
        broadcast_end_coordinator.stop_obs_callback = (
            obs_websocket_client.stop_stream
        )

    print("AI YouTuberループを開始します。")
    print(f"最大取得回数：{max_loops}")
    if autonomous_buffer is not None:
        print(autonomous_buffer.theme_manager.describe())
        runtime.update_admin_status(
            **autonomous_buffer.theme_manager.status(),
            available=True,
            autonomous_paused=False,
            phase="preparing",
            message="配信構成を準備しました。開始挨拶を再生します。",
        )
    print(
        "自発発話の無言時間："
        f"{speech_scheduler.silence_seconds:g}秒"
        "（ライブ音声ではWAVの実時間から計測）"
    )

    def process_live_wait():
        if autonomous_buffer is None:
            return False
        if (
            broadcast_end_coordinator is not None
            and broadcast_end_coordinator.tick()
        ):
            raise AdminRequestedBroadcastEnd()
        if process_next_admin_command(
            runtime,
            autonomous_buffer,
            stream_context,
            schedule_broadcast_end=(
                broadcast_end_coordinator.schedule
                if broadcast_end_coordinator is not None
                else None
            ),
        ):
            return True
        return autonomous_buffer.tick()

    chat_poller = YouTubeChatPoller(
        live_chat_id,
        max_loops=max_loops,
        message_callback=(
            runtime.publish_chat_messages if runtime is not None else None
        ),
        live_status_callback=(
            _create_youtube_live_status_callback(video_id)
            if video_id is not None
            else None
        ),
    ).start()
    if autonomous_buffer is not None:
        try:
            autonomous_buffer.publish_opening()
        except (RuntimeError, ValueError):
            chat_poller.stop()
            raise
    chat_results = chat_poller.iter_results(
        wait_callback=(process_live_wait if autonomous_buffer is not None else None)
    )
    index = 0
    while True:
        try:
            if (
                broadcast_end_coordinator is not None
                and broadcast_end_coordinator.tick()
            ):
                print("管理画面の指示によりライブ処理を終了します。")
                break
            result = next(chat_results)
        except StopIteration:
            break
        except AdminRequestedBroadcastEnd:
            print("管理画面の指示によりライブ処理を終了します。")
            break
        except YouTubeLiveEndedError as exc:
            # YouTube側が先に終了した場合は、音声完了を待たずライブ処理を終了します。
            if autonomous_buffer is not None:
                autonomous_buffer.pause()
            if runtime is not None:
                runtime.update_active_broadcast_schedule("completed")
                runtime.update_admin_status(
                    autonomous_paused=True,
                    phase="youtube_ended",
                    message="YouTubeライブ終了を検知しました。Pythonを停止します。",
                )
            print(f"YouTubeライブ終了を検知しました：{exc}")
            print("新しい発話を停止し、音声完了を待たずPythonを終了します。")
            break
        index += 1
        messages = [
            message
            for message in result["messages"]
            if message["message_id"] not in processed_message_ids
        ]
        target_message = select_reply_target(messages)

        print(f"--- {index}回目 / 新規コメント数：{len(messages)} ---")

        for message in messages:
            processed_message_ids.add(message["message_id"])

        if target_message is None:
            print("返答対象のコメントはありませんでした。")
            if autonomous_buffer is not None:
                if not autonomous_buffer.tick():
                    print(
                        "次の準備済み自発発話まで："
                        f"約{autonomous_buffer.seconds_until_next_speech()}秒"
                    )
                continue

            if not speech_scheduler.should_speak_autonomously():
                print(
                    "次の自発発話まで："
                    f"約{speech_scheduler.seconds_until_autonomous_speech()}秒"
                )
                continue

            try:
                selected_topic = topic_selector.select(
                    previous_autonomous_topic
                )
                article = None
                if selected_topic == "news":
                    try:
                        article = get_unused_news_article(used_news_links)
                    except RuntimeError as exc:
                        print(
                            "ニュースを取得できないため雑学へ切り替えます: "
                            f"{exc}"
                        )
                        selected_topic = "trivia"

                if selected_topic == "news":
                    ai_response = generate_news_commentary(
                        article,
                        context_builder=stream_context.context_builder,
                        character_memory_repository=(
                            stream_context.character_memory_repository
                        ),
                    )
                    used_news_links.add(article["link"])
                else:
                    audience_situation = (
                        "配信開始後、まだコメントは一件もない。"
                        "視聴者がいない前提で独り言を話す"
                        if not has_received_comment
                        else
                        "現在はコメントが途切れている。"
                        "誰かに回答せず、独り言を続ける"
                    )
                    ai_response = generate_autonomous_speech(
                        audience_situation,
                        recent_utterances,
                        context_builder=stream_context.context_builder,
                        topic_instruction=TOPIC_INSTRUCTIONS[selected_topic],
                        character_memory_repository=(
                            stream_context.character_memory_repository
                        ),
                    )

                delivered_count = None
                if runtime is not None:
                    ai_response, delivered_count = deliver_generated_response(
                        runtime, ai_response
                    )
                record_ai_speech_with_character_event(
                    stream_context,
                    ai_response,
                    source=(
                        "news"
                        if selected_topic == "news"
                        else "autonomous_speech"
                    ),
                )
                recent_utterances.append(ai_response["text"])
                recent_utterances = recent_utterances[-8:]
                estimated_duration = speech_scheduler.record_speech(
                    ai_response["text"]
                )
                previous_autonomous_topic = selected_topic
                if selected_topic == "news":
                    print_news_commentary(
                        article,
                        ai_response,
                        delivered_count,
                    )
                else:
                    print(f"--- 自発雑談 / 種類：{selected_topic} ---")
                    print(f"AI：{ai_response['text']}")
                    print(f"emotion：{ai_response['emotion']}")
                    print(f"motion：{ai_response.get('motion')}")
                    if delivered_count is not None:
                        print(f"接続中クライアント数：{delivered_count}")
                print(f"推定音声時間：{estimated_duration:.1f}秒")
            except (RuntimeError, ValueError) as exc:
                print(f"自発発話エラー: {exc}")
                # 失敗時は固定時間だけ待ち、取得ループごとの連続再試行を防ぎます。
                speech_scheduler.record_failed_attempt(retry_seconds=10)
            continue

        if autonomous_buffer is not None:
            autonomous_buffer.cancel_for_comment()

        if runtime is not None:
            runtime.publish_chat_reply_state(
                target_message["message_id"],
                "thinking",
            )
        try:
            ai_response = generate_ai_response(
                target_message["user_name"],
                target_message["comment"],
                context_builder=stream_context.context_builder,
                user_id=target_message.get("user_id", ""),
                character_memory_repository=(
                    stream_context.character_memory_repository
                ),
            )
        except (RuntimeError, ValueError):
            if runtime is not None:
                runtime.publish_chat_reply_state(
                    target_message["message_id"],
                    "clear",
                )
            raise
        has_received_comment = True

        print(f"{target_message['user_name']}：{target_message['comment']}")
        print(f"AI：{ai_response['text']}")
        print(f"emotion：{ai_response['emotion']}")
        command = None
        if runtime is not None:
            try:
                ai_response, delivered_count, command = (
                    deliver_generated_response_with_command(
                        runtime,
                        ai_response,
                    )
                )
            except (RuntimeError, ValueError):
                runtime.publish_chat_reply_state(
                    target_message["message_id"],
                    "clear",
                )
                raise
            print(f"motion：{ai_response['motion']}")
            print(f"接続中クライアント数：{delivered_count}")
            runtime.publish_chat_reply_state(
                target_message["message_id"],
                "speaking",
                command["duration_ms"],
            )
        stream_context.record_comment_exchange(
            target_message.get("user_id", ""),
            target_message["user_name"],
            target_message["comment"],
            ai_response,
        )
        if autonomous_buffer is not None:
            autonomous_buffer.resume_after_comment(
                ai_response,
                command["duration_ms"],
                comment=target_message["comment"],
            )
        else:
            recent_utterances.append(ai_response["text"])
            recent_utterances = recent_utterances[-8:]
            speech_scheduler.record_speech(ai_response["text"])


def run_ai_youtuber_live(max_loops, stream_topic=None, stream_plan=None):
    # YouTube、OpenAI、AivisSpeech、AITuber OnAirをまとめて実行します。
    local_services = ensure_live_local_services()
    server = None

    try:
        server, version, speaker_id, port = start_external_control_server()
        print_external_control_server_info(version, speaker_id, port)

        from news_history import get_news_history_repository

        news_history_repository = get_news_history_repository()
        obs_websocket_client = ObsWebSocketClient.from_env()
        if obs_websocket_client is not None:
            output_active = wait_for_obs_ready(obs_websocket_client)
            print(
                "OBS WebSocket接続確認：成功 "
                f"配信出力={'稼働中' if output_active else '停止中'}"
            )
        run_ai_youtuber_loop(
            max_loops,
            runtime=server.runtime,
            stream_topic=stream_topic,
            stream_plan=stream_plan,
            obs_websocket_client=obs_websocket_client,
            news_history_repository=news_history_repository,
        )
    finally:
        if server is not None:
            server.runtime.update_admin_status(
                available=False,
                phase="stopped",
                message="ライブ制御は停止しています。",
            )
            server.stop()
            print("外部制御サーバーを停止しました。")
        local_services.stop()


def prepare_managed_live_services():
    # 予定時刻より前にローカルアプリを起動し、ライブ開始に必要な接続だけ確認します。
    local_services = ensure_live_local_services()
    try:
        client = create_aivis_client()
        version = client.get_version()
        speaker_id = get_aivis_speaker_id(client)
        print(
            "配信アプリの事前準備を確認しました。"
            f"AivisSpeech={version} speaker_id={speaker_id}"
        )
        obs_websocket_client = ObsWebSocketClient.from_env()
        if obs_websocket_client is not None:
            output_active = wait_for_obs_ready(obs_websocket_client)
            print(
                "OBS WebSocket事前接続確認：成功 "
                f"配信出力={'稼働中' if output_active else '停止中'}"
            )
            if output_active:
                raise RuntimeError(
                    "予定時刻前ですがOBSの配信出力がすでに稼働しています。"
                    "OBSで配信を停止してから再度予定を準備してください。"
                )
        return local_services
    except Exception:
        local_services.stop()
        raise


def run_managed_live_session(
    runtime,
    max_loops,
    stream_topic=None,
    stream_plan=None,
    local_services=None,
):
    # 常駐管理サーバーを止めず、事前準備済みまたは新規起動したアプリで配信します。
    local_services = local_services or ensure_live_local_services()
    try:
        client = create_aivis_client()
        version = client.get_version()
        speaker_id = get_aivis_speaker_id(client)
        runtime.aivis_client = client
        runtime.speaker_id = speaker_id
        print(
            "ライブ制御用サービスを準備しました。"
            f"AivisSpeech={version} speaker_id={speaker_id}"
        )

        from news_history import get_news_history_repository

        news_history_repository = get_news_history_repository()
        obs_websocket_client = ObsWebSocketClient.from_env()
        if obs_websocket_client is not None:
            output_active = wait_for_obs_ready(obs_websocket_client)
            print(
                "OBS WebSocket接続確認：成功 "
                f"配信出力={'稼働中' if output_active else '停止中'}"
            )
        run_ai_youtuber_loop(
            max_loops,
            runtime=runtime,
            stream_topic=stream_topic,
            stream_plan=stream_plan,
            obs_websocket_client=obs_websocket_client,
            news_history_repository=news_history_repository,
        )
    finally:
        local_services.stop()


def run_admin_service(max_loops):
    # ログイン中は管理画面だけを常駐させ、ボタン操作でライブ処理を起動します。
    client = create_aivis_client()
    speaker_id = get_configured_aivis_speaker_id()
    port = get_control_server_port()
    try:
        server = ExternalControlServer(
            aivis_client=client,
            speaker_id=speaker_id,
            port=port,
        )
        server.start()
    except OSError as exc:
        raise RuntimeError(
            f"常駐管理サーバーを起動できません。port={port} detail={exc}"
        ) from exc

    def start_live_callback(prepared_local_services=None):
        try:
            run_managed_live_session(
                server.runtime,
                max_loops,
                local_services=prepared_local_services,
            )
        except AdminRequestedBroadcastEnd as exc:
            print(str(exc))

    controller = LiveServiceController(
        server.runtime,
        start_live_callback,
        prepare_callback=prepare_managed_live_services,
    )
    server.runtime.attach_live_service_controller(controller)
    server.runtime.update_admin_status(
        available=True,
        phase="service_idle",
        message="管理画面は稼働中です。ライブ制御は停止しています。",
    )
    auto_scheduler = None
    if auto_schedule_enabled():
        auto_scheduler = BroadcastAutoScheduler.from_env(server.runtime)
        auto_scheduler.start()
        server.runtime.update_admin_status(auto_scheduler_running=True)
    else:
        server.runtime.update_admin_status(auto_scheduler_running=False)
    print(f"常駐配信管理画面: http://127.0.0.1:{port}/admin")
    print(
        "予定時刻の自動配信開始: "
        f"{'有効' if auto_scheduler is not None else '無効'}"
    )
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        print("\n常駐配信管理の終了操作を受け付けました。")
    finally:
        if auto_scheduler is not None:
            auto_scheduler.stop()
        if controller.is_running():
            try:
                controller.request_stop()
                controller.wait(timeout=5)
            except RuntimeError as exc:
                print(f"ライブ制御停止エラー: {exc}")
        server.stop()
        print("常駐配信管理画面を停止しました。")


def parse_args():
    parser = argparse.ArgumentParser(description="AI YouTuber Phase 1")
    parser.add_argument(
        "--mode",
        choices=[
            "openai-test",
            "obs-test",
            "youtube-upcoming",
            "youtube-chat-id",
            "youtube-messages",
            "youtube-loop",
            "ai-youtuber-once",
            "ai-youtuber-loop",
            "aivis-info",
            "external-control-server",
            "interactive-ai",
            "news-test",
            "news-voice",
            "ai-youtuber-live",
            "admin-service",
            "mock-live",
            "character-memory-drafts",
            "character-memory-approved",
            "character-memory-approve",
            "character-memory-reject",
            "x-draft",
            "x-post",
        ],
        default="openai-test",
        help="実行する確認処理を選びます。",
    )
    parser.add_argument(
        "--x-topic",
        default=None,
        help="x-draftまたはx-postモードで投稿案の話題を任意指定します。",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="x-postモードで対話確認を有効にします。",
    )
    parser.add_argument(
        "--max-loops",
        type=int,
        default=3,
        help="youtube-loopまたはai-youtuber-loopモードでコメント取得を繰り返す回数です。",
    )
    parser.add_argument(
        "--mock-delay-seconds",
        type=float,
        default=None,
        help="mock-liveモードで各発話の間に待つ秒数です。",
    )
    parser.add_argument(
        "--stream-topic",
        default=None,
        help=(
            "ai-youtuber-liveのメインテーマを手動指定します。"
            "省略した場合は配信開始時にAIが自動決定します。"
        ),
    )
    parser.add_argument(
        "--stream-plan",
        default=None,
        help=(
            "配信構成の希望を200文字以内で指定します。"
            "例：初見向けの自己紹介配信"
        ),
    )
    parser.add_argument(
        "--character-memory-id",
        default=None,
        help="character-memory-approveまたはcharacter-memory-rejectの対象IDです。",
    )
    return parser.parse_args()


def main():
    # .envの内容を環境変数として読み込みます。
    load_dotenv()
    args = parse_args()

    try:
        if args.mode == "openai-test":
            run_openai_test()
        elif args.mode == "obs-test":
            run_obs_websocket_test()
        elif args.mode == "youtube-upcoming":
            run_youtube_upcoming_test()
        elif args.mode == "x-draft":
            run_x_post_draft(args.x_topic)
        elif args.mode == "x-post":
            run_x_post(args.x_topic, confirm=args.confirm)
        elif args.mode == "youtube-chat-id":
            run_youtube_chat_id_test()
        elif args.mode == "youtube-messages":
            run_youtube_messages_test()
        elif args.mode == "youtube-loop":
            run_youtube_loop_test(args.max_loops)
        elif args.mode == "ai-youtuber-once":
            run_ai_youtuber_once()
        elif args.mode == "ai-youtuber-loop":
            run_ai_youtuber_loop(args.max_loops)
        elif args.mode == "aivis-info":
            run_aivis_info()
        elif args.mode == "external-control-server":
            run_external_control_server()
        elif args.mode == "interactive-ai":
            run_interactive_ai()
        elif args.mode == "news-test":
            run_news_test()
        elif args.mode == "news-voice":
            run_news_voice_test()
        elif args.mode == "ai-youtuber-live":
            run_ai_youtuber_live(
                args.max_loops,
                args.stream_topic,
                args.stream_plan,
            )
        elif args.mode == "admin-service":
            run_admin_service(args.max_loops)
        elif args.mode == "mock-live":
            run_mock_live(args.mock_delay_seconds)
        elif args.mode == "character-memory-drafts":
            run_character_memory_list("draft")
        elif args.mode == "character-memory-approved":
            run_character_memory_list("approved")
        elif args.mode == "character-memory-approve":
            run_character_memory_review(args.character_memory_id, "approved")
        elif args.mode == "character-memory-reject":
            run_character_memory_review(args.character_memory_id, "rejected")
    except (RuntimeError, ValueError) as exc:
        print(f"エラー: {exc}")
        return


if __name__ == "__main__":
    main()
