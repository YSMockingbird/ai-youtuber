import unittest

from control_server import AudioStore, ExternalControlRuntime


class FakeAivisSpeechClient:
    def __init__(self):
        self.last_emotion = None

    def synthesize(self, text, speaker_id, emotion="neutral"):
        if not text or speaker_id != 101:
            raise AssertionError("テスト用AivisSpeech呼び出しの引数が不正です。")
        self.last_emotion = emotion
        return b"RIFF-test-wav"


class AudioStoreTest(unittest.TestCase):
    def test_old_audio_is_removed(self):
        store = AudioStore(max_items=1)
        old_audio_id = store.put(b"old")
        new_audio_id = store.put(b"new")

        self.assertIsNone(store.get(old_audio_id))
        self.assertEqual(store.get(new_audio_id), b"new")


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
        self.assertEqual(aivis_client.last_emotion, "happy")
        self.assertEqual(runtime.audio_store.get(audio_id), b"RIFF-test-wav")

    def test_unknown_emotion_is_rejected(self):
        runtime = ExternalControlRuntime(
            aivis_client=FakeAivisSpeechClient(),
            speaker_id=101,
            public_base_url="http://127.0.0.1:8765",
        )

        with self.assertRaisesRegex(ValueError, "未対応のemotion"):
            runtime.speak("こんにちは", "unknown")

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
