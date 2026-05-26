from datetime import datetime, timezone
import unittest

from invest.scheduler import should_run_pipeline


class SchedulerTests(unittest.TestCase):
    def test_premarket_runs_at_eight_eastern_during_dst(self):
        decision = should_run_pipeline("premarket", datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc))

        self.assertTrue(decision.should_run)
        self.assertTrue(decision.trading_day)

    def test_duplicate_dst_cron_window_is_skipped(self):
        decision = should_run_pipeline("premarket", datetime(2026, 7, 6, 13, 0, tzinfo=timezone.utc))

        self.assertFalse(decision.should_run)
        self.assertIn("outside", decision.reason)

    def test_midday_runs_at_noon_eastern_during_dst(self):
        decision = should_run_pipeline("midday", datetime(2026, 7, 6, 16, 0, tzinfo=timezone.utc))

        self.assertTrue(decision.should_run)
        self.assertTrue(decision.trading_day)

    def test_midday_duplicate_standard_dst_window_is_skipped(self):
        decision = should_run_pipeline("midday", datetime(2026, 7, 6, 17, 0, tzinfo=timezone.utc))

        self.assertFalse(decision.should_run)
        self.assertIn("outside", decision.reason)

    def test_market_open_runs_at_open_during_dst(self):
        decision = should_run_pipeline("market_open", datetime(2026, 7, 6, 13, 30, tzinfo=timezone.utc))

        self.assertTrue(decision.should_run)
        self.assertTrue(decision.trading_day)

    def test_intraday_runs_on_hourly_windows(self):
        eleven = should_run_pipeline("intraday", datetime(2026, 7, 6, 15, 0, tzinfo=timezone.utc))
        noon = should_run_pipeline("intraday", datetime(2026, 7, 6, 16, 0, tzinfo=timezone.utc))
        three = should_run_pipeline("intraday", datetime(2026, 1, 5, 20, 0, tzinfo=timezone.utc))

        self.assertTrue(eleven.should_run)
        self.assertFalse(noon.should_run)
        self.assertTrue(three.should_run)

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


if __name__ == "__main__":
    unittest.main()
