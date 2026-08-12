import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import call, Mock, patch

from youtube_chat import (
    fetch_chat_messages,
    get_live_chat_id,
    iter_chat_messages,
    resolve_youtube_video_id,
    YouTubeChatPoller,
)


class YouTubeChatTest(unittest.TestCase):
    @patch.dict(
        "os.environ",
        {"YOUTUBE_VIDEO_ID": "manual-video-id"},
        clear=False,
    )
    def test_configured_video_id_is_used_first(self):
        self.assertEqual(resolve_youtube_video_id(), "manual-video-id")

    @patch.dict("os.environ", {"YOUTUBE_VIDEO_ID": ""}, clear=False)
    @patch("youtube_oauth.find_active_youtube_broadcast")
    def test_active_broadcast_is_used_when_video_id_is_empty(
        self,
        find_broadcast_mock,
    ):
        find_broadcast_mock.return_value = {
            "video_id": "auto-video-id",
            "title": "テスト配信",
        }

        result = resolve_youtube_video_id()

        self.assertEqual(result, "auto-video-id")
        find_broadcast_mock.assert_called_once_with()

    @patch.dict(
        "os.environ",
        {
            "YOUTUBE_API_KEY": "test-key",
            "YOUTUBE_VIDEO_ID": "manual-video-id",
        },
        clear=False,
    )
    @patch("youtube_chat.requests.get")
    def test_live_chat_id_uses_resolved_video_id(self, get_mock):
        response = Mock()
        response.json.return_value = {
            "items": [
                {
                    "liveStreamingDetails": {
                        "activeLiveChatId": "live-chat-id"
                    }
                }
            ]
        }
        get_mock.return_value = response

        result = get_live_chat_id()

        self.assertEqual(result, "live-chat-id")
        self.assertEqual(
            get_mock.call_args.kwargs["params"]["id"],
            "manual-video-id",
        )

    @patch("youtube_chat.time.sleep")
    @patch("youtube_chat.time.monotonic")
    @patch("youtube_chat.fetch_chat_messages")
    def test_wait_callback_runs_in_small_steps(
        self,
        fetch_mock,
        monotonic_mock,
        sleep_mock,
    ):
        fetch_mock.side_effect = [
            {
                "messages": [],
                "next_page_token": "next",
                "polling_interval_millis": 1000,
            },
            {
                "messages": [],
                "next_page_token": None,
                "polling_interval_millis": 1000,
            },
        ]
        current_time = [0.0]
        monotonic_mock.side_effect = lambda: current_time[0]
        sleep_mock.side_effect = lambda seconds: current_time.__setitem__(
            0,
            current_time[0] + seconds,
        )
        wait_callback = Mock()

        results = list(
            iter_chat_messages(
                "live-chat-id",
                max_loops=2,
                wait_callback=wait_callback,
                wait_step_seconds=0.25,
            )
        )

        self.assertEqual(len(results), 2)
        self.assertEqual(wait_callback.call_count, 4)
        self.assertEqual(sleep_mock.call_count, 4)

    @patch("youtube_chat.time.sleep")
    @patch("youtube_chat.time.monotonic")
    @patch("youtube_chat.fetch_chat_messages")
    def test_wait_uses_real_elapsed_time_during_callback(
        self,
        fetch_mock,
        monotonic_mock,
        sleep_mock,
    ):
        fetch_mock.side_effect = [
            {
                "messages": [],
                "next_page_token": "next",
                "polling_interval_millis": 1000,
            },
            {
                "messages": [],
                "next_page_token": None,
                "polling_interval_millis": 1000,
            },
        ]
        current_time = [0.0]
        monotonic_mock.side_effect = lambda: current_time[0]
        sleep_mock.side_effect = lambda seconds: current_time.__setitem__(
            0,
            current_time[0] + seconds,
        )

        def slow_callback():
            current_time[0] += 2.0

        results = list(
            iter_chat_messages(
                "live-chat-id",
                max_loops=2,
                wait_callback=slow_callback,
                wait_step_seconds=0.25,
            )
        )

        self.assertEqual(len(results), 2)
        sleep_mock.assert_not_called()

    @patch("youtube_chat.time.sleep")
    @patch("youtube_chat.time.monotonic")
    @patch("youtube_chat.fetch_chat_messages")
    def test_polling_log_shows_recommended_and_actual_interval(
        self,
        fetch_mock,
        monotonic_mock,
        sleep_mock,
    ):
        fetch_mock.side_effect = [
            {
                "messages": [],
                "next_page_token": "next",
                "polling_interval_millis": 60000,
            },
            {
                "messages": [{"message_id": "message-1"}],
                "next_page_token": None,
                "polling_interval_millis": 10000,
            },
        ]
        current_time = [0.0]
        monotonic_mock.side_effect = lambda: current_time[0]
        sleep_mock.side_effect = lambda seconds: current_time.__setitem__(
            0,
            current_time[0] + seconds,
        )
        output = StringIO()

        with redirect_stdout(output):
            list(
                iter_chat_messages(
                    "live-chat-id",
                    max_loops=2,
                    wait_step_seconds=10,
                )
            )

        log = output.getvalue()
        self.assertIn("前回取得から=初回", log)
        self.assertIn("次回取得目安=60.0秒", log)
        self.assertIn("前回取得から=60.0秒", log)
        self.assertIn("新規候補=1件", log)
        self.assertIn("次回取得目安=10.0秒", log)

    @patch.dict("os.environ", {"YOUTUBE_API_KEY": "test-key"})
    @patch("youtube_chat.requests.get")
    def test_channel_id_is_used_as_stable_user_id(self, get_mock):
        response = Mock()
        response.json.return_value = {
            "items": [
                {
                    "id": "message-1",
                    "snippet": {
                        "type": "textMessageEvent",
                        "displayMessage": "こんにちは",
                        "publishedAt": "2026-08-11T00:00:00Z",
                    },
                    "authorDetails": {
                        "channelId": "channel-1",
                        "displayName": "視聴者",
                    },
                }
            ]
        }
        get_mock.return_value = response

        result = fetch_chat_messages("live-chat-id")

        self.assertEqual(result["messages"][0]["user_id"], "channel-1")

    @patch("youtube_chat.iter_chat_messages")
    def test_poller_publishes_comments_before_main_loop_consumes_them(
        self,
        iter_messages_mock,
    ):
        first_message = {"message_id": "message-1", "comment": "最初"}
        second_message = {"message_id": "message-2", "comment": "次"}
        iter_messages_mock.return_value = iter(
            [
                {"messages": [first_message], "next_page_token": "next"},
                {"messages": [second_message], "next_page_token": None},
            ]
        )
        callback = Mock()
        poller = YouTubeChatPoller(
            "live-chat-id",
            max_loops=2,
            message_callback=callback,
        ).start()
        poller._thread.join(timeout=1)

        self.assertFalse(poller._thread.is_alive())
        self.assertEqual(
            callback.call_args_list,
            [
                call([first_message]),
                call([second_message]),
            ],
        )
        results = list(poller.iter_results())
        self.assertEqual(
            [message["message_id"] for message in results[0]["messages"]],
            ["message-1", "message-2"],
        )

    @patch("youtube_chat.iter_chat_messages")
    def test_poller_removes_duplicate_comments(self, iter_messages_mock):
        message = {"message_id": "message-1", "comment": "重複"}
        iter_messages_mock.return_value = iter(
            [
                {"messages": [message], "next_page_token": "next"},
                {"messages": [message], "next_page_token": None},
            ]
        )
        callback = Mock()
        poller = YouTubeChatPoller(
            "live-chat-id",
            max_loops=2,
            message_callback=callback,
        ).start()
        poller._thread.join(timeout=1)

        results = list(poller.iter_results())

        callback.assert_called_once_with([message])
        self.assertEqual(len(results[0]["messages"]), 1)

    @patch("youtube_chat.iter_chat_messages")
    def test_poller_coalesces_empty_results_while_reply_is_busy(
        self,
        iter_messages_mock,
    ):
        message = {"message_id": "message-1", "comment": "届いた"}
        iter_messages_mock.return_value = iter(
            [
                {"messages": [], "next_page_token": "next-1"},
                {"messages": [], "next_page_token": "next-2"},
                {"messages": [message], "next_page_token": None},
            ]
        )
        poller = YouTubeChatPoller("live-chat-id", max_loops=3).start()
        poller._thread.join(timeout=1)

        results = list(poller.iter_results())

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["messages"], [message])

    @patch("youtube_chat.iter_chat_messages")
    def test_poller_reports_fetch_thread_error(self, iter_messages_mock):
        def failed_iterator(*args, **kwargs):
            raise RuntimeError("API接続失敗")
            yield

        iter_messages_mock.side_effect = failed_iterator
        poller = YouTubeChatPoller("live-chat-id").start()

        with self.assertRaisesRegex(
            RuntimeError,
            "YouTubeコメント取得スレッドが停止しました: API接続失敗",
        ):
            list(poller.iter_results())


if __name__ == "__main__":
    unittest.main()
