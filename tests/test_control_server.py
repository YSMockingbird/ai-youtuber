import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from broadcast_schedule import BroadcastScheduleRepository
from character_memory import CharacterMemoryRepository
from control_server import (
    AudioStore,
    ExternalControlHttpServer,
    ExternalControlRuntime,
    SUBTITLE_OVERLAY_PATH,
    TOPIC_OVERLAY_PATH,
    get_wav_duration_ms,
    normalize_motion_command,
)
from stream_theme import StreamSegmentPlan, StreamThemePlan


def create_test_wav(duration_ms=1500, frame_rate=8000):
    frame_count = round(duration_ms / 1000 * frame_rate)
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(frame_rate)
        wav_file.writeframes(b"\x00\x00" * frame_count)
    return output.getvalue()


class FakeAivisSpeechClient:
    def __init__(self):
        self.last_emotion = None
        self.last_speech_style = None

    def synthesize(
        self,
        text,
        speaker_id,
        emotion="neutral",
        speech_style="normal",
    ):
        if not text or speaker_id != 101:
            raise AssertionError("テスト用AivisSpeech呼び出しの引数が不正です。")
        self.last_emotion = emotion
        self.last_speech_style = speech_style
        return create_test_wav()


class AudioStoreTest(unittest.TestCase):
    def test_old_audio_is_removed(self):
        store = AudioStore(max_items=1)
        old_audio_id = store.put(b"old")
        new_audio_id = store.put(b"new")

        self.assertIsNone(store.get(old_audio_id))
        self.assertEqual(store.get(new_audio_id), b"new")


class WavDurationTest(unittest.TestCase):
    def test_gets_duration_from_wav(self):
        self.assertEqual(get_wav_duration_ms(create_test_wav(2345)), 2345)

    def test_rejects_invalid_wav(self):
        with self.assertRaisesRegex(RuntimeError, "再生時間を取得できません"):
            get_wav_duration_ms(b"not-wav")


