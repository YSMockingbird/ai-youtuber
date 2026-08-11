import json
import queue
import threading
import uuid
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


ALLOWED_EMOTIONS = {
    "neutral",
    "happy",
    "angry",
    "sad",
    "surprised",
    "relaxed",
    "thinking",
}


class AudioStore:
    def __init__(self, max_items=32):
        self.max_items = max_items
        self._items = OrderedDict()
        self._lock = threading.Lock()

    def put(self, audio_data):
        audio_id = uuid.uuid4().hex
        with self._lock:
            self._items[audio_id] = audio_data
            while len(self._items) > self.max_items:
                self._items.popitem(last=False)
        return audio_id

    def get(self, audio_id):
        with self._lock:
            return self._items.get(audio_id)


class EventBroker:
    def __init__(self):
        self._subscribers = []
        self._lock = threading.Lock()

    def subscribe(self):
        subscriber = queue.Queue(maxsize=16)
        with self._lock:
            self._subscribers.append(subscriber)
        return subscriber

    def unsubscribe(self, subscriber):
        with self._lock:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)

    def publish(self, command):
        with self._lock:
            # React開発モードなどで接続が重複しても、最新の画面だけへ配信します。
            subscriber = self._subscribers[-1] if self._subscribers else None

        if subscriber is None:
            return 0

        try:
            subscriber.put_nowait(command)
            return 1
        except queue.Full:
            # 遅れている接続には新しい命令を追加しません。
            return 0

    def subscriber_count(self):
        with self._lock:
            # 実際に命令を配信するクライアント数を返します。
            return min(len(self._subscribers), 1)


class ExternalControlRuntime:
    def __init__(self, aivis_client, speaker_id, public_base_url):
        self.aivis_client = aivis_client
        self.speaker_id = speaker_id
        self.public_base_url = public_base_url.rstrip("/")
        self.audio_store = AudioStore()
        self.event_broker = EventBroker()

    def speak(self, text, emotion="neutral"):
        normalized_text = text.strip()
        if not normalized_text:
            raise ValueError("発話する文章が空です。")
        if emotion not in ALLOWED_EMOTIONS:
            raise ValueError(f"未対応のemotionです。emotion={emotion}")

        audio_data = self.aivis_client.synthesize(normalized_text, self.speaker_id)
        audio_id = self.audio_store.put(audio_data)
        command = {
            "type": "speak",
            "id": uuid.uuid4().hex,
            "text": normalized_text,
            "emotion": emotion,
            "audio_url": f"{self.public_base_url}/audio/{audio_id}.wav",
            "interrupt": True,
        }
        delivered_count = self.event_broker.publish(command)
        return command, delivered_count

    def reset(self):
        command = {"type": "reset", "id": uuid.uuid4().hex}
        delivered_count = self.event_broker.publish(command)
        return command, delivered_count


class ExternalControlHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address, runtime):
        super().__init__(server_address, ExternalControlRequestHandler)
        self.runtime = runtime


class ExternalControlRequestHandler(BaseHTTPRequestHandler):
    server_version = "AIYoutuberControl/1.0"

    @property
    def runtime(self):
        return self.server.runtime

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/events":
            self._serve_events()
            return
        if path == "/health":
            self._send_json(
                200,
                {
                    "status": "ok",
                    "sse_clients": self.runtime.event_broker.subscriber_count(),
                },
            )
            return
        if path.startswith("/audio/") and path.endswith(".wav"):
            self._serve_audio(path)
            return
        self._send_json(404, {"error": "指定されたエンドポイントはありません。"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/speak":
            self._handle_speak()
            return
        if path == "/api/reset":
            command, delivered_count = self.runtime.reset()
            self._send_json(
                200,
                {"command": command, "delivered_clients": delivered_count},
            )
            return
        self._send_json(404, {"error": "指定されたエンドポイントはありません。"})

    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _serve_events(self):
        subscriber = self.runtime.event_broker.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self._send_cors_headers()
        self.end_headers()

        try:
            while True:
                try:
                    command = subscriber.get(timeout=15)
                    payload = json.dumps(command, ensure_ascii=False)
                    data = f"data: {payload}\n\n".encode("utf-8")
                except queue.Empty:
                    data = b": keep-alive\n\n"
                self.wfile.write(data)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return
        finally:
            self.runtime.event_broker.unsubscribe(subscriber)

    def _serve_audio(self, path):
        filename = path.rsplit("/", 1)[-1]
        audio_id = filename[:-4]
        if not audio_id or not audio_id.isalnum():
            self._send_json(400, {"error": "音声IDが不正です。"})
            return

        audio_data = self.runtime.audio_store.get(audio_id)
        if audio_data is None:
            self._send_json(404, {"error": "音声データが見つかりません。"})
            return

        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(audio_data)))
        self.send_header("Cache-Control", "no-store")
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(audio_data)

    def _handle_speak(self):
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "Content-Lengthが不正です。"})
            return

        if content_length <= 0 or content_length > 64 * 1024:
            self._send_json(400, {"error": "リクエスト本文のサイズが不正です。"})
            return

        try:
            body = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(
                400,
                {"error": "リクエスト本文をJSONとして読み取れません。"},
            )
            return

        if not isinstance(body, dict):
            self._send_json(
                400,
                {"error": "リクエスト本文はJSONオブジェクトで指定してください。"},
            )
            return

        text = body.get("text", "")
        emotion = body.get("emotion", "neutral")
        if not isinstance(text, str) or not isinstance(emotion, str):
            self._send_json(
                400,
                {"error": "textとemotionは文字列で指定してください。"},
            )
            return

        try:
            command, delivered_count = self.runtime.speak(text, emotion)
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        except RuntimeError as exc:
            self._send_json(502, {"error": str(exc)})
            return

        self._send_json(
            200,
            {"command": command, "delivered_clients": delivered_count},
        )

    def _send_json(self, status_code, body):
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(payload)

    def _send_cors_headers(self):
        origin = self.headers.get("Origin", "")
        if is_allowed_local_origin(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def log_message(self, format_string, *args):
        print(f"HTTP: {format_string % args}")


def is_allowed_local_origin(origin):
    if not origin:
        return False
    try:
        parsed = urlparse(origin)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and parsed.hostname in {
        "127.0.0.1",
        "localhost",
    }


class ExternalControlServer:
    def __init__(self, aivis_client, speaker_id, host="127.0.0.1", port=8765):
        public_base_url = f"http://127.0.0.1:{port}"
        self.runtime = ExternalControlRuntime(
            aivis_client=aivis_client,
            speaker_id=speaker_id,
            public_base_url=public_base_url,
        )
        self.http_server = ExternalControlHttpServer((host, port), self.runtime)
        self._thread = None

    def start(self):
        if self._thread is not None:
            raise RuntimeError("外部制御サーバーはすでに起動しています。")
        self._thread = threading.Thread(
            target=self.http_server.serve_forever,
            name="external-control-server",
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        if self._thread is None:
            return
        self.http_server.shutdown()
        self.http_server.server_close()
        self._thread.join(timeout=5)
        self._thread = None
