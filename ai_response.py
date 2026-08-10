import os

from openai import APIConnectionError, APIStatusError, AuthenticationError, OpenAI, RateLimitError

from character import CHARACTER_PROMPT


def generate_ai_response(user_name, comment):
    # .envから読み込まれたAPIキーとモデル名を使います。
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip()

    if not api_key or "ここに" in api_key:
        raise RuntimeError("OPENAI_API_KEYが未設定です。.envにOpenAI APIキーを設定してください。")

    if not model:
        raise RuntimeError("OPENAI_MODELが未設定です。.envに使用するモデル名を設定してください。")

    client = OpenAI(api_key=api_key)

    prompt = (
        f"ユーザー名: {user_name}\n"
        f"コメント: {comment}\n\n"
        "このコメントに対して、AIキャラクターとして自然に返答してください。"
    )

    try:
        response = client.responses.create(
            model=model,
            instructions=CHARACTER_PROMPT,
            input=prompt,
            max_output_tokens=120,
        )
    except AuthenticationError as exc:
        raise RuntimeError("OpenAI APIの認証に失敗しました。.envのOPENAI_API_KEYを確認してください。") from exc
    except RateLimitError as exc:
        raise RuntimeError("OpenAI APIのレート制限または利用上限に達しました。OpenAIの利用状況を確認してください。") from exc
    except APIConnectionError as exc:
        raise RuntimeError("OpenAI APIへの接続に失敗しました。ネットワーク接続を確認してください。") from exc
    except APIStatusError as exc:
        raise RuntimeError(f"OpenAI APIでエラーが発生しました。status_code={exc.status_code}") from exc

    answer = response.output_text.strip()
    if not answer:
        raise RuntimeError("OpenAI APIから空の返答が返りました。")

    return answer

