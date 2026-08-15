import unittest
from unittest.mock import Mock, call, patch

from main import (
    AdminRequestedBroadcastEnd,
    generate_and_deliver_ai_response,
    generate_and_deliver_news_commentary,
    get_autonomous_speech_interval_seconds,
    get_mock_live_delay_seconds,
    get_obs_overlay_wait_seconds,
    get_youtube_live_wait_settings,
    is_reply_candidate,
    process_next_admin_command,
    run_ai_youtuber_loop,
    run_mock_live,
    run_obs_websocket_test,
    start_obs_for_upcoming_youtube_broadcast,
    run_x_post,
    run_x_post_draft,
    wait_for_youtube_live,
    wait_for_youtube_stream_active,
    YouTubeBroadcastEndCoordinator,
)
from youtube_chat import YouTubeLiveEndedError, YouTubeLiveNotStartedError
from youtube_oauth import NoUpcomingYouTubeBroadcastError


class ReplyCandidateTest(unittest.TestCase):
    def test_low_information_reactions_are_skipped(self):
        for comment in ("www", "ＷＷＷ", "草草草", "8888", "っっっっっf"):
            with self.subTest(comment=comment):
                self.assertFalse(is_reply_candidate({"comment": comment}))

    def test_url_only_comment_is_skipped(self):
        self.assertFalse(
            is_reply_candidate({"comment": "https://example.com/test"})
        )

    def test_short_meaningful_comments_remain_candidates(self):
        for comment in ("かわいい", "ええっ", "AIって何？", "hello"):
            with self.subTest(comment=comment):
                self.assertTrue(is_reply_candidate({"comment": comment}))


class ObsOverlayWaitConfigTest(unittest.TestCase):
    @patch("main.ObsWebSocketClient.from_env")
    def test_obs_connection_test_reports_stream_status(self, from_env_mock):
        client = from_env_mock.return_value
        client.get_stream_status.return_value = False

        run_obs_websocket_test()

        client.get_stream_status.assert_called_once_with()

    @patch.dict("os.environ", {"OBS_OVERLAY_WAIT_SECONDS": "90"})
    def test_reads_overlay_wait_seconds(self):
        self.assertEqual(get_obs_overlay_wait_seconds(), 90)

    @patch.dict("os.environ", {"OBS_OVERLAY_WAIT_SECONDS": "invalid"})
    def test_rejects_invalid_overlay_wait_seconds(self):
        with self.assertRaisesRegex(RuntimeError, "数値"):
            get_obs_overlay_wait_seconds()


