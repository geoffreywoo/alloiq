from pathlib import Path
import unittest

from invest.filings.sec import parse_13f_xml


class SecParserTests(unittest.TestCase):
    def test_parse_13f_xml_reads_reported_dollar_values(self):
        xml = (Path(__file__).parent / "fixtures" / "salp_13f.xml").read_bytes()
        holdings = parse_13f_xml(xml, accession_number="0002045724-26-000002")

        self.assertEqual(len(holdings), 2)
        self.assertEqual(holdings[0].issuer, "BLOOM ENERGY CORP")
        self.assertEqual(holdings[0].value_usd, 875505)
        self.assertEqual(holdings[1].put_call, "Call")


if __name__ == "__main__":
    unittest.main()
