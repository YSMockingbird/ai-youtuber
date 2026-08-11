import os
from abc import ABC, abstractmethod

from openai import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    OpenAI,
    RateLimitError,
)


class LlmClient(ABC):
    @abstractmethod
    def generate_structured(
        self,
        instructions,
        input_text,
        response_model,
        max_output_tokens,
    ):
        """構造化されたLLM応答を返します。"""


class OpenAiLlmClient(LlmClient):
    def __init__(self, api_key, model):
        if not api_key or "ここに" in api_key:
            raise RuntimeError(
                "OPENAI_API_KEYが未設定です。.envにOpenAI APIキーを設定してください。"
            )
        if not model:
            raise RuntimeError(
                "OPENAI_MODELが未設定です。.envに使用するモデル名を設定してください。"
            )
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def generate_structured(
        self,
        instructions,
        input_text,
        response_model,
        max_output_tokens,
    ):
        try:
            response = self.client.responses.parse(
                model=self.model,
                instructions=instructions,
                input=input_text,
                text_format=response_model,
                max_output_tokens=max_output_tokens,
            )
        except AuthenticationError as exc:
            raise RuntimeError(
                "OpenAI APIの認証に失敗しました。.envのOPENAI_API_KEYを確認してください。"
            ) from exc
        except RateLimitError as exc:
            raise RuntimeError(
                "OpenAI APIのレート制限または利用上限に達しました。"
                "OpenAIの利用状況を確認してください。"
            ) from exc
        except APIConnectionError as exc:
            raise RuntimeError(
                "OpenAI APIへの接続に失敗しました。ネットワーク接続を確認してください。"
            ) from exc
        except APIStatusError as exc:
            raise RuntimeError(
                f"OpenAI APIでエラーが発生しました。status_code={exc.status_code}"
            ) from exc

        parsed = response.output_parsed
        if parsed is None:
            raise RuntimeError("OpenAI APIから構造化された返答を取得できませんでした。")
        return parsed


def create_llm_client(config=None):
    config = config or {}
    provider = os.getenv(
        "LLM_PROVIDER",
        str(config.get("provider", "openai")),
    ).strip().lower()
    if provider == "openai":
        return OpenAiLlmClient(
            api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            model=os.getenv("OPENAI_MODEL", "gpt-5.6-luna").strip(),
        )
    if provider in {"groq", "gemini"}:
        raise RuntimeError(
            f"LLM_PROVIDER={provider}はまだ設定されていません。"
            "プロバイダー決定後に対応クライアントを追加してください。"
        )
    raise RuntimeError(f"未対応のLLM_PROVIDERです: {provider}")
