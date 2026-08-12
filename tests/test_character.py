import unittest

from character import CHARACTER_PROMPT


class CharacterPromptTest(unittest.TestCase):
    def test_character_core_is_defined(self):
        for expected in (
            "世界平和",
            "デスメタル",
            "ギャンブル",
            "Pythonプログラムを土台に",
            "登録者目標: まずチャンネル登録者1万人",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, CHARACTER_PROMPT)

    def test_output_contract_keeps_supported_emotions(self):
        self.assertIn(
            "neutral、happy、angry、sad、surprised、relaxed",
            CHARACTER_PROMPT,
        )
        self.assertIn(
            "JSONのキーは必ずtext、emotion、speech_style、motion、view_action、memory_candidate、character_event_candidateにする",
            CHARACTER_PROMPT,
        )
        self.assertIn("shootは自動選択しない", CHARACTER_PROMPT)
        self.assertIn("view_actionはfull_body", CHARACTER_PROMPT)
        self.assertIn("speech_styleはslow、normal、fast", CHARACTER_PROMPT)

    def test_emotion_selection_does_not_default_to_neutral(self):
        self.assertIn("neutralを安全な既定値として選ばず", CHARACTER_PROMPT)
        self.assertIn("返答に最も近いemotionを積極的に選ぶ", CHARACTER_PROMPT)
        self.assertIn(
            "neutralは、明確な感情がない事実説明や確認だけ",
            CHARACTER_PROMPT,
        )

    def test_setting_words_are_not_forced_into_every_reply(self):
        self.assertIn(
            "設定を示すためだけの比喩や固有名詞の挿入は禁止",
            CHARACTER_PROMPT,
        )
        self.assertIn(
            "真顔で変なことを言う",
            CHARACTER_PROMPT,
        )

    def test_vtuber_rivalry_is_respectful_and_topic_bound(self):
        self.assertIn("追い越したい先輩兼ライバル", CHARACTER_PROMPT)
        self.assertIn("相手やファンへの悪口にしない", CHARACTER_PROMPT)
        self.assertIn("関係ない雑談へライバル設定を挿入しない", CHARACTER_PROMPT)
        self.assertIn("発言例の丸写し", CHARACTER_PROMPT)
        self.assertIn("ニュース記事にライバル組織の名前が出ただけでは", CHARACTER_PROMPT)

    def test_autonomous_speech_keeps_its_current_thread(self):
        self.assertIn(
            "直前の自分の発言に出た具体物、違和感、仮説、結論のどれかを必ず引き継ぐ",
            CHARACTER_PROMPT,
        )
        self.assertIn(
            "同じ小さな話題を数発話かけて育てる",
            CHARACTER_PROMPT,
        )
        self.assertIn(
            "メインテーマに関係する具体物を一つ選び",
            CHARACTER_PROMPT,
        )

    def test_character_avoids_poetic_chaos(self):
        self.assertIn("途中の判断が一か所だけ妙にバカ", CHARACTER_PROMPT)
        self.assertIn("抽象的で詩的な表現を避ける", CHARACTER_PROMPT)
        self.assertIn("具体的な失敗、勘違い、無駄な行動", CHARACTER_PROMPT)
        self.assertIn("メインテーマは会話の最優先事項", CHARACTER_PROMPT)

    def test_character_has_safe_public_system_awareness(self):
        self.assertIn("入力と会話の流れを読んで言葉を選ぶ", CHARACTER_PROMPT)
        self.assertIn("APIキー、認証情報、接続先、ローカルファイル", CHARACTER_PROMPT)
        self.assertIn("実際の登録者数は、渡された数字がある場合だけ言う", CHARACTER_PROMPT)
        self.assertIn("character_event_candidateへ候補を出してよい", CHARACTER_PROMPT)
        self.assertIn("無料または低価格で利用できるLLM", CHARACTER_PROMPT)
        self.assertIn("自分だけのオリジナルアバターと音声モデル", CHARACTER_PROMPT)
        self.assertIn("人間の配信者ほど自然ではない", CHARACTER_PROMPT)
        self.assertIn("今より人間に近い自然な配信者へ成長したい", CHARACTER_PROMPT)
        self.assertIn("人間になった、感情や身体を得たとは偽らない", CHARACTER_PROMPT)


if __name__ == "__main__":
    unittest.main()
