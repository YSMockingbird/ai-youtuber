import unittest
from unittest.mock import Mock, patch

from youtube_chat import fetch_chat_messages


class YouTubeChatTest(unittest.TestCase):
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
