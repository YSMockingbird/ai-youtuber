import unittest
from unittest.mock import Mock, patch

from main import (
    generate_and_deliver_ai_response,
    generate_and_deliver_news_commentary,
    get_autonomous_speech_interval_seconds,
)


class GenerateAndDeliverAiResponseTest(unittest.TestCase):
    @patch("main.generate_ai_response")
    def test_openai_response_is_delivered_to_external_control(self, generate_mock):
        generate_mock.return_value = {
            "text": "今日は調子がいいよ。",
            "emotion": "happy",
        }
        runtime = Mock()
        runtime.speak.return_value = ({"type": "speak"}, 1)

        response, delivered_count = generate_and_deliver_ai_response(
            runtime,
            "テストユーザー",
            "今日の調子はどう？",
        )

        generate_mock.assert_called_once_with(
            "テストユーザー",
            "今日の調子はどう？",
        )
        runtime.speak.assert_called_once_with("今日は調子がいいよ。", "happy")
        self.assertEqual(response["emotion"], "happy")
        self.assertEqual(delivered_count, 1)

    @patch("main.generate_news_commentary")
    def test_news_commentary_is_delivered_to_external_control(self, generate_mock):
        generate_mock.return_value = {
            "text": "新しい技術、少し気になるね。",
            "emotion": "surprised",
        }
        runtime = Mock()
        runtime.speak.return_value = ({"type": "speak"}, 1)

        response, delivered_count = generate_and_deliver_news_commentary(
            runtime,
            {"title": "新しい技術が発表"},
        )

        runtime.speak.assert_called_once_with(
            "新しい技術、少し気になるね。",
            "surprised",
        )
        self.assertEqual(response["emotion"], "surprised")
        self.assertEqual(delivered_count, 1)

    @patch.dict(
        "os.environ",
        {"AUTONOMOUS_SPEECH_INTERVAL_SECONDS": "300"},
    )
    def test_autonomous_speech_interval_is_read_from_environment(self):
        self.assertEqual(get_autonomous_speech_interval_seconds(), 300)

    @patch.dict(
        "os.environ",
        {"AUTONOMOUS_SPEECH_INTERVAL_SECONDS": "10"},
    )
    def test_too_short_autonomous_speech_interval_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "60〜3600秒"):
            get_autonomous_speech_interval_seconds()


if __name__ == "__main__":
    unittest.main()
