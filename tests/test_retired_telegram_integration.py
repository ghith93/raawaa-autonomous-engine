"""Owner-cancelled Telegram integration must not reappear in runtime, workflow, or config paths."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RetiredTelegramTests(unittest.TestCase):
    def test_handler_and_setup_are_absent(self):
        for name in (
            "worker/telegram-webhook.js",
            ".github/workflows/telegram-register.yml",
            "scripts/telegram_bootstrap.py",
        ):
            self.assertFalse((ROOT / name).exists(), name)

    def test_runtime_has_no_telegram_integration(self):
        for name in (
            "worker/index.js",
            "worker/mobile-status.js",
            "engine/mobile_status_probe.py",
            "engine/credentials.py",
            "scripts/raawaa_doctor.py",
        ):
            self.assertNotIn("telegram", (ROOT / name).read_text(encoding="utf-8").lower(), name)

    def test_workflows_and_configs_have_no_telegram_references(self):
        for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
            rel = str(path.relative_to(ROOT))
            self.assertNotIn("telegram", path.read_text(encoding="utf-8").lower(), rel)
        for path in sorted((ROOT / "config").rglob("*.json")):
            rel = str(path.relative_to(ROOT))
            self.assertNotIn("telegram", path.read_text(encoding="utf-8").lower(), rel)


if __name__ == "__main__":
    unittest.main()
