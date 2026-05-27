from datetime import date
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from invest.config import AppConfig
from invest.llm_review import (
    apply_llm_review_to_approval_tickets,
    attach_llm_review_to_data_health,
    build_evidence_packets,
    build_llm_review_snapshot,
    privacy_violations,
    validate_review_response,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class LLMReviewTests(unittest.TestCase):
    def test_disabled_review_skips_without_api_key(self):
        snapshot = build_llm_review_snapshot(
            AppConfig(path=Path("config/invest.toml"), data={"llm": {"enabled": False}}),
            date(2026, 5, 27),
            "postmarket",
            {"rows": []},
            {"items": []},
            {"sources": []},
            [],
            [],
        )

        self.assertEqual(snapshot["status"], "disabled")
        self.assertEqual(snapshot["mode"], "disabled")
        self.assertFalse(snapshot["affected_approval_gate"])

    def test_enabled_review_skips_when_openai_key_is_missing(self):
        config = AppConfig(path=Path("config/invest.toml"), data={"llm": {"enabled": True, "mode": "shadow"}})

        with patch.dict("os.environ", {}, clear=True):
            snapshot = build_llm_review_snapshot(
                config,
                date(2026, 5, 27),
                "postmarket",
                {"rows": []},
                {"items": []},
                {"sources": []},
                [],
                [],
        )

        self.assertEqual(snapshot["status"], "skipped")
        self.assertIn("Missing configured API key", snapshot["detail"])

    def test_evidence_packets_are_privacy_safe(self):
        packet = sample_evidence_packets()[0]

        self.assertEqual(privacy_violations(packet), [])
        text = json.dumps(packet)
        self.assertNotIn("market_value", text)
        self.assertNotIn("shares", text.lower())
        self.assertNotIn("Broker positions", text)
        self.assertIn("Position snapshot", text)

    def test_structured_response_validation_accepts_valid_reviews(self):
        payload = {
            "output_text": json.dumps(
                {
                    "reviews": [
                        {
                            "symbol": "NVDA",
                            "thesis_quality": "mixed",
                            "evidence_gaps": ["Confirm margin durability."],
                            "contradictions": [],
                            "stale_assumptions": ["Refresh supply constraint evidence."],
                            "risk_questions": ["What would invalidate demand?"],
                            "decision_usefulness_score": 82,
                            "review_required": True,
                            "confidence": 0.7,
                        }
                    ]
                }
            )
        }

        reviews = validate_review_response(payload, {"NVDA"})

        self.assertEqual(reviews[0]["symbol"], "NVDA")
        self.assertEqual(reviews[0]["thesis_quality"], "mixed")
        self.assertTrue(reviews[0]["review_required"])

    def test_structured_response_validation_rejects_unexpected_symbols(self):
        payload = {
            "output_text": json.dumps(
                {
                    "reviews": [
                        {
                            "symbol": "TSLA",
                            "thesis_quality": "strong",
                            "evidence_gaps": [],
                            "contradictions": [],
                            "stale_assumptions": [],
                            "risk_questions": [],
                            "decision_usefulness_score": 80,
                            "review_required": False,
                            "confidence": 0.8,
                        }
                    ]
                }
            )
        }

        with self.assertRaises(ValueError):
            validate_review_response(payload, {"NVDA"})

    def test_openai_request_uses_responses_structured_outputs(self):
        requests = []
        config = AppConfig(
            path=Path("config/invest.toml"),
            data={"llm": {"enabled": True, "mode": "shadow", "max_symbols_per_run": 1}},
        )

        def fake_urlopen(req, timeout=20):
            requests.append((req, timeout, json.loads(req.data.decode("utf-8"))))
            return FakeResponse(
                {
                    "output_text": json.dumps(
                        {
                            "reviews": [
                                {
                                    "symbol": "NVDA",
                                    "thesis_quality": "strong",
                                    "evidence_gaps": [],
                                    "contradictions": [],
                                    "stale_assumptions": [],
                                    "risk_questions": [],
                                    "decision_usefulness_score": 91,
                                    "review_required": False,
                                    "confidence": 0.86,
                                }
                            ]
                        }
                    )
                }
            )

        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
            snapshot = build_llm_review_snapshot(
                config,
                date(2026, 5, 27),
                "postmarket",
                sample_feature_matrix(),
                sample_research_book(),
                sample_data_health(),
                sample_cards(),
                sample_tickets(),
                urlopen=fake_urlopen,
            )

        body = requests[0][2]
        self.assertEqual(snapshot["status"], "ok")
        self.assertEqual(body["model"], "gpt-4o-mini")
        self.assertEqual(body["text"]["format"]["type"], "json_schema")
        self.assertTrue(body["text"]["format"]["strict"])
        self.assertNotIn("test-key", json.dumps(body))

    def test_shadow_mode_does_not_mutate_tickets(self):
        tickets = sample_tickets()
        before = json.loads(json.dumps(tickets))
        snapshot = {
            "status": "ok",
            "mode": "shadow",
            "reviews": [sample_review(review_required=True)],
        }

        apply_llm_review_to_approval_tickets(tickets, snapshot)

        self.assertEqual(tickets, before)
        self.assertFalse(snapshot.get("affected_approval_gate", False))

    def test_review_gate_adds_review_check_without_changing_sizing(self):
        tickets = sample_tickets()
        before_weights = {
            key: tickets[0][key]
            for key in ("current_weight", "recommended_delta_weight", "target_weight", "model_target_weight")
        }
        snapshot = {
            "status": "ok",
            "mode": "review_gate",
            "reviews": [sample_review(review_required=True)],
        }

        apply_llm_review_to_approval_tickets(tickets, snapshot)

        after_weights = {
            key: tickets[0][key]
            for key in ("current_weight", "recommended_delta_weight", "target_weight", "model_target_weight")
        }
        self.assertEqual(after_weights, before_weights)
        self.assertTrue(snapshot["affected_approval_gate"])
        self.assertEqual(tickets[0]["approval_gate_status"], "review_required")
        self.assertTrue(any(check["check"] == "llm_evidence_reviewed" for check in tickets[0]["approval_checks"]))

    def test_data_health_counts_enabled_llm_review_failures(self):
        data_health = {"sources": [{"source": "prices", "status": "ok"}], "weak_source_count": 0}

        attach_llm_review_to_data_health(
            data_health,
            {"status": "schema_error", "detail": "bad schema", "mode": "shadow", "reviewed_symbol_count": 0},
        )

        self.assertEqual(data_health["sources"][-1]["source"], "llm_review")
        self.assertEqual(data_health["sources"][-1]["status"], "error")
        self.assertEqual(data_health["weak_source_count"], 1)


def sample_evidence_packets():
    return build_evidence_packets(
        date(2026, 5, 27),
        "postmarket",
        sample_feature_matrix(),
        sample_research_book(),
        sample_data_health(),
        sample_cards(),
        sample_tickets(),
    )


def sample_feature_matrix():
    return {
        "rows": [
            {
                "feature_id": "feature-nvda",
                "symbol": "NVDA",
                "bucket": "semis_networking_hbm",
                "current_weight": 0.1,
                "peer_avg_weight": 0.08,
                "manager_count": 4,
                "tier1_manager_count": 1,
                "signal_families": ["manager", "catalyst"],
                "event_types": ["contract_win"],
                "company_underwriting_score": 68,
                "sector_setup_score": 72,
                "timing_score": 60,
                "drawdown_risk": 35,
                "evidence_quality": 77,
                "valuation_support": 45,
                "price_return_5d": 3.2,
                "external_feed_status": "ok",
                "external_coverage_multiplier": 1.0,
                "external_provider_ok_ratio": 1.0,
                "external_signal_count": 3,
                "external_source_count": 2,
            }
        ]
    }


def sample_research_book():
    return {
        "items": [
            {
                "symbol": "NVDA",
                "bucket": "semis_networking_hbm",
                "rank": 1,
                "current_weight": 0.1,
                "peer_avg_weight": 0.08,
                "risk_adjusted_expected_return": 24.5,
                "probability_weighted_return": 18.2,
                "bull_return_12m": 44,
                "base_return_12m": 20,
                "bear_return_12m": -25,
                "verdict": "add",
                "thesis_summary": "AI compute demand supports durable accelerator growth.",
                "company_reason": "Evidence supports a starter if risk limits permit.",
                "sector_reason": "AI infrastructure demand remains constructive.",
                "tertiary_signal_summary": "Manager and catalyst evidence are aligned.",
            }
        ]
    }


def sample_data_health():
    return {
        "sources": [
            {"source": "broker_positions", "label": "Broker positions", "status": "ok"},
            {"source": "prices", "label": "Market prices", "status": "ok"},
        ]
    }


def sample_cards():
    return [
        {
            "symbol": "NVDA",
            "bucket": "semis_networking_hbm",
            "consensus_manager_count": 4,
            "signal_families": ["manager", "catalyst"],
            "top_event_types": ["contract_win"],
            "counterargument": "Valuation already discounts perfect execution.",
            "falsifier": "Demand weakens while inventories rise.",
        }
    ]


def sample_tickets():
    return [
        {
            "symbol": "NVDA",
            "trade_action": "add",
            "current_weight": 0.1,
            "recommended_delta_weight": 0.01,
            "target_weight": 0.11,
            "model_target_weight": 0.12,
            "approval_checks": [{"check": "approval_only_no_live_order", "status": "passed", "detail": "Approval only."}],
            "approval_gate_status": "ready_for_review",
            "approval_open_check_count": 0,
        }
    ]


def sample_review(review_required: bool):
    return {
        "symbol": "NVDA",
        "thesis_quality": "weak" if review_required else "strong",
        "evidence_gaps": ["Refresh customer evidence."],
        "contradictions": [],
        "stale_assumptions": [],
        "risk_questions": ["What would invalidate the thesis?"],
        "decision_usefulness_score": 55,
        "review_required": review_required,
        "confidence": 0.75,
    }


if __name__ == "__main__":
    unittest.main()
