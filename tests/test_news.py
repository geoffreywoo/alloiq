import unittest

from invest.news import classify_news_event, classify_source_tier, enrich_news_item


class NewsSignalTests(unittest.TestCase):
    def test_classifies_financing_risk_with_primary_source_weight(self):
        item = {
            "title": "CoreWeave announces convertible debt financing",
            "summary": "The company said proceeds will fund AI data center expansion.",
            "source": "Business Wire",
            "url": "https://www.businesswire.com/example",
            "query": "CoreWeave financing",
        }

        enriched = enrich_news_item(item)

        self.assertEqual(enriched["event_type"], "financing_risk")
        self.assertEqual(enriched["event_direction"], "negative")
        self.assertEqual(enriched["source_tier"], "primary")
        self.assertGreater(enriched["event_score"], enriched["event_weight"])

    def test_classifies_capex_signal(self):
        item = {
            "title": "Hyperscaler AI capex climbs on data center spending",
            "summary": "GPU cluster demand remains strong.",
            "source": "Reuters",
            "url": "https://reuters.com/example",
            "query": "AI capex",
        }

        self.assertEqual(classify_news_event(item)["event_type"], "capex_signal")
        self.assertEqual(classify_source_tier(item)["source_tier"], "market_news")


if __name__ == "__main__":
    unittest.main()
