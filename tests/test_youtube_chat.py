import unittest
from unittest.mock import Mock, patch

from youtube_chat import (
    fetch_chat_messages,
    get_live_chat_id,
    iter_chat_messages,
    resolve_youtube_video_id,
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


if __name__ == "__main__":
    unittest.main()
