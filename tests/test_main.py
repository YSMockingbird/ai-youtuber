import unittest
from unittest.mock import Mock, call, patch

from main import (
    generate_and_deliver_ai_response,
    generate_and_deliver_news_commentary,
    get_autonomous_speech_interval_seconds,
    get_mock_live_delay_seconds,
    process_next_admin_command,
    run_ai_youtuber_loop,
    run_mock_live,
)


class GenerateAndDeliverAiResponseTest(unittest.TestCase):
    def test_admin_pause_command_pauses_only_autonomous_buffer(self):
        runtime = Mock()
        runtime.get_next_admin_command.return_value = {
            "action": "pause_autonomous",
        }
        autonomous_buffer = Mock()
        stream_context = Mock()

        handled = process_next_admin_command(
            runtime,
            autonomous_buffer,
            stream_context,
        )

        self.assertTrue(handled)
        autonomous_buffer.pause.assert_called_once_with()
        runtime.update_admin_status.assert_called_once_with(
            autonomous_paused=True,
            phase="paused",
            message="自発発話を一時停止しました。コメント返信は継続します。",
        )

    def test_admin_direct_speech_bypasses_llm_and_reschedules(
        self,
    ):
        runtime = Mock()
        runtime.get_next_admin_command.return_value = {
            "action": "direct_speech",
            "text": "そのまま読む文章",
            "emotion": "relaxed",
            "speech_style": "normal",
            "motion": None,
        }
        runtime.speak.return_value = (
            {"duration_ms": 1800},
            1,
        )
        autonomous_buffer = Mock(paused=False)
        stream_context = Mock()

        handled = process_next_admin_command(
            runtime,
            autonomous_buffer,
            stream_context,
        )

        self.assertTrue(handled)
        autonomous_buffer.cancel_next.assert_called_once_with()
        autonomous_buffer.schedule_after_external_speech.assert_called_once_with(
            1800
        )
        runtime.speak.assert_called_once_with(
            "そのまま読む文章",
            "relaxed",
            None,
            None,
            "normal",
        )

    def test_admin_direct_speech_accepts_string_motion(self):
        runtime = Mock()
        runtime.get_next_admin_command.return_value = {
            "action": "direct_speech",
            "text": "手を振るよ。",
            "emotion": "happy",
            "speech_style": "normal",
            "motion": "greeting",
        }
        runtime.speak.return_value = (
            {"duration_ms": 1500},
            1,
        )
        autonomous_buffer = Mock(paused=False)

        handled = process_next_admin_command(
            runtime,
            autonomous_buffer,
            Mock(),
        )

        self.assertTrue(handled)
        runtime.speak.assert_called_once_with(
            "手を振るよ。",
            "happy",
            "greeting",
            None,
            "normal",
        )

    @patch("main.deliver_generated_response_with_command")
    @patch("main.generate_admin_directed_speech")
    def test_closing_greeting_pauses_autonomous_speech(
        self,
        generate_mock,
        deliver_mock,
    ):
        runtime = Mock()
        runtime.get_next_admin_command.return_value = {
            "action": "closing_greeting",
        }
        generate_mock.return_value = {
            "text": "今日はここまで。またね。",
            "emotion": "relaxed",
            "motion": None,
        }
        deliver_mock.return_value = (
            generate_mock.return_value,
            1,
            {"duration_ms": 2000},
        )
        autonomous_buffer = Mock(paused=False)

        handled = process_next_admin_command(
            runtime,
            autonomous_buffer,
            Mock(),
        )

        self.assertTrue(handled)
        autonomous_buffer.pause.assert_called_once_with()
        autonomous_buffer.schedule_after_external_speech.assert_not_called()
        generate_mock.assert_called_once()

    @patch("main.deliver_generated_response_with_command")
    @patch("main.generate_ai_response")
    @patch("main.AutonomousSpeechBuffer")
    @patch("main.SpeechScheduler.from_config")
    @patch("main.StreamContextManager")
    @patch("main.load_llm_config")
    @patch("main.iter_chat_messages")
    @patch("main.get_live_chat_id")
    def test_live_loop_marks_selected_comment_as_reply_target(
        self,
        live_chat_id_mock,
        iter_messages_mock,
        load_config_mock,
        context_manager_mock,
        scheduler_factory_mock,
        buffer_class_mock,
        generate_mock,
        deliver_mock,
    ):
        live_chat_id_mock.return_value = "live-chat-id"
        target_message = {
            "message_id": "message-1",
            "user_id": "channel-1",
            "user_name": "視聴者",
            "comment": "どのコメントに返事してる？",
            "published_at": "2026-08-12T00:00:00Z",
        }
        iter_messages_mock.return_value = iter(
            [{"messages": [target_message], "next_page_token": None}]
        )
        load_config_mock.return_value = {
            "autonomous_speech": {"silence_seconds": 3}
        }
        stream_context = context_manager_mock.return_value
        scheduler_factory_mock.return_value.silence_seconds = 3
        autonomous_buffer = buffer_class_mock.return_value
        autonomous_buffer.theme_manager.describe.return_value = "テーマ"
        generate_mock.return_value = {
            "text": "このコメントに返しているよ。",
            "emotion": "happy",
            "motion": None,
        }
        deliver_mock.return_value = (
            generate_mock.return_value,
            1,
            {"duration_ms": 2400},
        )
        runtime = Mock()

        run_ai_youtuber_loop(max_loops=1, runtime=runtime)

        runtime.publish_chat_messages.assert_called_once_with([target_message])
        self.assertEqual(
            runtime.publish_chat_reply_state.call_args_list,
            [
                call("message-1", "thinking"),
                call("message-1", "speaking", 2400),
            ],
        )

    @patch("main.generate_ai_response")
    def test_openai_response_is_delivered_to_external_control(self, generate_mock):
        generate_mock.return_value = {
            "text": "今日は調子がいいよ。",
            "emotion": "happy",
            "speech_style": "fast",
            "motion": {
                "name": "peace_sign",
                "speed": 1.0,
                "intensity": 0.8,
                "head": "nod",
            },
            "view_action": "full_body",
        }
        runtime = Mock()
        prepared_speech = object()
        runtime.prepare_speech.return_value = prepared_speech
        runtime.publish_prepared_speech.return_value = (
            {"type": "speak", "duration_ms": 1500},
            1,
        )

        response, delivered_count = generate_and_deliver_ai_response(
            runtime,
            "テストユーザー",
            "今日の調子はどう？",
        )

        generate_mock.assert_called_once_with(
            "テストユーザー",
            "今日の調子はどう？",
        )
        runtime.prepare_speech.assert_called_once_with(
            "今日は調子がいいよ。",
            "happy",
            "fast",
        )
        runtime.publish_prepared_speech.assert_called_once_with(
            prepared_speech,
            {
                "name": "peace_sign",
                "speed": 1.0,
                "intensity": 0.8,
                "head": "nod",
            },
            "full_body",
        )
        self.assertEqual(response["emotion"], "happy")
        self.assertEqual(delivered_count, 1)

    @patch("main.generate_news_commentary")
    def test_news_commentary_is_delivered_to_external_control(self, generate_mock):
        generate_mock.return_value = {
            "text": "新しい技術、少し気になるね。",
            "emotion": "surprised",
            "motion": None,
        }
        runtime = Mock()
        prepared_speech = object()
        runtime.prepare_speech.return_value = prepared_speech
        runtime.publish_prepared_speech.return_value = (
            {"type": "speak", "duration_ms": 1500},
            1,
        )

        response, delivered_count = generate_and_deliver_news_commentary(
            runtime,
            {"title": "新しい技術が発表"},
        )

        runtime.prepare_speech.assert_called_once_with(
            "新しい技術、少し気になるね。",
            "surprised",
            "normal",
        )
        runtime.publish_prepared_speech.assert_called_once_with(
            prepared_speech,
            None,
            None,
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

    @patch("main.generate_autonomous_speech")
    @patch("main.SpeechScheduler.from_config")
    @patch("main.StreamContextManager")
    @patch("main.load_llm_config")
    @patch("main.iter_chat_messages")
    @patch("main.get_live_chat_id")
    def test_live_loop_generates_autonomous_speech_after_fixed_silence(
        self,
        live_chat_id_mock,
        iter_messages_mock,
        load_config_mock,
        context_manager_mock,
        scheduler_factory_mock,
        autonomous_mock,
    ):
        live_chat_id_mock.return_value = "live-chat-id"
        iter_messages_mock.return_value = iter(
            [{"messages": [], "next_page_token": None}]
        )
        load_config_mock.return_value = {
            "autonomous_speech": {
                "silence_seconds": 3,
                "topic_weights": {
                    "news": 0,
                    "trivia": 1,
                    "observation": 0,
                    "character_thought": 0,
                },
            }
        }
        stream_context = context_manager_mock.return_value
        scheduler = scheduler_factory_mock.return_value
        scheduler.silence_seconds = 3
        scheduler.should_speak_autonomously.return_value = True
        scheduler.record_speech.return_value = 4.0
        autonomous_mock.return_value = {
            "text": "静かな時間も配信の一部だね。",
            "emotion": "relaxed",
            "motion": None,
        }

        run_ai_youtuber_loop(max_loops=1)

        autonomous_mock.assert_called_once()
        stream_context.record_ai_speech.assert_called_once_with(
            "静かな時間も配信の一部だね。"
        )
        scheduler.record_speech.assert_called_once_with(
            "静かな時間も配信の一部だね。"
        )

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
