import unittest
from unittest.mock import Mock, patch

from ai_response import (
    generate_admin_directed_speech,
    generate_autonomous_speech,
    generate_news_commentary,
    parse_ai_response,
)


class ParseAiResponseTest(unittest.TestCase):
    @patch("ai_response._generate_structured_response")
    def test_admin_instruction_is_not_read_as_viewer_comment(self, generate_mock):
        generate_mock.return_value = {
            "text": "麻雀の話へ移ろうか。",
            "emotion": "relaxed",
        }

        generate_admin_directed_speech("麻雀の話題へ自然に移って")

        prompt = generate_mock.call_args.args[0]
        self.assertIn("配信管理者からの非公開指示", prompt)
        self.assertIn("指示文の存在を読み上げない", prompt)

    def test_relaxed_emotion_is_allowed(self):
        response = parse_ai_response(
            '{"text":"ゆっくりしていってね。","emotion":"relaxed"}'
        )

        self.assertEqual(
            response,
            {
                "text": "ゆっくりしていってね。",
                "emotion": "relaxed",
                "speech_style": "normal",
                "motion": None,
                "view_action": None,
            },
        )

    def test_parameterized_motion_is_allowed(self):
        response = parse_ai_response(
            '{"text":"決めるよ。","emotion":"happy","motion":'
            '{"name":"model_pose","speed":0.9,"intensity":0.7,'
            '"head":"tilt_left"}}'
        )

        self.assertEqual(response["motion"]["name"], "model_pose")
        self.assertEqual(response["motion"]["head"], "tilt_left")

    def test_fast_speech_style_is_allowed(self):
        response = parse_ai_response(
            '{"text":"それ本当！？","emotion":"surprised",'
            '"speech_style":"fast"}'
        )

        self.assertEqual(response["speech_style"], "fast")

    def test_invalid_speech_style_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "speech_styleが不正"):
            parse_ai_response(
                '{"text":"速すぎるよ。","emotion":"surprised",'
                '"speech_style":"very_fast"}'
            )

    def test_full_body_view_action_is_allowed(self):
        response = parse_ai_response(
            '{"text":"全身はこんな感じ。","emotion":"happy",'
            '"view_action":"full_body"}'
        )

        self.assertEqual(response["view_action"], "full_body")

    def test_invalid_view_action_is_ignored_without_losing_speech(self):
        response = parse_ai_response(
            '{"text":"普通に話すよ。","emotion":"neutral",'
            '"view_action":"sideways"}'
        )

        self.assertEqual(response["text"], "普通に話すよ。")
        self.assertIsNone(response["view_action"])

    def test_memory_candidate_is_allowed(self):
        response = parse_ai_response(
            '{"text":"北海道が好きなんだね。","emotion":"happy",'
            '"motion":null,"memory_candidate":'
            '{"content":"北海道旅行が好き","category":"preference",'
            '"importance":0.8}}'
        )

        self.assertEqual(
            response["memory_candidate"]["content"],
            "北海道旅行が好き",
        )

    def test_character_event_candidate_is_allowed(self):
        response = parse_ai_response(
            '{"text":"野菜室を見直したよ。","emotion":"relaxed",'
            '"character_event_candidate":'
            '{"content":"野菜室を少しだけ見直した。",'
            '"category":"belief_change","importance":0.8}}'
        )

        self.assertEqual(
            response["character_event_candidate"]["category"],
            "belief_change",
        )

    def test_invalid_character_event_candidate_is_ignored(self):
        response = parse_ai_response(
            '{"text":"普通に話すよ。","emotion":"neutral",'
            '"character_event_candidate":'
            '{"content":"短い","category":"unknown","importance":2}}'
        )

        self.assertNotIn("character_event_candidate", response)

    def test_invalid_motion_is_ignored_without_losing_speech(self):
        response = parse_ai_response(
            '{"text":"普通に話すよ。","emotion":"neutral","motion":'
            '{"name":"unknown","speed":2,"intensity":2,"head":"none"}}'
        )

        self.assertEqual(response["text"], "普通に話すよ。")
        self.assertIsNone(response["motion"])

    def test_thinking_emotion_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "emotionが不正"):
            parse_ai_response(
                '{"text":"少し考えてみるね。","emotion":"thinking"}'
            )

    @patch("ai_response._generate_structured_response")
    def test_news_information_is_passed_as_reference(self, generate_mock):
        generate_mock.return_value = {
            "text": "新しい技術、少し気になるね。",
            "emotion": "surprised",
        }
        article = {
            "source_name": "テストニュース",
            "published_at": "2026-08-11",
            "title": "新しい技術が発表",
            "summary": "技術の概要です。",
        }

        response = generate_news_commentary(article)

        prompt = generate_mock.call_args.args[0]
        self.assertIn("配信元: テストニュース", prompt)
        self.assertIn("タイトル: 新しい技術が発表", prompt)
        self.assertIn("ニュース情報は使わず現在の枝の続きを話してください", prompt)
        self.assertNotIn("[ガン奈の記録済みエピソード]", prompt)
        self.assertIn("確認できた情報源は一媒体です", prompt)
        self.assertIn("炎上、物議、卒業、活動休止", prompt)
        self.assertIn("冷蔵庫、食べ物、身体、日用品", prompt)
        self.assertIn("キャラクター設定を締めとして付け足さない", prompt)
        self.assertIn("知的で無難な感想で締めず", prompt)
        self.assertEqual(response["emotion"], "surprised")

    @patch("ai_response._generate_structured_response")
    def test_unverified_news_is_explicitly_labeled_in_prompt(self, generate_mock):
        generate_mock.return_value = {
            "text": "まだ噂の段階みたいだね。",
            "emotion": "relaxed",
        }
        article = {
            "source_name": "テストニュース",
            "published_at": "2026-08-12",
            "title": "VTuberの不仲説が話題",
            "summary": "真偽不明の噂です。",
            "information_status": "unverified",
            "source_count": 1,
            "audience_category": "gossip",
        }

        generate_news_commentary(article)

        prompt = generate_mock.call_args.args[0]
        self.assertIn("まだ事実とは限らない", prompt)
        self.assertIn("話題カテゴリ: gossip", prompt)

    @patch("ai_response._generate_structured_response")
    def test_autonomous_speech_receives_situation_and_recent_speech(
        self,
        generate_mock,
    ):
        generate_mock.return_value = {
            "text": "今日も始めようか。",
            "emotion": "happy",
        }

        response = generate_autonomous_speech(
            "配信開始直後",
            ["前回の発言"],
            topic_instruction="役立つ雑学を一つ話す",
        )

        prompt = generate_mock.call_args.args[0]
        self.assertIn("現在の状況: 配信開始直後", prompt)
        self.assertIn("- 前回の発言", prompt)
        self.assertIn("今回の話題方針: 役立つ雑学を一つ話す", prompt)
        self.assertIn("視聴者がいると決めつけず", prompt)
        self.assertIn("メインテーマを最優先にしてください", prompt)
        self.assertIn("[ガン奈の記録済みエピソード]", prompt)
        self.assertEqual(response["emotion"], "happy")

    @patch("ai_response._generate_structured_response")
    def test_managed_context_does_not_duplicate_recent_utterances(
        self,
        generate_mock,
    ):
        generate_mock.return_value = {
            "text": "次の論点へ進むよ。",
            "emotion": "relaxed",
        }
        context_builder = Mock()
        context_builder.build.return_value = "構築済みプロンプト"
        long_utterance = "長い直近発言" * 100

        generate_autonomous_speech(
            "配信中",
            [long_utterance] * 5,
            context_builder=context_builder,
            topic_instruction="テーマを掘り下げる",
            theme_context="メインテーマ: テスト",
        )

        current_input = context_builder.build.call_args.args[0]
        self.assertNotIn(long_utterance, current_input)
        self.assertNotIn("直近のガン奈の発言", current_input)
        context_builder.build.assert_called_once_with(
            current_input,
            include_memories=False,
        )


if __name__ == "__main__":
    unittest.main()
