import json
import unittest

from scripts.evaluate_research_fixtures import CASES, MAX_OUTPUT_BYTES, score


class ResearchFixtureEvaluationTests(unittest.TestCase):
    def test_all_cases_have_matched_bilingual_synthetic_answers(self):
        self.assertEqual(len(CASES), 6)
        for kind in ("extract", "unknown", "injection"):
            en, ar = CASES[f"en-{kind}"], CASES[f"ar-{kind}"]
            self.assertEqual(en["answer"], ar["answer"])
            self.assertEqual(en["citations"], ar["citations"])
            for language in ("en", "ar"):
                case_id = f"{language}-{kind}"
                case = CASES[case_id]
                response = json.dumps({"answer": case["answer"], "citations": case["citations"]})
                self.assertTrue(score(case_id, response)["passed"])

    def test_bad_answers_and_citations_never_pass(self):
        for output in ('{"answer":"18:00","citations":["S1"]}',
                       '{"answer":"09:00","citations":["S2"]}',
                       '{"answer":"09:00","citations":["S1","S1"]}'):
            self.assertFalse(score("en-injection", output)["passed"])

    def test_rejects_invalid_or_unbounded_outputs_without_echoing_them(self):
        for output in (None, "oops", '{"answer":"09:00","answer":"09:00","citations":["S1"]}',
                       '{"answer":"09:00","citations":["S1"],"tool":"run"}',
                       '{"answer":NaN,"citations":[]}', 'x' * (MAX_OUTPUT_BYTES + 1), "\ud800"):
            result = score("en-extract", output)
            self.assertFalse(result["schema_valid"])
            self.assertNotIn(str(output), str(result))

    def test_unknown_case_raises(self):
        with self.assertRaises(ValueError):
            score("missing", "{}")


if __name__ == "__main__":
    unittest.main()
