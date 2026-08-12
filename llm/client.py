import os
import logging
import time
from abc import ABC, abstractmethod

from pydantic import ValidationError

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
        request_label="llm",
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
        request_label="llm",
    ):
        retry_tokens = max(int(max_output_tokens) * 2, 1200)
        token_limits = (int(max_output_tokens), retry_tokens)

        for attempt, token_limit in enumerate(token_limits):
            started_at = time.monotonic()
            try:
                response = self._request_structured(
                    instructions=instructions,
                    input_text=input_text,
                    response_model=response_model,
                    max_output_tokens=token_limit,
                )
            except ValidationError as exc:
                if attempt == 0 and _is_incomplete_json_error(exc):
                    logging.warning(
                        "OpenAI APIのJSONが途中で終了したため、"
                        "出力上限を増やして1回再試行します。"
                        " max_output_tokens=%s->%s",
                        token_limit,
                        retry_tokens,
                    )
                    continue
                raise RuntimeError(
                    "OpenAI APIの構造化出力を検証できませんでした。"
                    f" detail={_validation_error_summary(exc)}"
                ) from exc

            _print_usage(
                response,
                request_label=request_label,
                attempt=attempt + 1,
                elapsed_seconds=time.monotonic() - started_at,
            )

            parsed = response.output_parsed
            if parsed is not None:
                return parsed

            status, incomplete_reason, error_code = _response_diagnostics(
                response
            )
            if attempt == 0 and incomplete_reason == "max_output_tokens":
                logging.warning(
                    "OpenAI APIの出力上限に達したため、"
                    "上限を増やして1回再試行します。"
                    " max_output_tokens=%s->%s",
                    token_limit,
                    retry_tokens,
                )
                continue
            raise RuntimeError(
                "OpenAI APIから構造化された返答を取得できませんでした。"
                f" status={status} incomplete_reason={incomplete_reason}"
                f" error_code={error_code}"
            )

        raise RuntimeError(
            "OpenAI APIの構造化出力を再試行後も取得できませんでした。"
        )

    def _request_structured(
        self,
        instructions,
        input_text,
        response_model,
        max_output_tokens,
    ):
        try:
            return self.client.responses.parse(
                model=self.model,
                instructions=instructions,
                input=input_text,
                text_format=response_model,
                max_output_tokens=max_output_tokens,
                reasoning={"effort": "low"},
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


def _is_incomplete_json_error(error):
    for detail in error.errors():
        if detail.get("type") != "json_invalid":
            continue
        message = str(detail.get("msg", "")).lower()
        if "eof" in message or "end of input" in message:
            return True
    return False


def _validation_error_summary(error):
    details = error.errors()
    if not details:
        return "unknown"
    first = details[0]
    return f"type={first.get('type', 'unknown')} msg={first.get('msg', '')}"


def _response_diagnostics(response):
    status = getattr(response, "status", None) or "unknown"
    incomplete_details = getattr(response, "incomplete_details", None)
    incomplete_reason = (
        getattr(incomplete_details, "reason", None) or "none"
    )
    error = getattr(response, "error", None)
    error_code = getattr(error, "code", None) or "none"
    return status, incomplete_reason, error_code


def _print_usage(response, request_label, attempt, elapsed_seconds):
    # APIが返す実測値だけを表示し、プロンプト本文や認証情報はログへ出しません。
    usage = getattr(response, "usage", None)
    if usage is None:
        print(
            "LLM使用量："
            f"処理={request_label} 試行={attempt} "
            "usage=取得不可 "
            f"応答時間={elapsed_seconds:.2f}秒"
        )
        return

    input_details = getattr(usage, "input_tokens_details", None)
    output_details = getattr(usage, "output_tokens_details", None)
    print(
        "LLM使用量："
        f"処理={request_label} 試行={attempt} "
        f"入力={getattr(usage, 'input_tokens', 0)} "
        f"キャッシュ={getattr(input_details, 'cached_tokens', 0)} "
        f"出力={getattr(usage, 'output_tokens', 0)} "
        f"推論={getattr(output_details, 'reasoning_tokens', 0)} "
        f"合計={getattr(usage, 'total_tokens', 0)} "
        f"応答時間={elapsed_seconds:.2f}秒"
    )


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
