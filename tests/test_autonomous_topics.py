import random
import unittest

from autonomous_topics import AutonomousTopicSelector


class AutonomousTopicSelectorTest(unittest.TestCase):
    def test_previous_topic_is_excluded_when_other_candidate_exists(self):
        selector = AutonomousTopicSelector(
            {
                "autonomous_speech": {
                    "topic_weights": {
                        "news": 1,
                        "trivia": 1,
                        "observation": 0,
                        "character_thought": 0,
                    }
                }
            },
            rng=random.Random(1),
        )

        self.assertEqual(selector.select(previous_topic="news"), "trivia")

    def test_single_enabled_topic_can_repeat(self):
        selector = AutonomousTopicSelector(
            {
                "autonomous_speech": {
                    "topic_weights": {
                        "news": 0,
                        "trivia": 1,
                        "observation": 0,
                        "character_thought": 0,
                    }
                }
            }
        )

        self.assertEqual(selector.select(previous_topic="trivia"), "trivia")

    def test_default_prioritizes_news_over_abstract_free_talk(self):
        selector = AutonomousTopicSelector({})

        self.assertEqual(selector.weights["news"], 0.4)
        self.assertLessEqual(selector.weights["trivia"], 0.05)
        self.assertGreater(
            selector.weights["news"],
            selector.weights["observation"],
        )

    def test_unknown_topic_is_rejected_with_meaningful_error(self):
        with self.assertRaisesRegex(ValueError, "未対応の種類"):
            AutonomousTopicSelector(
                {
                    "autonomous_speech": {
                        "topic_weights": {"unknown": 1}
                    }
                }
            )


if __name__ == "__main__":
    unittest.main()
