import unittest
from unittest.mock import patch

from ai_response import (
    generate_autonomous_speech,
    generate_news_commentary,
    parse_ai_response,
)


class ParseAiResponseTest(unittest.TestCase):
    def test_relaxed_emotion_is_allowed(self):
        response = parse_ai_response(
            '{"text":"ゆっくりしていってね。","emotion":"relaxed"}'
        )

        self.assertEqual(
            response,
            {
                "text": "ゆっくりしていってね。",
                "emotion": "relaxed",
                "motion": None,
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
        self.assertEqual(response["emotion"], "surprised")

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
        self.assertEqual(response["emotion"], "happy")


if __name__ == "__main__":
    unittest.main()
