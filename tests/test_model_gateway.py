import tempfile
import unittest
from pathlib import Path
from engine.model_router import ModelOption
from engine.provider import GenerationRequest, MockAdapter
from engine.model_gateway import BudgetExceeded, ModelGateway, ProviderUnavailable, UsageLedger


class ModelGatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.ledger=UsageLedger(Path(self.tmp.name)/"usage.db")
    def tearDown(self): self.tmp.cleanup()
    def test_mock_offline_path(self):
        gateway=ModelGateway([ModelOption("mock","offline","free",True,0.0)],
            {"mock":MockAdapter()},self.ledger,network_calls_enabled=False)
        result=gateway.generate(GenerationRequest("offline_test","test prompt"))
        self.assertTrue(result.mocked)
        self.assertEqual(result.cost_usd,0.0)
        self.assertEqual(self.ledger.totals()["daily_usd"],0.0)
    def test_live_task_blocked_by_default(self):
        gateway=ModelGateway([ModelOption("mock","offline","free",True,0.0)],
            {"mock":MockAdapter()},self.ledger)
        with self.assertRaises(ProviderUnavailable):
            gateway.generate(GenerationRequest("research","test"))
    def test_no_model_fails_closed(self):
        gateway=ModelGateway([],{"mock":MockAdapter()},self.ledger,network_calls_enabled=True)
        with self.assertRaises(ProviderUnavailable):
            gateway.generate(GenerationRequest("research","test"))
    def test_zero_cost_model_is_allowed_with_zero_budget(self):
        gateway=ModelGateway([ModelOption("mock","offline","free",True,0.0)],
            {"mock":MockAdapter()},self.ledger,network_calls_enabled=False,
            daily_budget_usd=0.0,monthly_budget_usd=0.0)
        result=gateway.generate(GenerationRequest("offline_test","test"))
        self.assertEqual(result.cost_usd,0.0)

    def test_paid_model_not_selected_when_paid_calls_disabled(self):
        gateway=ModelGateway([ModelOption("mock","paid-model","paid",True,0.1)],
            {"mock":MockAdapter()},self.ledger,network_calls_enabled=True,
            daily_budget_usd=1.0,monthly_budget_usd=1.0,allow_paid_calls=False)
        with self.assertRaises(ProviderUnavailable):
            gateway.generate(GenerationRequest("research","test"))

    def test_usage_ledger_rejects_invalid_tokens_and_non_finite_costs(self):
        for kwargs in [
            {"input_tokens": -1, "output_tokens": 1, "cost_usd": 0.0},
            {"input_tokens": 1, "output_tokens": -1, "cost_usd": 0.0},
            {"input_tokens": 1, "output_tokens": 1, "cost_usd": float("nan")},
            {"input_tokens": 1, "output_tokens": 1, "cost_usd": float("inf")},
        ]:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    self.ledger.record("provider", "model", "task", **kwargs)

    def test_status_exposes_no_live_adapter(self):
        gateway=ModelGateway([],{},self.ledger)
        self.assertFalse(gateway.status()["live_provider_adapters_enabled"])


if __name__=="__main__": unittest.main()
