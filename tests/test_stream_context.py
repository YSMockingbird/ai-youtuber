import unittest
from unittest.mock import Mock

from llm.session import StreamContextManager


class FakeMemoryRepository:
    def __init__(self):
        self.saved = []

    def save(self, **kwargs):
        self.saved.append(kwargs)

    def find_relevant(self, user_id, query, limit):
        return []


class StreamContextManagerTest(unittest.TestCase):
    def setUp(self):
        self.repository = FakeMemoryRepository()
        self.manager = StreamContextManager(
            config={
                "context": {
                    "recent_message_count": 4,
                    "summary_max_characters": 200,
                    "total_token_budget": 2400,
                },
                "memory": {"minimum_importance": 0.65},
            },
            memory_repository=self.repository,
            character_memory_repository=Mock(),
        )

    def test_important_memory_candidate_is_saved(self):
        self.manager.record_comment_exchange(
            "channel-1",
            "視聴者",
            "北海道が好き",
            {
                "text": "北海道、いいね。",
                "memory_candidate": {
                    "content": "北海道旅行が好き",
                    "category": "preference",
                    "importance": 0.8,
                },
            },
        )

        self.assertEqual(len(self.repository.saved), 1)
        self.assertEqual(self.repository.saved[0]["user_id"], "channel-1")

    def test_character_event_candidate_is_saved_as_draft(self):
        self.manager.record_ai_speech(
            "野菜室を少し見直したよ。",
            {
                "content": "野菜室を少し見直した。",
                "category": "belief_change",
                "importance": 0.8,
            },
        )

        self.manager.character_memory_repository.save_draft.assert_called_once_with(
            content="野菜室を少し見直した。",
            category="belief_change",
            importance=0.8,
            source="autonomous_speech",
        )

    def test_sensitive_or_low_importance_memory_is_not_saved(self):
        for content, importance in (("住所は東京都", 0.9), ("猫を見た", 0.3)):
            self.manager.record_comment_exchange(
                "channel-1",
                "視聴者",
                content,
                {
                    "text": "そうなんだね。",
                    "memory_candidate": {
                        "content": content,
                        "category": "profile",
                        "importance": importance,
                    },
                },
            )

        self.assertEqual(self.repository.saved, [])


if __name__ == "__main__":
    unittest.main()
