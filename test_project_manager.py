import unittest

from project_manager import ProjectManager


class ProjectManagerPricingTests(unittest.TestCase):
    def test_gpt_5_6_pricing_is_calculated_from_the_selected_model(self):
        manager = ProjectManager()

        self.assertEqual(manager.calculate_cost("GPT-5.6 Sol", 1_000_000, 1_000_000), 35.0)
        self.assertEqual(manager.calculate_cost("GPT-5.6 Terra", 1_000_000, 1_000_000), 17.5)
        self.assertEqual(manager.calculate_cost("GPT-5.6 Luna", 1_000_000, 1_000_000), 7.0)

    def test_unknown_discovered_model_does_not_borrow_another_models_price(self):
        manager = ProjectManager()

        self.assertEqual(manager.calculate_cost("gpt-future-model", 1_000_000, 1_000_000), 0.0)


if __name__ == "__main__":
    unittest.main()