class YouTubeLiveWaitTest(unittest.TestCase):
    def test_waits_until_youtube_receives_obs_stream(self):
        status_checker = Mock(side_effect=[False, False, True])
        sleep_callback = Mock()
        times = iter([0, 0, 2, 4])

        wait_for_youtube_stream_active(
            "stream-id",
            timeout_seconds=10,
            interval_seconds=2,
            status_checker=status_checker,
            sleep_callback=sleep_callback,
            now=lambda: next(times),
        )

        self.assertEqual(status_checker.call_count, 3)
        self.assertEqual(sleep_callback.call_count, 2)

    @patch("youtube_oauth.transition_youtube_broadcast_to_live")
    @patch("main.wait_for_youtube_stream_active")
    @patch("youtube_oauth.find_upcoming_youtube_broadcast")
    def test_obs_start_transitions_broadcast_when_auto_start_is_off(
        self,
        find_upcoming_mock,
        wait_stream_mock,
        transition_mock,
    ):
        find_upcoming_mock.return_value = {
            "video_id": "video-id",
            "title": "限定公開テスト",
            "privacy_status": "unlisted",
            "enable_auto_start": False,
            "bound_stream_id": "stream-id",
        }
        obs_client = Mock()
        obs_client.start_stream.return_value = True

        result = start_obs_for_upcoming_youtube_broadcast(
            Mock(),
            obs_client,
        )

        self.assertEqual(result, ("video-id", None))
        obs_client.start_stream.assert_called_once_with()
        wait_stream_mock.assert_called_once_with("stream-id")
        transition_mock.assert_called_once_with("video-id")

    @patch("youtube_oauth.transition_youtube_broadcast_to_live")
    @patch("main.wait_for_youtube_stream_active")
    @patch("youtube_oauth.create_youtube_broadcast")
    @patch("youtube_oauth.find_upcoming_youtube_broadcast")
    def test_configure_creates_broadcast_when_no_ready_slot_exists(
        self,
        find_upcoming_mock,
        create_broadcast_mock,
        wait_stream_mock,
        transition_mock,
    ):
        find_upcoming_mock.side_effect = NoUpcomingYouTubeBroadcastError(
            "配信枠なし"
        )
        create_broadcast_mock.return_value = {
            "video_id": "created-video",
            "title": "AIの自己紹介配信",
            "description": "AI VTuberが自己紹介します。",
            "privacy_status": "unlisted",
            "bound_stream_id": "stream-id",
            "enable_auto_start": False,
            "enable_auto_stop": True,
        }
        runtime = Mock()
        runtime.select_prepared_broadcast_plan.return_value = (
            "初見向けの自己紹介配信"
        )
        obs_client = Mock()
        obs_client.start_stream.return_value = True
        command = {
            "action": "configure_broadcast",
            "title": "AIの自己紹介配信",
            "description": "AI VTuberが自己紹介します。",
            "privacy_status": "unlisted",
            "stream_plan": "初見向けの自己紹介配信",
            "draft_id": "draft-1",
        }

        result = start_obs_for_upcoming_youtube_broadcast(
            runtime,
            obs_client,
            command=command,
        )

        self.assertEqual(
            result,
            ("created-video", "初見向けの自己紹介配信"),
        )
        create_broadcast_mock.assert_called_once_with(
            title="AIの自己紹介配信",
            description="AI VTuberが自己紹介します。",
            privacy_status="unlisted",
        )
        wait_stream_mock.assert_called_once_with("stream-id")
        transition_mock.assert_called_once_with("created-video")

    @patch("youtube_oauth.find_upcoming_youtube_broadcast")
    @patch("main.get_live_chat_id")
    def test_admin_command_starts_obs_and_waits_for_expected_youtube_live(
        self,
        get_chat_id,
        find_upcoming_mock,
    ):
        get_chat_id.side_effect = [
            YouTubeLiveNotStartedError("まだ開始前"),
            ("live-chat-id", "video-id"),
        ]
        find_upcoming_mock.return_value = {
            "video_id": "video-id",
            "title": "限定公開テスト",
            "privacy_status": "unlisted",
            "enable_auto_start": True,
        }
        runtime = Mock()
        runtime.get_next_admin_command.return_value = {
            "action": "start_broadcast"
        }
        obs_client = Mock()
        obs_client.start_stream.return_value = True

        result = wait_for_youtube_live(
            runtime,
            obs_websocket_client=obs_client,
        )

        self.assertEqual(result, ("live-chat-id", "video-id", None))
        obs_client.start_stream.assert_called_once_with()
        runtime.get_next_admin_command.assert_called_once_with(
            timeout_seconds=10
        )

    @patch.dict(
        "os.environ",
        {
            "YOUTUBE_LIVE_WAIT_INTERVAL_SECONDS": "10",
            "YOUTUBE_LIVE_WAIT_TIMEOUT_SECONDS": "0",
        },
    )
    def test_reads_live_wait_settings(self):
        self.assertEqual(get_youtube_live_wait_settings(), (10, 0))

    @patch("main.time.sleep")
    @patch("main.get_live_chat_id")
    def test_retries_only_until_live_starts(self, get_chat_id, sleep_mock):
        get_chat_id.side_effect = [
            YouTubeLiveNotStartedError("まだ開始前"),
            YouTubeLiveNotStartedError("まだ開始前"),
            ("live-chat-id", "video-id"),
        ]
        runtime = Mock()

        result = wait_for_youtube_live(runtime)

        self.assertEqual(result, ("live-chat-id", "video-id", None))
        self.assertEqual(sleep_mock.call_count, 2)
        runtime.update_admin_status.assert_called_with(
            phase="preparing",
            message="YouTubeライブを確認しました。配信を準備しています。",
            youtube_wait_attempts=3,
        )

    @patch("main.time.sleep")
    @patch("main.get_live_chat_id")
    def test_authentication_error_is_not_retried(self, get_chat_id, sleep_mock):
        get_chat_id.side_effect = RuntimeError("認証失敗")

        with self.assertRaisesRegex(RuntimeError, "認証失敗"):
            wait_for_youtube_live(Mock())

        sleep_mock.assert_not_called()

    @patch("youtube_oauth.update_youtube_broadcast_metadata")
    @patch("youtube_oauth.find_upcoming_youtube_broadcast")
    @patch("main.get_live_chat_id")
    def test_admin_can_configure_persistent_broadcast_and_apply_stream_plan(
        self,
        get_chat_id,
        find_upcoming_mock,
        update_metadata_mock,
    ):
        get_chat_id.side_effect = [
            YouTubeLiveNotStartedError("まだ開始前"),
            ("live-chat-id", "persistent-video"),
        ]
        find_upcoming_mock.return_value = {
            "video_id": "persistent-video",
            "title": "S のライブ配信",
            "privacy_status": "unlisted",
            "enable_auto_start": True,
        }
        update_metadata_mock.return_value = {
            "video_id": "persistent-video",
            "title": "自己紹介ライブ",
            "description": "テスト説明",
            "privacy_status": "unlisted",
        }
        runtime = Mock()
        runtime.get_next_admin_command.return_value = {
            "action": "configure_broadcast",
            "title": "自己紹介ライブ",
            "description": "テスト説明",
            "privacy_status": "unlisted",
            "stream_plan": "初見向けの自己紹介配信",
        }
        obs_client = Mock()
        obs_client.start_stream.return_value = True

        result = wait_for_youtube_live(
            runtime,
            obs_websocket_client=obs_client,
        )

        self.assertEqual(
            result,
            (
                "live-chat-id",
                "persistent-video",
                "初見向けの自己紹介配信",
            ),
        )
        update_metadata_mock.assert_called_once_with(
            video_id="persistent-video",
            title="自己紹介ライブ",
            description="テスト説明",
            privacy_status="unlisted",
        )
        obs_client.start_stream.assert_called_once_with()

    @patch("stream_theme.generate_stream_theme_plan")
    @patch("youtube_oauth.update_youtube_broadcast_metadata")
    @patch("youtube_oauth.find_upcoming_youtube_broadcast")
    @patch("main.get_live_chat_id")
    def test_admin_prepares_then_starts_with_same_broadcast_plan(
        self,
        get_chat_id,
        find_upcoming_mock,
        update_metadata_mock,
        generate_plan_mock,
    ):
        get_chat_id.side_effect = [
            YouTubeLiveNotStartedError("まだ開始前"),
            YouTubeLiveNotStartedError("まだ開始前"),
            ("live-chat-id", "persistent-video"),
        ]
        find_upcoming_mock.return_value = {
            "video_id": "persistent-video",
            "title": "S のライブ配信",
            "privacy_status": "unlisted",
            "enable_auto_start": True,
        }
        update_metadata_mock.return_value = {
            "video_id": "persistent-video",
            "title": "AIの自己紹介配信",
            "description": "AI VTuberが自己紹介します。",
            "privacy_status": "unlisted",
        }
        plan = Mock()
        generate_plan_mock.return_value = plan
        runtime = Mock()
        runtime.get_next_admin_command.side_effect = [
            {
                "action": "prepare_broadcast",
                "id": "draft-1",
                "stream_plan": "初見向けの自己紹介配信",
            },
            {
                "action": "configure_broadcast",
                "title": "AIの自己紹介配信",
                "description": "AI VTuberが自己紹介します。",
                "privacy_status": "unlisted",
                "stream_plan": "初見向けの自己紹介配信",
                "draft_id": "draft-1",
            },
        ]
        runtime.store_prepared_broadcast_draft.return_value = {
            "title": "AIの自己紹介配信",
            "theme": "自己紹介配信",
            "news_description": "使用しない",
        }
        runtime.select_prepared_broadcast_plan.return_value = (
            "初見向けの自己紹介配信"
        )
        obs_client = Mock()
        obs_client.start_stream.return_value = True

        result = wait_for_youtube_live(
            runtime,
            obs_websocket_client=obs_client,
        )

        self.assertEqual(
            result,
            (
                "live-chat-id",
                "persistent-video",
                "初見向けの自己紹介配信",
            ),
        )
        generate_plan_mock.assert_called_once_with(
            instruction="初見向けの自己紹介配信"
        )
        runtime.store_prepared_broadcast_draft.assert_called_once_with(
            plan,
            "初見向けの自己紹介配信",
            "draft-1",
        )
        runtime.select_prepared_broadcast_plan.assert_called_once_with(
            "draft-1"
        )
        obs_client.start_stream.assert_called_once_with()


