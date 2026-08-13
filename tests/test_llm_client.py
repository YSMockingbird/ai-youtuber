import os
import unittest
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from httpx import ConnectError, Request
from pydantic import BaseModel, ValidationError

from llm.client import GeminiLlmClient, OpenAiLlmClient, create_llm_client


class TestResponse(BaseModel):
    text: str


class OpenAiLlmClientTest(unittest.TestCase):
    def create_client(self):
        client = OpenAiLlmClient("test-api-key", "gpt-5.6-luna")
        client.client = Mock()
        return client

    def test_uses_low_reasoning_effort(self):
        client = self.create_client()
        parsed = TestResponse(text="成功")
        client.client.responses.parse.return_value = SimpleNamespace(
            output_parsed=parsed,
        )

        result = client.generate_structured(
            "指示",
            "入力",
            TestResponse,
            800,
        )

        self.assertEqual(result, parsed)
        call_kwargs = client.client.responses.parse.call_args.kwargs
        self.assertEqual(call_kwargs["reasoning"], {"effort": "low"})
        self.assertEqual(call_kwargs["max_output_tokens"], 800)

    def test_retries_once_when_json_ends_midway(self):
        client = self.create_client()
        try:
            TestResponse.model_validate_json('{"text":"途中')
        except ValidationError as exc:
            validation_error = exc
        parsed = TestResponse(text="再試行成功")
        client.client.responses.parse.side_effect = [
            validation_error,
            SimpleNamespace(output_parsed=parsed),
        ]

        result = client.generate_structured(
            "指示",
            "入力",
            TestResponse,
            800,
        )

        self.assertEqual(result, parsed)
        self.assertEqual(client.client.responses.parse.call_count, 2)
        first_call, second_call = (
            client.client.responses.parse.call_args_list
        )
        self.assertEqual(first_call.kwargs["max_output_tokens"], 800)
        self.assertEqual(second_call.kwargs["max_output_tokens"], 1600)

    def test_retries_once_when_response_reports_output_limit(self):
        client = self.create_client()
        incomplete_response = SimpleNamespace(
            output_parsed=None,
            status="incomplete",
            incomplete_details=SimpleNamespace(reason="max_output_tokens"),
            error=None,
        )
        parsed = TestResponse(text="再試行成功")
        client.client.responses.parse.side_effect = [
            incomplete_response,
            SimpleNamespace(output_parsed=parsed),
        ]

        result = client.generate_structured(
            "指示",
            "入力",
            TestResponse,
            800,
        )

        self.assertEqual(result, parsed)
        self.assertEqual(client.client.responses.parse.call_count, 2)

    def test_content_filter_is_not_retried_and_reason_is_reported(self):
        client = self.create_client()
        client.client.responses.parse.return_value = SimpleNamespace(
            output_parsed=None,
            status="incomplete",
            incomplete_details=SimpleNamespace(reason="content_filter"),
            error=None,
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "incomplete_reason=content_filter",
        ):
            client.generate_structured(
                "指示",
                "入力",
                TestResponse,
                800,
            )

        client.client.responses.parse.assert_called_once()

    def test_prints_api_usage_without_prompt_content(self):
        client = self.create_client()
        parsed = TestResponse(text="成功")
        client.client.responses.parse.return_value = SimpleNamespace(
            output_parsed=parsed,
            usage=SimpleNamespace(
                input_tokens=120,
                output_tokens=30,
                total_tokens=150,
                input_tokens_details=SimpleNamespace(cached_tokens=80),
                output_tokens_details=SimpleNamespace(reasoning_tokens=10),
            ),
        )
        output = StringIO()

        with redirect_stdout(output):
            client.generate_structured(
                "秘密の指示",
                "秘密の入力",
                TestResponse,
                800,
                request_label="comment_reply",
            )

        log = output.getvalue()
        self.assertIn("処理=comment_reply", log)
        self.assertIn("入力=120", log)
        self.assertIn("キャッシュ=80", log)
        self.assertIn("出力=30", log)
        self.assertIn("推論=10", log)
        self.assertIn("合計=150", log)
        self.assertNotIn("秘密の指示", log)
        self.assertNotIn("秘密の入力", log)


