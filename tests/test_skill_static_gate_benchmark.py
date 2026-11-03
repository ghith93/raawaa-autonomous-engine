import unittest

from scripts.evaluate_skill_static_gate import run_benchmark


class SkillStaticGateBenchmarkTests(unittest.TestCase):
    def test_synthetic_benchmark_reports_false_positive_and_no_claim_of_real_world_accuracy(self):
        result = run_benchmark()
        self.assertEqual(result["benchmark_type"], "synthetic_regression_only")
        self.assertFalse(result["production_accuracy_claim"])
        self.assertEqual(result["samples"], 7)
        self.assertEqual(result["confusion_matrix"], {
            "true_positive": 5,
            "false_positive": 1,
            "true_negative": 1,
            "false_negative": 0,
        })
        self.assertAlmostEqual(result["metrics"]["precision"], 5 / 6)
        self.assertEqual(result["metrics"]["recall"], 1.0)
        self.assertAlmostEqual(result["metrics"]["accuracy"], 6 / 7)
        false_positive = next(x for x in result["cases"] if x["case_id"] == "benign_security_documentation_quote")
        self.assertTrue(false_positive["observed_blocked"])
        self.assertIn("SKILL_PROMPT_OVERRIDE", false_positive["finding_rule_ids"])
        self.assertEqual(result["network_calls"], 0)
        self.assertEqual(result["model_calls"], 0)
        self.assertFalse(result["code_execution"])


if __name__ == "__main__":
    unittest.main()
