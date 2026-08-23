import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from broadcast_schedule import BroadcastScheduleRepository


class BroadcastScheduleRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.repository = BroadcastScheduleRepository(
            Path(self.temporary_directory.name) / "broadcast_schedule.db"
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_template_can_be_created_updated_and_deleted(self):
        template = self.repository.create_template(
            name="通常配信",
            title="ガン奈の雑談配信",
            description="ガン奈が今日も話します。",
            privacy_status="unlisted",
        )

        self.assertEqual(self.repository.list_templates(), [template])
        updated = self.repository.update_template(
            template["template_id"],
            name="通常配信・公開用",
            title="ガン奈の雑談配信",
            description="コメント歓迎です。",
            privacy_status="public",
        )
        self.assertEqual(updated["name"], "通常配信・公開用")
        self.assertEqual(updated["privacy_status"], "public")

        self.repository.delete_template(template["template_id"])
        self.assertEqual(self.repository.list_templates(), [])

    def test_duplicate_template_name_is_rejected(self):
        arguments = {
            "name": "通常配信",
            "title": "ガン奈の雑談配信",
            "description": "説明",
        }
        self.repository.create_template(**arguments)

        with self.assertRaisesRegex(ValueError, "同じ名前"):
            self.repository.create_template(**arguments)

    def test_ai_schedule_keeps_template_values_as_a_snapshot(self):
        template = self.repository.create_template(
            name="通常配信",
            title="最初のタイトル",
            description="最初の説明",
        )
        scheduled_at = datetime(2026, 8, 20, 21, 0, tzinfo=timezone(timedelta(hours=9)))
        schedule = self.repository.create_schedule(
            scheduled_start_at=scheduled_at,
            planning_mode="ai",
            content_request="日常的で親しみやすい雑談",
            title=template["title"],
            description=template["description"],
            template_id=template["template_id"],
        )

        self.repository.update_template(
            template["template_id"],
            name="通常配信",
            title="変更後のタイトル",
            description="変更後の説明",
            privacy_status="unlisted",
        )
        saved = self.repository.get_schedule(schedule["schedule_id"])

        self.assertEqual(saved["scheduled_start_at"], "2026-08-20T12:00:00+00:00")
        self.assertEqual(saved["title"], "最初のタイトル")
        self.assertEqual(saved["description"], "最初の説明")
        self.assertEqual(saved["planning_mode"], "ai")
        self.assertTrue(saved["auto_start"])
        self.assertEqual(saved["status"], "draft")

    def test_schedule_auto_start_can_be_disabled(self):
        schedule = self.repository.create_schedule(
            scheduled_start_at="2026-08-20T21:00:00+09:00",
            planning_mode="ai",
            content_request="",
            title="手動開始の配信",
            description="説明",
            auto_start=False,
        )

        self.assertFalse(schedule["auto_start"])
        updated = self.repository.update_schedule(
            schedule["schedule_id"],
            auto_start=True,
            status="starting",
        )
        self.assertTrue(updated["auto_start"])
        self.assertEqual(updated["status"], "starting")

    def test_existing_database_is_migrated_with_auto_start_enabled(self):
        database_path = Path(self.temporary_directory.name) / "legacy.db"
        with sqlite3.connect(str(database_path)) as connection:
            connection.execute(
                """
                CREATE TABLE broadcast_schedules (
                    schedule_id TEXT PRIMARY KEY,
                    scheduled_start_at TEXT NOT NULL,
                    planning_mode TEXT NOT NULL,
                    content_request TEXT NOT NULL,
                    prepared_stream_plan TEXT,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    privacy_status TEXT NOT NULL,
                    template_id TEXT,
                    status TEXT NOT NULL,
                    youtube_video_id TEXT,
                    last_error TEXT,
                    prepared_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

        BroadcastScheduleRepository(database_path)

        with sqlite3.connect(str(database_path)) as connection:
            columns = {
                row[1]: row
                for row in connection.execute(
                    "PRAGMA table_info(broadcast_schedules)"
                )
            }
        self.assertIn("auto_start", columns)
        self.assertEqual(columns["auto_start"][4], "1")

    def test_manual_schedule_requires_content(self):
        with self.assertRaisesRegex(ValueError, "配信内容を入力"):
            self.repository.create_schedule(
                scheduled_start_at="2026-08-20T21:00:00+09:00",
                planning_mode="manual",
                content_request="",
                title="自己紹介配信",
                description="説明",
            )

    def test_ai_schedule_cannot_be_changed_to_empty_manual_schedule(self):
        schedule = self.repository.create_schedule(
            scheduled_start_at="2026-08-20T21:00:00+09:00",
            planning_mode="ai",
            content_request="",
            title="雑談配信",
            description="説明",
        )

        with self.assertRaisesRegex(ValueError, "配信内容を入力"):
            self.repository.update_schedule(
                schedule["schedule_id"],
                planning_mode="manual",
            )

    def test_schedule_can_be_filtered_and_prepared(self):
        first = self.repository.create_schedule(
            scheduled_start_at="2026-08-20T21:00:00+09:00",
            planning_mode="manual",
            content_request="自己紹介をする",
            title="自己紹介配信",
            description="説明",
        )
        self.repository.create_schedule(
            scheduled_start_at="2026-09-20T21:00:00+09:00",
            planning_mode="ai",
            content_request="",
            title="雑談配信",
            description="説明",
        )

        august = self.repository.list_schedules(
            start_at="2026-08-01T00:00:00+09:00",
            end_at="2026-09-01T00:00:00+09:00",
        )
        prepared = self.repository.update_schedule(
            first["schedule_id"],
            prepared_stream_plan="自己紹介を3つの話題に分けて話す。",
            status="prepared",
        )

        self.assertEqual([item["schedule_id"] for item in august], [first["schedule_id"]])
        self.assertEqual(prepared["status"], "prepared")
        self.assertIsNotNone(prepared["prepared_at"])

    def test_changing_content_invalidates_prepared_plan(self):
        schedule = self.repository.create_schedule(
            scheduled_start_at="2026-08-20T21:00:00+09:00",
            planning_mode="manual",
            content_request="自己紹介をする",
            title="自己紹介配信",
            description="説明",
        )
        self.repository.update_schedule(
            schedule["schedule_id"],
            prepared_stream_plan='{"theme":"自己紹介"}',
            status="prepared",
        )

        updated = self.repository.update_schedule(
            schedule["schedule_id"],
            content_request="AIの仕組みを説明する",
        )

        self.assertEqual(updated["status"], "draft")
        self.assertIsNone(updated["prepared_stream_plan"])
        self.assertIsNone(updated["prepared_at"])

    def test_deleting_template_does_not_delete_existing_schedule(self):
        template = self.repository.create_template(
            name="通常配信",
            title="ガン奈の雑談配信",
            description="説明",
        )
        schedule = self.repository.create_schedule(
            scheduled_start_at="2026-08-20T21:00:00+09:00",
            planning_mode="ai",
            content_request="",
            title=template["title"],
            description=template["description"],
            template_id=template["template_id"],
        )

        self.repository.delete_template(template["template_id"])
        saved = self.repository.get_schedule(schedule["schedule_id"])

        self.assertIsNone(saved["template_id"])
        self.assertEqual(saved["title"], "ガン奈の雑談配信")

    def test_naive_scheduled_datetime_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "タイムゾーン"):
            self.repository.create_schedule(
                scheduled_start_at="2026-08-20T21:00:00",
                planning_mode="ai",
                content_request="",
                title="雑談配信",
                description="説明",
            )


if __name__ == "__main__":
    unittest.main()
