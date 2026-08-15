import io
import json
import math
import queue
import sys
import threading
import time
import uuid
import wave
from collections import OrderedDict, deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from character_memory import get_character_memory_repository


ALLOWED_EMOTIONS = {
    "neutral",
    "happy",
    "angry",
    "sad",
    "surprised",
    "relaxed",
    "thinking",
}

ALLOWED_MOTIONS = {
    "laugh",
    "pout",
    "teary",
    "show_body",
    "greeting",
    "peace_sign",
    "shoot",
    "spin",
    "model_pose",
    "squat",
}

ALLOWED_HEAD_MOTIONS = {
    "none",
    "nod",
    "tilt_left",
    "tilt_right",
}

ALLOWED_VIEW_ACTIONS = {
    "full_body",
    "upper_body",
    "turn_left",
    "turn_right",
    "reset",
}

ALLOWED_SPEECH_STYLES = {"slow", "normal", "fast"}

SUBTITLE_OVERLAY_PATH = Path(__file__).with_name("subtitle_overlay.html")
TOPIC_OVERLAY_PATH = Path(__file__).with_name("topic_overlay.html")
CHAT_OVERLAY_PATH = Path(__file__).with_name("chat_overlay.html")
ADMIN_PANEL_PATH = Path(__file__).with_name("admin_panel.html")
CHARACTER_MEMORY_PANEL_PATH = Path(__file__).with_name(
    "character_memory_panel.html"
)
ALLOWED_ADMIN_ACTIONS = {
    "direct_speech",
    "ai_instruction",
    "closing_greeting",
    "end_broadcast",
    "start_broadcast",
    "prepare_broadcast",
    "configure_broadcast",
    "pause_autonomous",
    "resume_autonomous",
    "cancel_next",
    "change_stream_plan",
}


def _remove_news_source_suffix(title, source_name):
    # Google News由来の「見出し - Yahoo!ニュース」のような媒体名重複を除きます。
    normalized_title = str(title or "").strip()
    normalized_source = str(source_name or "").strip()
    if not normalized_title or not normalized_source:
        return normalized_title
    for separator in (" - ", " – ", " — ", "｜", " | "):
        suffix = f"{separator}{normalized_source}"
        if normalized_title.endswith(suffix):
            return normalized_title[: -len(suffix)].rstrip()
    return normalized_title


@dataclass(frozen=True)
class PreparedSpeech:
    text: str
    emotion: str
    speech_style: str
    audio_data: bytes
    duration_ms: int


def get_wav_duration_ms(audio_data):
    try:
        with wave.open(io.BytesIO(audio_data), "rb") as wav_file:
            frame_rate = wav_file.getframerate()
            frame_count = wav_file.getnframes()
    except (EOFError, wave.Error) as exc:
        raise RuntimeError(
            "AivisSpeechのWAV音声から再生時間を取得できませんでした。"
        ) from exc

    if frame_rate <= 0 or frame_count <= 0:
        raise RuntimeError("AivisSpeechのWAV音声に有効な再生時間がありません。")
    return round(frame_count / frame_rate * 1000)


def normalize_motion_command(motion):
    if motion is None:
        return None
    if isinstance(motion, str):
        normalized_motion = motion.strip()
        if normalized_motion not in ALLOWED_MOTIONS:
            raise ValueError(f"未対応のmotionです。motion={normalized_motion}")
        return normalized_motion
    if not isinstance(motion, dict):
        raise ValueError("motionは文字列、JSONオブジェクト、nullのいずれかです。")

    extra_keys = set(motion) - {"name", "speed", "intensity", "head"}
    if extra_keys:
        raise ValueError(
            "motionに未対応の項目があります。"
            f"keys={','.join(sorted(extra_keys))}"
        )

    name = motion.get("name")
    if name is not None:
        if not isinstance(name, str) or name not in ALLOWED_MOTIONS:
            raise ValueError(f"未対応のmotion.nameです。name={name}")

    head = motion.get("head", "none")
    if not isinstance(head, str) or head not in ALLOWED_HEAD_MOTIONS:
        raise ValueError(f"未対応のmotion.headです。head={head}")

    speed = motion.get("speed", 1.0)
    intensity = motion.get("intensity", 1.0)
    if (
        isinstance(speed, bool)
        or not isinstance(speed, (int, float))
        or not math.isfinite(speed)
        or not 0.85 <= speed <= 1.15
    ):
        raise ValueError("motion.speedは0.85〜1.15の数値で指定してください。")
    if (
        isinstance(intensity, bool)
        or not isinstance(intensity, (int, float))
        or not math.isfinite(intensity)
        or not 0.55 <= intensity <= 1.0
    ):
        raise ValueError("motion.intensityは0.55〜1.0の数値で指定してください。")

    if name is None and head == "none":
        return None
    return {
        "name": name,
        "speed": float(speed),
        "intensity": float(intensity),
        "head": head,
    }


