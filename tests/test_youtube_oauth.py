import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from youtube_oauth import (
    _save_credentials,
    find_active_youtube_broadcast,
    get_youtube_oauth_paths,
)


class YouTubeOAuthTest(unittest.TestCase):
    @patch.dict(
        "os.environ",
        {
            "YOUTUBE_OAUTH_CLIENT_FILE": ".secrets/client.json",
            "YOUTUBE_OAUTH_TOKEN_FILE": ".secrets/token.json",
        },
        clear=False,
    )
    def test_relative_paths_are_resolved_from_project_root(self):
        client_file, token_file = get_youtube_oauth_paths()

        self.assertTrue(client_file.is_absolute())
        self.assertTrue(token_file.is_absolute())
        self.assertEqual(client_file.name, "client.json")
        self.assertEqual(token_file.name, "token.json")

    @patch("youtube_oauth.requests.get")
    def test_single_active_broadcast_is_returned(self, get_mock):
        response = Mock()
        response.json.return_value = {
            "items": [
                {
                    "id": "video-id",
                    "snippet": {"title": "ライブ配信"},
                    "status": {"lifeCycleStatus": "live"},
                }
            ]
        }
        get_mock.return_value = response

        result = find_active_youtube_broadcast(
            credentials=SimpleNamespace(token="access-token")
        )

        self.assertEqual(
            result,
            {"video_id": "video-id", "title": "ライブ配信"},
        )
        self.assertEqual(
            get_mock.call_args.kwargs["headers"]["Authorization"],
            "Bearer access-token",
        )
        self.assertNotIn(
            "broadcastStatus",
            get_mock.call_args.kwargs["params"],
        )
        self.assertEqual(get_mock.call_args.kwargs["params"]["mine"], "true")

    @patch("youtube_oauth.requests.get")
    def test_no_active_broadcast_has_meaningful_error(self, get_mock):
        response = Mock()
        response.json.return_value = {"items": []}
        get_mock.return_value = response

        with self.assertRaisesRegex(RuntimeError, "現在ライブ中の配信がありません"):
            find_active_youtube_broadcast(
                credentials=SimpleNamespace(token="access-token")
            )

    @patch("youtube_oauth.requests.get")
    def test_multiple_active_broadcasts_are_not_selected(self, get_mock):
        response = Mock()
        response.json.return_value = {
            "items": [
                {
                    "id": "video-1",
                    "snippet": {"title": "配信1"},
                    "status": {"lifeCycleStatus": "live"},
                },
                {
                    "id": "video-2",
                    "snippet": {"title": "配信2"},
                    "status": {"lifeCycleStatus": "live"},
                },
            ]
        }
        get_mock.return_value = response

        with self.assertRaisesRegex(RuntimeError, "複数見つかった"):
            find_active_youtube_broadcast(
                credentials=SimpleNamespace(token="access-token")
            )

    @patch("youtube_oauth.requests.get")
    def test_upcoming_broadcast_is_not_selected(self, get_mock):
        response = Mock()
        response.json.return_value = {
            "items": [
                {
                    "id": "upcoming-video",
                    "snippet": {"title": "予約配信"},
                    "status": {"lifeCycleStatus": "ready"},
                }
            ]
        }
        get_mock.return_value = response

        with self.assertRaisesRegex(RuntimeError, "現在ライブ中の配信がありません"):
            find_active_youtube_broadcast(
                credentials=SimpleNamespace(token="access-token")
            )

    def test_saved_token_is_owner_read_write_only(self):
        credentials = Mock()
        credentials.to_json.return_value = '{"token":"test"}'
        with tempfile.TemporaryDirectory() as temporary_directory:
            token_file = Path(temporary_directory) / "token.json"

            _save_credentials(credentials, token_file)

            self.assertEqual(token_file.read_text(), '{"token":"test"}')
            self.assertEqual(token_file.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
