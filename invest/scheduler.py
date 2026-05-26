from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


EASTERN = ZoneInfo("America/New_York")
DAILY_RUN_WINDOWS = {
    "premarket": (8, 0),
    "midday": (12, 0),
    "postmarket": (16, 30),
}
PIPELINE_KINDS = {"premarket", "midday", "postmarket", "weekly"}


@dataclass(frozen=True)
class ScheduleDecision:
    kind: str
    should_run: bool
    reason: str
    scheduled_at_utc: str
    scheduled_at_et: str
    trading_day: bool | None
    forced: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "should_run": self.should_run,
            "reason": self.reason,
            "scheduled_at_utc": self.scheduled_at_utc,
            "scheduled_at_et": self.scheduled_at_et,
            "trading_day": self.trading_day,
            "forced": self.forced,
        }


def should_run_pipeline(kind: str, scheduled_at: datetime | None = None, force: bool = False) -> ScheduleDecision:
    if kind not in PIPELINE_KINDS:
        raise ValueError(f"Unknown pipeline kind: {kind}")
    now_utc = normalize_utc(scheduled_at)
    now_et = now_utc.astimezone(EASTERN)
    if force:
        return decision(kind, True, "forced", now_utc, now_et, trading_day_for(kind, now_et), forced=True)
    if kind == "weekly":
        should_run = now_et.weekday() == 6
        reason = "weekly Sunday research window" if should_run else "weekly reports only run on Sunday ET"
        return decision(kind, should_run, reason, now_utc, now_et, None)

    trading_day = is_nyse_trading_day(now_et)
    if not trading_day:
        return decision(kind, False, "not an NYSE trading day", now_utc, now_et, False)
    expected_hour, expected_minute = DAILY_RUN_WINDOWS[kind]
    if (now_et.hour, now_et.minute) != (expected_hour, expected_minute):
        return decision(
            kind,
            False,
            f"outside {expected_hour:02d}:{expected_minute:02d} ET run window",
            now_utc,
            now_et,
            True,
        )
    return decision(kind, True, "NYSE trading day run window", now_utc, now_et, True)


def parse_scheduled_at(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    return normalize_utc(parsed)


def normalize_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def trading_day_for(kind: str, scheduled_at_et: datetime) -> bool | None:
    if kind in DAILY_RUN_WINDOWS:
        return is_nyse_trading_day(scheduled_at_et)
    return None


def is_nyse_trading_day(value: datetime) -> bool:
    try:
        import pandas_market_calendars as mcal
    except ModuleNotFoundError as exc:
        raise RuntimeError("Install pandas-market-calendars to evaluate NYSE trading days.") from exc
    market_date = value.astimezone(EASTERN).date()
    calendar = mcal.get_calendar("XNYS")
    schedule = calendar.schedule(start_date=market_date.isoformat(), end_date=market_date.isoformat())
    return not schedule.empty


def decision(
    kind: str,
    should_run: bool,
    reason: str,
    scheduled_at_utc: datetime,
    scheduled_at_et: datetime,
    trading_day: bool | None,
    forced: bool = False,
) -> ScheduleDecision:
    return ScheduleDecision(
        kind=kind,
        should_run=should_run,
        reason=reason,
        scheduled_at_utc=scheduled_at_utc.isoformat(),
        scheduled_at_et=scheduled_at_et.isoformat(),
        trading_day=trading_day,
        forced=forced,
    )
