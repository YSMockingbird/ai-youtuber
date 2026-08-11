import unittest
from unittest.mock import Mock, patch

from aivis_speech import AivisSpeechClient


class AivisSpeechClientTest(unittest.TestCase):
    @patch("aivis_speech.requests.request")
    def test_get_styles_flattens_speakers(self, request_mock):
        response = Mock()
        response.json.return_value = [
            {
                "name": "才羽 ガン奈",
                "styles": [
                    {"id": 101, "name": "ノーマル"},
                    {"id": 102, "name": "楽しい"},
                ],
            }
        ]
        request_mock.return_value = response

        client = AivisSpeechClient()

        self.assertEqual(
            client.get_styles(),
            [
                {
                    "speaker_name": "才羽 ガン奈",
                    "style_name": "ノーマル",
                    "style_id": 101,
                },
                {
                    "speaker_name": "才羽 ガン奈",
                    "style_name": "楽しい",
                    "style_id": 102,
                },
            ],
        )

    @patch("aivis_speech.requests.request")
    def test_synthesize_returns_wav_data(self, request_mock):
        query_response = Mock()
        query_response.json.return_value = {"speedScale": 1.0}
        synthesis_response = Mock()
        synthesis_response.content = b"RIFF-test-wav"
        request_mock.side_effect = [query_response, synthesis_response]

        client = AivisSpeechClient()

        self.assertEqual(client.synthesize("こんにちは", 101), b"RIFF-test-wav")
        self.assertEqual(request_mock.call_count, 2)
        synthesis_request = request_mock.call_args_list[1]
        self.assertEqual(synthesis_request.kwargs["json"]["speedScale"], 1.2)

    @patch("aivis_speech.requests.request")
    def test_non_neutral_emotion_keeps_default_speed(self, request_mock):
        query_response = Mock()
        query_response.json.return_value = {"speedScale": 1.0}
        synthesis_response = Mock()
        synthesis_response.content = b"RIFF-test-wav"
        request_mock.side_effect = [query_response, synthesis_response]

        client = AivisSpeechClient()

        client.synthesize("うれしいな", 101, "happy")

        synthesis_request = request_mock.call_args_list[1]
        self.assertEqual(synthesis_request.kwargs["json"]["speedScale"], 1.0)


if __name__ == "__main__":
    unittest.main()
