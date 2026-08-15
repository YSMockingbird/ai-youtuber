import random


DEFAULT_TOPIC_WEIGHTS = {
    "news": 0.40,
    "trivia": 0.05,
    "observation": 0.35,
    "character_thought": 0.20,
}

TOPIC_INSTRUCTIONS = {
    "trivia": (
        "現在の会話の枝に直接つながる、生活、科学、言葉、文化、"
        "インターネットの雑学を一つだけ短く話す。"
        "確信のない数字や固有名詞は作らず、正確性を優先する。"
        "直前の発話と接続できない雑学は選ばない。知識を披露した後に、"
        "無理なボケや教訓を足さない"
    ),
    "observation": (
        "直前の発話に出た実在の人物、作品、出来事、違和感のどれかを一つ選び、"
        "もう一段だけ掘る。"
        "具体的な失敗、勘違い、無駄な行動、雑な偏見のどれかを低温で足す。"
        "新しい無関係な対象を導入せず、詩的な比喩、役立つ見方、知的な考察、"
        "きれいな結論にはしない"
    ),
    "character_thought": (
        "キャラクターの気まぐれな独り言として、現在の会話の枝を別の角度から見る。"
        "直前の発話に含まれる具体的な言葉を一つ引き継ぎ、説明し切らずに終えてよい。"
        "抽象語で雰囲気を作らず、具体的でくだらない行動や判断を中心にする。"
        "実在ネタが会話にある時はそれを優先し、世界平和や趣味の設定語を、"
        "人格を示すためだけに持ち出さない"
    ),
}


class AutonomousTopicSelector:
    def __init__(self, config, rng=None):
        autonomous_config = config.get("autonomous_speech", {})
        configured_weights = autonomous_config.get(
            "topic_weights",
            DEFAULT_TOPIC_WEIGHTS,
        )
        if not isinstance(configured_weights, dict):
            raise ValueError("topic_weightsはオブジェクトで設定してください。")

        unknown_topics = set(configured_weights) - set(DEFAULT_TOPIC_WEIGHTS)
        if unknown_topics:
            raise ValueError(
                "topic_weightsに未対応の種類があります: "
                + ", ".join(sorted(unknown_topics))
            )

        self.weights = {}
        for topic in DEFAULT_TOPIC_WEIGHTS:
            raw_weight = configured_weights.get(topic, 0)
            try:
                weight = float(raw_weight)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"topic_weights.{topic}は数値で設定してください。"
                ) from exc
            if weight < 0:
                raise ValueError(
                    f"topic_weights.{topic}は0以上で設定してください。"
                )
            self.weights[topic] = weight

        if sum(self.weights.values()) <= 0:
            raise ValueError("topic_weightsは少なくとも1種類を0より大きくしてください。")

        self.rng = rng or random.Random()

    def select(self, previous_topic=None, allow_news=True):
        # 直前と同じ種類を除外し、規則的すぎない重み付き抽選にします。
        candidates = [
            topic
            for topic, weight in self.weights.items()
            if weight > 0
            and topic != previous_topic
            and (allow_news or topic != "news")
        ]
        if not candidates:
            candidates = [
                topic
                for topic, weight in self.weights.items()
                if weight > 0 and (allow_news or topic != "news")
            ]
        if not candidates:
            raise RuntimeError(
                "ニュースを除外すると選択可能な自発発話の種類がありません。"
            )
        weights = [self.weights[topic] for topic in candidates]
        return self.rng.choices(candidates, weights=weights, k=1)[0]
