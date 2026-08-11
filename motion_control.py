import os
import time


AUTO_BODY_MOTIONS = {
    "show_body",
    "greeting",
    "peace_sign",
    "spin",
    "model_pose",
    "squat",
}

HEAD_MOTIONS = {
    "none",
    "nod",
    "tilt_left",
    "tilt_right",
}


def get_motion_cooldown_seconds():
    raw_value = os.getenv("MOTION_COOLDOWN_SECONDS", "12").strip()
    try:
        cooldown_seconds = float(raw_value)
    except ValueError as exc:
        raise RuntimeError(
            "MOTION_COOLDOWN_SECONDSは数値で設定してください。"
        ) from exc
    if not 0 <= cooldown_seconds <= 120:
        raise RuntimeError(
            "MOTION_COOLDOWN_SECONDSは0〜120秒で設定してください。"
        )
    return cooldown_seconds


class MotionRateLimiter:
    def __init__(self, cooldown_seconds=None):
        self.cooldown_seconds = (
            get_motion_cooldown_seconds()
            if cooldown_seconds is None
            else cooldown_seconds
        )
        self._last_body_motion_at = float("-inf")

    def filter(self, motion, now=None):
        if motion is None:
            return None

        filtered_motion = dict(motion)
        body_motion = filtered_motion.get("name")
        current_time = time.monotonic() if now is None else now

        if body_motion is not None:
            elapsed = current_time - self._last_body_motion_at
            if elapsed < self.cooldown_seconds:
                filtered_motion["name"] = None
            else:
                self._last_body_motion_at = current_time

        if (
            filtered_motion.get("name") is None
            and filtered_motion.get("head", "none") == "none"
        ):
            return None
        return filtered_motion
