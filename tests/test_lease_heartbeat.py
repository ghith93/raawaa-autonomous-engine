import time
import unittest
from unittest.mock import Mock

from engine.lease_heartbeat import LeaseHeartbeat, LeaseHeartbeatLost


class LeaseHeartbeatTests(unittest.TestCase):
    def test_renews_lease_while_handler_is_running(self):
        store = Mock()
        keeper = LeaseHeartbeat(
            store, "task-1", "lease-1",
            interval_seconds=0.01, lease_timeout_seconds=1,
        ).start()
        try:
            time.sleep(0.04)
            keeper.check()
        finally:
            keeper.stop()
        self.assertGreaterEqual(store.heartbeat.call_count, 1)
        store.heartbeat.assert_called_with("task-1", "lease-1", 1)

    def test_heartbeat_failure_is_fail_closed(self):
        store = Mock()
        store.heartbeat.side_effect = RuntimeError("temporary network failure")
        keeper = LeaseHeartbeat(
            store, "task-1", "lease-1",
            interval_seconds=0.01, lease_timeout_seconds=1,
        ).start()
        time.sleep(0.04)
        keeper.stop()
        with self.assertRaisesRegex(LeaseHeartbeatLost, "persistence is blocked"):
            keeper.check()

    def test_rejects_invalid_configuration(self):
        with self.assertRaises(ValueError):
            LeaseHeartbeat(Mock(), "task-1", "lease-1", 10, 10)
        with self.assertRaises(ValueError):
            LeaseHeartbeat(Mock(), "task-1", "", 1, 10)

    def test_cannot_start_twice(self):
        keeper = LeaseHeartbeat(
            Mock(), "task-1", "lease-1",
            interval_seconds=1, lease_timeout_seconds=10,
        ).start()
        keeper.stop()
        with self.assertRaises(RuntimeError):
            keeper.start()


if __name__ == "__main__":
    unittest.main()
