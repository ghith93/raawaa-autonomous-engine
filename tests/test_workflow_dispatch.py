import unittest

from engine.workflow_dispatch import (
    SCHEMA_VERSION,
    dispatch_body,
    render_markdown,
    step_idempotency_key,
    vector_matches,
)
from engine.workflow_manifest import build_workflow_run_manifest

PLAN = {
    "workflow_id": "demo-workflow",
    "mode": "plan_only",
    "dispatch_authorized": False,
    "execution_started": False,
    "provider_calls": 0,
    "external_writes": 0,
    "order": ["prepare", "verify"],
    "steps": {
        "prepare": {"task_type": "system_audit", "title": "prepare", "assigned_agent": "engine", "depends_on": []},
        "verify": {"task_type": "system_audit", "title": "verify", "assigned_agent": "engine",
                   "depends_on": ["prepare"]},
    },
}


def manifest(**kwargs):
    return build_workflow_run_manifest(PLAN, "run-9", **kwargs)


class IdempotencyKeyTest(unittest.TestCase):
    def test_pinned_vector_matches_the_worker(self):
        # The same assertion exists in tests/workflow-dispatch.test.js. Two
        # implementations of one derivation are only safe while both are pinned.
        self.assertTrue(vector_matches())
        self.assertEqual(
            step_idempotency_key("demo-workflow", "run-9", 1, "prepare"),
            "workflow-step:730e3fe09f1dbace1419a3f7cbd2e3008fc7b1c8541a7b95c3e6036c0e2c76af",
        )

    def test_separators_are_not_optional(self):
        # Without the null separators these two pairs would hash the same string.
        self.assertNotEqual(
            step_idempotency_key("a", "bc", 1, "d"),
            step_idempotency_key("ab", "c", 1, "d"),
        )

    def test_version_and_step_change_the_key(self):
        base = step_idempotency_key("w", "r", 1, "s")
        self.assertNotEqual(base, step_idempotency_key("w", "r", 2, "s"))
        self.assertNotEqual(base, step_idempotency_key("w", "r", 1, "t"))


class DispatchBodyTest(unittest.TestCase):
    def test_body_keys_match_the_manifest_it_came_from(self):
        # If these diverge, the Worker would enqueue tasks under identities the
        # manifest does not know, and a replay would duplicate instead of no-op.
        document = manifest()
        body = dispatch_body(document)
        by_step = {step["step_id"]: step for step in document.as_dict()["steps"]}
        for step in body["steps"]:
            self.assertEqual(body["task_keys"][step["step_id"]], by_step[step["step_id"]]["task_idempotency_key"])

    def test_body_carries_the_manifest_digest_and_identity(self):
        document = manifest()
        body = dispatch_body(document)
        self.assertEqual(body["plan_digest"], document.as_dict()["plan_digest"])
        self.assertEqual(body["run_id"], "run-9")
        self.assertEqual(body["workflow_id"], "demo-workflow")
        self.assertEqual(body["schema_version"], SCHEMA_VERSION)
        self.assertEqual([step["step_id"] for step in body["steps"]], ["prepare", "verify"])
        self.assertEqual(body["steps"][1]["depends_on"], ["prepare"])

    def test_payloads_are_attached_per_step(self):
        document = manifest()
        body = dispatch_body(document, payloads={"prepare": {"k": 1}})
        payloads = {step["step_id"]: step["payload"] for step in body["steps"]}
        self.assertEqual(payloads["prepare"], {"k": 1})
        self.assertEqual(payloads["verify"], {})

    def test_payload_for_an_unknown_step_is_refused(self):
        document = manifest()
        with self.assertRaises(ValueError):
            dispatch_body(document, payloads={"ghost": {}})

    def test_malformed_manifests_are_refused_locally(self):
        for broken in (
            {},
            {"workflow_id": "w"},
            {"workflow_id": "w", "run_id": "r", "plan_digest": "short", "steps": []},
        ):
            with self.assertRaises(ValueError):
                dispatch_body(broken)

    def test_render_markdown_names_every_step(self):
        document = manifest()
        text = render_markdown(dispatch_body(document))
        self.assertIn("prepare", text)
        self.assertIn("verify", text)
        self.assertIn("Not sent by this module", text)


if __name__ == "__main__":
    unittest.main()