class XPostDraftCommandTest(unittest.TestCase):
    @patch("x_post.generate_x_post_draft")
    def test_x_failure_does_not_propagate(self, generate_draft):
        generate_draft.side_effect = RuntimeError("Gemini接続失敗")

        result = run_x_post_draft()

        self.assertFalse(result)

    @patch("x_post.publish_x_post")
    @patch("x_post.generate_x_post_draft")
    def test_x_post_requires_confirm_flag(self, generate_draft, publish):
        generate_draft.return_value = {"text": "確認する本文"}

        result = run_x_post(confirm=False)

        self.assertFalse(result)
        publish.assert_not_called()

    @patch("x_post.publish_x_post")
    @patch("x_post.generate_x_post_draft")
    def test_x_post_requires_exact_confirmation(self, generate_draft, publish):
        generate_draft.return_value = {"text": "確認する本文"}

        result = run_x_post(
            confirm=True,
            input_func=lambda _prompt: "no",
        )

        self.assertFalse(result)
        publish.assert_not_called()

    @patch("x_post.publish_x_post")
    @patch("x_post.generate_x_post_draft")
    def test_x_post_publishes_after_exact_confirmation(
        self,
        generate_draft,
        publish,
    ):
        generate_draft.return_value = {"text": "確認する本文"}
        publish.return_value = {"post_id": "12345", "text": "確認する本文"}

        result = run_x_post(
            confirm=True,
            input_func=lambda _prompt: "POST",
        )

        self.assertTrue(result)
        publish.assert_called_once_with("確認する本文")


