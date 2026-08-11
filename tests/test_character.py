import unittest

from character import CHARACTER_PROMPT


class CharacterPromptTest(unittest.TestCase):
    def test_character_core_is_defined(self):
        for expected in ("世界平和", "デスメタル", "麻雀", "ギャンブル"):
            with self.subTest(expected=expected):
                self.assertIn(expected, CHARACTER_PROMPT)

    def test_output_contract_keeps_supported_emotions(self):
        self.assertIn(
            "neutral、happy、angry、sad、surprised、relaxed",
            CHARACTER_PROMPT,
        )
        self.assertIn("JSONのキーは必ずtextとemotionだけ", CHARACTER_PROMPT)

    def test_emotion_selection_does_not_default_to_neutral(self):
        self.assertIn("neutralを安全な既定値として選ばず", CHARACTER_PROMPT)
        self.assertIn("返答に最も近いemotionを積極的に選ぶ", CHARACTER_PROMPT)
        self.assertIn(
            "neutralは、明確な感情がない事実説明や確認だけ",
            CHARACTER_PROMPT,
        )


if __name__ == "__main__":
    unittest.main()
