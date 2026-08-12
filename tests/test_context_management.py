import tempfile
import unittest
from pathlib import Path

from character import CHARACTER_PROMPT
from llm.config import load_llm_config
from llm.context_builder import ContextBuilder, estimate_tokens
from llm.conversation import ConversationState
from llm.memory import SQLiteMemoryRepository


class ContextManagementTest(unittest.TestCase):
    def test_actual_autonomous_input_fits_configured_budget(self):
        conversation = ConversationState(8, 700)
        builder = ContextBuilder(
            CHARACTER_PROMPT,
            load_llm_config(),
            conversation,
            memory_repository=None,
        )
        current_input = (
            "視聴者コメントへの返答ではなく、現在の状況に合う独り言を話してください。\n"
            "視聴者がいると決めつけず、呼びかけ、同意の要求、質問はしないでください。\n"
            "直近の発言と話題、結論、導入表現が重ならない、自然な2〜4文にしてください。\n"
            "聞いた人に知識や教訓を残そうとせず、どうでもいい一点への妙な執着や、"
            "半歩ずれた仮説を優先してください。\n"
            "無理にオチを付けず、真顔で少し変なことを言って終えてください。\n\n"
            "[配信テーマ]\n"
            "メインテーマ: インターネットと集中力\n"
            "中心となる問い: 集中力はどこまで環境に左右されるのか\n"
            "現在の論点: 通知を切っても気になってしまう心理について考える\n"
            "一時的な脱線: なし\n"
            "すでに扱った論点:\n"
            "- ブラウザのタブを開きすぎると意識まで分割された気分になる\n"
            "- 通知音が鳴っていないのにスマートフォンを確認してしまう\n"
            "- 集中しようと決めた瞬間に机の汚れが気になり始める\n"
            "今回の展開方法: メインテーマの周辺にある、どうでもいい一点へ寄り道する。\n"
            "テーマを言い直さず、前の発言から話を続ける。\n"
            "同じ結論を繰り返さない。脱線を毎回戻す必要はない。\n\n"
            "今回の話題方針: 身近な習慣から妙な分類を一つだけ話す\n"
            "現在の状況: ライブ配信中。コメントが来ていない静かな時間"
        )

        prompt = builder.build(current_input, include_memories=False)

        configured_budget = load_llm_config()["context"]["total_token_budget"]
        self.assertLessEqual(
            estimate_tokens(CHARACTER_PROMPT) + estimate_tokens(prompt),
            configured_budget,
        )

    def test_live_theme_input_keeps_room_for_conversation_history(self):
        config = load_llm_config()
        conversation = ConversationState(8, 700)
        for index in range(4):
            conversation.add("assistant", f"直近の発言{index}: " + "話題を続ける" * 10)
        builder = ContextBuilder(
            CHARACTER_PROMPT,
            config,
            conversation,
            memory_repository=None,
        )
        current_input = (
            "[配信の会話の流れ]\n"
            "メインテーマ: 人が先延ばしをする理由\n"
            "中心となる問い: 明日の自分へ渡す判断はなぜ増えるのか\n"
            "直前のガン奈の発言: 野菜室は明日の自分への先送り棚だね。\n"
            "今回の展開方法: 現在の枝を一段だけ進める\n\n"
            "[ガン奈の記録済みエピソード]\n"
            "- 野菜室を食材の待機画面だと思っていた時期がある。\n\n"
            "今回の話題方針: 直前の発話をもう一段だけ掘る\n"
            "現在の状況: コメントが途切れているため独り言を続ける"
        )

        prompt = builder.build(current_input, include_memories=False)

        self.assertIn("ガン奈: 直近の発言3", prompt)
        self.assertLessEqual(
            estimate_tokens(CHARACTER_PROMPT) + estimate_tokens(prompt),
            config["context"]["total_token_budget"],
        )

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

    def test_budget_error_reports_token_breakdown(self):
        conversation = ConversationState(4, 200)
        builder = ContextBuilder(
            "固定人格" * 100,
            {
                "context": {
                    "total_token_budget": 100,
                    "recent_conversation_token_budget": 20,
                }
            },
            conversation,
            memory_repository=None,
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "system_prompt_tokens=.*current_input_tokens=.*total_tokens=.*budget=100",
        ):
            builder.build("今回の入力")


if __name__ == "__main__":
    unittest.main()