class GenerateAndDeliverAiResponseTest(unittest.TestCase):
    def test_admin_broadcast_end_signal_is_a_distinct_normal_exit(self):
        self.assertTrue(issubclass(AdminRequestedBroadcastEnd, RuntimeError))

    def test_youtube_end_waits_for_closing_audio(self):
        current_time = [100.0]
        runtime = Mock()
        complete_callback = Mock(return_value="complete")
        stop_obs_callback = Mock(return_value=True)
        coordinator = YouTubeBroadcastEndCoordinator(
            "video-id",
            runtime,
            complete_callback=complete_callback,
            stop_obs_callback=stop_obs_callback,
            now=lambda: current_time[0],
        )

        coordinator.schedule(2000)
        current_time[0] = 102.7
        self.assertFalse(coordinator.tick())
        complete_callback.assert_not_called()

        current_time[0] = 102.75
        self.assertTrue(coordinator.tick())
        complete_callback.assert_called_once_with("video-id")
        stop_obs_callback.assert_called_once_with()

    @patch("main.deliver_generated_response_with_command")
    @patch("main.generate_admin_directed_speech")
    def test_end_broadcast_schedules_after_closing_greeting(
        self,
        generate_mock,
        deliver_mock,
    ):
        runtime = Mock()
        runtime.get_next_admin_command.return_value = {
            "action": "end_broadcast",
        }
        generate_mock.return_value = {
            "text": "今日はここまで。またね。",
            "emotion": "relaxed",
            "motion": None,
        }
        deliver_mock.return_value = (
            generate_mock.return_value,
            1,
            {"duration_ms": 2400},
        )
        autonomous_buffer = Mock(paused=False)
        schedule_end = Mock()

        handled = process_next_admin_command(
            runtime,
            autonomous_buffer,
            Mock(),
            schedule_broadcast_end=schedule_end,
        )

        self.assertTrue(handled)
        autonomous_buffer.pause.assert_called_once_with()
        schedule_end.assert_called_once_with(2400)

    @patch("main.AutonomousSpeechBuffer")
    @patch("main.SpeechScheduler.from_config")
    @patch("main.StreamContextManager")
    @patch("main.load_llm_config")
    @patch("main.YouTubeChatPoller")
    @patch("main.get_live_chat_id")
    def test_live_loop_stops_without_waiting_when_youtube_ends(
        self,
        live_chat_id_mock,
        poller_class_mock,
        load_config_mock,
        context_manager_mock,
        scheduler_factory_mock,
        buffer_class_mock,
    ):
        live_chat_id_mock.return_value = ("live-chat-id", "video-id")
        poller = poller_class_mock.return_value
        poller.start.return_value = poller

        def ended_results(*args, **kwargs):
            raise YouTubeLiveEndedError("ライブチャット終了")
            yield

        poller.iter_results.side_effect = ended_results
        load_config_mock.return_value = {
            "autonomous_speech": {"silence_seconds": 3}
        }
        scheduler_factory_mock.return_value.silence_seconds = 3
        autonomous_buffer = buffer_class_mock.return_value
        autonomous_buffer.theme_manager.describe.return_value = "テーマ"
        runtime = Mock()

        run_ai_youtuber_loop(max_loops=1000, runtime=runtime)

        autonomous_buffer.pause.assert_called_once_with()
        runtime.update_admin_status.assert_called_with(
            autonomous_paused=True,
            phase="youtube_ended",
            message="YouTubeライブ終了を検知しました。Pythonを停止します。",
        )

    def test_admin_can_change_stream_plan(self):
        runtime = Mock()
        runtime.get_next_admin_command.return_value = {
            "action": "change_stream_plan",
            "text": "初見向けの自己紹介配信",
        }
        autonomous_buffer = Mock()
        autonomous_buffer.replace_program.return_value = "新しい構成"
        autonomous_buffer.theme_manager.status.return_value = {
            "stream_theme": "自己紹介配信",
            "stream_segment": "どんなAIか",
        }

        handled = process_next_admin_command(
            runtime,
            autonomous_buffer,
            Mock(),
        )

        self.assertTrue(handled)
        autonomous_buffer.replace_program.assert_called_once_with(
            "初見向けの自己紹介配信"
        )
        runtime.update_admin_status.assert_called_with(
            stream_theme="自己紹介配信",
            stream_segment="どんなAIか",
            phase="waiting",
            message="配信企画と話題構成を変更しました。",
        )

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
    @patch("main.YouTubeChatPoller")
    @patch("main.get_live_chat_id")
    def test_live_loop_marks_selected_comment_as_reply_target(
        self,
        live_chat_id_mock,
        poller_class_mock,
        load_config_mock,
        context_manager_mock,
        scheduler_factory_mock,
        buffer_class_mock,
        generate_mock,
        deliver_mock,
    ):
        live_chat_id_mock.return_value = ("live-chat-id", "video-id")
        target_message = {
            "message_id": "message-1",
            "user_id": "channel-1",
            "user_name": "視聴者",
            "comment": "どのコメントに返事してる？",
            "published_at": "2026-08-12T00:00:00Z",
        }
        poller = poller_class_mock.return_value
        poller.start.return_value = poller
        poller.iter_results.return_value = iter(
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

        self.assertIs(
            poller_class_mock.call_args.kwargs["message_callback"],
            runtime.publish_chat_messages,
        )
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
    @patch("main.YouTubeChatPoller")
    @patch("main.get_live_chat_id")
    def test_live_loop_generates_autonomous_speech_after_fixed_silence(
        self,
        live_chat_id_mock,
        poller_class_mock,
        load_config_mock,
        context_manager_mock,
        scheduler_factory_mock,
        autonomous_mock,
    ):
        live_chat_id_mock.return_value = "live-chat-id"
        poller = poller_class_mock.return_value
        poller.start.return_value = poller
        poller.iter_results.return_value = iter(
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
