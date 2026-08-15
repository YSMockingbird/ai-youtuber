import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from youtube_oauth import (
    _save_credentials,
    complete_youtube_broadcast,
    create_youtube_broadcast,
    find_active_youtube_broadcast,
    find_youtube_autostart_conflicts,
    find_upcoming_youtube_broadcast,
    find_reusable_youtube_stream,
    get_youtube_oauth_paths,
    is_youtube_broadcast_live,
    is_youtube_stream_active,
    NoActiveYouTubeBroadcastError,
    NoUpcomingYouTubeBroadcastError,
    transition_youtube_broadcast_to_live,
    update_youtube_broadcast_metadata,
    YOUTUBE_MANAGE_SCOPE,
    get_youtube_credentials,
)


class YouTubeOAuthTest(unittest.TestCase):
    @patch("youtube_oauth.requests.put")
    @patch("youtube_oauth.requests.get")
    def test_persistent_broadcast_metadata_can_be_updated(
        self,
        get_mock,
        put_mock,
    ):
        get_response = Mock()
        get_response.json.return_value = {
            "items": [
                {
                    "snippet": {
                        "title": "S のライブ配信",
                        "description": "以前の説明",
                        "categoryId": "22",
                        "tags": ["AI", "VTuber"],
                    },
                    "status": {
                        "privacyStatus": "unlisted",
                        "embeddable": True,
                        "license": "youtube",
                        "publicStatsViewable": True,
                        "selfDeclaredMadeForKids": False,
                    },
                }
            ]
        }
        get_mock.return_value = get_response
        put_mock.return_value = Mock()

        result = update_youtube_broadcast_metadata(
            "persistent-video",
            "自己紹介ライブ",
            description="新しい説明",
            privacy_status="unlisted",
            credentials=SimpleNamespace(token="access-token"),
        )

        self.assertEqual(result["video_id"], "persistent-video")
        body = put_mock.call_args.kwargs["json"]
        self.assertEqual(body["snippet"]["categoryId"], "22")
        self.assertEqual(body["snippet"]["tags"], ["AI", "VTuber"])
        self.assertTrue(body["status"]["embeddable"])
        self.assertEqual(body["status"]["privacyStatus"], "unlisted")

    @patch.dict("os.environ", {"YOUTUBE_STREAM_ID": "stream-id"})
    @patch("youtube_oauth.requests.get")
    def test_configured_reusable_stream_is_selected(self, get_mock):
        response = Mock()
        response.json.return_value = {
            "items": [
                {
                    "id": "stream-id",
                    "snippet": {"title": "OBSストリーム"},
                    "status": {"streamStatus": "ready"},
                    "contentDetails": {"isReusable": True},
                }
            ]
        }
        get_mock.return_value = response

        result = find_reusable_youtube_stream(
            credentials=SimpleNamespace(token="access-token")
        )

        self.assertEqual(result["stream_id"], "stream-id")
        self.assertEqual(get_mock.call_args.kwargs["params"]["id"], "stream-id")

    @patch("youtube_oauth.find_youtube_autostart_conflicts")
    @patch("youtube_oauth.find_reusable_youtube_stream")
    @patch("youtube_oauth.requests.post")
    def test_broadcast_is_created_and_bound_to_reusable_stream(
        self,
        post_mock,
        find_stream_mock,
        find_conflicts_mock,
    ):
        find_stream_mock.return_value = {
            "stream_id": "stream-id",
            "title": "OBSストリーム",
            "stream_status": "ready",
        }
        find_conflicts_mock.return_value = []
        create_response = Mock()
        create_response.json.return_value = {"id": "created-video"}
        bind_response = Mock()
        bind_response.json.return_value = {"id": "created-video"}
        post_mock.side_effect = [create_response, bind_response]

        result = create_youtube_broadcast(
            "自己紹介ライブ",
            description="テスト説明",
            privacy_status="unlisted",
            credentials=SimpleNamespace(token="access-token"),
            scheduled_start_time=datetime(
                2026,
                8,
                14,
                12,
                0,
                tzinfo=timezone.utc,
            ),
        )

        self.assertEqual(result["video_id"], "created-video")
        self.assertEqual(result["bound_stream_id"], "stream-id")
        self.assertFalse(result["enable_auto_start"])
        self.assertEqual(post_mock.call_count, 2)
        self.assertEqual(
            post_mock.call_args_list[0].kwargs["json"]["snippet"]["scheduledStartTime"],
            "2026-08-14T12:00:00Z",
        )
        self.assertEqual(
            post_mock.call_args_list[1].kwargs["params"]["streamId"],
            "stream-id",
        )

    @patch("youtube_oauth.requests.get")
    def test_autostart_conflict_is_detected(self, get_mock):
        response = Mock()
        response.json.return_value = {
            "items": [
                {
                    "id": "persistent-video",
                    "snippet": {"title": "S のライブ配信"},
                    "status": {"lifeCycleStatus": "ready"},
                    "contentDetails": {
                        "boundStreamId": "stream-id",
                        "enableAutoStart": True,
                    },
                }
            ]
        }
        get_mock.return_value = response

        result = find_youtube_autostart_conflicts(
            "stream-id",
            credentials=SimpleNamespace(token="access-token"),
        )

        self.assertEqual(result[0]["video_id"], "persistent-video")

    @patch("youtube_oauth.requests.get")
    def test_stream_active_status_is_returned(self, get_mock):
        response = Mock()
        response.json.return_value = {
            "items": [{"status": {"streamStatus": "active"}}]
        }
        get_mock.return_value = response

        self.assertTrue(
            is_youtube_stream_active(
                "stream-id",
                credentials=SimpleNamespace(token="access-token"),
            )
        )

    @patch("youtube_oauth.requests.post")
    def test_broadcast_can_transition_to_live(self, post_mock):
        response = Mock()
        response.json.return_value = {
            "status": {"lifeCycleStatus": "liveStarting"}
        }
        post_mock.return_value = response

        result = transition_youtube_broadcast_to_live(
            "video-id",
            credentials=SimpleNamespace(token="access-token"),
        )

        self.assertEqual(result, "liveStarting")
        self.assertEqual(
            post_mock.call_args.kwargs["params"]["broadcastStatus"],
            "live",
        )

    @patch("youtube_oauth.requests.get")
    def test_single_ready_upcoming_broadcast_is_returned(self, get_mock):
        response = Mock()
        response.json.return_value = {
            "items": [
                {
                    "id": "upcoming-video",
                    "snippet": {
                        "title": "予約テスト",
                        "scheduledStartTime": "2026-08-14T12:00:00Z",
                    },
                    "status": {
                        "lifeCycleStatus": "ready",
                        "privacyStatus": "unlisted",
                    },
                    "contentDetails": {
                        "boundStreamId": "stream-id",
                        "enableAutoStart": False,
                        "enableAutoStop": True,
                    },
                }
            ]
        }
        get_mock.return_value = response

        result = find_upcoming_youtube_broadcast(
            credentials=SimpleNamespace(token="access-token")
        )

        self.assertEqual(result["video_id"], "upcoming-video")
        self.assertEqual(result["bound_stream_id"], "stream-id")
        self.assertFalse(result["enable_auto_start"])

    @patch("youtube_oauth.requests.get")
    def test_no_ready_broadcast_uses_specific_error(self, get_mock):
        response = Mock()
        response.json.return_value = {"items": []}
        get_mock.return_value = response

        with self.assertRaises(NoUpcomingYouTubeBroadcastError):
            find_upcoming_youtube_broadcast(
                credentials=SimpleNamespace(token="access-token")
            )

    @patch("youtube_oauth.requests.post")
    def test_broadcast_can_be_transitioned_to_complete(self, post_mock):
        response = Mock()
        response.json.return_value = {
            "status": {"lifeCycleStatus": "complete"}
        }
        post_mock.return_value = response

        result = complete_youtube_broadcast(
            "video-id",
            credentials=SimpleNamespace(token="access-token"),
        )

        self.assertEqual(result, "complete")
        self.assertEqual(
            post_mock.call_args.kwargs["params"]["broadcastStatus"],
            "complete",
        )
        self.assertEqual(post_mock.call_args.kwargs["params"]["id"], "video-id")

    def test_manage_scope_includes_read_and_broadcast_control(self):
        self.assertEqual(
            YOUTUBE_MANAGE_SCOPE,
            "https://www.googleapis.com/auth/youtube",
        )

    @patch("youtube_oauth._save_credentials")
    @patch("youtube_oauth.InstalledAppFlow.from_client_secrets_file")
    @patch("youtube_oauth._load_saved_credentials")
    @patch("youtube_oauth.get_youtube_oauth_paths")
    def test_readonly_token_triggers_manage_scope_reauthorization(
        self,
        paths_mock,
        load_credentials_mock,
        flow_factory_mock,
        save_credentials_mock,
    ):
        client_file = Mock()
        client_file.is_file.return_value = True
        token_file = Mock()
        paths_mock.return_value = (client_file, token_file)
        readonly_credentials = Mock(expired=False, valid=True)
        readonly_credentials.has_scopes.return_value = False
        load_credentials_mock.return_value = readonly_credentials
        managed_credentials = Mock(valid=True)
        flow_factory_mock.return_value.run_local_server.return_value = (
            managed_credentials
        )

        result = get_youtube_credentials()

        self.assertIs(result, managed_credentials)
        flow_factory_mock.assert_called_once_with(
            str(client_file),
            scopes=[YOUTUBE_MANAGE_SCOPE],
        )
        save_credentials_mock.assert_called_once_with(
            managed_credentials,
            token_file,
        )

    @patch("youtube_oauth.requests.get")
    def test_specific_broadcast_live_status_is_returned(self, get_mock):
        response = Mock()
        response.json.return_value = {
            "items": [
                {
                    "id": "video-id",
                    "status": {"lifeCycleStatus": "complete"},
                }
            ]
        }
        get_mock.return_value = response

        result = is_youtube_broadcast_live(
            "video-id",
            credentials=SimpleNamespace(token="access-token"),
        )

        self.assertFalse(result)
        self.assertEqual(get_mock.call_args.kwargs["params"]["id"], "video-id")

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

        with self.assertRaisesRegex(
            NoActiveYouTubeBroadcastError,
            "現在ライブ中の配信がありません",
        ):
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
