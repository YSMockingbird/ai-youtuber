from datetime import datetime
import re

from pydantic import BaseModel, ConfigDict, Field

from character import CHARACTER_PROMPT
from llm.client import create_llm_client


URL_PATTERN = re.compile(r"(?:https?://|www\.)", re.IGNORECASE)


class XPostDraftSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=130)


def generate_x_post_draft(topic=None, now=None):
    normalized_topic = str(topic or "").strip()
    if len(normalized_topic) > 200:
        raise ValueError("X投稿の話題は200文字以内で指定してください。")

    current_time = now or datetime.now().astimezone()
    topic_instruction = (
        (
            "管理者が指定した今回の話題を最優先してください。"
            "自己紹介を指定された場合は、簡単な自己紹介にしてください。\n"
            f"今回の話題: {normalized_topic}"
        )
        if normalized_topic
        else (
            "今回の話題は、時刻に合う軽い日常、インターネット、"
            "AIとしての小さな気付き、趣味のいずれか一つを選んでください。"
        )
    )
    style_instruction = (
        "管理者が話題内で指定した文体や、冗談の有無を優先してください。"
        "指定されていない要素を無理に追加しないでください。"
        if normalized_topic
        else (
            "具体的な一点へ軽く執着し、真面目な口調のまま"
            "判断を一か所だけ妙にしてください。"
        )
    )
    prompt = (
        "Xへ投稿する短い独り言を1件だけ作ってください。\n"
        "返信ではないため、ユーザー名、呼びかけ、質問、回答を含めないでください。\n"
        "130文字以内、1〜3文の自然な日本語にしてください。\n"
        "絵文字は内容に合う場合だけ0〜1個使えます。ハッシュタグは付けないでください。\n"
        "投稿日時だけから天気、ニュース、記念日、現実の出来事を推測しないでください。\n"
        f"{style_instruction}\n"
        "文章を引用符で囲まず、投稿本文だけをtextへ入れてください。\n"
        f"投稿日時: {current_time.isoformat(timespec='minutes')}\n"
        f"{topic_instruction}"
    )
    # ライブ用LLM_PROVIDERを参照せず、X専用設定だけでクライアントを選びます。
    client = create_llm_client(
        {"provider": "gemini"},
        provider_env_var="X_LLM_PROVIDER",
    )
    parsed = client.generate_structured(
        instructions=CHARACTER_PROMPT,
        input_text=prompt,
        response_model=XPostDraftSchema,
        max_output_tokens=800,
        request_label="x_post_draft",
    )
    return parsed.model_dump()


def publish_x_post(text, client=None, history_repository=None):
    normalized_text = str(text or "").strip()
    if not normalized_text:
        raise ValueError("Xへ投稿する本文が空です。")
    if len(normalized_text) > 280:
        raise ValueError("Xへ投稿する本文は280文字以内にしてください。")
    if URL_PATTERN.search(normalized_text):
        raise ValueError(
            "URLを含む投稿は料金が高いため、現在は投稿できません。"
        )

    if client is None:
        from x_api import XApiClient

        client = XApiClient.from_env()
    if history_repository is None:
        from x_post_history import XPostHistoryRepository

        history_repository = XPostHistoryRepository()

    history_id = history_repository.reserve(normalized_text)
    try:
        result = client.create_post(normalized_text)
    except RuntimeError as exc:
        history_repository.record_failed(history_id, exc)
        raise
    history_repository.record_posted(history_id, result["post_id"])
    return result
