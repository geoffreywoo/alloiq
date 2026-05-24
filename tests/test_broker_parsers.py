from pathlib import Path
import unittest

from invest.brokers.ibkr import normalize_ibkr_action, parse_flex_xml, summarize_flex_xml
from invest.brokers.vanguard import parse_vanguard_csv, parse_vanguard_positions_csv


FIXTURES = Path(__file__).parent / "fixtures"


class BrokerParserTests(unittest.TestCase):
    def test_ibkr_flex_parser_reads_trades_cash_and_positions(self):
        transactions, positions = parse_flex_xml(FIXTURES / "ibkr_flex.xml")

        self.assertEqual(len(transactions), 3)
        self.assertEqual(transactions[0].action, "BUY")
        self.assertEqual(transactions[0].symbol, "NVDA")
        self.assertEqual(transactions[1].action, "SELL")
        self.assertEqual(transactions[2].action, "DIVIDEND")
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0].symbol, "NVDA")

    def test_ibkr_flex_summary_is_safe_for_status_output(self):
        summary = summarize_flex_xml(FIXTURES / "ibkr_flex.xml")

        self.assertEqual(summary["accounts"], ["SIM-ACCOUNT"])
        self.assertEqual(summary["transaction_count"], 3)
        self.assertEqual(summary["position_count"], 1)
        self.assertIn("NVDA", summary["symbols"])

    def test_ibkr_action_text_wins_over_quantity_sign(self):
        self.assertEqual(normalize_ibkr_action({"buySell": "SELL", "quantity": "10"}), "SELL")
        self.assertEqual(normalize_ibkr_action({"buySell": "BUY", "quantity": "-10"}), "BUY")

    def test_vanguard_csv_parser_maps_common_export_headers(self):
        transactions = parse_vanguard_csv(FIXTURES / "vanguard_transactions.csv")

        self.assertEqual(len(transactions), 3)
        self.assertEqual(transactions[0].broker, "vanguard")
        self.assertEqual(transactions[0].action, "BUY")
        self.assertEqual(transactions[1].action, "SELL")
        self.assertEqual(transactions[2].action, "DIVIDEND")

    def test_vanguard_positions_csv_parser_maps_holdings_export(self):
        positions = parse_vanguard_positions_csv(FIXTURES / "vanguard_positions.csv")

        self.assertEqual(len(positions), 2)
        self.assertEqual(positions[0].broker, "vanguard")
        self.assertEqual(positions[0].symbol, "NVDA")
        self.assertEqual(str(positions[0].market_value), "4600.00")
        self.assertEqual(str(positions[1].cost_basis), "900.00")


if __name__ == "__main__":
    unittest.main()
