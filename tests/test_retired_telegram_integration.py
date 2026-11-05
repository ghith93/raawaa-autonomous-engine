"""Owner-cancelled Telegram integration must not reappear in runtime paths."""
from pathlib import Path
import unittest
ROOT = Path(__file__).resolve().parents[1]

class RetiredTelegramTests(unittest.TestCase):
    def test_handler_and_setup_are_absent(self):
        for name in ('worker/telegram-webhook.js', '.github/workflows/telegram-register.yml',
                     'scripts/telegram_bootstrap.py'):
            self.assertFalse((ROOT / name).exists(), name)

    def test_runtime_has_no_telegram_integration(self):
        for name in ('worker/index.js', 'worker/mobile-status.js',
                     'engine/mobile_status_probe.py', '.github/workflows/task-runner.yml',
                     '.github/workflows/secret-readiness.yml'):
            self.assertNotIn('telegram', (ROOT / name).read_text().lower(), name)
