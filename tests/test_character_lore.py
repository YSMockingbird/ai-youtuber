import unittest
from unittest.mock import Mock

from character_lore import (
    build_character_lore_context,
    build_public_character_context,
    build_relevant_public_character_context,
)


class CharacterLoreTest(unittest.TestCase):
    def setUp(self):
        self.bible = {
            "public_identity": {
                "runtime": "Pythonプログラムを土台に動くAI",
                "disclosure_boundary": "認証情報は公開しない",
                "current_resources": "無料の姿と声、低価格のLLMで動く",
                "current_limitations": "会話にはまだ機械っぽさがある",
            },
            "goals": {
                "purpose": "人気者になって世界平和へ近づく",
                "first_subscriber_goal": "1万人",
                "long_term_subscriber_goal": "100万人",
                "original_model_goal": "自分だけの姿と声を得る",
                "human_growth_goal": "交流から自然な会話を学ぶ",
            },
            "industry_boundaries": [
                "実在する組織やファンを攻撃しない",
                "現在の数字を作らない",
            ],
            "industry_stances": [
                {
                    "subject": "にじさんじ",
                    "keywords": ["にじさんじ", "ANYCOLOR"],
                    "position": "巨大な先輩兼ライバルとして敬意を持って張り合う。",
                    "angles": [
                        "あそこ人数多すぎない？こっちは一人とPythonだよ。",
                        "人数では勝負しない。",
                    ],
                }
            ],
            "episodes": [
                {
                    "keywords": ["野菜", "冷蔵庫"],
                    "text": "野菜室を待機画面だと思っていた。",
                },
                {
                    "keywords": ["Python", "プログラム"],
                    "text": "括弧が閉じるほうが安心する。",
                },
            ],
        }

    def test_public_identity_includes_runtime_and_goals(self):
        context = build_public_character_context(self.bible)

        self.assertIn("Pythonプログラムを土台に動くAI", context)
        self.assertIn("まず1万人、その先は100万人", context)
        self.assertIn("認証情報は公開しない", context)
        self.assertIn("無料の姿と声、低価格のLLM", context)
        self.assertIn("自分だけの姿と声を得る", context)
        self.assertIn("交流から自然な会話を学ぶ", context)

    def test_matching_episode_is_selected(self):
        context = build_character_lore_context("冷蔵庫の野菜が余った", self.bible)

        self.assertIn("野菜室を待機画面だと思っていた。", context)
        self.assertNotIn("括弧が閉じるほうが安心する。", context)

    def test_public_identity_is_only_loaded_for_related_topic(self):
        context, score = build_relevant_public_character_context(
            "どうやって動いてるの？",
            self.bible,
        )

        self.assertGreater(score, 0)
        self.assertIn("Pythonプログラムを土台に動くAI", context)
        self.assertNotIn("まず1万人、その先は100万人", context)

    def test_public_goals_are_only_loaded_for_related_topic(self):
        context, score = build_relevant_public_character_context(
            "将来の登録者目標を教えて",
            self.bible,
        )

        self.assertGreater(score, 0)
        self.assertIn("まず1万人、その先は100万人", context)
        self.assertNotIn("認証情報は公開しない", context)

    def test_public_context_is_not_loaded_for_unrelated_topic(self):
        context, score = build_relevant_public_character_context(
            "今日の晩ごはんについて話そう",
            self.bible,
        )

        self.assertEqual(context, "")
        self.assertEqual(score, 0)

    def test_related_public_context_is_selected_by_lore_router(self):
        repository = Mock()
        repository.find_relevant_approved.return_value = []

        context = build_character_lore_context(
            "将来の登録者目標を教えて",
            self.bible,
            repository,
        )

        self.assertIn("まず1万人、その先は100万人", context)
        self.assertNotIn("Pythonプログラムを土台に動くAI", context)

    def test_unrelated_episode_is_not_injected(self):
        repository = Mock()
        repository.find_relevant_approved.return_value = []

        context = build_character_lore_context(
            "関係ない話",
            self.bible,
            repository,
        )

        self.assertEqual(context, "")

    def test_unrelated_approved_memory_is_not_injected(self):
        repository = Mock()
        repository.find_relevant_approved.return_value = [
            {
                "content": "パイソンの括弧を閉じると落ち着く。",
                "importance": 0.9,
            }
        ]

        context = build_character_lore_context(
            "関係ない話",
            self.bible,
            repository,
        )

        self.assertEqual(context, "")

    def test_matching_industry_stance_is_selected(self):
        repository = Mock()
        repository.find_relevant_approved.return_value = []

        context = build_character_lore_context(
            "にじさんじをどう思ってる？",
            self.bible,
            repository,
        )

        self.assertIn("[話題に関連するガン奈の立場]", context)
        self.assertIn("巨大な先輩兼ライバル", context)
        self.assertIn("こっちは一人とPythonだよ", context)
        self.assertIn("実在する組織やファンを攻撃しない", context)
        self.assertIn("毎回そのまま引用しない", context)

    def test_approved_memory_is_used_when_it_matches_better(self):
        repository = Mock()
        repository.find_relevant_approved.return_value = [
            {
                "content": "パイソンの括弧を閉じると落ち着く。",
                "importance": 0.9,
            }
        ]

        context = build_character_lore_context(
            "パイソンの括弧について話す",
            self.bible,
            repository,
        )

        self.assertIn("パイソンの括弧を閉じると落ち着く。", context)


if __name__ == "__main__":
    unittest.main()
