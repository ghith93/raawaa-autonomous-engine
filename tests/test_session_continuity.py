import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.session_continuity import (
    ROOT,
    SessionContinuityError,
    compute_inbox_state,
    load_manifest,
    main,
    reconstruct_repo_capabilities,
    update_watermark,
)


class SessionContinuityTests(unittest.TestCase):
    def test_live_repository_manifest_and_capabilities_are_valid(self):
        manifest = load_manifest(ROOT)
        self.assertEqual(manifest["channel"]["issue_number"], 464)
        self.assertFalse(manifest["channel"]["automatic_session_wake_supported"])
        self.assertTrue(manifest["channel"]["unofficial_wake_interfaces_prohibited"])
        self.assertGreaterEqual(manifest["watermark"]["last_processed_comment_id"], 6692775468)

        caps = reconstruct_repo_capabilities(ROOT)
        self.assertEqual(caps["policy_documents_verified"], 5)
        self.assertEqual(caps["policy_mode"], "safe_dry_run")
        self.assertEqual(caps["monthly_budget_usd"], 0)
        self.assertEqual(caps["monthly_total_hard_cap_usd"], 0)
        self.assertFalse(caps["paid_model_calls_allowed"])
        self.assertEqual(caps["provider_profiles_count"], 4)
        self.assertEqual(caps["enabled_provider_profiles"], [])
        self.assertGreaterEqual(caps["workflows_count"], 25)
        self.assertIn(
            caps["integrations"].get("arena_agent_mode"),
            {"active_manual_handoff_via_issue_464_no_auto_wake"},
        )

    def test_inbox_watermark_resolves_arena_report_and_unprocessed_comments(self):
        manifest = load_manifest(ROOT)
        base_id = manifest["watermark"]["last_processed_comment_id"]
        comments = [
            {
                "id": base_id,
                "created_at": "2026-11-06T18:51:16Z",
                "user": {"login": "ghith93"},
                "body": "CHATGPT REVIEW — checkpoint",
                "html_url": f"https://example.test/#{base_id}",
            },
            {
                "id": base_id + 10,
                "created_at": "2026-11-06T19:35:00Z",
                "user": {"login": "ghith93"},
                "body": f"ARENA REPORT\n- Processed comment ID: {base_id}",
                "html_url": f"https://example.test/#{base_id + 10}",
            },
            {
                "id": base_id + 25,
                "created_at": "2026-11-06T20:00:00Z",
                "user": {"login": "ghith93"},
                "body": "OPEN TASK — follow-up engineering slice",
                "html_url": f"https://example.test/#{base_id + 25}",
            },
        ]
        inbox = compute_inbox_state(manifest, comments)
        self.assertEqual(inbox["latest_arena_report_comment_id"], base_id + 10)
        self.assertEqual(inbox["effective_watermark_comment_id"], base_id + 10)
        self.assertEqual(inbox["unprocessed_count"], 1)
        self.assertEqual(inbox["unprocessed_comments"][0]["id"], base_id + 25)

    def test_update_watermark_enforces_monotonicity_and_required_files(self):
        manifest = load_manifest(ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            for rel in manifest["startup_required_files"] + ["config/session_continuity.json"]:
                src = ROOT / rel
                dst = temp_root / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

            with self.assertRaises(SessionContinuityError):
                update_watermark(
                    root=temp_root,
                    comment_id=100,
                    comment_created_at="2026-11-06T00:00:00Z",
                    main_sha="a" * 40,
                    summary="regression must fail",
                )

            next_id = manifest["watermark"]["last_processed_comment_id"] + 42
            updated = update_watermark(
                root=temp_root,
                comment_id=next_id,
                comment_created_at="2026-11-06T20:10:00Z",
                main_sha="b" * 40,
                summary="Verified next cycle.",
            )
            self.assertEqual(updated["watermark"]["last_processed_comment_id"], next_id)
            reloaded = json.loads((temp_root / "config" / "session_continuity.json").read_text(encoding="utf-8"))
            self.assertEqual(reloaded["watermark"]["last_verified_main_sha"], "b" * 40)

            (temp_root / "AGENTS.md").unlink()
            with self.assertRaises(SessionContinuityError):
                load_manifest(temp_root)

    def test_cli_status_offline_exits_zero(self):
        self.assertEqual(main(["status"]), 0)


if __name__ == "__main__":
    unittest.main()
