import unittest
from unittest.mock import Mock, patch

import requests

from x_api import XApiClient


class XApiClientTest(unittest.TestCase):
    def create_client(self):
        return XApiClient(
            "api-key",
            "api-secret",
            "access-token",
            "access-secret",
        )

    @patch("x_api.requests.post")
    def test_creates_post_with_oauth(self, post):
        response = Mock(status_code=201)
        response.json.return_value = {
            "data": {"id": "12345", "text": "投稿本文"},
        }
        post.return_value = response
        client = self.create_client()

        result = client.create_post("投稿本文")

        self.assertEqual(result["post_id"], "12345")
        call_kwargs = post.call_args.kwargs
        self.assertEqual(call_kwargs["json"], {"text": "投稿本文"})
        self.assertEqual(call_kwargs["timeout"], 20)
        self.assertIs(call_kwargs["auth"], client.auth)

    @patch("x_api.requests.post")
    def test_reports_api_error_without_credentials(self, post):
        response = Mock(status_code=403)
        response.json.return_value = {"detail": "Forbidden"}
        post.return_value = response

        with self.assertRaisesRegex(RuntimeError, "status_code=403"):
            self.create_client().create_post("投稿本文")

    @patch("x_api.requests.post")
    def test_reports_connection_error(self, post):
        post.side_effect = requests.ConnectionError("接続失敗")

        with self.assertRaisesRegex(RuntimeError, "ネットワーク接続"):
            self.create_client().create_post("投稿本文")


if __name__ == "__main__":
    unittest.main()
