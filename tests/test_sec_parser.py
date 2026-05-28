from pathlib import Path
from urllib.error import HTTPError
import unittest
from unittest.mock import patch

import invest.filings.sec as sec
from invest.filings.sec import fetch_json, parse_13f_xml


class SecParserTests(unittest.TestCase):
    def test_parse_13f_xml_reads_reported_dollar_values(self):
        xml = (Path(__file__).parent / "fixtures" / "salp_13f.xml").read_bytes()
        holdings = parse_13f_xml(xml, accession_number="0002045724-26-000002")

        self.assertEqual(len(holdings), 2)
        self.assertEqual(holdings[0].issuer, "BLOOM ENERGY CORP")
        self.assertEqual(holdings[0].value_usd, 875505)
        self.assertEqual(holdings[1].put_call, "Call")

    def test_fetch_json_retries_sec_rate_limit(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"ok": true}'

        sec._SEC_LAST_REQUEST_AT = 0.0
        rate_limited = HTTPError(
            "https://www.sec.gov/example",
            429,
            "Too Many Requests",
            {"Retry-After": "0.5"},
            None,
        )
        with (
            patch("invest.filings.sec.urllib.request.urlopen", side_effect=[rate_limited, Response()]) as urlopen,
            patch("invest.filings.sec.time.sleep") as sleep,
        ):
            payload = fetch_json("https://www.sec.gov/example")

        self.assertEqual(payload, {"ok": True})
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_any_call(0.5)


if __name__ == "__main__":
    unittest.main()
