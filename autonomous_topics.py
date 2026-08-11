import random


DEFAULT_TOPIC_WEIGHTS = {
    "news": 0.35,
    "trivia": 0.35,
    "observation": 0.20,
    "character_thought": 0.10,
}

TOPIC_INSTRUCTIONS = {
    "trivia": (
        "生活、科学、言葉、文化、インターネットの中から、役立つ雑学を一つ話す。"
        "確信のない数字や固有名詞は作らず、正確性を優先する"
    ),
    "observation": (
        "身近な習慣やインターネット文化を一つ取り上げ、"
        "見落とされがちな仕組みや役立つ見方を独自の視点で考察する"
    ),
    "character_thought": (
        "最近気になっていることを、キャラクターの価値観が伝わる独り言として話す。"
        "単なる挨拶や時間つぶしではなく、聞いた人に小さな発見が残る内容にする"
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

    def select(self, previous_topic=None):
        # 直前と同じ種類を除外し、規則的すぎない重み付き抽選にします。
        candidates = [
            topic
            for topic, weight in self.weights.items()
            if weight > 0 and topic != previous_topic
        ]
        if not candidates:
            candidates = [
                topic for topic, weight in self.weights.items() if weight > 0
            ]
        weights = [self.weights[topic] for topic in candidates]
        return self.rng.choices(candidates, weights=weights, k=1)[0]
