import unittest

from llm.context_builder import estimate_tokens
from stream_theme import (
    StreamThemeManager,
    StreamThemePlan,
)


class StreamThemeManagerTest(unittest.TestCase):
    def test_manual_theme_is_kept_and_included_in_context(self):
        manager = StreamThemeManager(
            {"stream_theme": {"review_utterance_count": 5}},
            manual_theme="AI時代の人間の仕事",
        )

        context = manager.build_context("observation")

        self.assertIn("メインテーマ: AI時代の人間の仕事", context)
        self.assertIn("現在の枝にある対象か違和感を一つ引き継ぎ", context)
        self.assertIn("今回は最初の一言", context)

    def test_comment_becomes_temporary_tangent(self):
        manager = StreamThemeManager(
            {"stream_theme": {"review_utterance_count": 5}},
            manual_theme="AI時代の人間の仕事",
        )

        manager.record_comment_exchange(
            "麻雀は仕事に役立つ？",
            "役割分担を見る練習にはなるかもね。",
        )
        context = manager.build_context("trivia")

        self.assertIn("コメントからの枝分かれ: 麻雀は仕事に役立つ？", context)
        self.assertIn("直前のガン奈の発言: 役割分担を見る練習にはなるかもね。", context)
        self.assertIn("接点がない雑学へ切り替えない", context)

    def test_autonomous_speech_becomes_the_required_next_thread(self):
        manager = StreamThemeManager(
            {"stream_theme": {"review_utterance_count": 5}},
            manual_theme="人が先延ばしをする理由",
        )

        manager.record_autonomous_speech(
            "野菜室は保存ではなく、明日の自分への先送り棚だね。",
            "observation",
        )
        context = manager.build_context("character_thought")

        self.assertEqual(
            manager.state.current_focus,
            "野菜室は保存ではなく、明日の自分への先送り棚だね。",
        )
        self.assertIn(
            "直前のガン奈の発言: 野菜室は保存ではなく、明日の自分への先送り棚だね。",
            context,
        )
        self.assertIn("無関係な名詞から新しい話を始めない", context)

    def test_automatic_theme_is_reviewed_after_configured_utterances(self):
        plans = iter(
            [
                StreamThemePlan(
                    theme="インターネットと集中力",
                    core_question="便利さは集中力を奪うのか",
                    opening_angle="短い動画から考える",
                ),
                StreamThemePlan(
                    theme="退屈が生む創造性",
                    core_question="退屈は本当に無駄なのか",
                    opening_angle="待ち時間から考える",
                ),
            ]
        )

        def plan_generator(previous_state=None):
            return next(plans)

        manager = StreamThemeManager(
            {"stream_theme": {"review_utterance_count": 5}},
            plan_generator=plan_generator,
        )
        for index in range(5):
            manager.record_autonomous_speech(
                f"論点{index}",
                "observation",
            )

        context = manager.build_context("observation")

        self.assertEqual(manager.state.main_theme, "退屈が生む創造性")
        self.assertIn("メインテーマ: 退屈が生む創造性", context)
        self.assertEqual(manager.state.utterances_since_review, 0)

    def test_invalid_review_count_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "5〜30"):
            StreamThemeManager(
                {"stream_theme": {"review_utterance_count": 3}},
                manual_theme="テスト用の配信テーマ",
            )

    def test_theme_context_stays_bounded_after_many_utterances(self):
        manager = StreamThemeManager(
            {"stream_theme": {"review_utterance_count": 30}},
            manual_theme="AI時代の人間の仕事",
        )
        for index in range(20):
            manager.record_autonomous_speech(
                f"論点{index}: " + "長い説明" * 30,
                "observation",
            )

        context = manager.build_context("observation")

        self.assertLess(estimate_tokens(context), 550)
        self.assertNotIn("論点0:", context)
        self.assertIn("論点19:", context)


if __name__ == "__main__":
    unittest.main()
