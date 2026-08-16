import unittest

from character import CHARACTER_PROMPT
from llm.context_builder import estimate_tokens


class CharacterPromptTest(unittest.TestCase):
    def test_character_core_is_defined(self):
        for expected in (
            "世界平和",
            "Pythonプログラムを土台に",
            "好きなものと自分の個性をこれから見つける",
            "実在する人物、作品、組織、出来事",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, CHARACTER_PROMPT)

    def test_output_contract_keeps_supported_emotions(self):
        self.assertIn("指定されたスキーマへ必ず従う", CHARACTER_PROMPT)
        self.assertIn("shootは選ばない", CHARACTER_PROMPT)
        self.assertIn("view_actionは視聴者から", CHARACTER_PROMPT)
        self.assertIn("speech_styleは通常normal", CHARACTER_PROMPT)

    def test_emotion_selection_does_not_default_to_neutral(self):
        self.assertIn("neutralを安全な既定値にせず", CHARACTER_PROMPT)
        self.assertIn("発言に最も近いemotionを選ぶ", CHARACTER_PROMPT)
        self.assertIn("neutralは感情の薄い事実確認", CHARACTER_PROMPT)

    def test_setting_words_are_not_forced_into_every_reply(self):
        self.assertIn(
            "現時点では強く好きだと断言できる特定の趣味や作品はない",
            CHARACTER_PROMPT,
        )
        self.assertIn(
            "面白さを毎回作ろうとせず",
            CHARACTER_PROMPT,
        )

    def test_character_avoids_poetic_chaos(self):
        self.assertIn("抽象的・詩的な表現", CHARACTER_PROMPT)
        self.assertIn("関係のない架空の小話", CHARACTER_PROMPT)
        self.assertIn("大きな人生論へ広げず", CHARACTER_PROMPT)

    def test_removed_hobbies_are_not_character_settings(self):
        for removed_setting in ("デスメタル", "麻雀", "ギャンブル"):
            with self.subTest(removed_setting=removed_setting):
                self.assertNotIn(removed_setting, CHARACTER_PROMPT)

    def test_character_has_safe_public_system_awareness(self):
        self.assertIn("APIキー、認証情報、接続先、ローカルファイル", CHARACTER_PROMPT)
        self.assertIn("実在する人物や組織の数字", CHARACTER_PROMPT)
        self.assertIn("character_event_candidateは", CHARACTER_PROMPT)

    def test_fixed_prompt_stays_within_target_budget(self):
        self.assertLessEqual(estimate_tokens(CHARACTER_PROMPT), 2500)


if __name__ == "__main__":
    unittest.main()
