import unittest
from unittest.mock import Mock, patch

from llm.context_builder import estimate_tokens
from stream_theme import (
    generate_stream_theme_plan,
    StreamSegmentPlan,
    StreamThemeManager,
    StreamThemePlan,
)


def create_plan(
    theme="休日の過ごし方",
    prefix="休日",
    news_policy="general",
    news_query=None,
):
    return StreamThemePlan(
        theme=theme,
        core_question=f"{theme}を身近な体験からどう話すか",
        opening_angle=f"{theme}の分かりやすい入口から始める",
        opening_greeting=f"こんばんは。今日は{theme}について、のんびり話してみる。",
        segments=[
            StreamSegmentPlan(
                title=f"{prefix}の入口",
                talking_points=["朝に最初にすること", "予定を決める時の癖"],
                tangent_ideas=["寝坊した日の話"],
                target_utterances=3,
            ),
            StreamSegmentPlan(
                title=f"{prefix}の失敗",
                talking_points=["準備しすぎた失敗", "何もしなかった失敗"],
                tangent_ideas=["買い物へ出た時の話"],
                target_utterances=3,
            ),
            StreamSegmentPlan(
                title=f"{prefix}の楽しみ",
                talking_points=["最近試したこと", "次にやってみたいこと"],
                tangent_ideas=["趣味との接点"],
                target_utterances=3,
            ),
        ],
        closing_direction="印象に残った話を一つ振り返って終える",
        youtube_title=f"{theme}を話すAI VTuber雑談配信",
        youtube_description=(
            f"AI VTuberの才羽ガン奈が、{theme}について雑談します。"
            "コメントにも反応しながら進めます。"
        ),
        news_policy=news_policy,
        news_query=news_query,
    )


