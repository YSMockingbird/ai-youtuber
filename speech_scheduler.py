import math
import time


class SpeechScheduler:
    def __init__(
        self,
        silence_seconds=3,
        estimated_characters_per_second=7.0,
        minimum_duration_seconds=2.0,
        maximum_duration_seconds=20.0,
        now=None,
    ):
        if not 1 <= float(silence_seconds) <= 300:
            raise ValueError("silence_secondsは1〜300秒で設定してください。")
        if float(estimated_characters_per_second) <= 0:
            raise ValueError(
                "estimated_characters_per_secondは0より大きくしてください。"
            )
        self.silence_seconds = float(silence_seconds)
        self.estimated_characters_per_second = float(
            estimated_characters_per_second
        )
        self.minimum_duration_seconds = float(minimum_duration_seconds)
        self.maximum_duration_seconds = float(maximum_duration_seconds)
        current_time = time.monotonic() if now is None else float(now)
        self.next_autonomous_speech_at = current_time + self.silence_seconds

    @classmethod
    def from_config(cls, config, now=None):
        scheduler_config = config.get("autonomous_speech", {})
        return cls(
            silence_seconds=scheduler_config.get("silence_seconds", 3),
            estimated_characters_per_second=scheduler_config.get(
                "estimated_characters_per_second", 7.0
            ),
            minimum_duration_seconds=scheduler_config.get(
                "minimum_duration_seconds", 2.0
            ),
            maximum_duration_seconds=scheduler_config.get(
                "maximum_duration_seconds", 20.0
            ),
            now=now,
        )

    def estimate_duration_seconds(self, text):
        normalized_length = len(str(text).strip())
        estimated = normalized_length / self.estimated_characters_per_second
        return min(
            max(estimated, self.minimum_duration_seconds),
            self.maximum_duration_seconds,
        )

    def record_speech(self, text, now=None):
        current_time = time.monotonic() if now is None else float(now)
        duration = self.estimate_duration_seconds(text)
        self.next_autonomous_speech_at = (
            current_time + duration + self.silence_seconds
        )
        return duration

    def record_failed_attempt(self, retry_seconds=10, now=None):
        current_time = time.monotonic() if now is None else float(now)
        self.next_autonomous_speech_at = current_time + max(
            float(retry_seconds), 1
        )

    def should_speak_autonomously(self, now=None):
        current_time = time.monotonic() if now is None else float(now)
        return current_time >= self.next_autonomous_speech_at

    def seconds_until_autonomous_speech(self, now=None):
        current_time = time.monotonic() if now is None else float(now)
        return max(
            0,
            math.ceil(self.next_autonomous_speech_at - current_time),
        )
