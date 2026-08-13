import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from x_post import XPostDraftSchema, generate_x_post_draft, publish_x_post
from x_post_history import XPostHistoryRepository


class XPostDraftTest(unittest.TestCase):
    @patch("x_post.create_llm_client")
    def test_generates_short_draft_without_posting(
        self,
        create_client,
    ):
        client = Mock()
        client.generate_structured.return_value = XPostDraftSchema(
            text="深夜のタブを三つ閉じたよ。七つ増えてるけど、整理はした判定でいいかな。🖥️"
        )
        create_client.return_value = client

        result = generate_x_post_draft(
            topic="深夜のインターネット",
            now=datetime(2026, 8, 13, 23, 30, tzinfo=timezone.utc),
        )

        self.assertLessEqual(len(result["text"]), 130)
        call_kwargs = client.generate_structured.call_args.kwargs
        create_client.assert_called_once_with(
            {"provider": "gemini"},
            provider_env_var="X_LLM_PROVIDER",
        )
        self.assertEqual(call_kwargs["request_label"], "x_post_draft")
        self.assertEqual(call_kwargs["response_model"], XPostDraftSchema)
        self.assertEqual(call_kwargs["max_output_tokens"], 800)
        self.assertIn("深夜のインターネット", call_kwargs["input_text"])
        self.assertIn("管理者が指定した今回の話題を最優先", call_kwargs["input_text"])
        self.assertIn("冗談の有無を優先", call_kwargs["input_text"])
        self.assertIn("絵文字", call_kwargs["input_text"])

    def test_rejects_overly_long_topic(self):
        with self.assertRaisesRegex(ValueError, "200文字以内"):
            generate_x_post_draft(topic="長" * 201)


class XPostPublishTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = TemporaryDirectory()
        self.repository = XPostHistoryRepository(
            Path(self.temporary_directory.name) / "x_posts.db"
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_posts_and_records_history(self):
        client = Mock()
        client.create_post.return_value = {
            "post_id": "12345",
            "text": "投稿本文",
        }

        result = publish_x_post(
            "投稿本文",
            client=client,
            history_repository=self.repository,
        )

        self.assertEqual(result["post_id"], "12345")
        self.assertTrue(self.repository.has_posted("投稿本文"))

    def test_blocks_duplicate_before_api_request(self):
        first_client = Mock()
        first_client.create_post.return_value = {
            "post_id": "12345",
            "text": "同じ本文",
        }
        publish_x_post(
            "同じ本文",
            client=first_client,
            history_repository=self.repository,
        )
        second_client = Mock()

        with self.assertRaisesRegex(RuntimeError, "投稿済み"):
            publish_x_post(
                "同じ本文",
                client=second_client,
                history_repository=self.repository,
            )

        second_client.create_post.assert_not_called()

    def test_failed_request_can_be_retried(self):
        failed_client = Mock()
        failed_client.create_post.side_effect = RuntimeError("接続失敗")
        with self.assertRaisesRegex(RuntimeError, "接続失敗"):
            publish_x_post(
                "再試行する本文",
                client=failed_client,
                history_repository=self.repository,
            )

        retry_client = Mock()
        retry_client.create_post.return_value = {
            "post_id": "67890",
            "text": "再試行する本文",
        }
        result = publish_x_post(
            "再試行する本文",
            client=retry_client,
            history_repository=self.repository,
        )

        self.assertEqual(result["post_id"], "67890")

    def test_blocks_url_before_api_request(self):
        client = Mock()
        with self.assertRaisesRegex(ValueError, "URL"):
            publish_x_post(
                "詳しくは https://example.com を見てね",
                client=client,
                history_repository=self.repository,
            )
        client.create_post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
