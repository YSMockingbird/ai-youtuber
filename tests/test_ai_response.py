import unittest
from unittest.mock import patch

from ai_response import generate_news_commentary, parse_ai_response


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
            },
        )

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


if __name__ == "__main__":
    unittest.main()