class ExternalControlRuntimeTest(unittest.TestCase):
    def create_schedule_runtime(self):
        temporary_directory = tempfile.TemporaryDirectory()
        repository = BroadcastScheduleRepository(
            Path(temporary_directory.name) / "broadcast_schedule.db"
        )
        runtime = ExternalControlRuntime(
            aivis_client=FakeAivisSpeechClient(),
            speaker_id=101,
            public_base_url="http://127.0.0.1:8765",
            broadcast_schedule_repository=repository,
        )
        return temporary_directory, repository, runtime

    def create_stream_plan(self):
        return StreamThemePlan(
            theme="自己紹介配信",
            core_question="ガン奈はどんなAIなのか",
            opening_angle="まず仕組みから紹介する",
            opening_greeting="こんばんは。今日はわたしがどんなAIなのか話します。",
            segments=[
                StreamSegmentPlan(
                    title=f"話題{index}",
                    talking_points=["具体例A", "具体例B"],
                    tangent_ideas=["関連する話"],
                    target_utterances=3,
                )
                for index in range(1, 4)
            ],
            closing_direction="今後やりたい配信を話す",
            news_policy="off",
            news_query=None,
        )

    def test_obs_overlay_status_requires_subtitle_topic_and_chat(self):
        runtime = ExternalControlRuntime(
            aivis_client=FakeAivisSpeechClient(),
            speaker_id=101,
            public_base_url="http://127.0.0.1:8765",
        )

        self.assertFalse(runtime.get_obs_overlay_status()["ready"])
        runtime.subtitle_event_broker.subscribe()
        self.assertFalse(runtime.get_obs_overlay_status()["ready"])
        runtime.topic_event_broker.subscribe()
        self.assertFalse(runtime.get_obs_overlay_status()["ready"])
        runtime.chat_event_broker.subscribe()

        status = runtime.get_obs_overlay_status()
        self.assertTrue(status["ready"])
        self.assertTrue(status["topic_connected"])
        self.assertTrue(runtime.wait_for_obs_overlays(0.1))

    def test_topic_card_is_sanitized_and_replayed_after_reconnect(self):
        runtime = ExternalControlRuntime(
            aivis_client=FakeAivisSpeechClient(),
            speaker_id=101,
            public_base_url="http://127.0.0.1:8765",
        )

        runtime.publish_topic_card(
            {
                "kind": "news",
                "title": "  VTuber事務所が   新企画を発表 - テストニュース  ",
                "summary": "新企画の内容と開始時期が公開された。",
                "source_name": "テストニュース",
                "published_at": "2026-08-13",
                "information_status": "official_basis",
            }
        )
        subscriber = runtime.topic_event_broker.subscribe()
        command = subscriber.get_nowait()

        self.assertEqual(command["type"], "topic_card")
        self.assertEqual(command["title"], "VTuber事務所が 新企画を発表")
        self.assertEqual(command["summary"], "新企画の内容と開始時期が公開された。")
        self.assertEqual(command["source_name"], "テストニュース")

    def test_subtitle_and_topic_overlays_are_separate_files(self):
        subtitle_html = SUBTITLE_OVERLAY_PATH.read_text(encoding="utf-8")
        topic_html = TOPIC_OVERLAY_PATH.read_text(encoding="utf-8")

        self.assertNotIn("topic-events", subtitle_html)
        self.assertNotIn("topicCard", subtitle_html)
        self.assertIn("width: min(88%, 900px)", subtitle_html)
        self.assertIn("const MAX_SEGMENT_LENGTH = 60", subtitle_html)
        self.assertIn("topic-events", topic_html)
        self.assertIn("topicCard", topic_html)
        self.assertNotIn("現在のトークテーマ", topic_html)
        self.assertIn("width: min(100%, 520px)", topic_html)
        self.assertIn("max-aspect-ratio: 4 / 3", topic_html)

    def test_obs_overlay_wait_can_be_disabled_for_non_obs_use(self):
        runtime = ExternalControlRuntime(
            aivis_client=FakeAivisSpeechClient(),
            speaker_id=101,
            public_base_url="http://127.0.0.1:8765",
        )

        self.assertTrue(runtime.wait_for_obs_overlays(0))

    def test_character_memory_can_be_listed_and_reviewed(self):
        repository = Mock()
        repository.list.return_value = [{"memory_id": "memory-1"}]
        runtime = ExternalControlRuntime(
            aivis_client=FakeAivisSpeechClient(),
            speaker_id=101,
            public_base_url="http://127.0.0.1:8765",
            character_memory_repository=repository,
        )

        self.assertEqual(
            runtime.list_character_memories("draft"),
            [{"memory_id": "memory-1"}],
        )
        runtime.review_character_memory("memory-1", "approved")

        repository.list.assert_called_once_with("draft")
        repository.review.assert_called_once_with("memory-1", "approved")

    def test_admin_command_is_validated_and_queued(self):
        runtime = ExternalControlRuntime(
            aivis_client=FakeAivisSpeechClient(),
            speaker_id=101,
            public_base_url="http://127.0.0.1:8765",
        )

        accepted = runtime.enqueue_admin_command(
            {
                "action": "direct_speech",
                "text": "少し待ってね。",
                "emotion": "relaxed",
                "speech_style": "normal",
                "motion": "greeting",
            }
        )

        self.assertEqual(accepted["action"], "direct_speech")
        self.assertEqual(runtime.get_next_admin_command(), accepted)

    def test_unknown_admin_command_is_rejected(self):
        runtime = ExternalControlRuntime(
            aivis_client=FakeAivisSpeechClient(),
            speaker_id=101,
            public_base_url="http://127.0.0.1:8765",
        )

        with self.assertRaisesRegex(ValueError, "未対応の管理命令"):
            runtime.enqueue_admin_command({"action": "stop_youtube"})

    def test_end_broadcast_command_is_queued(self):
        runtime = ExternalControlRuntime(
            aivis_client=FakeAivisSpeechClient(),
            speaker_id=101,
            public_base_url="http://127.0.0.1:8765",
        )

        accepted = runtime.enqueue_admin_command(
            {"action": "end_broadcast"}
        )

        self.assertEqual(accepted["action"], "end_broadcast")

    def test_start_broadcast_command_is_queued(self):
        runtime = ExternalControlRuntime(
            aivis_client=FakeAivisSpeechClient(),
            speaker_id=101,
            public_base_url="http://127.0.0.1:8765",
        )

        accepted = runtime.enqueue_admin_command(
            {"action": "start_broadcast"}
        )

        self.assertEqual(accepted["action"], "start_broadcast")

    def test_configure_broadcast_command_is_validated_and_queued(self):
        runtime = ExternalControlRuntime(
            aivis_client=FakeAivisSpeechClient(),
            speaker_id=101,
            public_base_url="http://127.0.0.1:8765",
        )

        accepted = runtime.enqueue_admin_command(
            {
                "action": "configure_broadcast",
                "title": "自己紹介ライブ",
                "description": "AI VTuberのテスト配信です。",
                "privacy_status": "unlisted",
                "stream_plan": "初見向けの自己紹介配信",
            }
        )

        self.assertEqual(accepted["title"], "自己紹介ライブ")
        self.assertEqual(accepted["privacy_status"], "unlisted")

    def test_prepare_broadcast_allows_empty_instruction(self):
        runtime = ExternalControlRuntime(
            aivis_client=FakeAivisSpeechClient(),
            speaker_id=101,
            public_base_url="http://127.0.0.1:8765",
        )

        accepted = runtime.enqueue_admin_command(
            {"action": "prepare_broadcast", "stream_plan": ""}
        )

        self.assertEqual(accepted["action"], "prepare_broadcast")
        self.assertEqual(accepted["stream_plan"], "")

    def test_schedule_command_uses_values_saved_in_sqlite(self):
        temporary_directory, repository, runtime = self.create_schedule_runtime()
        self.addCleanup(temporary_directory.cleanup)
        schedule = repository.create_schedule(
            scheduled_start_at="2026-08-20T21:00:00+09:00",
            planning_mode="manual",
            content_request="初見向けの自己紹介配信",
            title="保存済みタイトル",
            description="保存済み説明",
            privacy_status="unlisted",
        )

        prepare = runtime.enqueue_admin_command(
            {"action": "prepare_broadcast", "schedule_id": schedule["schedule_id"]}
        )
        self.assertEqual(prepare["stream_plan"], "初見向けの自己紹介配信")

        with self.assertRaisesRegex(ValueError, "まだ準備されていません"):
            runtime.enqueue_admin_command(
                {
                    "action": "configure_broadcast",
                    "schedule_id": schedule["schedule_id"],
                    "title": "ブラウザからの別タイトル",
                }
            )

        runtime.store_prepared_broadcast_draft(
            self.create_stream_plan(),
            schedule["content_request"],
            "draft-schedule",
            schedule_id=schedule["schedule_id"],
        )
        configure = runtime.enqueue_admin_command(
            {
                "action": "configure_broadcast",
                "schedule_id": schedule["schedule_id"],
                "title": "ブラウザからの別タイトル",
            }
        )

        self.assertEqual(configure["title"], "保存済みタイトル")
        self.assertEqual(configure["description"], "保存済み説明")

    def test_prepared_schedule_plan_can_be_restored_from_sqlite(self):
        temporary_directory, repository, runtime = self.create_schedule_runtime()
        self.addCleanup(temporary_directory.cleanup)
        schedule = repository.create_schedule(
            scheduled_start_at="2026-08-20T21:00:00+09:00",
            planning_mode="ai",
            content_request="",
            title="雑談配信",
            description="説明",
        )
        plan = self.create_stream_plan()
        runtime.store_prepared_broadcast_draft(
            plan,
            "",
            "draft-schedule",
            schedule_id=schedule["schedule_id"],
        )
        restarted_runtime = ExternalControlRuntime(
            aivis_client=FakeAivisSpeechClient(),
            speaker_id=101,
            public_base_url="http://127.0.0.1:8765",
            broadcast_schedule_repository=repository,
        )

        instruction = restarted_runtime.select_prepared_broadcast_schedule(
            schedule["schedule_id"]
        )
        selected = restarted_runtime.consume_selected_broadcast_plan()

        self.assertIsNone(instruction)
        self.assertEqual(selected["plan"].theme, plan.theme)
        self.assertEqual(
            repository.get_schedule(schedule["schedule_id"])["status"],
            "prepared",
        )

    def test_prepared_broadcast_plan_is_selected_by_draft_id(self):
        runtime = ExternalControlRuntime(
            aivis_client=FakeAivisSpeechClient(),
            speaker_id=101,
            public_base_url="http://127.0.0.1:8765",
        )
        plan = SimpleNamespace(
            theme="自己紹介配信",
            segments=[SimpleNamespace(title="どんなAIか")],
            news_policy="off",
            news_query=None,
        )

        public_draft = runtime.store_prepared_broadcast_draft(
            plan,
            "初見向けの自己紹介配信",
            "draft-1",
        )
        instruction = runtime.select_prepared_broadcast_plan("draft-1")
        selected = runtime.consume_selected_broadcast_plan()

        self.assertNotIn("title", public_draft)
        self.assertNotIn("description", public_draft)
        self.assertEqual(
            runtime.get_admin_status()["broadcast_draft"],
            public_draft,
        )
        self.assertEqual(instruction, "初見向けの自己紹介配信")
        self.assertIs(selected["plan"], plan)
        self.assertIsNone(runtime.consume_selected_broadcast_plan())

    def test_outdated_broadcast_draft_is_rejected(self):
        runtime = ExternalControlRuntime(
            aivis_client=FakeAivisSpeechClient(),
            speaker_id=101,
            public_base_url="http://127.0.0.1:8765",
        )

        with self.assertRaisesRegex(RuntimeError, "AI配信構成が見つからない"):
            runtime.select_prepared_broadcast_plan("outdated-draft")

    def test_stream_plan_command_is_validated_and_queued(self):
        runtime = ExternalControlRuntime(
            aivis_client=FakeAivisSpeechClient(),
            speaker_id=101,
            public_base_url="http://127.0.0.1:8765",
        )

        accepted = runtime.enqueue_admin_command(
            {
                "action": "change_stream_plan",
                "text": "初見向けの自己紹介配信",
            }
        )

        self.assertEqual(accepted["action"], "change_stream_plan")
        self.assertEqual(accepted["text"], "初見向けの自己紹介配信")

    def test_chat_messages_are_published_without_channel_id(self):
        runtime = ExternalControlRuntime(
            aivis_client=FakeAivisSpeechClient(),
            speaker_id=101,
            public_base_url="http://127.0.0.1:8765",
        )
        subscriber = runtime.chat_event_broker.subscribe()
        subscriber.get_nowait()

        delivered_count = runtime.publish_chat_messages(
            [
                {
                    "message_id": "message-1",
                    "user_id": "private-channel-id",
                    "user_name": "視聴者",
                    "comment": "こんにちは",
                    "published_at": "2026-08-12T00:00:00Z",
                }
            ]
        )
        command = subscriber.get_nowait()

        self.assertEqual(delivered_count, 1)
        self.assertEqual(command["type"], "chat_messages")
        self.assertEqual(command["messages"][0]["comment"], "こんにちは")
        self.assertNotIn("user_id", command["messages"][0])

    def test_chat_history_is_replayed_after_reconnect(self):
        runtime = ExternalControlRuntime(
            aivis_client=FakeAivisSpeechClient(),
            speaker_id=101,
            public_base_url="http://127.0.0.1:8765",
        )
        runtime.publish_chat_messages(
            [
                {
                    "message_id": "message-1",
                    "user_name": "視聴者",
                    "comment": "再接続後も表示",
                    "published_at": "",
                }
            ]
        )

        subscriber = runtime.chat_event_broker.subscribe()
        snapshot = subscriber.get_nowait()

        self.assertEqual(snapshot["type"], "chat_snapshot")
        self.assertEqual(snapshot["messages"][0]["comment"], "再接続後も表示")

    def test_clear_overlays_removes_retained_display_state(self):
        runtime = ExternalControlRuntime(
            aivis_client=FakeAivisSpeechClient(),
            speaker_id=101,
            public_base_url="http://127.0.0.1:8765",
        )
        runtime.publish_chat_messages(
            [
                {
                    "message_id": "old-message",
                    "user_name": "前回の視聴者",
                    "comment": "前回のコメント",
                    "published_at": "",
                }
            ]
        )
        runtime.publish_topic_card(
            {"kind": "talk", "title": "前回の話題", "summary": "前回の内容"}
        )
        runtime.speak("前回の字幕", "neutral")

        runtime.clear_overlays()

        chat_snapshot = runtime.chat_event_broker.subscribe().get_nowait()
        topic_command = runtime.topic_event_broker.subscribe().get_nowait()
        subtitle_command = runtime.subtitle_event_broker.subscribe().get_nowait()
        self.assertEqual(chat_snapshot, {"type": "chat_snapshot", "messages": []})
        self.assertEqual(topic_command["type"], "clear_topic_card")
        self.assertEqual(subtitle_command["type"], "clear")

    def test_chat_reply_state_is_saved_and_published(self):
        runtime = ExternalControlRuntime(
            aivis_client=FakeAivisSpeechClient(),
            speaker_id=101,
            public_base_url="http://127.0.0.1:8765",
        )
        subscriber = runtime.chat_event_broker.subscribe()
        subscriber.get_nowait()
        runtime.publish_chat_messages(
            [
                {
                    "message_id": "message-1",
                    "user_name": "視聴者",
                    "comment": "どれに返事しているの？",
                    "published_at": "",
                }
            ]
        )
        subscriber.get_nowait()

        delivered_count = runtime.publish_chat_reply_state(
            "message-1",
            "speaking",
            1500,
        )
        reply_command = subscriber.get_nowait()

        self.assertEqual(delivered_count, 1)
        self.assertEqual(reply_command["type"], "chat_reply_state")
        self.assertEqual(reply_command["message_id"], "message-1")
        self.assertEqual(reply_command["state"], "speaking")
        self.assertGreater(reply_command["reply_until_ms"], 0)

        reconnected = runtime.chat_event_broker.subscribe()
        snapshot = reconnected.get_nowait()
        self.assertEqual(
            snapshot["messages"][0]["reply_state"],
            "speaking",
        )

    def test_clear_removes_reply_state_from_history(self):
        runtime = ExternalControlRuntime(
            aivis_client=FakeAivisSpeechClient(),
            speaker_id=101,
            public_base_url="http://127.0.0.1:8765",
        )
        runtime.publish_chat_messages(
            [
                {
                    "message_id": "message-1",
                    "user_name": "視聴者",
                    "comment": "テスト",
                    "published_at": "",
                }
            ]
        )
        runtime.publish_chat_reply_state("message-1", "thinking")

        runtime.publish_chat_reply_state("message-1", "clear")

        subscriber = runtime.chat_event_broker.subscribe()
        snapshot = subscriber.get_nowait()
        self.assertNotIn("reply_state", snapshot["messages"][0])

    def test_prepare_speech_does_not_publish_until_requested(self):
        runtime = ExternalControlRuntime(
            aivis_client=FakeAivisSpeechClient(),
            speaker_id=101,
            public_base_url="http://127.0.0.1:8765",
        )
        subscriber = runtime.event_broker.subscribe()

        prepared_speech = runtime.prepare_speech("先読み音声", "relaxed")

        self.assertEqual(prepared_speech.duration_ms, 1500)
        self.assertTrue(subscriber.empty())

        command, delivered_count = runtime.publish_prepared_speech(
            prepared_speech
        )

        self.assertEqual(delivered_count, 1)
        self.assertEqual(subscriber.get_nowait(), command)

    def test_speak_stores_audio_and_publishes_command(self):
        aivis_client = FakeAivisSpeechClient()
        runtime = ExternalControlRuntime(
            aivis_client=aivis_client,
            speaker_id=101,
            public_base_url="http://127.0.0.1:8765",
        )
        subscriber = runtime.event_broker.subscribe()

        command, delivered_count = runtime.speak("こんにちは", "happy")
        received_command = subscriber.get_nowait()
        audio_id = command["audio_url"].rsplit("/", 1)[-1][:-4]

        self.assertEqual(delivered_count, 1)
        self.assertEqual(received_command, command)
        self.assertEqual(command["text"], "こんにちは")
        self.assertEqual(command["emotion"], "happy")
        self.assertEqual(command["duration_ms"], 1500)
        self.assertEqual(aivis_client.last_emotion, "happy")
        self.assertEqual(aivis_client.last_speech_style, "normal")
        self.assertEqual(runtime.audio_store.get(audio_id), create_test_wav())

    def test_prepare_speech_passes_fast_style_to_aivis(self):
        aivis_client = FakeAivisSpeechClient()
        runtime = ExternalControlRuntime(
            aivis_client=aivis_client,
            speaker_id=101,
            public_base_url="http://127.0.0.1:8765",
        )

        prepared_speech = runtime.prepare_speech(
            "テンポを上げるよ",
            "surprised",
            "fast",
        )

        self.assertEqual(prepared_speech.speech_style, "fast")
        self.assertEqual(aivis_client.last_speech_style, "fast")

    def test_unknown_emotion_is_rejected(self):
        runtime = ExternalControlRuntime(
            aivis_client=FakeAivisSpeechClient(),
            speaker_id=101,
            public_base_url="http://127.0.0.1:8765",
        )

        with self.assertRaisesRegex(ValueError, "未対応のemotion"):
            runtime.speak("こんにちは", "unknown")

    def test_unknown_speech_style_is_rejected(self):
        runtime = ExternalControlRuntime(
            aivis_client=FakeAivisSpeechClient(),
            speaker_id=101,
            public_base_url="http://127.0.0.1:8765",
        )

        with self.assertRaisesRegex(ValueError, "未対応のspeech_style"):
            runtime.prepare_speech(
                "速すぎるよ",
                "surprised",
                "very_fast",
            )

    def test_speak_publishes_subtitle(self):
        runtime = ExternalControlRuntime(
            aivis_client=FakeAivisSpeechClient(),
            speaker_id=101,
            public_base_url="http://127.0.0.1:8765",
        )
        subtitle_subscriber = runtime.subtitle_event_broker.subscribe()

        command, _ = runtime.speak("字幕テスト", "surprised")
        subtitle_command = subtitle_subscriber.get_nowait()

        self.assertEqual(
            subtitle_command,
            {
                "type": "subtitle",
                "id": command["id"],
                "text": "字幕テスト",
                "emotion": "surprised",
                "duration_ms": 1500,
            },
        )

    def test_subtitle_is_replayed_to_reconnected_client(self):
        runtime = ExternalControlRuntime(
            aivis_client=FakeAivisSpeechClient(),
            speaker_id=101,
            public_base_url="http://127.0.0.1:8765",
        )

        runtime.speak("再接続後も表示", "neutral")
        subtitle_subscriber = runtime.subtitle_event_broker.subscribe()

        self.assertEqual(
            subtitle_subscriber.get_nowait()["text"],
            "再接続後も表示",
        )

    def test_reset_clears_subtitle(self):
        runtime = ExternalControlRuntime(
            aivis_client=FakeAivisSpeechClient(),
            speaker_id=101,
            public_base_url="http://127.0.0.1:8765",
        )
        subtitle_subscriber = runtime.subtitle_event_broker.subscribe()
        runtime.speak("消去対象", "neutral")
        subtitle_subscriber.get_nowait()

        reset_command, _ = runtime.reset()

        self.assertEqual(
            subtitle_subscriber.get_nowait(),
            {"type": "clear", "id": reset_command["id"]},
        )

    def test_speak_can_include_body_motion(self):
        runtime = ExternalControlRuntime(
            aivis_client=FakeAivisSpeechClient(),
            speaker_id=101,
            public_base_url="http://127.0.0.1:8765",
        )
        subscriber = runtime.event_broker.subscribe()

        command, delivered_count = runtime.speak("やあ", "happy", "greeting")

        self.assertEqual(delivered_count, 1)
        self.assertEqual(command["motion"], "greeting")
        self.assertEqual(subscriber.get_nowait(), command)

    def test_speak_can_include_parameterized_motion(self):
        runtime = ExternalControlRuntime(
            aivis_client=FakeAivisSpeechClient(),
            speaker_id=101,
            public_base_url="http://127.0.0.1:8765",
        )
        motion = {
            "name": "model_pose",
            "speed": 0.9,
            "intensity": 0.7,
            "head": "tilt_left",
        }

        command, _ = runtime.speak("決めるよ", "happy", motion)

        self.assertEqual(command["motion"], motion)

    def test_speak_can_include_view_action(self):
        runtime = ExternalControlRuntime(
            aivis_client=FakeAivisSpeechClient(),
            speaker_id=101,
            public_base_url="http://127.0.0.1:8765",
        )

        command, _ = runtime.speak(
            "全身を見せるね。",
            "happy",
            view_action="full_body",
        )

        self.assertEqual(command["view_action"], "full_body")

    def test_unknown_view_action_is_rejected(self):
        runtime = ExternalControlRuntime(
            aivis_client=FakeAivisSpeechClient(),
            speaker_id=101,
            public_base_url="http://127.0.0.1:8765",
        )

        with self.assertRaisesRegex(ValueError, "未対応のview_action"):
            runtime.speak("動くよ。", "neutral", view_action="sideways")

    def test_move_publishes_motion_without_audio(self):
        runtime = ExternalControlRuntime(
            aivis_client=FakeAivisSpeechClient(),
            speaker_id=101,
            public_base_url="http://127.0.0.1:8765",
        )
        subscriber = runtime.event_broker.subscribe()

        command, delivered_count = runtime.move("peace_sign")

        self.assertEqual(delivered_count, 1)
        self.assertEqual(command["motion"], "peace_sign")
        self.assertNotIn("audio_url", command)
        self.assertEqual(subscriber.get_nowait(), command)

    def test_unknown_motion_is_rejected(self):
        runtime = ExternalControlRuntime(
            aivis_client=FakeAivisSpeechClient(),
            speaker_id=101,
            public_base_url="http://127.0.0.1:8765",
        )

        with self.assertRaisesRegex(ValueError, "未対応のmotion"):
            runtime.move("unknown_motion")

    def test_invalid_parameterized_motion_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "motion.speed"):
            normalize_motion_command(
                {
                    "name": "greeting",
                    "speed": 2.0,
                    "intensity": 0.8,
                    "head": "nod",
                }
            )

    def test_speak_is_delivered_only_to_latest_subscriber(self):
        runtime = ExternalControlRuntime(
            aivis_client=FakeAivisSpeechClient(),
            speaker_id=101,
            public_base_url="http://127.0.0.1:8765",
        )
        old_subscriber = runtime.event_broker.subscribe()
        latest_subscriber = runtime.event_broker.subscribe()

        command, delivered_count = runtime.speak("二重再生を防ぎます", "neutral")

        self.assertEqual(delivered_count, 1)
        self.assertEqual(runtime.event_broker.subscriber_count(), 1)
        self.assertTrue(old_subscriber.empty())
        self.assertEqual(latest_subscriber.get_nowait(), command)


class CharacterMemoryHttpTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.repository = CharacterMemoryRepository(
            Path(self.temporary_directory.name) / "character_memory.db"
        )
        self.repository.save_draft(
            "野菜室を少しだけ見直した。",
            "belief_change",
            0.8,
            "autonomous_speech",
        )
        self.schedule_repository = BroadcastScheduleRepository(
            Path(self.temporary_directory.name) / "broadcast_schedule.db"
        )
        runtime = ExternalControlRuntime(
            aivis_client=FakeAivisSpeechClient(),
            speaker_id=101,
            public_base_url="http://127.0.0.1",
            character_memory_repository=self.repository,
            broadcast_schedule_repository=self.schedule_repository,
        )
        self.runtime = runtime
        self.server = ExternalControlHttpServer(("127.0.0.1", 0), runtime)
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
        )
        self.thread.start()
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temporary_directory.cleanup()

    def test_memory_panel_and_draft_api_are_available(self):
        with urllib.request.urlopen(
            f"{self.base_url}/character-memories",
            timeout=3,
        ) as response:
            html = response.read().decode("utf-8")
        with urllib.request.urlopen(
            f"{self.base_url}/api/character-memories?status=draft",
            timeout=3,
        ) as response:
            body = json.loads(response.read().decode("utf-8"))

        self.assertIn("ガン奈の記憶", html)
        self.assertEqual(len(body["memories"]), 1)
        self.assertEqual(body["memories"][0]["status"], "draft")

    def test_admin_panel_contains_broadcast_calendar(self):
        with urllib.request.urlopen(
            f"{self.base_url}/admin",
            timeout=3,
        ) as response:
            html = response.read().decode("utf-8")

        self.assertIn("配信カレンダー", html)
        self.assertIn("タイトル・説明文テンプレート", html)
        self.assertIn("/api/broadcast/schedules/save", html)
        self.assertIn("ライブ制御を起動", html)

    def test_managed_live_service_can_be_started_and_stopped_via_api(self):
        controller = Mock()
        controller.is_running.return_value = False
        self.runtime.attach_live_service_controller(controller)

        start_body = self._post_json("/api/live-service/start", {})
        controller.is_running.return_value = True
        stop_body = self._post_json("/api/live-service/stop", {})

        self.assertEqual(start_body["status"], "starting")
        self.assertEqual(stop_body["status"], "stopping")
        controller.start.assert_called_once_with()
        controller.request_stop.assert_called_once_with()

    def test_broadcast_template_and_schedule_can_be_managed_via_api(self):
        template_body = self._post_json(
            "/api/broadcast/templates/save",
            {
                "name": "通常配信",
                "title": "ガン奈の雑談配信",
                "description": "コメント歓迎です。",
                "privacy_status": "unlisted",
            },
        )
        template = template_body["template"]
        schedule_body = self._post_json(
            "/api/broadcast/schedules/save",
            {
                "scheduled_start_at": "2026-08-20T12:00:00.000Z",
                "planning_mode": "ai",
                "content_request": "親しみやすい日常雑談",
                "template_id": template["template_id"],
                "title": template["title"],
                "description": template["description"],
                "privacy_status": template["privacy_status"],
            },
        )
        schedule = schedule_body["schedule"]
        query = urllib.parse.urlencode(
            {
                "start_at": "2026-08-01T00:00:00+00:00",
                "end_at": "2026-09-01T00:00:00+00:00",
            }
        )

        with urllib.request.urlopen(
            f"{self.base_url}/api/broadcast/templates",
            timeout=3,
        ) as response:
            templates = json.loads(response.read().decode("utf-8"))["templates"]
        with urllib.request.urlopen(
            f"{self.base_url}/api/broadcast/schedules?{query}",
            timeout=3,
        ) as response:
            schedules = json.loads(response.read().decode("utf-8"))["schedules"]

        self.assertEqual([item["template_id"] for item in templates], [template["template_id"]])
        self.assertEqual([item["schedule_id"] for item in schedules], [schedule["schedule_id"]])

        self._post_json(
            "/api/broadcast/schedules/delete",
            {"schedule_id": schedule["schedule_id"]},
        )
        self._post_json(
            "/api/broadcast/templates/delete",
            {"template_id": template["template_id"]},
        )
        self.assertEqual(self.schedule_repository.list_schedules(), [])
        self.assertEqual(self.schedule_repository.list_templates(), [])

    def test_invalid_manual_schedule_returns_readable_error(self):
        with self.assertRaises(urllib.error.HTTPError) as context:
            self._post_json(
                "/api/broadcast/schedules/save",
                {
                    "scheduled_start_at": "2026-08-20T12:00:00.000Z",
                    "planning_mode": "manual",
                    "content_request": "",
                    "title": "自己紹介配信",
                    "description": "説明",
                    "privacy_status": "unlisted",
                },
            )

        self.assertEqual(context.exception.code, 400)
        body = json.loads(context.exception.read().decode("utf-8"))
        self.assertIn("配信内容を入力", body["error"])

    def test_topic_overlay_is_available_separately(self):
        with urllib.request.urlopen(
            f"{self.base_url}/topic-overlay",
            timeout=3,
        ) as response:
            html = response.read().decode("utf-8")

        self.assertIn("AI YouTuber Topic Overlay", html)
        self.assertIn("topic-events", html)
        self.assertNotIn("subtitle-events", html)

    def test_draft_can_be_approved_via_api(self):
        memory_id = self.repository.list("draft")[0]["memory_id"]
        request = urllib.request.Request(
            f"{self.base_url}/api/character-memories/review",
            data=json.dumps(
                {"memory_id": memory_id, "status": "approved"}
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(request, timeout=3) as response:
            body = json.loads(response.read().decode("utf-8"))

        self.assertEqual(body["status"], "approved")
        self.assertEqual(self.repository.list("draft"), [])
        self.assertEqual(len(self.repository.list("approved")), 1)

    def _post_json(self, path, body):
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
