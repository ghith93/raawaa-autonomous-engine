import copy
import json
import unittest
from pathlib import Path

from scripts.audit_groq_defaults import validate

ROOT = Path(__file__).resolve().parents[1]


class DefaultAuditTests(unittest.TestCase):
    def setUp(self):
        self.policy = json.loads((ROOT / 'config/policy.json').read_text())
        self.profiles = json.loads((ROOT / 'config/provider_profiles.json').read_text())

    def test_current_defaults_pass(self):
        self.assertEqual(validate(self.policy, self.profiles), [])

    def test_unsafe_defaults_fail_closed(self):
        for key, value in [('mode', 'live'), ('daily_budget_usd', 1),
                           ('monthly_budget_usd', None), ('per_task_budget_usd', False),
                           ('allow_paid_model_calls', True), ('allow_external_writes', True)]:
            with self.subTest(key=key):
                policy = copy.deepcopy(self.policy)
                policy[key] = value
                self.assertTrue(validate(policy, self.profiles))
        policy = copy.deepcopy(self.policy)
        policy['model_policy']['paid_fallback_enabled'] = True
        self.assertTrue(validate(policy, self.profiles))
        profiles = copy.deepcopy(self.profiles)
        profiles['routes'][0]['enabled'] = True
        self.assertTrue(validate(self.policy, profiles))
        profiles['routes'] = []
        self.assertTrue(validate(self.policy, profiles))


if __name__ == '__main__':
    unittest.main()
