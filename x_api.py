import os

import requests
from requests_oauthlib import OAuth1


X_CREATE_POST_URL = "https://api.x.com/2/tweets"


class XApiClient:
    def __init__(
        self,
        api_key,
        api_key_secret,
        access_token,
        access_token_secret,
        timeout_seconds=20,
    ):
        credentials = {
            "X_API_KEY": api_key,
            "X_API_KEY_SECRET": api_key_secret,
            "X_ACCESS_TOKEN": access_token,
            "X_ACCESS_TOKEN_SECRET": access_token_secret,
        }
        missing = [name for name, value in credentials.items() if not str(value).strip()]
        if missing:
            raise RuntimeError(
                "X API認証情報が未設定です。.envを確認してください。"
                f" missing={','.join(missing)}"
            )
        self.auth = OAuth1(
            api_key,
            api_key_secret,
            access_token,
            access_token_secret,
        )
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_env(cls):
        return cls(
            api_key=os.getenv("X_API_KEY", "").strip(),
            api_key_secret=os.getenv("X_API_KEY_SECRET", "").strip(),
            access_token=os.getenv("X_ACCESS_TOKEN", "").strip(),
            access_token_secret=os.getenv("X_ACCESS_TOKEN_SECRET", "").strip(),
        )

    def create_post(self, text):
        try:
            response = requests.post(
                X_CREATE_POST_URL,
                auth=self.auth,
                json={"text": text},
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise RuntimeError(
                "X APIへの接続に失敗しました。ネットワーク接続を確認してください。"
            ) from exc

        try:
            payload = response.json()
        except requests.JSONDecodeError as exc:
            raise RuntimeError(
                "X APIからJSONではない応答が返されました。"
                f" status_code={response.status_code}"
            ) from exc

        if response.status_code != 201:
            detail = _x_error_summary(payload)
            raise RuntimeError(
                "X APIへの投稿に失敗しました。"
                f" status_code={response.status_code} detail={detail}"
            )

        data = payload.get("data") if isinstance(payload, dict) else None
        post_id = data.get("id") if isinstance(data, dict) else None
        posted_text = data.get("text") if isinstance(data, dict) else None
        if not post_id or not posted_text:
            raise RuntimeError("X APIの投稿成功応答に投稿IDまたは本文がありません。")
        return {"post_id": str(post_id), "text": str(posted_text)}


def _x_error_summary(payload):
    if not isinstance(payload, dict):
        return "unknown"
    detail = payload.get("detail") or payload.get("title")
    if detail:
        return str(detail)[:300]
    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        first = errors[0]
        if isinstance(first, dict):
            return str(first.get("detail") or first.get("message") or "unknown")[:300]
    return "unknown"
