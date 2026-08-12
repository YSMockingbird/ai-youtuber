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

    def test_default_prioritizes_surreal_observations_over_information(self):
        selector = AutonomousTopicSelector({})

        self.assertLess(selector.weights["news"], 0.1)
        self.assertLessEqual(selector.weights["trivia"], 0.12)
        self.assertGreater(selector.weights["observation"], 0.5)

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
