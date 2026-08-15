import unittest
import threading
from unittest.mock import Mock, patch

from autonomous_buffer import AutonomousSpeechBuffer


def create_config():
    return {
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


class AutonomousSpeechBufferTest(unittest.TestCase):
    def setUp(self):
        self.runtime = Mock()
        self.prepared_speech = Mock(duration_ms=12000)
        self.runtime.prepare_speech.return_value = self.prepared_speech
        self.stream_context = Mock()
        self.publish_callback = Mock()
        self.ai_response = {
            "text": "役立つ雑学を一つだけ話すよ。",
            "emotion": "surprised",
            "motion": None,
        }
        self.publish_callback.return_value = (
            self.ai_response,
            1,
            {"duration_ms": 12000},
        )

    def test_opening_announces_program_before_autonomous_speech(self):
        self.publish_callback.side_effect = (
            lambda runtime, response, prepared: (
                response,
                1,
                {"duration_ms": 12000},
            )
        )
        buffer = AutonomousSpeechBuffer(
            runtime=self.runtime,
            stream_context=self.stream_context,
            config=create_config(),
            publish_callback=self.publish_callback,
            stream_topic="休日の過ごし方",
            now=100,
            prepare_in_background=False,
        )

        response, _, command = buffer.publish_opening(now=100)

        self.assertIn("今日は『休日の過ごし方』", response["text"])
        self.runtime.prepare_speech.assert_called_once_with(
            response["text"],
            "happy",
            "normal",
        )
        self.assertEqual(command["duration_ms"], 12000)
        self.assertEqual(buffer.next_speech_at, 115)

    def test_admin_can_replace_program(self):
        theme_manager = Mock()
        theme_manager.replace_program.return_value = "新しい構成"
        buffer = AutonomousSpeechBuffer(
            runtime=self.runtime,
            stream_context=self.stream_context,
            config=create_config(),
            publish_callback=self.publish_callback,
            theme_manager=theme_manager,
            now=100,
            prepare_in_background=False,
        )
        buffer.paused = True

        result = buffer.replace_program("自己紹介配信", now=100)

        self.assertEqual(result, "新しい構成")
        theme_manager.replace_program.assert_called_once_with("自己紹介配信")
        self.assertEqual(buffer.next_speech_at, 103)

    @patch("autonomous_buffer.generate_autonomous_speech")
    def test_pause_stops_preparation_and_resume_restarts_timer(
        self,
        generate_mock,
    ):
        generate_mock.return_value = self.ai_response
        buffer = AutonomousSpeechBuffer(
            runtime=self.runtime,
            stream_context=self.stream_context,
            config=create_config(),
            publish_callback=self.publish_callback,
            stream_topic="インターネットと集中力",
            now=100,
            prepare_in_background=False,
        )

        buffer.pause()
        self.assertFalse(buffer.tick(now=200))
        self.assertTrue(buffer.paused)
        self.assertIsNone(buffer.prepared)

        buffer.resume(now=200)
        self.assertFalse(buffer.paused)
        self.assertEqual(buffer.next_speech_at, 203)

    @patch("autonomous_buffer.generate_autonomous_speech")
    def test_external_speech_reschedules_autonomous_speech(
        self,
        generate_mock,
    ):
        generate_mock.return_value = self.ai_response
        buffer = AutonomousSpeechBuffer(
            runtime=self.runtime,
            stream_context=self.stream_context,
            config=create_config(),
            publish_callback=self.publish_callback,
            stream_topic="インターネットと集中力",
            now=100,
            prepare_in_background=False,
        )

        buffer.schedule_after_external_speech(2500, now=100)

        self.assertEqual(buffer.next_speech_at, 105.5)
        self.assertFalse(buffer.tick(now=105.4))

    @patch("autonomous_buffer.generate_autonomous_speech")
    def test_prepares_before_due_and_publishes_at_due_time(
        self,
        generate_mock,
    ):
        generate_mock.return_value = self.ai_response
        buffer = AutonomousSpeechBuffer(
            runtime=self.runtime,
            stream_context=self.stream_context,
            config=create_config(),
            publish_callback=self.publish_callback,
            stream_topic="インターネットと集中力",
            now=100,
            prepare_in_background=False,
        )

        self.assertFalse(buffer.tick(now=100))
        self.publish_callback.assert_not_called()
        self.runtime.prepare_speech.assert_called_once_with(
            self.ai_response["text"],
            self.ai_response["emotion"],
            "normal",
        )

        self.assertTrue(buffer.tick(now=103))
        self.publish_callback.assert_called_once()
        self.stream_context.record_ai_speech.assert_called_once_with(
            self.ai_response["text"]
        )
        self.assertEqual(buffer.next_speech_at, 118)
        self.assertEqual(self.runtime.prepare_speech.call_count, 2)
        topic_card = self.runtime.publish_topic_card.call_args.args[0]
        self.assertEqual(topic_card["kind"], "talk")
        self.assertEqual(topic_card["title"], "インターネットと集中力")

    @patch("autonomous_buffer.generate_autonomous_speech")
    def test_comment_discards_buffer_and_restarts_from_exact_audio_duration(
        self,
        generate_mock,
    ):
        generate_mock.return_value = self.ai_response
        buffer = AutonomousSpeechBuffer(
            runtime=self.runtime,
            stream_context=self.stream_context,
            config=create_config(),
            publish_callback=self.publish_callback,
            stream_topic="インターネットと集中力",
            now=100,
            prepare_in_background=False,
        )
        buffer.tick(now=100)

        buffer.cancel_for_comment()
        self.assertIsNone(buffer.prepared)
        self.assertEqual(buffer.discarded_for_comment_count, 1)
        self.runtime.update_admin_status.assert_called_once_with(
            discarded_prefetches=1,
        )

        comment_response = {
            "text": "コメントへの返答だよ。",
            "emotion": "happy",
            "motion": None,
        }
        buffer.resume_after_comment(
            comment_response,
            duration_ms=1500,
            now=100,
        )

        self.assertFalse(buffer.tick(now=104.4))
        self.assertTrue(buffer.tick(now=104.5))
        self.assertTrue(buffer.has_received_comment)

    @patch("autonomous_buffer.generate_autonomous_speech")
    def test_comment_without_prepared_speech_does_not_increment_discard_count(
        self,
        generate_mock,
    ):
        generate_mock.return_value = self.ai_response
        buffer = AutonomousSpeechBuffer(
            runtime=self.runtime,
            stream_context=self.stream_context,
            config=create_config(),
            publish_callback=self.publish_callback,
            stream_topic="インターネットと集中力",
            now=100,
            prepare_in_background=False,
        )

        buffer.cancel_for_comment()

        self.assertEqual(buffer.discarded_for_comment_count, 0)
        self.runtime.update_admin_status.assert_not_called()

    @patch("autonomous_buffer.generate_autonomous_speech")
    def test_no_comment_prompt_assumes_no_audience(self, generate_mock):
        generate_mock.return_value = self.ai_response
        buffer = AutonomousSpeechBuffer(
            runtime=self.runtime,
            stream_context=self.stream_context,
            config=create_config(),
            publish_callback=self.publish_callback,
            stream_topic="インターネットと集中力",
            now=100,
            prepare_in_background=False,
        )

        buffer.tick(now=100)

        situation = generate_mock.call_args.args[0]
        self.assertIn("視聴者がいない前提", situation)
        self.assertIn(
            "配信企画: インターネットと集中力",
            generate_mock.call_args.kwargs["theme_context"],
        )

    @patch("autonomous_buffer.generate_autonomous_speech")
    def test_background_preparation_does_not_block_tick(self, generate_mock):
        started = threading.Event()
        release = threading.Event()

        def delayed_response(*args, **kwargs):
            started.set()
            release.wait(timeout=1)
            return self.ai_response

        generate_mock.side_effect = delayed_response
        buffer = AutonomousSpeechBuffer(
            runtime=self.runtime,
            stream_context=self.stream_context,
            config=create_config(),
            publish_callback=self.publish_callback,
            stream_topic="インターネットと集中力",
            now=100,
        )

        self.assertFalse(buffer.tick(now=100))
        self.assertTrue(started.wait(timeout=1))
        self.assertIsNone(buffer.prepared)

        thread = buffer._preparation_thread
        release.set()
        thread.join(timeout=1)
        self.assertFalse(buffer.tick(now=100))
        self.assertIsNotNone(buffer.prepared)

    @patch("autonomous_buffer.fetch_news_articles")
    @patch("autonomous_buffer.select_news_article")
    @patch("autonomous_buffer.generate_news_commentary")
    def test_automatic_program_starts_with_news_and_continues_same_article(
        self,
        generate_news_mock,
        select_news_mock,
        fetch_news_mock,
    ):
        article = {
            "title": "VTuber事務所が新企画を発表",
            "link": "https://example.com/news",
            "source_name": "テストニュース",
            "published_at": "2026-08-13",
            "summary": "新企画の概要",
        }
        fetch_news_mock.return_value = [article]
        select_news_mock.return_value = article
        generate_news_mock.return_value = {
            **self.ai_response,
            "topic_summary": "新企画の内容と開始時期が公開された。",
        }
        config = create_config()
        config["autonomous_speech"]["news_story_utterances"] = 3
        theme_manager = Mock()
        theme_manager.manual_theme = False
        theme_manager.program_instruction = ""
        theme_manager.news_policy = "general"
        theme_manager.news_query = ""
        theme_manager.build_context.return_value = "配信構成"
        theme_manager.state.main_theme = "今週の界隈ニュース"
        theme_manager.status.return_value = {}
        news_history_repository = Mock()
        news_history_repository.recent_exclusions.return_value = {
            "story_keys": {"以前に使用した話題"},
            "links": {"https://example.com/old-news"},
        }
        buffer = AutonomousSpeechBuffer(
            runtime=self.runtime,
            stream_context=self.stream_context,
            config=config,
            publish_callback=self.publish_callback,
            theme_manager=theme_manager,
            news_history_repository=news_history_repository,
            now=100,
            prepare_in_background=False,
        )

        buffer.tick(now=100)
        self.assertEqual(buffer.prepared.article, article)
        self.assertEqual(buffer.prepared.news_story_turn, 0)
        buffer.tick(now=103)

        self.assertEqual(buffer.prepared.article, article)
        self.assertEqual(buffer.prepared.news_story_turn, 1)
        self.assertEqual(fetch_news_mock.call_count, 1)
        select_news_mock.assert_called_once()
        self.assertEqual(
            select_news_mock.call_args.args[1],
            {"https://example.com/old-news"},
        )
        self.assertEqual(
            select_news_mock.call_args.kwargs["excluded_story_keys"],
            {"以前に使用した話題"},
        )
        news_history_repository.record.assert_called_once_with(article)
        first_call, second_call = generate_news_mock.call_args_list[:2]
        self.assertEqual(first_call.kwargs["story_turn"], 1)
        self.assertEqual(second_call.kwargs["story_turn"], 2)
        self.assertEqual(second_call.kwargs["story_turn_count"], 3)
        topic_card = self.runtime.publish_topic_card.call_args.args[0]
        self.assertEqual(topic_card["kind"], "news")
        self.assertEqual(
            topic_card["summary"],
            "新企画の内容と開始時期が公開された。",
        )

    @patch("autonomous_buffer.generate_autonomous_speech")
    def test_comment_invalidates_in_flight_preparation(self, generate_mock):
        started = threading.Event()
        release = threading.Event()

        def delayed_response(*args, **kwargs):
            started.set()
            release.wait(timeout=1)
            return self.ai_response

        generate_mock.side_effect = delayed_response
        buffer = AutonomousSpeechBuffer(
            runtime=self.runtime,
            stream_context=self.stream_context,
            config=create_config(),
            publish_callback=self.publish_callback,
            stream_topic="インターネットと集中力",
            now=100,
        )
        buffer.tick(now=100)
        self.assertTrue(started.wait(timeout=1))

        buffer.cancel_for_comment()
        thread = buffer._preparation_thread
        release.set()
        thread.join(timeout=1)
        buffer.tick(now=100)

        self.assertEqual(buffer.discarded_for_comment_count, 1)
        self.assertIsNone(buffer.prepared)


if __name__ == "__main__":
    unittest.main()
