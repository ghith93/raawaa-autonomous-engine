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


class RealCorpusResearchSearchTests(unittest.TestCase):
    def setUp(self):
        from scripts.benchmark_research_search import build_real_corpus_from_data
        self.build = build_real_corpus_from_data
        self.snapshot = {
            "retrieved_at": "2026-11-07T00:00:00Z",
            "candidates": [{
                "full_name": "acme/agent-runtime",
                "description": "Agent orchestration runtime with durable task workflows",
                "topics": ["multi-agent", "workflow"],
                "discovered_via": ["agent_frameworks"],
                "pushed_at": "2026-11-06T00:00:00Z",
                "language": "Python",
                "license_spdx": "MIT",
            }],
            "academic_candidates": [{
                "doi": "10.1000/example",
                "title": "Task assignment in heterogeneous multi-agent teams",
                "authors": ["A. Author"],
                "container_title": "Journal of Agents",
                "published": "2026-01-01",
            }],
            "arxiv_candidates": [{
                "arxiv_id": "2601.00001v1",
                "title": "Reproducible agent benchmark artifacts",
                "categories": ["cs.AI"],
                "published": "2026-01-02",
            }],
        }
        self.qrels = {
            "arabic_aliases": {
                "agents": "تنسيق الوكلاء سير العمل",
                "research": "إعادة إنتاج الأبحاث معايير التحقق",
            },
            "queries": [
                {"query_id": "en-agents", "query": "agent orchestration workflow", "language": "en",
                 "capability": "agents", "expected_ids": ["github:acme/agent-runtime"]},
                {"query_id": "ar-agents", "query": "تنسيق الوكلاء", "language": "ar",
                 "capability": "agents", "expected_ids": ["github:acme/agent-runtime"]},
                {"query_id": "en-research", "query": "reproducible benchmark", "language": "en",
                 "capability": "research", "expected_ids": ["arxiv:2601.00001v1"]},
                {"query_id": "missing", "query": "not present", "language": "en",
                 "capability": "missing", "expected_ids": ["github:missing/repository"]},
            ],
        }

    def test_builds_public_snapshot_and_tracks_label_coverage(self):
        documents, queries, coverage = self.build(self.snapshot, self.qrels)
        self.assertEqual(len(documents), 3)
        self.assertEqual(len(queries), 3)
        self.assertFalse(coverage[-1]["evaluated"])
        self.assertEqual(coverage[-1]["coverage"], 0.0)
        agent = next(item for item in documents if item["id"] == "github:acme/agent-runtime")
        self.assertIn("تنسيق الوكلاء", agent["body"])

    def test_weighted_or_fts_finds_arabic_aliases(self):
        from scripts.benchmark_research_search import _create_database, search_fts_or
        documents, queries, _ = self.build(self.snapshot, self.qrels)
        db = _create_database(documents)
        try:
            arabic = next(case for case in queries if case.query_id == "ar-agents")
            self.assertEqual(search_fts_or(db, arabic.query, 10), ["github:acme/agent-runtime"])
        finally:
            db.close()

    def test_source_native_track_does_not_inject_qrels_aliases(self):
        from scripts.benchmark_research_search import build_real_corpus_source_native_from_data
        documents, queries, coverage = build_real_corpus_source_native_from_data(self.snapshot, self.qrels)
        agent = next(item for item in documents if item["id"] == "github:acme/agent-runtime")
        self.assertNotIn("تنسيق الوكلاء", agent["body"])
        self.assertEqual(len(queries), 3)
        self.assertEqual(coverage[1]["coverage"], 1.0)

    def test_real_corpus_report_is_offline_and_tracks_sources(self):
        from scripts.benchmark_research_search import run_real_corpus_benchmark
        result = run_real_corpus_benchmark(self.snapshot, self.qrels)
        self.assertEqual(result["network_calls"], 0)
        self.assertEqual(result["remote_writes"], 0)
        self.assertEqual(result["provider_calls"], 0)
        self.assertEqual(result["snapshot"]["documents"], 3)
        self.assertEqual(result["snapshot"]["source_counts"], {"github": 1, "crossref": 1, "arxiv": 1})
        self.assertEqual(result["snapshot"]["evaluated_queries"], 3)
        self.assertEqual(result["snapshot"]["benchmark_status"], "insufficient_coverage")
        self.assertEqual(result["methods_track"], "alias_assisted")
        self.assertIn("sqlite_fts5_or_bm25", result["methods"])
        self.assertIn("sqlite_fts5_or_bm25", result["source_native"]["methods"])
        self.assertIn("coverage_by_query", result["source_native"])
