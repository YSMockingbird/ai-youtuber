import io
import json
import tempfile
import threading
import unittest
import urllib.request
import wave
from pathlib import Path
from unittest.mock import Mock

from character_memory import CharacterMemoryRepository
from control_server import (
    AudioStore,
    ExternalControlHttpServer,
    ExternalControlRuntime,
    get_wav_duration_ms,
    normalize_motion_command,
)


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
        runtime = ExternalControlRuntime(
            aivis_client=FakeAivisSpeechClient(),
            speaker_id=101,
            public_base_url="http://127.0.0.1",
            character_memory_repository=self.repository,
        )
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


if __name__ == "__main__":
    unittest.main()
