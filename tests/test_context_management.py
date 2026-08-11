import tempfile
import unittest
from pathlib import Path

from llm.context_builder import ContextBuilder, estimate_tokens
from llm.conversation import ConversationState
from llm.memory import SQLiteMemoryRepository


class ContextManagementTest(unittest.TestCase):
    def test_old_messages_are_moved_to_bounded_summary(self):
        conversation = ConversationState(
            recent_message_count=2,
            summary_max_characters=100,
        )
        conversation.add("user", "最初のコメント", user_name="視聴者")
        conversation.add("assistant", "最初の返答")
        conversation.add("user", "新しいコメント", user_name="別の視聴者")

        self.assertEqual(len(conversation.messages), 2)
        self.assertIn("視聴者: 最初のコメント", conversation.summary)
        self.assertLessEqual(len(conversation.summary), 100)

    def test_context_contains_only_relevant_user_memory(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = SQLiteMemoryRepository(
                Path(temporary_directory) / "memory.db"
            )
            repository.save("user-a", "A", "北海道旅行が好き", "preference", 0.8)
            repository.save("user-b", "B", "麻雀が好き", "preference", 1.0)
            conversation = ConversationState(4, 200)
            conversation.add("assistant", "旅行の話をしていたね")
            config = {
                "context": {
                    "total_token_budget": 500,
                    "recent_conversation_token_budget": 100,
                    "relevant_memory_token_budget": 100,
                    "stream_summary_token_budget": 100,
                    "relevant_memory_count": 3,
                }
            }
            builder = ContextBuilder(
                "短い人格設定",
                config,
                conversation,
                repository,
            )

            prompt = builder.build("北海道へまた行きたい", user_id="user-a")

            self.assertIn("北海道旅行が好き", prompt)
            self.assertNotIn("麻雀が好き", prompt)
            self.assertLessEqual(
                estimate_tokens("短い人格設定") + estimate_tokens(prompt),
                500,
            )

    def test_memory_is_upserted_without_duplicates(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = SQLiteMemoryRepository(
                Path(temporary_directory) / "memory.db"
            )
            repository.save("user-a", "A", "猫が好き", "preference", 0.7)
            repository.save("user-a", "A", "猫が好き", "preference", 0.9)

            memories = repository.find_relevant("user-a", "猫", 10)

            self.assertEqual(len(memories), 1)
            self.assertEqual(memories[0]["importance"], 0.9)


if __name__ == "__main__":
    unittest.main()
