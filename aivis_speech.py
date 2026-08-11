import requests


DEFAULT_AIVIS_API_URL = "http://127.0.0.1:10101"
VOICE_SETTINGS_BY_EMOTION = {
    "neutral": {
        "speedScale": 1.2,
    },
}


class AivisSpeechClient:
    def __init__(self, base_url=DEFAULT_AIVIS_API_URL, timeout_seconds=180):
        normalized_url = base_url.strip().rstrip("/")
        if not normalized_url:
            raise ValueError("AivisSpeech APIのURLが空です。")

        self.base_url = normalized_url
        self.timeout_seconds = timeout_seconds

    def get_version(self):
        response = self._request("GET", "/version", timeout_seconds=5)
        version = response.text.strip().strip('"')
        if not version:
            raise RuntimeError(
                "AivisSpeech Engineからバージョン情報を取得できませんでした。"
            )
        return version

    def get_speakers(self):
        response = self._request("GET", "/speakers", timeout_seconds=10)
        try:
            speakers = response.json()
        except requests.exceptions.JSONDecodeError as exc:
            raise RuntimeError(
                "AivisSpeech Engineの話者一覧をJSONとして読み取れませんでした。"
            ) from exc

        if not isinstance(speakers, list):
            raise RuntimeError(
                "AivisSpeech Engineの話者一覧が期待する形式ではありません。"
            )
        return speakers

    def get_styles(self):
        # 話者とスタイルを設定しやすい一覧へ変換します。
        styles = []
        for speaker in self.get_speakers():
            speaker_name = str(speaker.get("name", "名前なし"))
            for style in speaker.get("styles", []):
                style_id = style.get("id")
                if not isinstance(style_id, int):
                    continue
                styles.append(
                    {
                        "speaker_name": speaker_name,
                        "style_name": str(style.get("name", "スタイル名なし")),
                        "style_id": style_id,
                    }
                )
        return styles

    def synthesize(self, text, speaker_id, emotion="neutral"):
        normalized_text = text.strip()
        if not normalized_text:
            raise ValueError("音声合成する文章が空です。")
        if len(normalized_text) > 1000:
            raise ValueError("音声合成する文章は1,000文字以内で指定してください。")
        if not isinstance(speaker_id, int):
            raise ValueError("AivisSpeechのスタイルIDは整数で指定してください。")

        query_response = self._request(
            "POST",
            "/audio_query",
            params={"text": normalized_text, "speaker": speaker_id},
        )
        try:
            audio_query = query_response.json()
        except requests.exceptions.JSONDecodeError as exc:
            raise RuntimeError(
                "AivisSpeechの音声合成クエリをJSONとして読み取れませんでした。"
            ) from exc

        # 感情ごとの音声設定を、AivisSpeechの既定クエリへ上書きします。
        voice_settings = VOICE_SETTINGS_BY_EMOTION.get(emotion, {})
        audio_query.update(voice_settings)

        synthesis_response = self._request(
            "POST",
            "/synthesis",
            params={"speaker": speaker_id},
            json=audio_query,
        )
        audio_data = synthesis_response.content
        if not audio_data:
            raise RuntimeError("AivisSpeech Engineから空の音声データが返りました。")
        return audio_data

    def _request(self, method, path, timeout_seconds=None, **kwargs):
        timeout = timeout_seconds or self.timeout_seconds
        try:
            response = requests.request(
                method,
                f"{self.base_url}{path}",
                timeout=timeout,
                **kwargs,
            )
            response.raise_for_status()
            return response
        except requests.exceptions.Timeout as exc:
            raise RuntimeError(
                "AivisSpeech Engineへの接続がタイムアウトしました。"
                f"URL={self.base_url}{path} timeout={timeout}秒"
            ) from exc
        except requests.exceptions.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else "unknown"
            detail = exc.response.text[:300] if exc.response is not None else ""
            raise RuntimeError(
                "AivisSpeech EngineでHTTPエラーが発生しました。"
                f"status_code={status_code} detail={detail}"
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise RuntimeError(
                "AivisSpeech Engineへ接続できません。"
                f"AivisSpeechを起動し、URLを確認してください。URL={self.base_url}"
            ) from exc
