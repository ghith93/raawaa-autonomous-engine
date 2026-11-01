import unittest
from engine.model_router import ModelOption, ModelRouter


class ModelRouterTests(unittest.TestCase):
    def test_no_provider_means_no_selection(self):
        self.assertIsNone(ModelRouter().select("research"))

    def test_free_route_only_by_default(self):
        options = [
            ModelOption("paid-provider", "large", "paid", True, 0.02),
            ModelOption("free-provider", "small", "free", True, 0.0),
        ]
        selected = ModelRouter(options).select("research")
        self.assertEqual(selected.provider, "free-provider")

    def test_paid_not_selected_without_explicit_enablement(self):
        router = ModelRouter([ModelOption("p", "m", "paid", True, 0.0)])
        self.assertIsNone(router.select("coding"))


if __name__ == "__main__":
    unittest.main()
