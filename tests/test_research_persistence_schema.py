import unittest

from scripts.verify_research_persistence_schema import (
    REQUIRED_MIGRATION,
    STAGING_DATABASE_ID,
    ResearchSchemaError,
    verify_research_schema,
)


class ResearchPersistenceSchemaTests(unittest.TestCase):
    def test_checks_exact_staging_database_with_parameterized_migration_read(self):
        calls = []
        class FakeD1:
            def __init__(self, **kwargs):
                calls.append(("init", kwargs))
            def query(self, sql, params):
                calls.append(("query", sql, params))
                return {"results": [{"version": REQUIRED_MIGRATION}]}
        result = verify_research_schema({
            "CLOUDFLARE_ACCOUNT_ID": "a" * 32,
            "CLOUDFLARE_API_TOKEN": "synthetic-token",
        }, client_factory=FakeD1)
        self.assertEqual(result, {
            "ready": True, "database": "staging", "migration": REQUIRED_MIGRATION,
        })
        self.assertEqual(calls[0][1]["database_id"], STAGING_DATABASE_ID)
        self.assertNotEqual(calls[0][1]["database_id"], "f0ffbed3-940f-488d-a7d1-31cbc5fbf742")
        self.assertEqual(calls[1][1], "SELECT version FROM schema_migrations WHERE version = ?")
        self.assertEqual(calls[1][2], [REQUIRED_MIGRATION])
        self.assertNotIn("synthetic-token", repr(calls[1]))

    def test_missing_migration_fails_closed(self):
        class FakeD1:
            def __init__(self, **kwargs): pass
            def query(self, sql, params): return {"results": []}
        with self.assertRaisesRegex(ResearchSchemaError, "not applied"):
            verify_research_schema({
                "CLOUDFLARE_ACCOUNT_ID": "a" * 32,
                "CLOUDFLARE_API_TOKEN": "synthetic-token",
            }, client_factory=FakeD1)

    def test_malformed_readback_fails_closed(self):
        class FakeD1:
            def __init__(self, **kwargs): pass
            def query(self, sql, params): return {"results": [{"version": "other"}]}
        with self.assertRaises(ResearchSchemaError):
            verify_research_schema({
                "CLOUDFLARE_ACCOUNT_ID": "a" * 32,
                "CLOUDFLARE_API_TOKEN": "synthetic-token",
            }, client_factory=FakeD1)

    def test_missing_credentials_fail_before_client_creation(self):
        called = []
        with self.assertRaisesRegex(ResearchSchemaError, "credentials"):
            verify_research_schema({}, client_factory=lambda **kwargs: called.append(kwargs))
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
