import unittest

from speech_scheduler import SpeechScheduler


class SpeechSchedulerTest(unittest.TestCase):
    def test_three_second_silence_is_allowed(self):
        scheduler = SpeechScheduler(silence_seconds=3, now=100)

        self.assertEqual(scheduler.silence_seconds, 3)

    def test_silence_shorter_than_one_second_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "1〜300秒"):
            SpeechScheduler(silence_seconds=0.5)

    def test_waits_for_estimated_audio_and_fixed_silence(self):
        scheduler = SpeechScheduler(
            silence_seconds=15,
            estimated_characters_per_second=5,
            minimum_duration_seconds=2,
            maximum_duration_seconds=20,
            now=100,
        )

        duration = scheduler.record_speech("1234567890", now=100)

        self.assertEqual(duration, 2)
        self.assertFalse(scheduler.should_speak_autonomously(now=116))
        self.assertTrue(scheduler.should_speak_autonomously(now=117))

    def test_failed_attempt_uses_fixed_retry_time(self):
        scheduler = SpeechScheduler(now=100)
        scheduler.record_failed_attempt(retry_seconds=10, now=100)

        self.assertEqual(scheduler.seconds_until_autonomous_speech(now=104), 6)
        self.assertTrue(scheduler.should_speak_autonomously(now=110))


if __name__ == "__main__":
    unittest.main()
