import unittest

from scripts.benchmark_research_search import (
    _create_database,
    build_fixture,
    normalize_text,
    run_benchmark,
    search_fts,
    search_like,
)


class ResearchSearchBenchmarkTests(unittest.TestCase):
    def test_fixture_is_bounded_and_bilingual(self):
        documents, queries = build_fixture()
        self.assertGreaterEqual(len(documents), 100)
        self.assertLessEqual(len(documents), 300)
        self.assertEqual(len(queries), 24)
        self.assertEqual(sum(any("\u0600" <= c <= "\u06ff" for c in q.query) for q in queries), 12)

    def test_arabic_normalization_removes_diacritics_and_normalizes_alef(self):
        self.assertEqual(normalize_text("إِكْلِيلٌ"), "اكليل")
        self.assertEqual(normalize_text("عِنَايَة"), "عنايه")

    def test_fts_retrieves_scattered_tokens_while_phrase_baseline_does_not(self):
        documents, queries = build_fixture()
        db = _create_database(documents)
        try:
            case = queries[0]
            fts = search_fts(db, case.query, 20)
            like = search_like(db, case.query, 20)
            self.assertEqual(set(fts), set(case.expected_ids))
            self.assertEqual(like, [])
        finally:
            db.close()

    def test_fts_query_treats_user_terms_as_quoted_tokens(self):
        documents, queries = build_fixture()
        db = _create_database(documents)
        try:
            results = search_fts(db, 'agent "task" graph', 20)
            self.assertEqual(len(results), 5)
        finally:
            db.close()

    def test_benchmark_reports_metrics_and_zero_external_effects(self):
        result = run_benchmark()
        self.assertEqual(result["network_calls"], 0)
        self.assertEqual(result["remote_writes"], 0)
        self.assertEqual(result["provider_calls"], 0)
        self.assertEqual(result["fixture"]["documents"], 144)
        self.assertEqual(result["fixture"]["queries"], 24)
        self.assertEqual(result["methods"]["sqlite_fts5"]["precision_at_10"], 0.5)
        self.assertEqual(result["methods"]["sqlite_fts5"]["recall_at_20"], 1.0)
        self.assertEqual(result["methods"]["sqlite_fts5"]["mrr_at_10"], 1.0)


if __name__ == "__main__":
    unittest.main()
