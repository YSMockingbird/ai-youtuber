import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from broadcast_auto_scheduler import BroadcastAutoScheduler


class BroadcastAutoSchedulerTest(unittest.TestCase):
    def setUp(self):
        self.current = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
        self.runtime = Mock()
        self.runtime.get_admin_status.return_value = {
            "phase": "service_idle",
            "live_control_running": False,
        }

    def create_schedule(
        self,
        *,
        starts_in_minutes=5,
        status="draft",
        prepared=False,
        auto_start=True,
    ):
        return {
            "schedule_id": "schedule-1",
            "scheduled_start_at": (
                self.current + timedelta(minutes=starts_in_minutes)
            ).isoformat(),
            "title": "自動配信テスト",
            "status": status,
            "prepared_stream_plan": "{}" if prepared else None,
            "auto_start": auto_start,
        }

    def create_scheduler(self):
        return BroadcastAutoScheduler(
            self.runtime,
            check_interval_seconds=15,
            prepare_minutes_before=10,
            late_start_grace_minutes=15,
            now=lambda: self.current,
        )

    def test_plan_and_local_services_are_prepared_before_start(self):
        schedule = self.create_schedule(starts_in_minutes=5)
        prepared = {**schedule, "status": "prepared", "prepared_stream_plan": "{}"}
        self.runtime.list_broadcast_schedules.return_value = [schedule]
        self.runtime.get_broadcast_schedule.return_value = prepared

        self.create_scheduler().run_once()

        self.runtime.generate_broadcast_draft.assert_called_once_with(
            schedule_id="schedule-1"
        )
        self.runtime.prepare_live_service.assert_called_once_with("schedule-1")
        self.runtime.queue_broadcast_start.assert_not_called()

    def test_prepared_plan_starts_at_scheduled_time(self):
        schedule = self.create_schedule(
            starts_in_minutes=0,
            status="prepared",
            prepared=True,
        )
        self.runtime.list_broadcast_schedules.return_value = [schedule]

        self.create_scheduler().run_once()

        self.runtime.prepare_live_service.assert_called_once_with("schedule-1")
        self.runtime.queue_broadcast_start.assert_called_once_with(
            {
                "action": "configure_broadcast",
                "schedule_id": "schedule-1",
            }
        )

    def test_disabled_schedule_is_not_processed(self):
        self.runtime.list_broadcast_schedules.return_value = [
            self.create_schedule(starts_in_minutes=5, auto_start=False)
        ]

        self.create_scheduler().run_once()

        self.runtime.generate_broadcast_draft.assert_not_called()
        self.runtime.prepare_live_service.assert_not_called()
        self.runtime.queue_broadcast_start.assert_not_called()
        self.runtime.ensure_youtube_broadcast_for_schedule.assert_called_once_with(
            "schedule-1"
        )

    def test_youtube_frame_is_not_created_more_than_48_hours_early(self):
        self.runtime.list_broadcast_schedules.return_value = [
            self.create_schedule(starts_in_minutes=48 * 60 + 1)
        ]

        self.create_scheduler().run_once()

        self.runtime.ensure_youtube_broadcast_for_schedule.assert_not_called()

    def test_youtube_frame_creation_is_retried_after_interval(self):
        schedule = self.create_schedule(starts_in_minutes=60)
        self.runtime.list_broadcast_schedules.return_value = [schedule]
        self.runtime.ensure_youtube_broadcast_for_schedule.side_effect = (
            RuntimeError("一時エラー")
        )
        scheduler = self.create_scheduler()

        scheduler.run_once()
        scheduler.run_once()
        self.current += timedelta(minutes=15)
        scheduler.run_once()

        self.assertEqual(
            self.runtime.ensure_youtube_broadcast_for_schedule.call_count,
            2,
        )

    def test_schedule_older_than_grace_period_is_not_started(self):
        self.runtime.list_broadcast_schedules.return_value = [
            self.create_schedule(
                starts_in_minutes=-16,
                status="prepared",
                prepared=True,
            )
        ]

        self.create_scheduler().run_once()

        self.runtime.queue_broadcast_start.assert_not_called()

    def test_failed_local_service_preparation_is_retried_at_start_time(self):
        schedule = self.create_schedule(
            starts_in_minutes=5,
            status="prepared",
            prepared=True,
        )
        self.runtime.list_broadcast_schedules.return_value = [schedule]
        self.runtime.prepare_live_service.side_effect = [
            RuntimeError("OBSを起動できません"),
            True,
        ]
        scheduler = self.create_scheduler()

        scheduler.run_once()
        scheduler.run_once()
        self.current += timedelta(minutes=5)
        retry_schedule = {
            **schedule,
            "scheduled_start_at": self.current.isoformat(),
            "status": "error",
        }
        self.runtime.list_broadcast_schedules.return_value = [retry_schedule]
        scheduler.run_once()

        self.assertEqual(self.runtime.prepare_live_service.call_count, 2)
        self.runtime.update_broadcast_schedule_status.assert_any_call(
            "schedule-1",
            "prepared",
            error=None,
        )
        self.runtime.queue_broadcast_start.assert_called_once()

    def test_failed_preparation_is_retried_only_at_start_time(self):
        schedule = self.create_schedule(starts_in_minutes=5)
        self.runtime.list_broadcast_schedules.return_value = [schedule]
        self.runtime.generate_broadcast_draft.side_effect = [
            RuntimeError("一時的なAPIエラー"),
            None,
        ]
        self.runtime.get_broadcast_schedule.return_value = {
            **schedule,
            "scheduled_start_at": self.current.isoformat(),
            "status": "prepared",
            "prepared_stream_plan": "{}",
        }
        scheduler = self.create_scheduler()

        scheduler.run_once()
        scheduler.run_once()
        self.current += timedelta(minutes=5)
        self.runtime.list_broadcast_schedules.return_value = [
            {**schedule, "scheduled_start_at": self.current.isoformat()}
        ]
        scheduler.run_once()

        self.assertEqual(self.runtime.generate_broadcast_draft.call_count, 2)
        self.runtime.queue_broadcast_start.assert_called_once()


if __name__ == "__main__":
    unittest.main()