class FakeGeminiClientError(Exception):
    def __init__(self, code):
        super().__init__(str(code))
        self.code = code


class FakeGeminiServerError(Exception):
    def __init__(self, code):
        super().__init__(str(code))
        self.code = code


class GeminiLlmClientTest(unittest.TestCase):
    def create_client(self):
        client = object.__new__(GeminiLlmClient)
        client.client = Mock()
        client.errors = SimpleNamespace(
            ClientError=FakeGeminiClientError,
            ServerError=FakeGeminiServerError,
        )
        client.types = SimpleNamespace(
            GenerateContentConfig=lambda **kwargs: kwargs,
            ThinkingConfig=lambda **kwargs: kwargs,
        )
        client.model = "gemini-test"
        return client

    def test_uses_structured_output_schema(self):
        client = self.create_client()
        parsed = TestResponse(text="成功")
        client.client.models.generate_content.return_value = SimpleNamespace(
            parsed=parsed,
        )

        result = client.generate_structured(
            "指示",
            "入力",
            TestResponse,
            300,
        )

        self.assertEqual(result, parsed)
        call_kwargs = client.client.models.generate_content.call_args.kwargs
        self.assertEqual(call_kwargs["model"], "gemini-test")
        self.assertEqual(call_kwargs["contents"], "入力")
        self.assertEqual(call_kwargs["config"]["system_instruction"], "指示")
        self.assertEqual(
            call_kwargs["config"]["response_json_schema"],
            TestResponse.model_json_schema(),
        )
        self.assertEqual(call_kwargs["config"]["max_output_tokens"], 300)
        self.assertEqual(
            call_kwargs["config"]["thinking_config"],
            {"thinking_budget": 0},
        )

    def test_reports_rate_limit(self):
        client = self.create_client()
        client.client.models.generate_content.side_effect = (
            FakeGeminiClientError(429)
        )

        with self.assertRaisesRegex(RuntimeError, "レート制限"):
            client.generate_structured(
                "指示",
                "入力",
                TestResponse,
                300,
            )

    def test_reports_network_error_without_leaking_sdk_exception(self):
        client = self.create_client()
        client.client.models.generate_content.side_effect = ConnectError(
            "接続失敗",
            request=Request("POST", "https://example.invalid"),
        )

        with self.assertRaisesRegex(RuntimeError, "ネットワーク接続"):
            client.generate_structured(
                "指示",
                "入力",
                TestResponse,
                300,
            )

    def test_reports_finish_reason_when_parsed_output_is_missing(self):
        client = self.create_client()
        client.client.models.generate_content.return_value = SimpleNamespace(
            parsed=None,
            candidates=[
                SimpleNamespace(
                    finish_reason=SimpleNamespace(name="MAX_TOKENS")
                )
            ],
        )

        with self.assertRaisesRegex(RuntimeError, "MAX_TOKENS"):
            client.generate_structured(
                "指示",
                "入力",
                TestResponse,
                300,
            )

    @patch("llm.client.GeminiLlmClient")
    @patch("llm.client.OpenAiLlmClient")
    def test_x_provider_is_independent_from_live_provider(
        self,
        openai_client,
        gemini_client,
    ):
        environment = {
            "LLM_PROVIDER": "openai",
            "X_LLM_PROVIDER": "gemini",
            "GEMINI_API_KEY": "test-gemini-key",
            "GEMINI_MODEL": "gemini-test",
        }
        with patch.dict(os.environ, environment, clear=False):
            result = create_llm_client(
                {"provider": "gemini"},
                provider_env_var="X_LLM_PROVIDER",
            )

        self.assertEqual(result, gemini_client.return_value)
        gemini_client.assert_called_once_with(
            api_key="test-gemini-key",
            model="gemini-test",
        )
        openai_client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
