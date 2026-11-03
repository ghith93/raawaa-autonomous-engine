import json
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch
from datetime import datetime, timezone
from engine.research_discovery import DEFAULT_QUERIES, MAX_RESPONSE_BYTES, ResearchDiscoveryError, _request_arxiv, _request_crossref, _request_json, _request_searxng, _validate_searxng_base_url, discover_repositories

class ResearchDiscoveryTests(unittest.TestCase):
    def test_default_queries_target_skill_scanners_and_evaluation_benchmarks(self):
        self.assertEqual(
            DEFAULT_QUERIES["agent_skills_security"],
            "agent skill scanner archived:false",
        )
        self.assertEqual(
            DEFAULT_QUERIES["agent_evaluation"],
            "agent evaluation benchmark harness archived:false",
        )
        self.assertEqual(
            DEFAULT_QUERIES["skill_benchmarks"],
            "skillsbench archived:false",
        )

    def test_discovery_returns_metadata_only_candidates_and_deduplicates(self):
        payloads = [
            {"items": [{"full_name": "org/agent-a", "html_url": "https://github.com/org/agent-a", "description": "agent framework", "stargazers_count": 120, "language": "Python", "license": {"spdx_id": "MIT"}, "pushed_at": "2026-10-20T00:00:00Z", "archived": False, "topics": ["agents"]}, {"full_name": "org/shared", "html_url": "https://github.com/org/shared", "stargazers_count": 5, "pushed_at": "2026-10-01T00:00:00Z"}]},
            {"items": [{"full_name": "org/shared", "html_url": "https://github.com/org/shared", "stargazers_count": 5, "pushed_at": "2026-10-01T00:00:00Z"}, {"full_name": "org/agent-b", "html_url": "https://github.com/org/agent-b", "stargazers_count": 20, "license": None, "archived": False}]},
        ]
        calls = []
        def fake_request(query, token):
            calls.append((query, token))
            return payloads[len(calls) - 1]
        result = discover_repositories({"agents": "agent framework", "memory": "agent memory"}, {"GITHUB_TOKEN": "test-token"}, request_fn=fake_request, now=datetime(2026, 11, 1, tzinfo=timezone.utc))
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][1], "test-token")
        self.assertEqual(result["candidate_count"], 3)
        shared = next(item for item in result["candidates"] if item["full_name"] == "org/shared")
        self.assertEqual(shared["discovered_via"], ["agents", "memory"])
        candidate = next(item for item in result["candidates"] if item["full_name"] == "org/agent-a")
        self.assertEqual(candidate["license_spdx"], "MIT")
        self.assertEqual(candidate["review_status"], "unreviewed")
        self.assertFalse(candidate["license_verified"])
        self.assertFalse(result["code_execution"])
        self.assertFalse(result["installation_or_cloning"])
        self.assertEqual(result["generation_requests"], 0)

    def test_github_and_crossref_responses_are_size_bounded(self):
        class OversizedResponse:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, limit): return b"x" * (MAX_RESPONSE_BYTES + 1)
        with patch("engine.research_discovery.urlopen", return_value=OversizedResponse()):
            with self.assertRaisesRegex(ResearchDiscoveryError, "1 MiB"):
                _request_json("agent framework")
        with patch("engine.research_discovery.urlopen", return_value=OversizedResponse()):
            with self.assertRaisesRegex(ResearchDiscoveryError, "1 MiB"):
                _request_crossref("agent memory")

    def test_invalid_utf8_from_both_sources_is_reported_safely(self):
        class InvalidUtf8Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, limit): return bytes([255])
        with patch("engine.research_discovery.urlopen", return_value=InvalidUtf8Response()):
            with self.assertRaisesRegex(ResearchDiscoveryError, "UnicodeDecodeError"):
                _request_json("agent framework")
        with patch("engine.research_discovery.urlopen", return_value=InvalidUtf8Response()):
            with self.assertRaisesRegex(ResearchDiscoveryError, "UnicodeDecodeError"):
                _request_crossref("agent memory")
    def test_query_and_limit_validation(self):
        with self.assertRaises(ValueError): discover_repositories({}, {})
        with self.assertRaises(ValueError): discover_repositories({"x": "y"}, {}, max_per_query=11)
        calls = []
        bounded = discover_repositories({"x": "y", "z": "q"}, {}, max_queries=1, request_fn=lambda q, token: (calls.append(q) or {"items": []}))
        self.assertEqual(len(calls), 1)
        self.assertEqual(bounded["queries_attempted"], 1)

    def test_query_count_is_bounded(self):
        calls = []
        result = discover_repositories({"a": "one", "b": "two", "c": "three"}, {}, max_queries=2, request_fn=lambda q, token: (calls.append(q) or {"items": []}))
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["queries_attempted"], 2)

    def test_crossref_metadata_is_normalized_without_abstract_or_licence_claims(self):
        def fake_crossref(query, limit):
            self.assertEqual(limit, 5)
            return {"items": [{"DOI": "10.1234/agent-paper", "title": ["Agent orchestration research"], "author": [{"given": "Ada", "family": "Lovelace"}], "published": {"date-parts": [[2025, 4, 2]]}, "URL": "https://doi.org/10.1234/agent-paper", "container-title": ["Journal of Agents"], "is-referenced-by-count": 12, "license": [{"URL": "https://creativecommons.org/licenses/by/4.0/"}], "type": "journal-article"}, {"DOI": "10.1234/no-title", "title": []}]}
        result = discover_repositories({"agents": "agent framework"}, {}, request_fn=lambda q, token: {"items": []}, academic_query="agent memory", max_academic_results=5, academic_request_fn=fake_crossref)
        self.assertEqual(result["academic_requests_made"], 1)
        self.assertEqual(result["academic_candidates"][0]["doi"], "10.1234/agent-paper")
        self.assertEqual(result["academic_candidates"][0]["authors"], ["Ada Lovelace"])
        self.assertEqual(result["academic_candidates"][0]["published"], "2025-4-2")
        self.assertEqual(result["academic_candidates"][0]["citation_count"], 12)
        self.assertFalse(result["academic_candidates"][0]["license_verified"])
        self.assertFalse(result["academic_candidates"][0]["abstract_retrieved"])
        self.assertEqual(result["generation_requests"], 0)

    def test_crossref_failure_is_nonfatal_and_reported(self):
        def fail(query, limit): raise ResearchDiscoveryError("Crossref returned HTTP 429")
        result = discover_repositories({"agents": "agent framework"}, {}, request_fn=lambda q, token: {"items": []}, academic_query="agent memory", academic_request_fn=fail)
        self.assertEqual(result["academic_requests_made"], 1)
        self.assertIn("429", result["academic_unavailable"])
        self.assertEqual(result["candidate_count"], 0)
    def test_failure_is_reported_without_exposing_response_body(self):
        def fail(query, token): raise ResearchDiscoveryError("GitHub search returned HTTP 403; rate_limit_remaining=0")
        result = discover_repositories({"agents": "agent framework"}, {}, request_fn=fail)
        self.assertEqual(result["requests_made"], 1)
        self.assertIn("agents", result["unavailable"])
        self.assertEqual(result["candidate_count"], 0)

    def test_arxiv_atom_metadata_is_bounded_and_does_not_ingest_abstract(self):
        atom = b'''<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
          <entry>
            <id>https://arxiv.org/abs/2611.01234v1</id>
            <title>  Safe   Multi-Agent Research </title>
            <summary>untrusted abstract must not be retained</summary>
            <published>2026-11-02T00:00:00Z</published>
            <updated>2026-11-02T00:00:00Z</updated>
            <author><name>Example Author</name></author>
            <category term="cs.AI"/>
            <arxiv:primary_category term="cs.AI"/>
            <arxiv:doi>10.1234/example</arxiv:doi>
          </entry>
        </feed>'''
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, limit): return atom
        with patch("engine.research_discovery.urlopen", return_value=Response()):
            payload = _request_arxiv('all:"multi-agent"', 5)
        self.assertEqual(len(payload["entries"]), 1)
        from engine.research_discovery import _normalize_arxiv_entry
        normalized = _normalize_arxiv_entry(payload["entries"][0], "2026-11-03T00:00:00+00:00")
        self.assertEqual(normalized["arxiv_id"], "2611.01234v1")
        self.assertEqual(normalized["title"], "Safe Multi-Agent Research")
        self.assertEqual(normalized["authors"], ["Example Author"])
        self.assertEqual(normalized["categories"], ["cs.AI"])
        self.assertEqual(normalized["doi"], "10.1234/example")
        self.assertFalse(normalized["abstract_retrieved"])
        self.assertFalse(normalized["full_text_retrieved"])
        self.assertNotIn("summary", normalized)

    def test_arxiv_rejects_dtd_and_oversized_or_malformed_xml(self):
        class Response:
            status = 200
            def __init__(self, body): self.body = body
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, limit): return self.body
        for body, expected in (
            (b'<!DOCTYPE feed [<!ENTITY x "y">]><feed/>', "DTD/entities"),
            (b"<feed>", "malformed Atom XML"),
            (b"x" * (MAX_RESPONSE_BYTES + 1), "1 MiB"),
        ):
            with patch("engine.research_discovery.urlopen", return_value=Response(body)):
                with self.assertRaisesRegex(ResearchDiscoveryError, expected):
                    _request_arxiv("all:agents", 5)

    def test_arxiv_discovery_is_one_bounded_request_and_failure_is_nonfatal(self):
        calls = []
        def fake_arxiv(query, limit):
            calls.append((query, limit))
            return {"entries": [{
                "arxiv_id": "2611.00001v1",
                "title": "LLM agent tool use",
                "url": "https://arxiv.org/abs/2611.00001v1",
                "published": "2026-11-02T00:00:00Z",
            }]}
        result = discover_repositories(
            {"agents": "agent framework"}, {},
            request_fn=lambda query, token: {"items": []},
            arxiv_query="all:agents", max_arxiv_results=3, arxiv_request_fn=fake_arxiv,
        )
        self.assertEqual(calls, [("all:agents", 3)])
        self.assertEqual(result["arxiv_requests_made"], 1)
        self.assertEqual(result["total_requests_made"], 2)
        self.assertEqual(result["arxiv_candidates"][0]["source"], "arxiv")
        self.assertFalse(result["arxiv_candidates"][0]["full_text_retrieved"])

        def fail(query, limit):
            raise ResearchDiscoveryError("arXiv returned HTTP 503")
        failed = discover_repositories(
            {"agents": "agent framework"}, {},
            request_fn=lambda query, token: {"items": []},
            arxiv_query="all:agents", arxiv_request_fn=fail,
        )
        self.assertEqual(failed["arxiv_requests_made"], 1)
        self.assertIn("503", failed["arxiv_unavailable"])
        self.assertEqual(failed["arxiv_candidates"], [])


    def test_searxng_requires_operator_configured_public_https_endpoint(self):
        self.assertEqual(
            _validate_searxng_base_url("https://search.example.org"),
            "https://search.example.org",
        )
        for endpoint in (
            "http://search.example.org",
            "https://user:pass@search.example.org",
            "https://localhost:8080",
            "https://127.0.0.1:8080",
            "https://search.example.org/?q=secret",
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(ResearchDiscoveryError):
                    _validate_searxng_base_url(endpoint)

    def test_searxng_json_response_is_bounded_and_normalized(self):
        payload = json.dumps({
            "results": [{
                "title": "Open source agent security",
                "url": "https://docs.example.org/agent-security",
                "content": "Research on agent tool security and prompt injection.",
                "engines": ["duckduckgo", "brave"],
                "category": "it",
                "score": 4.5,
            }]
        }).encode()
        class Response:
            status = 200
            headers = {"Content-Type": "application/json"}
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def geturl(self): return "https://search.example.org/search?q=agents&format=json"
            def read(self, limit): return payload
        with patch("engine.research_discovery.urlopen", return_value=Response()) as opened:
            result = _request_searxng("agent security", "https://search.example.org", 5)
        request = opened.call_args.args[0]
        self.assertIn("format=json", request.full_url)
        self.assertEqual(len(result["results"]), 1)
        from engine.research_discovery import _normalize_searxng_result
        normalized = _normalize_searxng_result(result["results"][0], "2026-11-03T00:00:00+00:00")
        self.assertEqual(normalized["source"], "searxng")
        self.assertEqual(normalized["engines"], ["duckduckgo", "brave"])
        self.assertFalse(normalized["license_verified"])

    def test_searxng_is_skipped_without_endpoint_and_failure_is_nonfatal(self):
        calls = []
        skipped = discover_repositories(
            {"agents": "agent framework"}, {},
            request_fn=lambda query, token: {"items": []},
            searxng_query="agent security",
            searxng_request_fn=lambda *args: calls.append(args),
        )
        self.assertEqual(calls, [])
        self.assertEqual(skipped["searxng_requests_made"], 0)
        self.assertEqual(skipped["searxng_unavailable"], "endpoint_not_configured")

        def fail(query, endpoint, limit):
            raise ResearchDiscoveryError("SearXNG returned HTTP 403; verify JSON format is enabled")
        failed = discover_repositories(
            {"agents": "agent framework"}, {},
            request_fn=lambda query, token: {"items": []},
            searxng_query="agent security",
            searxng_base_url="https://search.example.org",
            searxng_request_fn=fail,
        )
        self.assertEqual(failed["searxng_requests_made"], 1)
        self.assertIn("403", failed["searxng_unavailable"])
        self.assertEqual(failed["searxng_candidates"], [])

    def test_searxng_rejects_cross_host_or_port_redirect(self):
        class Response:
            status = 200
            headers = {"Content-Type": "application/json"}
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def geturl(self): return "https://other.example.org/search"
            def read(self, limit): return b'{"results":[]}'
        with patch("engine.research_discovery.urlopen", return_value=Response()):
            with self.assertRaisesRegex(ResearchDiscoveryError, "redirected outside"):
                _request_searxng("agent security", "https://search.example.org", 5)


    def test_searxng_rejects_oversized_response(self):
        class Response:
            status = 200
            headers = {"Content-Type": "application/json"}
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def geturl(self): return "https://search.example.org/search"
            def read(self, limit): return b"x" * (MAX_RESPONSE_BYTES + 1)
        with patch("engine.research_discovery.urlopen", return_value=Response()):
            with self.assertRaisesRegex(ResearchDiscoveryError, "1 MiB"):
                _request_searxng("agent security", "https://search.example.org", 5)


if __name__ == "__main__": unittest.main()
