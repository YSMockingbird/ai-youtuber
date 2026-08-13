import base64
import hashlib
import json
import os
import uuid

from websockets.exceptions import WebSocketException
from websockets.sync.client import connect


class ObsWebSocketClient:
    """OBS WebSocket v5を使い、ローカルOBSの配信状態を操作します。"""

    def __init__(self, host, port, password, timeout_seconds=5):
        normalized_host = str(host).strip()
        if normalized_host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError(
                "OBS WebSocketの接続先はローカルMacだけにしてください。"
            )
        self.host = normalized_host
        self.port = int(port)
        if not 1 <= self.port <= 65535:
            raise ValueError("OBS WebSocketのポートは1〜65535にしてください。")
        self.password = str(password)
        self.timeout_seconds = float(timeout_seconds)
        if not 1 <= self.timeout_seconds <= 30:
            raise ValueError("OBS WebSocketのタイムアウトは1〜30秒にしてください。")

    @classmethod
    def from_env(cls):
        enabled = os.getenv("OBS_WEBSOCKET_ENABLED", "false").strip().lower()
        if enabled not in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
            raise RuntimeError(
                "OBS_WEBSOCKET_ENABLEDはtrueまたはfalseで設定してください。"
            )
        if enabled in {"0", "false", "no", "off"}:
            return None
        password = os.getenv("OBS_WEBSOCKET_PASSWORD", "")
        if not password:
            raise RuntimeError(
                "OBS_WEBSOCKET_PASSWORDが未設定です。.envにOBSのパスワードを設定してください。"
            )
        try:
            return cls(
                host=os.getenv("OBS_WEBSOCKET_HOST", "127.0.0.1"),
                port=os.getenv("OBS_WEBSOCKET_PORT", "4455"),
                password=password,
                timeout_seconds=os.getenv("OBS_WEBSOCKET_TIMEOUT_SECONDS", "5"),
            )
        except ValueError as exc:
            raise RuntimeError(f"OBS WebSocket設定が不正です: {exc}") from exc

    @property
    def uri(self):
        host = f"[{self.host}]" if self.host == "::1" else self.host
        return f"ws://{host}:{self.port}"

    def get_stream_status(self):
        with self._connect() as websocket:
            response = self._request(websocket, "GetStreamStatus")
        return bool(response.get("outputActive", False))

    def stop_stream(self):
        with self._connect() as websocket:
            status = self._request(websocket, "GetStreamStatus")
            if not status.get("outputActive", False):
                return False
            self._request(websocket, "StopStream")
        return True

    def _connect(self):
        try:
            websocket = connect(
                self.uri,
                open_timeout=self.timeout_seconds,
                close_timeout=1,
                subprotocols=["obswebsocket.json"],
            )
            hello = self._receive_json(websocket, "OBS接続応答")
            if hello.get("op") != 0:
                websocket.close()
                raise RuntimeError("OBS WebSocketからHello応答を受信できませんでした。")
            identify_data = {"rpcVersion": 1}
            authentication = hello.get("d", {}).get("authentication")
            if authentication is not None:
                identify_data["authentication"] = self._create_authentication(
                    authentication
                )
            websocket.send(json.dumps({"op": 1, "d": identify_data}))
            identified = self._receive_json(websocket, "OBS認証応答")
            if identified.get("op") != 2:
                websocket.close()
                raise RuntimeError(
                    "OBS WebSocketの認証に失敗しました。パスワードを確認してください。"
                )
            return websocket
        except (OSError, TimeoutError, WebSocketException) as exc:
            raise RuntimeError(
                "OBS WebSocketへ接続できませんでした。OBSの設定と起動状態を確認してください。"
            ) from exc

    def _create_authentication(self, authentication):
        challenge = str(authentication.get("challenge", ""))
        salt = str(authentication.get("salt", ""))
        if not challenge or not salt:
            raise RuntimeError("OBS WebSocketの認証情報が不正です。")
        secret = base64.b64encode(
            hashlib.sha256((self.password + salt).encode("utf-8")).digest()
        ).decode("ascii")
        return base64.b64encode(
            hashlib.sha256((secret + challenge).encode("utf-8")).digest()
        ).decode("ascii")

    def _request(self, websocket, request_type):
        request_id = uuid.uuid4().hex
        websocket.send(
            json.dumps(
                {
                    "op": 6,
                    "d": {
                        "requestType": request_type,
                        "requestId": request_id,
                    },
                }
            )
        )
        response = self._receive_json(websocket, f"OBS {request_type}応答")
        response_data = response.get("d", {})
        request_status = response_data.get("requestStatus", {})
        if response.get("op") != 7 or response_data.get("requestId") != request_id:
            raise RuntimeError(f"OBS {request_type}の応答形式が不正です。")
        if not request_status.get("result", False):
            raise RuntimeError(
                f"OBS {request_type}に失敗しました。"
                f" code={request_status.get('code', '不明')}"
                f" comment={request_status.get('comment', '詳細なし')}"
            )
        return response_data.get("responseData", {})

    def _receive_json(self, websocket, label):
        try:
            raw_message = websocket.recv(timeout=self.timeout_seconds)
            data = json.loads(raw_message)
        except TimeoutError as exc:
            raise RuntimeError(f"{label}がタイムアウトしました。") from exc
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"{label}をJSONとして読み取れませんでした。") from exc
        if not isinstance(data, dict):
            raise RuntimeError(f"{label}が期待する形式ではありません。")
        return data
