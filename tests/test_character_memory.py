import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
