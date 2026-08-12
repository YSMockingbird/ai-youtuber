import unittest
from unittest.mock import Mock, patch

from aivis_speech import AivisSpeechClient, apply_pronunciation_replacements


class AivisSpeechClientTest(unittest.TestCase):
    def test_character_name_pronunciation_is_replaced(self):
        self.assertEqual(
            apply_pronunciation_replacements("才羽 ガン奈です"),
            "さいばね がんなです",
        )
        self.assertEqual(
            apply_pronunciation_replacements("才羽の配信です"),
            "さいばねの配信です",
        )

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
    def test_non_neutral_emotion_uses_common_speed(self, request_mock):
        query_response = Mock()
        query_response.json.return_value = {"speedScale": 1.0}
        synthesis_response = Mock()
        synthesis_response.content = b"RIFF-test-wav"
        request_mock.side_effect = [query_response, synthesis_response]

        client = AivisSpeechClient()

        client.synthesize("うれしいな", 101, "happy")

        synthesis_request = request_mock.call_args_list[1]
        self.assertEqual(synthesis_request.kwargs["json"]["speedScale"], 1.2)

    @patch("aivis_speech.requests.request")
    def test_slow_speech_style_uses_speed_1_0(self, request_mock):
        query_response = Mock()
        query_response.json.return_value = {"speedScale": 1.2}
        synthesis_response = Mock()
        synthesis_response.content = b"RIFF-test-wav"
        request_mock.side_effect = [query_response, synthesis_response]

        AivisSpeechClient().synthesize(
            "ゆっくり話すね",
            101,
            speech_style="slow",
        )

        synthesis_request = request_mock.call_args_list[1]
        self.assertEqual(synthesis_request.kwargs["json"]["speedScale"], 1.0)

    @patch("aivis_speech.requests.request")
    def test_fast_speech_style_uses_speed_1_4(self, request_mock):
        query_response = Mock()
        query_response.json.return_value = {"speedScale": 1.2}
        synthesis_response = Mock()
        synthesis_response.content = b"RIFF-test-wav"
        request_mock.side_effect = [query_response, synthesis_response]

        AivisSpeechClient().synthesize(
            "急いで話すよ",
            101,
            speech_style="fast",
        )

        synthesis_request = request_mock.call_args_list[1]
        self.assertEqual(synthesis_request.kwargs["json"]["speedScale"], 1.4)

    def test_unknown_speech_style_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "未対応のspeech_style"):
            AivisSpeechClient().synthesize(
                "話すよ",
                101,
                speech_style="very_fast",
            )

    @patch("aivis_speech.requests.request")
    def test_synthesize_sends_corrected_pronunciation(self, request_mock):
        query_response = Mock()
        query_response.json.return_value = {"speedScale": 1.0}
        synthesis_response = Mock()
        synthesis_response.content = b"RIFF-test-wav"
        request_mock.side_effect = [query_response, synthesis_response]

        client = AivisSpeechClient()

        client.synthesize("才羽 ガン奈です", 101)

        audio_query_request = request_mock.call_args_list[0]
        self.assertEqual(
            audio_query_request.kwargs["params"]["text"],
            "さいばね がんなです",
        )


if __name__ == "__main__":
    unittest.main()
