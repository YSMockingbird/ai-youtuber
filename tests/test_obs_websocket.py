import json
import unittest
from unittest.mock import patch

from obs_websocket import ObsWebSocketClient


class FakeWebSocket:
    def __init__(self, responses):
        self.responses = list(responses)
        self.sent = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def send(self, message):
        self.sent.append(json.loads(message))

    def recv(self, timeout=None):
        return json.dumps(self.responses.pop(0))


class ObsWebSocketClientTest(unittest.TestCase):
    @patch.dict(
        "os.environ",
        {
            "OBS_WEBSOCKET_ENABLED": "true",
            "OBS_WEBSOCKET_HOST": "127.0.0.1",
            "OBS_WEBSOCKET_PORT": "4455",
            "OBS_WEBSOCKET_PASSWORD": "secret",
        },
        clear=False,
    )
    def test_client_is_loaded_from_environment(self):
        client = ObsWebSocketClient.from_env()

        self.assertEqual(client.uri, "ws://127.0.0.1:4455")

    @patch.dict(
        "os.environ",
        {"OBS_WEBSOCKET_ENABLED": "false"},
        clear=False,
    )
    def test_disabled_client_is_not_created(self):
        self.assertIsNone(ObsWebSocketClient.from_env())

    @patch("obs_websocket.uuid.uuid4")
    def test_active_stream_is_stopped(self, uuid_mock):
        uuid_mock.return_value.hex = "request-id"
        websocket = FakeWebSocket(
            [
                {
                    "op": 7,
                    "d": {
                        "requestId": "request-id",
                        "requestStatus": {"result": True, "code": 100},
                        "responseData": {"outputActive": True},
                    },
                },
                {
                    "op": 7,
                    "d": {
                        "requestId": "request-id",
                        "requestStatus": {"result": True, "code": 100},
                        "responseData": {},
                    },
                },
            ]
        )
        client = ObsWebSocketClient("127.0.0.1", 4455, "secret")
        client._connect = lambda: websocket

        self.assertTrue(client.stop_stream())
        self.assertEqual(
            [message["d"]["requestType"] for message in websocket.sent],
            ["GetStreamStatus", "StopStream"],
        )

    def test_remote_host_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "ローカルMac"):
            ObsWebSocketClient("192.0.2.1", 4455, "secret")


if __name__ == "__main__":
    unittest.main()
