import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from character_memory import CharacterMemoryRepository


class CharacterMemoryRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.repository = CharacterMemoryRepository(
            Path(self.temporary_directory.name) / "character_memory.db"
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_draft_is_not_used_until_approved(self):
        self.repository.save_draft(
            "野菜室を明日の自分への先送り棚だと呼ぶようになった。",
            "belief_change",
            0.8,
            "autonomous_speech",
        )
        draft = self.repository.list("draft")[0]

        self.assertEqual(self.repository.find_relevant_approved("野菜室", 1), [])

        self.repository.review(draft["memory_id"], "approved")
        approved = self.repository.find_relevant_approved("野菜室", 1)

        self.assertEqual(len(approved), 1)
        self.assertEqual(approved[0]["status"], "approved")
        self.assertEqual(approved[0]["category"], "belief_change")

    def test_rejected_draft_cannot_be_approved_again(self):
        self.repository.save_draft(
            "コメント欄を小さな廊下だと思った。",
            "episode",
            0.7,
            "comment_reply",
        )
        draft = self.repository.list("draft")[0]

        self.repository.review(draft["memory_id"], "rejected")

        with self.assertRaisesRegex(RuntimeError, "下書き状態"):
            self.repository.review(draft["memory_id"], "approved")

    def test_sensitive_content_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "保存できない情報"):
            self.repository.save_draft(
                "視聴者の電話番号を覚えた。",
                "relationship",
                0.9,
                "comment_reply",
            )

    def test_drafts_are_limited_without_deleting_approved_memory(self):
        current_time = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
        repository = CharacterMemoryRepository(
            Path(self.temporary_directory.name) / "limited.db",
            max_drafts=2,
            now=lambda: current_time[0],
        )
        repository.save_draft(
            "承認して残す大切な記憶。", "episode", 0.7, "comment_reply"
        )
        approved_id = repository.list("draft")[0]["memory_id"]
        repository.review(approved_id, "approved")
        for index, importance in enumerate((0.6, 0.9, 0.8)):
            current_time[0] += timedelta(seconds=1)
            repository.save_draft(
                f"配信で得た新しい気付き{index}。",
                "belief_change",
                importance,
                "autonomous_speech",
            )

        self.assertEqual(len(repository.list("draft")), 2)
        self.assertEqual(len(repository.list("approved")), 1)
        self.assertEqual(repository.list("approved")[0]["memory_id"], approved_id)

    def test_rejected_memory_expires_but_approved_memory_does_not(self):
        current_time = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
        repository = CharacterMemoryRepository(
            Path(self.temporary_directory.name) / "retention.db",
            rejected_retention_days=30,
            now=lambda: current_time[0],
        )
        repository.save_draft(
            "長く残す承認済みの記憶。", "episode", 0.8, "comment_reply"
        )
        approved_id = repository.list("draft")[0]["memory_id"]
        repository.review(approved_id, "approved")
        repository.save_draft(
            "後で消す却下済みの記憶。", "episode", 0.7, "comment_reply"
        )
        rejected_id = repository.list("draft")[0]["memory_id"]
        repository.review(rejected_id, "rejected")
        current_time[0] += timedelta(days=31)

        self.assertEqual(repository.list("rejected"), [])
        self.assertEqual(len(repository.list("approved")), 1)


if __name__ == "__main__":
    unittest.main()
