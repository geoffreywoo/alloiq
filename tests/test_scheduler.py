from datetime import datetime, timezone
import unittest

from invest.scheduler import infer_scheduled_at_from_cron, kind_for_scheduled_at, should_run_pipeline


class SchedulerTests(unittest.TestCase):
    def test_premarket_runs_at_eight_eastern_during_dst(self):
        decision = should_run_pipeline("premarket", datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc))

        self.assertTrue(decision.should_run)
        self.assertTrue(decision.trading_day)

    def test_duplicate_dst_cron_window_is_skipped(self):
        decision = should_run_pipeline("premarket", datetime(2026, 7, 6, 13, 0, tzinfo=timezone.utc))

        self.assertFalse(decision.should_run)
        self.assertIn("outside", decision.reason)

    def test_midday_runs_at_one_eastern_during_dst(self):
        decision = should_run_pipeline("midday", datetime(2026, 7, 6, 17, 0, tzinfo=timezone.utc))

        self.assertTrue(decision.should_run)
        self.assertTrue(decision.trading_day)

    def test_midday_runs_at_one_eastern_during_standard_time(self):
        decision = should_run_pipeline("midday", datetime(2026, 1, 5, 18, 0, tzinfo=timezone.utc))

        self.assertTrue(decision.should_run)
        self.assertTrue(decision.trading_day)

    def test_midday_duplicate_standard_dst_window_is_skipped(self):
        decision = should_run_pipeline("midday", datetime(2026, 7, 6, 18, 0, tzinfo=timezone.utc))

        self.assertFalse(decision.should_run)
        self.assertIn("outside", decision.reason)

    def test_market_open_runs_at_open_during_dst(self):
        decision = should_run_pipeline("market_open", datetime(2026, 7, 6, 13, 30, tzinfo=timezone.utc))

        self.assertTrue(decision.should_run)
        self.assertTrue(decision.trading_day)

    def test_intraday_runs_on_hourly_windows(self):
        eleven = should_run_pipeline("intraday", datetime(2026, 7, 6, 15, 0, tzinfo=timezone.utc))
        noon = should_run_pipeline("intraday", datetime(2026, 7, 6, 16, 0, tzinfo=timezone.utc))
        one = should_run_pipeline("intraday", datetime(2026, 7, 6, 17, 0, tzinfo=timezone.utc))
        three = should_run_pipeline("intraday", datetime(2026, 1, 5, 20, 0, tzinfo=timezone.utc))

        self.assertTrue(eleven.should_run)
        self.assertTrue(noon.should_run)
        self.assertFalse(one.should_run)
        self.assertTrue(three.should_run)

    def test_intraday_allows_github_actions_start_delay(self):
        decision = should_run_pipeline("intraday", datetime(2026, 5, 26, 18, 3, tzinfo=timezone.utc))

        self.assertTrue(decision.should_run)
        self.assertIn("grace", decision.reason)

    def test_intraday_skips_after_grace_window(self):
        decision = should_run_pipeline("intraday", datetime(2026, 5, 26, 18, 25, tzinfo=timezone.utc))

        self.assertFalse(decision.should_run)
        self.assertIn("outside", decision.reason)

    def test_market_close_runs_at_close_during_standard_time(self):
        decision = should_run_pipeline("market_close", datetime(2026, 1, 5, 21, 0, tzinfo=timezone.utc))

        self.assertTrue(decision.should_run)
        self.assertTrue(decision.trading_day)

    def test_postmarket_runs_at_close_window_in_standard_time(self):
        decision = should_run_pipeline("postmarket", datetime(2026, 1, 5, 21, 30, tzinfo=timezone.utc))

        self.assertTrue(decision.should_run)
        self.assertTrue(decision.trading_day)

    def test_daily_jobs_skip_weekends_and_holidays(self):
        saturday = should_run_pipeline("premarket", datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc))
        memorial_day = should_run_pipeline("premarket", datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc))

        self.assertFalse(saturday.should_run)
        self.assertFalse(memorial_day.should_run)

    def test_force_bypasses_market_gate(self):
        decision = should_run_pipeline("premarket", datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc), force=True)

        self.assertTrue(decision.should_run)
        self.assertTrue(decision.forced)

    def test_weekly_runs_on_sunday(self):
        decision = should_run_pipeline("weekly", datetime(2026, 7, 5, 14, 0, tzinfo=timezone.utc))

        self.assertTrue(decision.should_run)
        self.assertIsNone(decision.trading_day)

    def test_infers_intended_cron_slot_for_delayed_github_start(self):
        scheduled_at = infer_scheduled_at_from_cron(
            "0 12 * * 1-5",
            datetime(2026, 7, 6, 12, 54, tzinfo=timezone.utc),
        )

        self.assertEqual(scheduled_at, datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(kind_for_scheduled_at(scheduled_at), "premarket")

    def test_duplicate_dst_cron_slot_maps_to_no_active_report_kind(self):
        scheduled_at = infer_scheduled_at_from_cron(
            "0 13 * * 1-5",
            datetime(2026, 7, 6, 14, 2, tzinfo=timezone.utc),
        )

        self.assertEqual(scheduled_at, datetime(2026, 7, 6, 13, 0, tzinfo=timezone.utc))
        self.assertIsNone(kind_for_scheduled_at(scheduled_at))

    def test_delayed_intraday_cron_uses_intended_hour(self):
        scheduled_at = infer_scheduled_at_from_cron(
            "0 18 * * 1-5",
            datetime(2026, 5, 26, 18, 43, tzinfo=timezone.utc),
        )

        self.assertEqual(scheduled_at, datetime(2026, 5, 26, 18, 0, tzinfo=timezone.utc))
        self.assertEqual(kind_for_scheduled_at(scheduled_at), "intraday")

    def test_postmarket_duplicate_cron_distinguishes_active_and_inactive_slots(self):
        active = infer_scheduled_at_from_cron(
            "30 20 * * 1-5",
            datetime(2026, 5, 26, 21, 7, tzinfo=timezone.utc),
        )
        inactive = infer_scheduled_at_from_cron(
            "30 21 * * 1-5",
            datetime(2026, 5, 26, 22, 5, tzinfo=timezone.utc),
        )

        self.assertEqual(active, datetime(2026, 5, 26, 20, 30, tzinfo=timezone.utc))
        self.assertEqual(kind_for_scheduled_at(active), "postmarket")
        self.assertEqual(inactive, datetime(2026, 5, 26, 21, 30, tzinfo=timezone.utc))
        self.assertIsNone(kind_for_scheduled_at(inactive))


if __name__ == "__main__":
    unittest.main()
