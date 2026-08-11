import io
import unittest
import wave

from control_server import (
    AudioStore,
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

    def synthesize(self, text, speaker_id, emotion="neutral"):
        if not text or speaker_id != 101:
            raise AssertionError("テスト用AivisSpeech呼び出しの引数が不正です。")
        self.last_emotion = emotion
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
        self.assertEqual(runtime.audio_store.get(audio_id), create_test_wav())

    def test_unknown_emotion_is_rejected(self):
        runtime = ExternalControlRuntime(
            aivis_client=FakeAivisSpeechClient(),
            speaker_id=101,
            public_base_url="http://127.0.0.1:8765",
        )

        with self.assertRaisesRegex(ValueError, "未対応のemotion"):
            runtime.speak("こんにちは", "unknown")

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


if __name__ == "__main__":
    unittest.main()
