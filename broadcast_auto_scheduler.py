import os
import threading
import traceback
from datetime import datetime, timedelta, timezone


FINAL_STATUSES = {"youtube_scheduled", "live", "completed", "cancelled"}
PRE_LIVE_PHASES = {
    "service_idle",
    "starting_live_control",
    "waiting_for_obs",
    "waiting_for_youtube",
    "live_control_error",
}


class BroadcastAutoScheduler:
    def __init__(
        self,
        runtime,
        *,
        check_interval_seconds=15,
        prepare_minutes_before=10,
        late_start_grace_minutes=15,
        youtube_create_hours_before=48,
        youtube_retry_minutes=15,
        now=None,
    ):
        self.runtime = runtime
        self.check_interval_seconds = _positive_number(
            check_interval_seconds,
            "自動配信の確認間隔",
            maximum=300,
        )
        self.prepare_seconds_before = _non_negative_number(
            prepare_minutes_before,
            "配信構成の事前準備時間",
            maximum=1440,
        ) * 60
        self.late_start_grace_seconds = _non_negative_number(
            late_start_grace_minutes,
            "自動配信の遅延許容時間",
            maximum=1440,
        ) * 60
        self.youtube_create_seconds_before = _positive_number(
            youtube_create_hours_before,
            "YouTube枠の事前作成時間",
            maximum=24 * 365,
        ) * 3600
        self.youtube_retry_seconds = _positive_number(
            youtube_retry_minutes,
            "YouTube枠作成の再試行間隔",
            maximum=1440,
        ) * 60
        self.now = now or (lambda: datetime.now(timezone.utc))
        self._stop_event = threading.Event()
        self._thread = None
        self._preparation_attempts = {}
        self._service_preparation_attempts = {}
        self._prepared_service_ids = set()
        self._youtube_retry_after = {}

    @classmethod
    def from_env(cls, runtime):
        return cls(
            runtime,
            check_interval_seconds=os.getenv(
                "AUTO_SCHEDULE_CHECK_INTERVAL_SECONDS",
                "15",
            ),
            prepare_minutes_before=os.getenv(
                "AUTO_SCHEDULE_PREPARE_MINUTES_BEFORE",
                "10",
            ),
            late_start_grace_minutes=os.getenv(
                "AUTO_SCHEDULE_LATE_GRACE_MINUTES",
                "15",
            ),
            youtube_create_hours_before=youtube_frame_create_hours_before(),
            youtube_retry_minutes=os.getenv(
                "YOUTUBE_FRAME_CREATE_RETRY_MINUTES",
                "15",
            ),
        )

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("自動配信スケジューラーはすでに起動しています。")
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="broadcast-auto-scheduler",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout=5):
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        self._thread = None

    def run_once(self):
        current = self.now()
        if current.tzinfo is None:
            raise RuntimeError("自動配信の現在時刻にタイムゾーンがありません。")
        current = current.astimezone(timezone.utc)
        schedules = self.runtime.list_broadcast_schedules(
            start_at=current - timedelta(seconds=self.late_start_grace_seconds),
            end_at=current
            + timedelta(
                seconds=max(
                    self.prepare_seconds_before,
                    self.youtube_create_seconds_before,
                )
                + self.check_interval_seconds
                + 1
            ),
        )
        for schedule in schedules:
            self._process_schedule(schedule, current)

    def _run(self):
        print(
            "自動配信スケジューラーを起動しました。"
            f"確認間隔={self.check_interval_seconds:g}秒 "
            f"事前準備={self.prepare_seconds_before / 60:g}分前 "
            f"遅延許容={self.late_start_grace_seconds / 60:g}分"
        )
        while not self._stop_event.is_set():
            try:
                self.run_once()
                self.runtime.update_admin_status(auto_scheduler_error=None)
            except Exception as exc:
                # 常駐スレッドを止めず、管理画面とログの両方へ原因を残します。
                traceback.print_exc()
                self.runtime.update_admin_status(auto_scheduler_error=str(exc))
                print(f"自動配信スケジューラーエラー: {exc}")
            self._stop_event.wait(self.check_interval_seconds)
        print("自動配信スケジューラーを停止しました。")

    def _process_schedule(self, schedule, current):
        schedule_id = schedule["schedule_id"]
        status = schedule.get("status")
        if status in FINAL_STATUSES:
            self._discard_service_preparation(schedule_id)
            return
        if status == "starting":
            return
        scheduled_at = _parse_datetime(schedule.get("scheduled_start_at"))
        seconds_until_start = (scheduled_at - current).total_seconds()
        if 0 < seconds_until_start <= self.youtube_create_seconds_before:
            provisioned_schedule = self._ensure_youtube_frame(schedule, current)
            if isinstance(provisioned_schedule, dict):
                schedule = provisioned_schedule
        if not schedule.get("auto_start", False):
            self._discard_service_preparation(schedule_id)
            return
        if seconds_until_start > self.prepare_seconds_before:
            return
        if seconds_until_start < -self.late_start_grace_seconds:
            return

        if not schedule.get("prepared_stream_plan"):
            if not self._should_attempt_preparation(
                schedule["schedule_id"],
                seconds_until_start,
            ):
                return
            try:
                self.runtime.generate_broadcast_draft(
                    schedule_id=schedule_id
                )
                print(
                    "自動配信の構成を準備しました："
                    f"{schedule['title']} / schedule_id={schedule_id}"
                )
                schedule = self.runtime.get_broadcast_schedule(
                    schedule_id
                )
            except Exception as exc:
                self.runtime.update_broadcast_schedule_status(
                    schedule_id,
                    "error",
                    error=f"自動配信の構成作成に失敗しました: {exc}",
                )
                print(
                    "自動配信の構成作成エラー："
                    f"schedule_id={schedule_id} detail={exc}"
                )
                return

        if not self._prepare_local_services(schedule, seconds_until_start):
            return

        if seconds_until_start <= 0 and schedule.get("status") == "prepared":
            self._start_schedule(schedule)

    def _should_attempt_preparation(self, schedule_id, seconds_until_start):
        attempts = self._preparation_attempts.get(schedule_id, 0)
        if attempts >= 2:
            return False
        # 1回目は事前準備時間内、2回目は予定時刻を過ぎてからだけ行います。
        if attempts == 1 and seconds_until_start > 0:
            return False
        self._preparation_attempts[schedule_id] = attempts + 1
        return True

    def _prepare_local_services(self, schedule, seconds_until_start):
        schedule_id = schedule["schedule_id"]
        if schedule_id in self._prepared_service_ids:
            return True
        attempts = self._service_preparation_attempts.get(schedule_id, 0)
        if attempts >= 2:
            return False
        # アプリ起動も、失敗時の2回目だけは予定時刻を過ぎてから再試行します。
        if attempts == 1 and seconds_until_start > 0:
            return False
        self._service_preparation_attempts[schedule_id] = attempts + 1
        try:
            self.runtime.prepare_live_service(schedule_id)
        except Exception as exc:
            self.runtime.update_broadcast_schedule_status(
                schedule_id,
                "error",
                error=f"配信アプリの事前準備に失敗しました: {exc}",
            )
            print(
                "配信アプリの事前準備エラー："
                f"schedule_id={schedule_id} detail={exc}"
            )
            return False

        self._prepared_service_ids.add(schedule_id)
        if schedule.get("status") == "error":
            self.runtime.update_broadcast_schedule_status(
                schedule_id,
                "prepared",
                error=None,
            )
            schedule["status"] = "prepared"
        print(
            "配信アプリの事前準備が完了しました："
            f"{schedule['title']} / schedule_id={schedule_id}"
        )
        return True

    def _discard_service_preparation(self, schedule_id):
        if schedule_id not in self._prepared_service_ids:
            return
        try:
            self.runtime.discard_live_service_preparation(schedule_id)
        finally:
            self._prepared_service_ids.discard(schedule_id)

    def _start_schedule(self, schedule):
        status = self.runtime.get_admin_status()
        if status.get("phase") not in PRE_LIVE_PHASES:
            message = (
                "別の配信処理が動作中のため、自動開始できませんでした。"
                f" phase={status.get('phase')}"
            )
            self.runtime.update_broadcast_schedule_status(
                schedule["schedule_id"],
                "error",
                error=message,
            )
            print(f"自動配信開始エラー: {message}")
            return

        try:
            self.runtime.queue_broadcast_start(
                {
                    "action": "configure_broadcast",
                    "schedule_id": schedule["schedule_id"],
                }
            )
        except Exception as exc:
            self._discard_service_preparation(schedule["schedule_id"])
            raise RuntimeError(
                "自動配信を開始できませんでした。"
                f" schedule_id={schedule['schedule_id']} detail={exc}"
            ) from exc
        self._prepared_service_ids.discard(schedule["schedule_id"])
        print(
            "予定時刻の自動配信開始を受け付けました："
            f"{schedule['title']} / schedule_id={schedule['schedule_id']}"
        )

    def _ensure_youtube_frame(self, schedule, current):
        if schedule.get("youtube_video_id"):
            self._youtube_retry_after.pop(schedule["schedule_id"], None)
            return schedule
        retry_after = self._youtube_retry_after.get(schedule["schedule_id"])
        if retry_after is not None and current < retry_after:
            return schedule
        try:
            provisioned_schedule = self.runtime.ensure_youtube_broadcast_for_schedule(
                schedule["schedule_id"]
            )
        except Exception as exc:
            self._youtube_retry_after[schedule["schedule_id"]] = (
                current + timedelta(seconds=self.youtube_retry_seconds)
            )
            print(
                "YouTube枠の自動作成エラー："
                f"schedule_id={schedule['schedule_id']} detail={exc}"
            )
            return schedule
        self._youtube_retry_after.pop(schedule["schedule_id"], None)
        print(
            "YouTube枠を自動作成しました："
            f"{schedule['title']} / schedule_id={schedule['schedule_id']}"
        )
        return provisioned_schedule


def auto_schedule_enabled():
    normalized = os.getenv("AUTO_SCHEDULE_ENABLED", "true").strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise RuntimeError(
        "AUTO_SCHEDULE_ENABLEDはtrueまたはfalseで設定してください。"
    )


def youtube_frame_create_hours_before():
    return _positive_number(
        os.getenv("YOUTUBE_FRAME_CREATE_HOURS_BEFORE", "48"),
        "YouTube枠の事前作成時間",
        maximum=24 * 365,
    )


def _parse_datetime(value):
    normalized = str(value or "").strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise RuntimeError(
            f"配信予定時刻を読み取れませんでした: {value}"
        ) from exc
    if parsed.tzinfo is None:
        raise RuntimeError("配信予定時刻にタイムゾーンがありません。")
    return parsed.astimezone(timezone.utc)


def _positive_number(value, label, maximum):
    number = _non_negative_number(value, label, maximum)
    if number <= 0:
        raise RuntimeError(f"{label}は0より大きくしてください。")
    return number


def _non_negative_number(value, label, maximum):
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{label}は数値で設定してください。") from exc
    if not 0 <= number <= maximum:
        raise RuntimeError(f"{label}は0〜{maximum}の範囲で設定してください。")
    return number
