import json
import unittest
from pathlib import Path

from engine.policy import evaluate_action

ROOT = Path(__file__).resolve().parents[1]


class PolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = json.loads((ROOT / "config" / "policy.json").read_text(encoding="utf-8"))

    def test_safe_action_is_allowed(self):
        decision = evaluate_action("research", self.policy)
        self.assertTrue(decision.allowed)
        self.assertFalse(decision.requires_approval)

    def test_paid_call_is_blocked(self):
        decision = evaluate_action("research", self.policy, estimated_cost_usd=0.01)
        self.assertFalse(decision.allowed)

    def test_publish_requires_approval(self):
        decision = evaluate_action("publish_content", self.policy)
        self.assertFalse(decision.allowed)
        self.assertTrue(decision.requires_approval)

    def test_unknown_action_fails_closed(self):
        decision = evaluate_action("run_arbitrary_shell", self.policy)
        self.assertFalse(decision.allowed)

    def test_negative_cost_is_rejected(self):
        decision = evaluate_action("research", self.policy, estimated_cost_usd=-1)
        self.assertFalse(decision.allowed)


if __name__ == "__main__":
    unittest.main()