class StreamThemeManagerTest(unittest.TestCase):
    def test_manual_theme_creates_compatible_program(self):
        manager = StreamThemeManager({}, manual_theme="AI時代の人間の仕事")

        context = manager.build_context("observation")

        self.assertIn("配信企画: AI時代の人間の仕事", context)
        self.assertIn("現在の区間: 1/3", context)
        self.assertIn("配信企画と最初の話題が分かる", context)

    def test_program_instruction_is_passed_to_generator(self):
        generator = Mock(
            return_value=create_plan(
                theme="自己紹介配信",
                news_policy="off",
            )
        )

        manager = StreamThemeManager(
            {},
            program_instruction="初見向けの自己紹介配信",
            plan_generator=generator,
        )

        generator.assert_called_once_with(None, "初見向けの自己紹介配信")
        self.assertEqual(manager.state.main_theme, "自己紹介配信")
        self.assertEqual(manager.news_policy, "off")

    def test_prepared_plan_is_reused_without_second_llm_call(self):
        generator = Mock()
        prepared_plan = create_plan(
            theme="自己紹介配信",
            news_policy="off",
        )

        manager = StreamThemeManager(
            {},
            program_instruction="初見向けの自己紹介配信",
            prepared_plan=prepared_plan,
            plan_generator=generator,
        )

        generator.assert_not_called()
        self.assertEqual(manager.state.main_theme, "自己紹介配信")
        self.assertEqual(manager.news_policy, "off")

    def test_specific_program_rejects_general_news(self):
        generator = Mock(
            return_value=create_plan(
                theme="自己紹介配信",
                news_policy="general",
            )
        )

        manager = StreamThemeManager(
            {},
            program_instruction="初見向けの自己紹介配信",
            plan_generator=generator,
        )

        self.assertEqual(manager.news_policy, "off")
        self.assertEqual(manager.news_query, "")
        self.assertIn("ニュース方針：使用しない", manager.describe())

    def test_related_news_keeps_search_query(self):
        generator = Mock(
            return_value=create_plan(
                theme="AI業界の話",
                news_policy="related",
                news_query="生成AI 最新発表",
            )
        )

        manager = StreamThemeManager(
            {},
            program_instruction="最近のAI業界について話す",
            plan_generator=generator,
        )

        self.assertEqual(manager.news_policy, "related")
        self.assertEqual(manager.news_query, "生成AI 最新発表")
        self.assertIn("関連ニュースのみ", manager.describe())

    def test_comment_becomes_temporary_tangent_without_advancing_segment(self):
        manager = StreamThemeManager({}, manual_theme="仕事の話")

        manager.record_comment_exchange(
            "麻雀は仕事に役立つ？",
            "役割分担を見る練習にはなるかもね。",
        )
        context = manager.build_context("trivia")

        self.assertIn("コメントからの枝分かれ: 麻雀は仕事に役立つ？", context)
        self.assertIn("直前の発言: 役割分担を見る練習にはなるかもね。", context)
        self.assertEqual(manager.state.segment_utterance_count, 0)

    def test_segment_advances_after_target_utterances(self):
        manager = StreamThemeManager(
            {},
            plan_generator=Mock(return_value=create_plan()),
        )

        for index in range(3):
            manager.record_autonomous_speech(f"発話{index}", "observation")

        context = manager.build_context("observation")
        self.assertEqual(manager.state.segment_index, 1)
        self.assertIn("現在の区間: 2/3 休日の失敗", context)
        self.assertIn("前の区間『休日の入口』から接点", context)

    def test_finished_program_is_replaced_on_next_generation(self):
        generator = Mock(
            side_effect=[
                create_plan(theme="休日の過ごし方", prefix="休日"),
                create_plan(theme="好きな食べ物", prefix="食事"),
            ]
        )
        manager = StreamThemeManager({}, plan_generator=generator)
        for index in range(9):
            manager.record_autonomous_speech(f"発話{index}", "observation")

        context = manager.build_context("observation")

        self.assertEqual(manager.state.main_theme, "好きな食べ物")
        self.assertIn("配信企画: 好きな食べ物", context)
        self.assertEqual(generator.call_count, 2)

    def test_every_fifth_utterance_reintroduces_current_subject(self):
        manager = StreamThemeManager(
            {},
            plan_generator=Mock(return_value=create_plan()),
        )
        manager.state.utterance_count = 5
        manager.state.segment_utterance_count = 1

        context = manager.build_context("observation")

        self.assertIn("途中参加者向けに", context)
        self.assertIn("現在の対象を一言示して", context)

    def test_program_can_be_replaced_from_admin_instruction(self):
        generator = Mock(
            side_effect=[create_plan(), create_plan(theme="自己紹介配信", prefix="自分")]
        )
        manager = StreamThemeManager({}, plan_generator=generator)

        description = manager.replace_program("自己紹介配信をして")

        self.assertIn("配信企画：自己紹介配信", description)
        self.assertEqual(
            generator.call_args.args[1],
            "自己紹介配信をして",
        )

    def test_theme_context_stays_bounded_after_many_utterances(self):
        manager = StreamThemeManager({}, manual_theme="AI時代の人間の仕事")
        for index in range(20):
            manager.record_autonomous_speech(
                f"論点{index}: " + "長い説明" * 30,
                "observation",
            )

        context = manager.build_context("observation")

        self.assertLess(estimate_tokens(context), 550)
        self.assertNotIn("論点0:", context)
        self.assertIn("論点19:", context)

    @patch("stream_theme.create_llm_client")
    def test_plan_prompt_requires_real_world_topics(self, client_factory):
        client = client_factory.return_value
        client.generate_structured.return_value = create_plan()

        generate_stream_theme_plan(instruction="AIにおまかせ")

        prompt = client.generate_structured.call_args.kwargs["input_text"]
        self.assertIn("VTuber、アニメ、ゲーム", prompt)
        self.assertIn("一般的な日常あるあるを番組の主題にしない", prompt)
        self.assertIn("一つの実在する話題を3〜5発話", prompt)
        self.assertIn("『人はなぜ』で始まる学術的な題名を避け", prompt)
        self.assertIn("開始挨拶と3〜5個の話題区間", prompt)
        self.assertIn("news_policy", prompt)
        self.assertIn("youtube_title", prompt)
        self.assertIn("youtube_description", prompt)
        self.assertIn("日時は関連する場合だけ言及する", prompt)
        self.assertEqual(
            client.generate_structured.call_args.kwargs["max_output_tokens"],
            1500,
        )


if __name__ == "__main__":
    unittest.main()
