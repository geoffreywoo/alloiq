import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from invest.config import AppConfig
from invest.notifications import (
    format_briefing_message,
    latest_report_payload,
    send_latest_briefing,
    send_telegram_message,
)


class NotificationTests(unittest.TestCase):
    def test_latest_report_payload_filters_session_and_uses_report_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports_dir = Path(tmp)
            write_report(reports_dir / "2026-05-25-premarket.json", {"as_of": "2026-05-25", "session": "premarket"})
            write_report(reports_dir / "2026-05-26-midday.json", {"as_of": "2026-05-26", "session": "midday"})
            write_report(reports_dir / "2026-05-26-postmarket.json", {"as_of": "2026-05-26", "session": "postmarket"})
            write_report(reports_dir / "2026-05-26-premarket.json", {"as_of": "2026-05-26", "session": "premarket"})

            path, payload = latest_report_payload(reports_dir, session="premarket")

            self.assertEqual(path.name, "2026-05-26-premarket.json")
            self.assertEqual(payload["session"], "premarket")

            path, payload = latest_report_payload(reports_dir, session="midday")

            self.assertEqual(path.name, "2026-05-26-midday.json")
            self.assertEqual(payload["session"], "midday")

    def test_format_briefing_message_uses_public_safe_fields(self):
        payload = {
            "as_of": "2026-05-26",
            "session": "premarket",
            "portfolio": {
                "cash_weight": 0.2,
                "equity_weight": 0.8,
                "quantity": 1443.12,
                "broker": "ibkr",
                "market_value": 999999,
            },
            "portfolio_benchmark": {
                "return_analytics": {
                    "primary": {
                        "label": "3M",
                        "total_portfolio_return": 26.0,
                        "invested_equity_return": 32.6,
                    }
                },
                "action_queue": [
                    {
                        "symbol": "AVGO",
                        "trade_action": "add",
                        "current_weight": 0.019,
                        "target_weight": 0.029,
                        "recommended_delta_weight": 0.01,
                        "risk_adjusted_expected_return": 27.5,
                        "confidence": 87,
                        "funding_source": "funded_by_named_trims",
                        "funding_counterpart_symbols": ["GOOG", "MU"],
                        "company_reason": "Company evidence supports a starter add. Do not include private notes.",
                        "catalyst_clock": "Active catalyst tape: hyperscaler capex.",
                    },
                    {
                        "symbol": "GOOG",
                        "trade_action": "trim",
                        "current_weight": 0.193,
                        "target_weight": 0.163,
                        "recommended_delta_weight": -0.03,
                        "risk_adjusted_expected_return": 1.0,
                        "confidence": 82,
                        "company_reason": "Valuation support is weak.",
                        "active_constraints": ["hard_cap", "low_expected_return"],
                    },
                ],
            },
            "site": {"stale_status": {"status": "fresh"}},
        }

        message = format_briefing_message(payload, site_url="https://alloiq.com")

        self.assertIn("AlloIQ Pre-market Brief - 2026-05-26", message)
        self.assertIn("Portfolio: 80.0% equity / 20.0% cash", message)
        self.assertIn("AVGO: Add 1.0%; 1.9% -> 2.9%; ER +27.5%; conf 87", message)
        self.assertIn("funded by trims: GOOG, MU", message)
        self.assertIn("GOOG: Trim 3.0%", message)
        self.assertIn("Open: https://alloiq.com/dashboard", message)
        self.assertNotIn("quantity", message)
        self.assertNotIn("market_value", message)
        self.assertNotIn("1443.12", message)
        self.assertNotIn("ibkr", message.lower())

    def test_format_midday_briefing_label(self):
        payload = {
            "as_of": "2026-05-26",
            "session": "midday",
            "portfolio": {"cash_weight": 0.2, "equity_weight": 0.8},
            "portfolio_benchmark": {"action_queue": []},
        }

        message = format_briefing_message(payload, site_url="https://alloiq.com")

        self.assertIn("AlloIQ Midday Brief - 2026-05-26", message)

    def test_send_latest_briefing_dry_run_does_not_require_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports_dir = Path(tmp) / "reports"
            write_report(
                reports_dir / "2026-05-26-weekly.json",
                {
                    "as_of": "2026-05-26",
                    "session": "weekly",
                    "portfolio": {"cash_weight": 0.1, "equity_weight": 0.9},
                    "portfolio_benchmark": {"action_queue": []},
                },
            )
            config = AppConfig(path=Path("config/invest.toml"), data={"reports": {"directory": str(reports_dir)}})

            result = send_latest_briefing(config, session="weekly", dry_run=True)

            self.assertEqual(result["status"], "dry_run")
            self.assertIn("AlloIQ Weekend Brief - 2026-05-26", result["message"])

    def test_send_latest_briefing_skips_when_credentials_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports_dir = Path(tmp) / "reports"
            write_report(reports_dir / "2026-05-26-premarket.json", {"as_of": "2026-05-26", "session": "premarket"})
            config = AppConfig(path=Path("config/invest.toml"), data={"reports": {"directory": str(reports_dir)}})

            with patch.dict("os.environ", {}, clear=True):
                result = send_latest_briefing(config, session="premarket")

            self.assertEqual(result["status"], "skipped")
            self.assertEqual(result["reason"], "missing telegram bot token or chat id")

    def test_send_telegram_message_posts_plain_text(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"ok": true, "result": {"message_id": 42}}'

        captured = {}

        def fake_urlopen(req, timeout):
            captured["url"] = req.full_url
            captured["body"] = req.data.decode("utf-8")
            captured["timeout"] = timeout
            return FakeResponse()

        with patch("invest.notifications.request.urlopen", side_effect=fake_urlopen):
            result = send_telegram_message("token", "chat-1", "hello", timeout_seconds=3)

        self.assertEqual(result["status"], "sent")
        self.assertEqual(result["telegram_message_id"], 42)
        self.assertTrue(captured["url"].endswith("/bottoken/sendMessage"))
        self.assertIn("chat_id=chat-1", captured["body"])
        self.assertIn("text=hello", captured["body"])
        self.assertEqual(captured["timeout"], 3)


def write_report(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