def normalize_view_action(view_action):
    if view_action is None:
        return None
    if not isinstance(view_action, str) or view_action not in ALLOWED_VIEW_ACTIONS:
        raise ValueError(f"未対応のview_actionです。view_action={view_action}")
    return view_action


class AudioStore:
    def __init__(self, max_items=32):
        self.max_items = max_items
        self._items = OrderedDict()
        self._lock = threading.Lock()

    def put(self, audio_data):
        audio_id = uuid.uuid4().hex
        with self._lock:
            self._items[audio_id] = audio_data
            while len(self._items) > self.max_items:
                self._items.popitem(last=False)
        return audio_id

    def get(self, audio_id):
        with self._lock:
            return self._items.get(audio_id)


class EventBroker:
    def __init__(self, retain_latest=False):
        self._subscribers = []
        self._lock = threading.Lock()
        self._retain_latest = retain_latest
        self._latest_command = None

    def subscribe(self):
        subscriber = queue.Queue(maxsize=16)
        with self._lock:
            self._subscribers.append(subscriber)
            if self._retain_latest and self._latest_command is not None:
                subscriber.put_nowait(self._latest_command)
        return subscriber

    def unsubscribe(self, subscriber):
        with self._lock:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)

    def publish(self, command):
        with self._lock:
            if self._retain_latest:
                self._latest_command = command
            # React開発モードなどで接続が重複しても、最新の画面だけへ配信します。
            subscriber = self._subscribers[-1] if self._subscribers else None

        if subscriber is None:
            return 0

        try:
            subscriber.put_nowait(command)
            return 1
        except queue.Full:
            # 遅れている接続には新しい命令を追加しません。
            return 0

    def subscriber_count(self):
        with self._lock:
            # 実際に命令を配信するクライアント数を返します。
            return min(len(self._subscribers), 1)


class ChatEventBroker:
    def __init__(self, history_size=20):
        self._subscribers = []
        self._history = deque(maxlen=history_size)
        self._lock = threading.Lock()

    def subscribe(self):
        subscriber = queue.Queue(maxsize=16)
        with self._lock:
            self._subscribers.append(subscriber)
            subscriber.put_nowait(
                {
                    "type": "chat_snapshot",
                    "messages": list(self._history),
                }
            )
        return subscriber

    def unsubscribe(self, subscriber):
        with self._lock:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)

    def publish(self, messages):
        normalized_messages = list(messages)
        if not normalized_messages:
            return 0
        command = {
            "type": "chat_messages",
            "messages": normalized_messages,
        }
        with self._lock:
            self._history.extend(normalized_messages)
            subscriber = self._subscribers[-1] if self._subscribers else None
        if subscriber is None:
            return 0
        try:
            subscriber.put_nowait(command)
            return 1
        except queue.Full:
            return 0

    def publish_reply_state(self, message_id, state, duration_ms=None):
        normalized_id = str(message_id or "").strip()
        if not normalized_id:
            raise ValueError("返信状態のmessage_idが空です。")
        if state not in {"thinking", "speaking", "clear"}:
            raise ValueError(f"未対応の返信状態です。state={state}")
        if duration_ms is not None and (
            isinstance(duration_ms, bool)
            or not isinstance(duration_ms, (int, float))
            or duration_ms < 0
        ):
            raise ValueError("返信状態のduration_msは0以上の数値にしてください。")

        with self._lock:
            target_message = None
            for message in self._history:
                if message.get("message_id") == normalized_id:
                    target_message = message
                    break
            if target_message is None:
                return 0
            if state == "clear":
                target_message.pop("reply_state", None)
                target_message.pop("reply_until_ms", None)
            else:
                target_message["reply_state"] = state
                target_message.pop("reply_until_ms", None)
                if state == "speaking" and duration_ms is not None:
                    target_message["reply_until_ms"] = round(
                        time.time() * 1000 + duration_ms
                    )
            command = {
                "type": "chat_reply_state",
                "message_id": normalized_id,
                "state": state,
                "message": dict(target_message),
            }
            if "reply_until_ms" in target_message:
                command["reply_until_ms"] = target_message["reply_until_ms"]
            subscriber = self._subscribers[-1] if self._subscribers else None

        if subscriber is None:
            return 0
        try:
            subscriber.put_nowait(command)
            return 1
        except queue.Full:
            return 0

    def subscriber_count(self):
        with self._lock:
            return min(len(self._subscribers), 1)


