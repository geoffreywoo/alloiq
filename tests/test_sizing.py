import unittest

from invest.sizing import model_target_summary, normalize_model_targets


class SizingTests(unittest.TestCase):
    def test_trim_targets_are_preserved_while_other_targets_scale_to_book_total(self):
        targets = [
            {
                "symbol": "WEAK",
                "current_weight": 0.10,
                "unscaled_model_target_weight": 0.06,
                "verdict": "trim",
            },
            {
                "symbol": "STRONG",
                "current_weight": 0.05,
                "unscaled_model_target_weight": 0.30,
                "verdict": "buy",
            },
            {
                "symbol": "STUDY",
                "current_weight": 0.02,
                "unscaled_model_target_weight": 0.50,
                "verdict": "study",
            },
        ]

        normalize_model_targets(targets, target_total=0.80)

        by_symbol = {row["symbol"]: row for row in targets}
        self.assertEqual(by_symbol["WEAK"]["model_target_weight"], 0.06)
        self.assertEqual(by_symbol["WEAK"]["desired_delta_weight"], -0.04)
        self.assertLessEqual(by_symbol["WEAK"]["model_target_weight"], by_symbol["WEAK"]["current_weight"])
        self.assertAlmostEqual(sum(row["model_target_weight"] for row in targets), 0.80, places=6)
        self.assertGreater(by_symbol["STRONG"]["model_target_weight"], by_symbol["STRONG"]["unscaled_model_target_weight"] * 0.9)

    def test_company_trim_signal_cannot_scale_above_current_weight(self):
        targets = [
            {
                "symbol": "DETERIORATING",
                "current_weight": 0.08,
                "unscaled_model_target_weight": 0.20,
                "company_trim_signal": True,
            },
            {
                "symbol": "HIGHCONV",
                "current_weight": 0.04,
                "unscaled_model_target_weight": 0.40,
            },
        ]

        normalize_model_targets(targets, target_total=0.60)

        by_symbol = {row["symbol"]: row for row in targets}
        self.assertEqual(by_symbol["DETERIORATING"]["model_target_weight"], 0.08)
        self.assertEqual(by_symbol["DETERIORATING"]["desired_delta_weight"], 0.0)
        self.assertEqual(by_symbol["HIGHCONV"]["model_target_weight"], 0.52)
        self.assertAlmostEqual(sum(row["model_target_weight"] for row in targets), 0.60, places=6)

    def test_scaled_targets_do_not_exceed_max_allowed_weight(self):
        targets = [
            {
                "symbol": "CAPPED",
                "current_weight": 0.05,
                "unscaled_model_target_weight": 0.10,
                "max_allowed_weight": 0.12,
            },
            {
                "symbol": "SCALABLE",
                "current_weight": 0.04,
                "unscaled_model_target_weight": 0.20,
                "max_allowed_weight": 0.90,
            },
        ]

        normalize_model_targets(targets, target_total=0.60)

        by_symbol = {row["symbol"]: row for row in targets}
        self.assertEqual(by_symbol["CAPPED"]["model_target_weight"], 0.12)
        self.assertLessEqual(by_symbol["CAPPED"]["model_target_weight"], by_symbol["CAPPED"]["max_allowed_weight"])
        self.assertEqual(by_symbol["SCALABLE"]["model_target_weight"], 0.48)
        self.assertAlmostEqual(sum(row["model_target_weight"] for row in targets), 0.60, places=6)

    def test_model_target_summary_surfaces_cap_bindings_and_unallocated_weight(self):
        targets = [
            {"symbol": "FULL", "model_target_weight": 0.12, "max_allowed_weight": 0.12},
            {"symbol": "ROOM", "model_target_weight": 0.18, "max_allowed_weight": 0.50},
        ]

        summary = model_target_summary(targets, target_total=0.60)

        self.assertEqual(summary["model_target_total_weight"], 0.30)
        self.assertEqual(summary["model_target_unallocated_weight"], 0.30)
        self.assertEqual(summary["model_target_overallocated_weight"], 0.0)
        self.assertEqual(summary["model_target_utilization_pct"], 50.0)
        self.assertEqual(summary["max_allowed_binding_count"], 1)
        self.assertEqual(summary["max_allowed_binding_symbols"], ["FULL"])


if __name__ == "__main__":
    unittest.main()
