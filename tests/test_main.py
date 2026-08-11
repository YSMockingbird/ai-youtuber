import unittest
from unittest.mock import Mock, patch

from main import (
    generate_and_deliver_ai_response,
    generate_and_deliver_news_commentary,
    get_autonomous_speech_interval_seconds,
    get_mock_live_delay_seconds,
    run_mock_live,
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

    def test_mock_live_delay_accepts_command_line_override(self):
        self.assertEqual(get_mock_live_delay_seconds(0.5), 0.5)

    def test_too_long_mock_live_delay_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "0〜60秒"):
            get_mock_live_delay_seconds(61)

    @patch("main.time.sleep")
    @patch("main.get_unused_news_article")
    @patch("main.generate_and_deliver_news_commentary")
    @patch("main.generate_and_deliver_ai_response")
    @patch("main.deliver_autonomous_speech")
    @patch("main.start_external_control_server")
    def test_mock_live_runs_opening_comments_news_and_closing(
        self,
        start_server_mock,
        autonomous_mock,
        comment_mock,
        news_mock,
        article_mock,
        sleep_mock,
    ):
        server = Mock()
        start_server_mock.return_value = (server, "1.0", 101, 8765)
        autonomous_mock.side_effect = [
            ({"text": "始めようか。", "emotion": "happy"}, 1),
            ({"text": "またね。", "emotion": "relaxed"}, 1),
        ]
        comment_mock.return_value = (
            {"text": "コメントありがとう。", "emotion": "happy"},
            1,
        )
        article_mock.return_value = {
            "title": "新しい技術が発表",
            "source_name": "テストニュース",
            "published_at": "2026-08-11",
            "link": "https://example.com/news/1",
        }
        news_mock.return_value = (
            {"text": "少し気になるね。", "emotion": "surprised"},
            1,
        )

        run_mock_live(0)

        self.assertEqual(autonomous_mock.call_count, 2)
        self.assertEqual(comment_mock.call_count, 3)
        news_mock.assert_called_once_with(
            server.runtime,
            article_mock.return_value,
        )
        self.assertEqual(sleep_mock.call_count, 6)
        sleep_mock.assert_called_with(1)
        server.stop.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
