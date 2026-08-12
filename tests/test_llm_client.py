import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from pydantic import BaseModel, ValidationError

from llm.client import OpenAiLlmClient


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


if __name__ == "__main__":
    unittest.main()