class ExternalControlRuntime:
    def __init__(
        self,
        aivis_client,
        speaker_id,
        public_base_url,
        character_memory_repository=None,
    ):
        self.aivis_client = aivis_client
        self.speaker_id = speaker_id
        self.public_base_url = public_base_url.rstrip("/")
        self.audio_store = AudioStore()
        self.event_broker = EventBroker()
        self.subtitle_event_broker = EventBroker(retain_latest=True)
        self.topic_event_broker = EventBroker(retain_latest=True)
        self.chat_event_broker = ChatEventBroker(history_size=20)
        self.admin_command_queue = queue.Queue(maxsize=32)
        self.character_memory_repository = character_memory_repository
        self._admin_status_lock = threading.Lock()
        self._prepared_broadcast_draft = None
        self._selected_broadcast_plan = None
        self._admin_status = {
            "available": False,
            "autonomous_paused": False,
            "phase": "starting",
            "message": "ライブ制御の開始を待っています。",
            "stream_theme": "",
            "stream_segment": "",
            "stream_segment_index": 0,
            "stream_segment_count": 0,
            "discarded_prefetches": 0,
            "updated_at_ms": round(time.time() * 1000),
        }

    def enqueue_admin_command(self, command):
        if not isinstance(command, dict):
            raise ValueError("管理命令はJSONオブジェクトで指定してください。")
        action = str(command.get("action", "")).strip()
        if action not in ALLOWED_ADMIN_ACTIONS:
            raise ValueError(f"未対応の管理命令です。action={action}")
        normalized = {"action": action, "id": uuid.uuid4().hex}
        if action in {"prepare_broadcast", "configure_broadcast"}:
            stream_plan = str(command.get("stream_plan", "")).strip()
            if len(stream_plan) > 200:
                raise ValueError("配信企画は200文字以内にしてください。")
            normalized["stream_plan"] = stream_plan
        if action == "configure_broadcast":
            title = str(command.get("title", "")).strip()
            description = str(command.get("description", "")).strip()
            privacy_status = str(
                command.get("privacy_status", "unlisted")
            ).strip()
            draft_id = str(command.get("draft_id", "")).strip()
            if not 1 <= len(title) <= 100:
                raise ValueError("配信タイトルは1〜100文字にしてください。")
            if len(description) > 5000:
                raise ValueError("配信説明は5000文字以内にしてください。")
            if privacy_status not in {"private", "unlisted", "public"}:
                raise ValueError(
                    "公開設定はprivate、unlisted、publicのいずれかです。"
                )
            if not stream_plan and not draft_id:
                raise ValueError(
                    "配信企画を入力するか、AIで配信案を作成してください。"
                )
            if len(draft_id) > 64:
                raise ValueError("配信案IDが長すぎます。")
            normalized.update(
                {
                    "title": title,
                    "description": description,
                    "privacy_status": privacy_status,
                    "stream_plan": stream_plan,
                    "draft_id": draft_id,
                }
            )
        if action in {"direct_speech", "ai_instruction", "change_stream_plan"}:
            text = str(command.get("text", "")).strip()
            maximum_length = {
                "ai_instruction": 500,
                "change_stream_plan": 200,
            }.get(action, 300)
            if not text:
                raise ValueError("管理画面から送る文章が空です。")
            if len(text) > maximum_length:
                raise ValueError(
                    f"文章は{maximum_length}文字以内にしてください。"
                )
            normalized["text"] = text
        if action == "direct_speech":
            emotion = str(command.get("emotion", "neutral")).strip()
            speech_style = str(
                command.get("speech_style", "normal")
            ).strip()
            motion = command.get("motion")
            if emotion not in ALLOWED_EMOTIONS:
                raise ValueError(f"未対応のemotionです。emotion={emotion}")
            if speech_style not in ALLOWED_SPEECH_STYLES:
                raise ValueError(
                    "未対応のspeech_styleです。"
                    f"speech_style={speech_style}"
                )
            if isinstance(motion, str) and motion in {"", "none"}:
                motion = None
            normalized["emotion"] = emotion
            normalized["speech_style"] = speech_style
            normalized["motion"] = normalize_motion_command(motion)
        try:
            self.admin_command_queue.put_nowait(normalized)
        except queue.Full as exc:
            raise RuntimeError(
                "管理命令が混み合っています。処理完了後に再度送信してください。"
            ) from exc
        return normalized

    def get_next_admin_command(self, timeout_seconds=0):
        timeout_seconds = float(timeout_seconds)
        if timeout_seconds < 0:
            raise ValueError("管理命令の待機時間は0以上にしてください。")
        try:
            if timeout_seconds > 0:
                return self.admin_command_queue.get(timeout=timeout_seconds)
            return self.admin_command_queue.get_nowait()
        except queue.Empty:
            return None

    def update_admin_status(self, **changes):
        with self._admin_status_lock:
            self._admin_status.update(changes)
            self._admin_status["updated_at_ms"] = round(time.time() * 1000)

    def store_prepared_broadcast_draft(self, plan, instruction, draft_id):
        normalized_draft_id = str(draft_id).strip()
        if not normalized_draft_id:
            raise ValueError("生成した配信案のIDが空です。")
        segment_titles = [segment.title for segment in plan.segments]
        news_description = {
            "off": "使用しない",
            "related": f"関連ニュースのみ（検索: {plan.news_query}）",
            "general": "幅広いニュースを使用",
        }[plan.news_policy]
        public_draft = {
            "id": normalized_draft_id,
            "title": plan.youtube_title,
            "description": plan.youtube_description,
            "theme": plan.theme,
            "segments": segment_titles,
            "news_policy": plan.news_policy,
            "news_description": news_description,
        }
        with self._admin_status_lock:
            self._prepared_broadcast_draft = {
                "id": normalized_draft_id,
                "instruction": str(instruction or "").strip(),
                "plan": plan,
            }
            self._admin_status["broadcast_draft"] = public_draft
            self._admin_status["updated_at_ms"] = round(time.time() * 1000)
        return public_draft

    def select_prepared_broadcast_plan(self, draft_id):
        normalized_draft_id = str(draft_id or "").strip()
        with self._admin_status_lock:
            draft = self._prepared_broadcast_draft
            if draft is None or draft["id"] != normalized_draft_id:
                raise RuntimeError(
                    "AI配信案が見つからないか更新されています。"
                    "「AIで配信案を作成」をもう一度押してください。"
                )
            self._selected_broadcast_plan = draft
            return draft["instruction"]

    def clear_selected_broadcast_plan(self):
        with self._admin_status_lock:
            self._selected_broadcast_plan = None

    def consume_selected_broadcast_plan(self):
        with self._admin_status_lock:
            selected = self._selected_broadcast_plan
            self._selected_broadcast_plan = None
        return selected

    def get_admin_status(self):
        with self._admin_status_lock:
            speaking_until_ms = self._admin_status.get("speaking_until_ms")
            if (
                self._admin_status.get("phase") == "speaking"
                and isinstance(speaking_until_ms, (int, float))
                and speaking_until_ms <= time.time() * 1000
            ):
                self._admin_status["phase"] = (
                    "paused"
                    if self._admin_status.get("autonomous_paused")
                    else "waiting"
                )
                self._admin_status["message"] = (
                    "自発発話は一時停止中です。"
                    if self._admin_status.get("autonomous_paused")
                    else "コメントまたは次の発話を待っています。"
                )
                self._admin_status.pop("speaking_until_ms", None)
                self._admin_status["updated_at_ms"] = round(
                    time.time() * 1000
                )
            status = dict(self._admin_status)
        status["queued_commands"] = self.admin_command_queue.qsize()
        return status

    def get_obs_overlay_status(self):
        subtitle_clients = self.subtitle_event_broker.subscriber_count()
        topic_clients = self.topic_event_broker.subscriber_count()
        chat_clients = self.chat_event_broker.subscriber_count()
        return {
            "subtitle_connected": subtitle_clients > 0,
            "topic_connected": topic_clients > 0,
            "chat_connected": chat_clients > 0,
            "subtitle_clients": subtitle_clients,
            "topic_clients": topic_clients,
            "chat_clients": chat_clients,
            "ready": (
                subtitle_clients > 0
                and topic_clients > 0
                and chat_clients > 0
            ),
        }

    def wait_for_obs_overlays(self, timeout_seconds, poll_seconds=0.25):
        # OBSの字幕・コメントが両方SSE接続するまで、開始挨拶を保留します。
        timeout_seconds = float(timeout_seconds)
        poll_seconds = float(poll_seconds)
        if timeout_seconds < 0:
            raise ValueError("OBS接続待機時間は0以上にしてください。")
        if poll_seconds <= 0:
            raise ValueError("OBS接続確認間隔は0より大きくしてください。")
        if timeout_seconds == 0:
            return True
        deadline = time.monotonic() + timeout_seconds
        while True:
            if self.get_obs_overlay_status()["ready"]:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(min(poll_seconds, max(deadline - time.monotonic(), 0)))

    def list_character_memories(self, status):
        if self.character_memory_repository is None:
            raise RuntimeError("キャラクター記憶の保存先が設定されていません。")
        return self.character_memory_repository.list(status)

    def review_character_memory(self, memory_id, status):
        if self.character_memory_repository is None:
            raise RuntimeError("キャラクター記憶の保存先が設定されていません。")
        self.character_memory_repository.review(memory_id, status)

    def publish_chat_messages(self, messages):
        # 表示に必要な公開情報だけをチャット画面へ送り、Channel IDは渡しません。
        normalized_messages = []
        for message in messages:
            if not isinstance(message, dict):
                raise ValueError("チャットメッセージはJSONオブジェクトで指定してください。")
            message_id = str(message.get("message_id", "")).strip()
            user_name = str(message.get("user_name", "")).strip()
            comment = str(message.get("comment", "")).strip()
            if not message_id:
                raise ValueError("チャットメッセージのmessage_idが空です。")
            if not user_name:
                user_name = "unknown"
            if not comment:
                continue
            normalized_messages.append(
                {
                    "message_id": message_id,
                    "user_name": user_name[:100],
                    "comment": comment[:500],
                    "published_at": str(
                        message.get("published_at", "")
                    ).strip(),
                }
            )
        return self.chat_event_broker.publish(normalized_messages)

    def publish_chat_reply_state(self, message_id, state, duration_ms=None):
        return self.chat_event_broker.publish_reply_state(
            message_id,
            state,
            duration_ms,
        )

    def publish_topic_card(self, card):
        # 記事由来のHTMLは渡さず、OBS側でtextContentとして表示する公開情報だけに絞ります。
        if not isinstance(card, dict):
            raise ValueError("話題カードはJSONオブジェクトで指定してください。")
        kind = str(card.get("kind", "")).strip()
        if kind not in {"news", "talk"}:
            raise ValueError(f"未対応の話題カード種別です。kind={kind}")
        source_name = " ".join(
            str(card.get("source_name", "")).split()
        )[:100]
        title = _remove_news_source_suffix(
            " ".join(str(card.get("title", "")).split()),
            source_name,
        )[:120]
        summary = " ".join(str(card.get("summary", "")).split())[:180]
        if not title:
            raise ValueError("話題カードのタイトルが空です。")
        command = {
            "type": "topic_card",
            "id": uuid.uuid4().hex,
            "kind": kind,
            "title": title,
            "summary": summary,
        }
        if kind == "news":
            command["source_name"] = source_name
            command["published_at"] = " ".join(
                str(card.get("published_at", "")).split()
            )[:100]
            command["information_status"] = str(
                card.get("information_status", "single_report")
            ).strip()
        return self.topic_event_broker.publish(command)

    def clear_topic_card(self):
        return self.topic_event_broker.publish(
            {"type": "clear_topic_card", "id": uuid.uuid4().hex}
        )

    def prepare_speech(self, text, emotion="neutral", speech_style="normal"):
        # 再生中に次の音声を先読みできるよう、合成と配信を分離します。
        normalized_text = text.strip()
        if not normalized_text:
            raise ValueError("発話する文章が空です。")
        if emotion not in ALLOWED_EMOTIONS:
            raise ValueError(f"未対応のemotionです。emotion={emotion}")
        if (
            not isinstance(speech_style, str)
            or speech_style not in ALLOWED_SPEECH_STYLES
        ):
            raise ValueError(
                "未対応のspeech_styleです。"
                f"speech_style={speech_style}"
            )

        audio_data = self.aivis_client.synthesize(
            normalized_text,
            self.speaker_id,
            emotion,
            speech_style,
        )
        duration_ms = get_wav_duration_ms(audio_data)
        return PreparedSpeech(
            text=normalized_text,
            emotion=emotion,
            speech_style=speech_style,
            audio_data=audio_data,
            duration_ms=duration_ms,
        )

    def publish_prepared_speech(
        self,
        prepared_speech,
        motion=None,
        view_action=None,
    ):
        if not isinstance(prepared_speech, PreparedSpeech):
            raise ValueError("prepared_speechが正しい形式ではありません。")
        normalized_motion = normalize_motion_command(motion)
        normalized_view_action = normalize_view_action(view_action)
        audio_id = self.audio_store.put(prepared_speech.audio_data)
        command = {
            "type": "speak",
            "id": uuid.uuid4().hex,
            "text": prepared_speech.text,
            "emotion": prepared_speech.emotion,
            "audio_url": f"{self.public_base_url}/audio/{audio_id}.wav",
            "duration_ms": prepared_speech.duration_ms,
            "interrupt": True,
        }
        if normalized_motion is not None:
            command["motion"] = normalized_motion
        if normalized_view_action is not None:
            command["view_action"] = normalized_view_action
        delivered_count = self.event_broker.publish(command)
        self.subtitle_event_broker.publish(
            {
                "type": "subtitle",
                "id": command["id"],
                "text": prepared_speech.text,
                "emotion": prepared_speech.emotion,
                "duration_ms": prepared_speech.duration_ms,
            }
        )
        return command, delivered_count

    def speak(
        self,
        text,
        emotion="neutral",
        motion=None,
        view_action=None,
        speech_style="normal",
    ):
        prepared_speech = self.prepare_speech(text, emotion, speech_style)
        return self.publish_prepared_speech(
            prepared_speech,
            motion,
            view_action,
        )

    def move(self, motion):
        normalized_motion = normalize_motion_command(motion)
        if normalized_motion is None:
            raise ValueError("再生するmotionが指定されていません。")
        command = {
            "type": "speak",
            "id": uuid.uuid4().hex,
            "motion": normalized_motion,
            "interrupt": True,
        }
        delivered_count = self.event_broker.publish(command)
        return command, delivered_count

    def reset(self):
        command = {"type": "reset", "id": uuid.uuid4().hex}
        delivered_count = self.event_broker.publish(command)
        self.subtitle_event_broker.publish(
            {"type": "clear", "id": command["id"]}
        )
        self.clear_topic_card()
        return command, delivered_count


class ExternalControlHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address, runtime):
        super().__init__(server_address, ExternalControlRequestHandler)
        self.runtime = runtime

    def handle_error(self, request, client_address):
        error = sys.exc_info()[1]
        if isinstance(error, (BrokenPipeError, ConnectionResetError)):
            print(
                "HTTPクライアントが接続を終了しました。"
                f"address={client_address[0]}:{client_address[1]}"
            )
            return
        super().handle_error(request, client_address)


class ExternalControlRequestHandler(BaseHTTPRequestHandler):
    server_version = "AIYoutuberControl/1.0"

    @property
    def runtime(self):
        return self.server.runtime

    def do_GET(self):
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        if path == "/events":
            self._serve_events(self.runtime.event_broker)
            return
        if path == "/subtitle-events":
            self._serve_events(self.runtime.subtitle_event_broker)
            return
        if path == "/topic-events":
            self._serve_events(self.runtime.topic_event_broker)
            return
        if path == "/chat-events":
            self._serve_events(self.runtime.chat_event_broker)
            return
        if path in {"/overlay", "/overlay/"}:
            self._serve_subtitle_overlay()
            return
        if path in {"/topic-overlay", "/topic-overlay/"}:
            self._serve_topic_overlay()
            return
        if path in {"/chat-overlay", "/chat-overlay/"}:
            self._serve_chat_overlay()
            return
        if path in {"/admin", "/admin/"}:
            self._serve_admin_panel()
            return
        if path in {"/character-memories", "/character-memories/"}:
            self._serve_character_memory_panel()
            return
        if path == "/api/admin/status":
            self._send_json(200, self.runtime.get_admin_status())
            return
        if path == "/api/character-memories":
            self._handle_character_memory_list(parsed_url.query)
            return
        if path == "/health":
            overlay_status = self.runtime.get_obs_overlay_status()
            self._send_json(
                200,
                {
                    "status": "ok",
                    "sse_clients": self.runtime.event_broker.subscriber_count(),
                    "subtitle_sse_clients": (
                        self.runtime.subtitle_event_broker.subscriber_count()
                    ),
                    "topic_sse_clients": (
                        self.runtime.topic_event_broker.subscriber_count()
                    ),
                    "chat_sse_clients": (
                        self.runtime.chat_event_broker.subscriber_count()
                    ),
                    "obs_overlays_ready": overlay_status["ready"],
                },
            )
            return
        if path.startswith("/audio/") and path.endswith(".wav"):
            self._serve_audio(path)
            return
        self._send_json(404, {"error": "指定されたエンドポイントはありません。"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/speak":
            self._handle_speak()
            return
        if path == "/api/motion":
            self._handle_motion()
            return
        if path == "/api/reset":
            command, delivered_count = self.runtime.reset()
            self._send_json(
                200,
                {"command": command, "delivered_clients": delivered_count},
            )
            return
        if path == "/api/admin/command":
            self._handle_admin_command()
            return
        if path == "/api/character-memories/review":
            self._handle_character_memory_review()
            return
        self._send_json(404, {"error": "指定されたエンドポイントはありません。"})

    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _serve_events(self, event_broker):
        subscriber = event_broker.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        # 字幕・コメントなどの読み取り専用SSEは、OBSのローカルHTMLからも許可します。
        # OBSのChromiumはfile://のOrigin表現が環境により異なるため、ワイルドカードを使います。
        self._send_cors_headers(allow_any_origin=True)
        self.end_headers()

        try:
            while True:
                try:
                    command = subscriber.get(timeout=15)
                    payload = json.dumps(command, ensure_ascii=False)
                    data = f"data: {payload}\n\n".encode("utf-8")
                except queue.Empty:
                    data = b": keep-alive\n\n"
                self.wfile.write(data)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return
        finally:
            event_broker.unsubscribe(subscriber)

    def _serve_subtitle_overlay(self):
        try:
            payload = SUBTITLE_OVERLAY_PATH.read_bytes()
        except OSError as exc:
            print(f"字幕オーバーレイを読み込めませんでした: {exc}")
            self._send_json(
                500,
                {"error": "字幕オーバーレイのHTMLを読み込めませんでした。"},
            )
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _serve_topic_overlay(self):
        try:
            payload = TOPIC_OVERLAY_PATH.read_bytes()
        except OSError as exc:
            print(f"話題カードオーバーレイを読み込めませんでした: {exc}")
            self._send_json(
                500,
                {"error": "話題カードオーバーレイのHTMLを読み込めませんでした。"},
            )
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _serve_chat_overlay(self):
        try:
            payload = CHAT_OVERLAY_PATH.read_bytes()
        except OSError as exc:
            print(f"チャットオーバーレイを読み込めませんでした: {exc}")
            self._send_json(
                500,
                {"error": "チャットオーバーレイのHTMLを読み込めませんでした。"},
            )
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _serve_admin_panel(self):
        try:
            payload = ADMIN_PANEL_PATH.read_bytes()
        except OSError as exc:
            print(f"配信管理画面を読み込めませんでした: {exc}")
            self._send_json(
                500,
                {"error": "配信管理画面のHTMLを読み込めませんでした。"},
            )
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _serve_character_memory_panel(self):
        try:
            payload = CHARACTER_MEMORY_PANEL_PATH.read_bytes()
        except OSError as exc:
            print(f"キャラクター記憶管理画面を読み込めませんでした: {exc}")
            self._send_json(
                500,
                {"error": "キャラクター記憶管理画面のHTMLを読み込めませんでした。"},
            )
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _serve_audio(self, path):
        filename = path.rsplit("/", 1)[-1]
        audio_id = filename[:-4]
        if not audio_id or not audio_id.isalnum():
            self._send_json(400, {"error": "音声IDが不正です。"})
            return

        audio_data = self.runtime.audio_store.get(audio_id)
        if audio_data is None:
            self._send_json(404, {"error": "音声データが見つかりません。"})
            return

        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(audio_data)))
        self.send_header("Cache-Control", "no-store")
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(audio_data)

    def _handle_speak(self):
        body = self._read_json_object()
        if body is None:
            return

        text = body.get("text", "")
        emotion = body.get("emotion", "neutral")
        motion = body.get("motion")
        view_action = body.get("view_action")
        speech_style = body.get("speech_style", "normal")
        if not isinstance(text, str) or not isinstance(emotion, str):
            self._send_json(
                400,
                {"error": "textとemotionは文字列で指定してください。"},
            )
            return

        try:
            command, delivered_count = self.runtime.speak(
                text,
                emotion,
                motion,
                view_action,
                speech_style,
            )
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        except RuntimeError as exc:
            self._send_json(502, {"error": str(exc)})
            return

        self._send_json(
            200,
            {"command": command, "delivered_clients": delivered_count},
        )

    def _handle_motion(self):
        body = self._read_json_object()
        if body is None:
            return
        motion = body.get("motion")
        if motion is None:
            self._send_json(400, {"error": "motionを指定してください。"})
            return
        try:
            command, delivered_count = self.runtime.move(motion)
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        self._send_json(
            200,
            {"command": command, "delivered_clients": delivered_count},
        )

    def _handle_admin_command(self):
        body = self._read_json_object()
        if body is None:
            return
        try:
            command = self.runtime.enqueue_admin_command(body)
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        except RuntimeError as exc:
            self._send_json(503, {"error": str(exc)})
            return
        self._send_json(
            202,
            {
                "status": "accepted",
                "command_id": command["id"],
                "action": command["action"],
            },
        )

    def _handle_character_memory_list(self, query):
        status = parse_qs(query).get("status", ["draft"])[0]
        if status not in {"draft", "approved", "rejected"}:
            self._send_json(400, {"error": "statusが不正です。"})
            return
        try:
            memories = self.runtime.list_character_memories(status)
        except (RuntimeError, ValueError) as exc:
            self._send_json(500, {"error": str(exc)})
            return
        self._send_json(200, {"status": status, "memories": memories})

    def _handle_character_memory_review(self):
        body = self._read_json_object()
        if body is None:
            return
        memory_id = body.get("memory_id")
        status = body.get("status")
        if not isinstance(memory_id, str) or not isinstance(status, str):
            self._send_json(
                400,
                {"error": "memory_idとstatusは文字列で指定してください。"},
            )
            return
        if status not in {"approved", "rejected"}:
            self._send_json(
                400,
                {"error": "statusはapprovedまたはrejectedにしてください。"},
            )
            return
        try:
            self.runtime.review_character_memory(memory_id, status)
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        except RuntimeError as exc:
            self._send_json(409, {"error": str(exc)})
            return
        self._send_json(
            200,
            {"memory_id": memory_id, "status": status},
        )

    def _read_json_object(self):
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "Content-Lengthが不正です。"})
            return None

        if content_length <= 0 or content_length > 64 * 1024:
            self._send_json(400, {"error": "リクエスト本文のサイズが不正です。"})
            return None

        try:
            body = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(
                400,
                {"error": "リクエスト本文をJSONとして読み取れません。"},
            )
            return None

        if not isinstance(body, dict):
            self._send_json(
                400,
                {"error": "リクエスト本文はJSONオブジェクトで指定してください。"},
            )
            return None
        return body

    def _send_json(self, status_code, body):
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(payload)

    def _send_cors_headers(self, allow_any_origin=False):
        if allow_any_origin:
            self.send_header("Access-Control-Allow-Origin", "*")
            return
        origin = self.headers.get("Origin", "")
        if is_allowed_local_origin(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def log_message(self, format_string, *args):
        print(f"HTTP: {format_string % args}")


def is_allowed_local_origin(origin):
    if not origin:
        return False
    try:
        parsed = urlparse(origin)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and parsed.hostname in {
        "127.0.0.1",
        "localhost",
    }


class ExternalControlServer:
    def __init__(
        self,
        aivis_client,
        speaker_id,
        host="127.0.0.1",
        port=8765,
        character_memory_repository=None,
    ):
        public_base_url = f"http://127.0.0.1:{port}"
        self.runtime = ExternalControlRuntime(
            aivis_client=aivis_client,
            speaker_id=speaker_id,
            public_base_url=public_base_url,
            character_memory_repository=(
                character_memory_repository
                or get_character_memory_repository()
            ),
        )
        self.http_server = ExternalControlHttpServer((host, port), self.runtime)
        self._thread = None

    def start(self):
        if self._thread is not None:
            raise RuntimeError("外部制御サーバーはすでに起動しています。")
        self._thread = threading.Thread(
            target=self.http_server.serve_forever,
            name="external-control-server",
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        if self._thread is None:
            return
        self.http_server.shutdown()
        self.http_server.server_close()
        self._thread.join(timeout=5)
        self._thread = None
