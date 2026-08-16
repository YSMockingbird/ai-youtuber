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
        manager.record_autonomous_speech("仕事では段取りを先に決めるよ。", "observation")
        covered_before_comment = list(manager.state.covered_points)

        manager.record_comment_exchange(
            "資格勉強は仕事に役立つ？",
            "役割分担を見る練習にはなるかもね。",
        )
        context = manager.build_context("trivia")

        self.assertIn("現在の一時的な脱線: 視聴者コメントへの返信は完了", context)
        self.assertIn("直前の発言: 視聴者コメントへの返信（完了）", context)
        self.assertNotIn("資格勉強は仕事に役立つ？", context)
        self.assertNotIn("役割分担を見る練習にはなるかもね。", context)
        self.assertIn("コメントへの返答は直前の発言で完了", context)
        self.assertEqual(manager.state.covered_points, covered_before_comment)
        self.assertEqual(manager.state.segment_utterance_count, 1)

        manager.record_autonomous_speech(
            "仕事の段取りでは、最初に期限だけ確認するよ。",
            "observation",
        )
        resumed_context = manager.build_context("observation")

        self.assertNotIn("コメントからの枝分かれ", resumed_context)
        self.assertNotIn("コメントへの返答は直前の発言で完了", resumed_context)

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

    def test_each_utterance_receives_only_one_material(self):
        manager = StreamThemeManager(
            {},
            plan_generator=Mock(return_value=create_plan()),
        )

        first_context = manager.build_context("observation")
        self.assertIn("今回必ず扱う中心材料: 朝に最初にすること", first_context)
        self.assertNotIn("予定を決める時の癖", first_context)

        manager.record_autonomous_speech("最初の発話", "observation")
        second_context = manager.build_context("observation")
        self.assertIn("今回必ず扱う中心材料: 予定を決める時の癖", second_context)
        self.assertNotIn("朝に最初にすること", second_context)

        manager.record_autonomous_speech("二番目の発話", "observation")
        third_context = manager.build_context("observation")
        self.assertIn("今回必ず扱う中心材料: 寝坊した日の話", third_context)
        self.assertNotIn("予定を決める時の癖", third_context)

    def test_delivery_style_changes_between_utterances(self):
        manager = StreamThemeManager(
            {},
            plan_generator=Mock(return_value=create_plan()),
        )

        first_context = manager.build_context("observation")
        manager.record_autonomous_speech("最初の発話", "observation")
        second_context = manager.build_context("observation")

        self.assertIn("今回の話し方: 具体的な場面", first_context)
        self.assertIn("今回の話し方: 率直な好み", second_context)

    def test_last_utterance_prepares_transition_without_summary(self):
        manager = StreamThemeManager(
            {},
            plan_generator=Mock(return_value=create_plan()),
        )
        manager.state.segment_utterance_count = 2
        manager.state.utterance_count = 2

        context = manager.build_context("observation")

        self.assertIn("今回の話し方: 次の区間へのつなぎ", context)
        self.assertIn("要約や箇条書きをせず", context)

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

        self.assertLess(estimate_tokens(context), 700)
        self.assertNotIn("論点0:", context)
        self.assertIn("論点19:", context)

    def test_recent_small_jokes_are_marked_as_not_reusable(self):
        manager = StreamThemeManager({}, manual_theme="自己紹介")
        manager.record_autonomous_speech(
            "配信前に設定画面の保存ボタンを三回確認した。",
            "observation",
        )
        manager.record_autonomous_speech("次の発話", "observation")

        context = manager.build_context("observation")

        self.assertIn("再利用しない直近の具体例・論点", context)
        self.assertIn("設定画面の保存ボタン", context)
        self.assertIn("論点、結論を再登場させず", context)

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
        self.assertNotIn("youtube_title", prompt)
        self.assertNotIn("youtube_description", prompt)
        self.assertIn("自己紹介の場合、talking_points", prompt)
        self.assertIn("現時点では固定の好きなものがなく", prompt)
        self.assertIn("登録者とオリジナルモデルの目標", prompt)
        self.assertIn("世界平和へ近づく活動目的", prompt)
        self.assertIn("日時は関連する場合だけ言及する", prompt)
        self.assertEqual(
            client.generate_structured.call_args.kwargs["max_output_tokens"],
            2200,
        )


if __name__ == "__main__":
    unittest.main()
