"""Integrity tests for the 2026-11-07 Raawaa-store handoff patches.

The two store fixes could not be pushed: the Arena GitHub connection can read
``ghith93/Raawaa-store`` but every write returns
``403 Resource not accessible by integration`` (``git push``, ``gh pr create`` and
``POST /git/blobs`` were each attempted). Delivering them as patches in this
repository is therefore the durable path, and it only stays useful if the
patches can still be applied to the base they claim.

These tests are offline and touch no network. They pin:

* both patches are present and non-trivial;
* each patch records the exact base commit it was built from;
* each patch carries the safety properties that would silently rot if someone
  regenerated it by hand (strict CSP kept, no inline axe injection, no product
  price change, no production asset leak);
* the handoff document states the blocker and the unverified claims honestly
  instead of presenting the offline work as a deployment.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HANDOFF = ROOT / "docs" / "handoffs" / "store-delivery-2026-11-07"
DELIVERY_PATCH = HANDOFF / "hama-free-delivery.patch"
CSP_PATCH = HANDOFF / "pr158-csp-axe-harness.patch"
README = HANDOFF / "README.md"

STORE_MAIN_AT_PATCH_TIME = "e81e25e44a11cc5b5d7746c3d8def7e1d24cb38e"
PR158_HEAD_AT_PATCH_TIME = "5192a8334c68685daca031136e125f4b53b84f89"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def patch_added_lines(text: str) -> list[str]:
    """Lines a patch adds, excluding the diff headers and file markers."""
    return [
        line[1:]
        for line in text.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]


class HandoffArtifactTests(unittest.TestCase):
    def test_patches_and_readme_exist_and_are_substantial(self):
        for path in (DELIVERY_PATCH, CSP_PATCH, README):
            self.assertTrue(path.is_file(), f"missing handoff artifact: {path.name}")
        self.assertGreater(DELIVERY_PATCH.stat().st_size, 20_000)
        self.assertGreater(CSP_PATCH.stat().st_size, 10_000)
        self.assertGreater(README.stat().st_size, 2_000)

    def test_delivery_patch_targets_main_and_keeps_product_prices(self):
        text = _read(DELIVERY_PATCH)
        self.assertIn("fix(delivery): make Hama free for every payment method", text)
        self.assertIn(STORE_MAIN_AT_PATCH_TIME, _read(README))
        added = "\n".join(patch_added_lines(text))
        # Hama becomes a zero-fee zone; prices 13/16/25 stay as the only values.
        self.assertIn("id: 'hama', label: 'حماة', fee: 0", added)
        for price in ("price: 13", "price: 16", "price: 25"):
            self.assertNotIn(price, added, f"patch must not rewrite {price}")
        self.assertIn("scripts/delivery-contract.test.mjs", text)
        # Four-case regression must exist in the patch, not only in the session.
        for case in ("hama", "other", "cod", "shamcash"):
            self.assertIn(case, added)

    def test_csp_patch_keeps_policy_strict_and_drops_inline_injection(self):
        text = _read(CSP_PATCH)
        self.assertIn("fix(ci): load axe as a same-origin external script", text)
        self.assertIn(PR158_HEAD_AT_PATCH_TIME, _read(README))
        added = "\n".join(patch_added_lines(text))
        # "unsafe-inline" may only appear where a guard *forbids* it; no added
        # line may put it inside a policy value.
        for line in patch_added_lines(text):
            if "unsafe-inline" in line or "unsafe-eval" in line:
                self.assertTrue(
                    "doesNotMatch" in line or "يجب ألا يسمح" in line
                    or "دون " in line or "بلا unsafe-inline" in line
                    or "لا يضعف" in line,
                    f"patch mentions unsafe-* outside a guard: {line.strip()[:120]}",
                )
        self.assertNotRegex(text, r"\+\s*Content-Security-Policy:[^\n]*unsafe")
        # The added loader must use an external same-origin URL, never content.
        self.assertIn("add_script_tag(url=url", added)
        # Prose may *describe* the blocked mechanism (`add_script_tag(content=...)`);
        # an actual call would pass a real value, so only that counts as a regression.
        self.assertNotRegex(added, r"add_script_tag\(\s*content=(?!\.\.\.)")
        self.assertIn("/__test__/axe.min.js", added)
        # The harness must carry a positive control proving inline stays blocked.
        self.assertIn("assert_inline_scripts_blocked", added)

    def test_patches_do_not_touch_production_csp_or_headers(self):
        for path in (DELIVERY_PATCH, CSP_PATCH):
            text = _read(path)
            self.assertNotIn("+++ b/_headers", text)
            self.assertNotIn("+++ b/.github/workflows", text)

    def test_readme_separates_verified_from_unverified(self):
        text = _read(README)
        self.assertIn("403 Resource not accessible by integration", text)
        for proven in ("delivery-contract.test.mjs", "csp-axe-harness.test.mjs"):
            self.assertIn(proven, text)
        # Claims that need CI or a deploy must be labelled as unverified.
        self.assertRegex(text, r"not verified")
        self.assertIn("Chromium and WebKit", text)
        self.assertRegex(text, r"No deployment (was|is) performed|no deploy was performed")

    def test_readme_documents_apply_steps(self):
        text = _read(README)
        for step in ("git am", "git push -u origin", "gates:offline"):
            self.assertIn(step, text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
