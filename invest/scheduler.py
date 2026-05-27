from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


EASTERN = ZoneInfo("America/New_York")
DAILY_RUN_WINDOWS = {
    "premarket": [(8, 0)],
    "market_open": [(9, 30)],
    "intraday": [(10, 0), (11, 0), (12, 0), (14, 0), (15, 0)],
    "midday": [(13, 0)],
    "market_close": [(16, 0)],
    "postmarket": [(16, 30)],
}
PIPELINE_KINDS = {"premarket", "market_open", "intraday", "midday", "market_close", "postmarket", "weekly"}
RUN_WINDOW_GRACE_MINUTES = 20


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
    expected_windows = DAILY_RUN_WINDOWS[kind]
    in_window, matched_window = within_run_window(now_et, expected_windows)
    if not in_window:
        return decision(
            kind,
            False,
            f"outside {format_run_windows(expected_windows)} ET run window with {RUN_WINDOW_GRACE_MINUTES}m grace",
            now_utc,
            now_et,
            True,
        )
    return decision(
        kind,
        True,
        f"NYSE trading day run window ({matched_window[0]:02d}:{matched_window[1]:02d} ET + {RUN_WINDOW_GRACE_MINUTES}m grace)",
        now_utc,
        now_et,
        True,
    )


def parse_scheduled_at(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    return normalize_utc(parsed)


def infer_scheduled_at_from_cron(expression: str, now_utc: datetime | None = None) -> datetime:
    """Return the latest UTC cron slot at or before now.

    GitHub Actions can start scheduled workflows substantially late. The
    scheduler should evaluate the intended cron slot, not the delayed runner
    start time.
    """
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError(f"Unsupported cron expression: {expression}")
    minutes = parse_cron_field(fields[0], 0, 59)
    hours = parse_cron_field(fields[1], 0, 23)
    days_of_week = parse_cron_field(fields[4], 0, 7)
    days_of_week = {0 if value == 7 else value for value in days_of_week}
    current = normalize_utc(now_utc).replace(second=0, microsecond=0)
    for minute_offset in range(8 * 24 * 60 + 1):
        candidate = current - timedelta(minutes=minute_offset)
        if (
            candidate.minute in minutes
            and candidate.hour in hours
            and cron_weekday(candidate) in days_of_week
        ):
            return candidate
    raise ValueError(f"No recent matching cron slot found for: {expression}")


def parse_cron_field(field: str, minimum: int, maximum: int) -> set[int]:
    values: set[int] = set()
    for part in field.split(","):
        token = part.strip()
        if not token:
            continue
        if token == "*":
            values.update(range(minimum, maximum + 1))
            continue
        if "-" in token:
            start_text, end_text = token.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            if start > end:
                raise ValueError(f"Unsupported wrapped cron range: {field}")
            values.update(range(start, end + 1))
            continue
        values.add(int(token))
    if not values or min(values) < minimum or max(values) > maximum:
        raise ValueError(f"Cron field out of range: {field}")
    return values


def cron_weekday(value: datetime) -> int:
    return (normalize_utc(value).weekday() + 1) % 7


def kind_for_scheduled_at(value: datetime) -> str | None:
    scheduled_at_et = normalize_utc(value).astimezone(EASTERN)
    if scheduled_at_et.weekday() == 6:
        return "weekly"
    if scheduled_at_et.weekday() > 4:
        return None
    wall_clock = (scheduled_at_et.hour, scheduled_at_et.minute)
    for kind, windows in DAILY_RUN_WINDOWS.items():
        if wall_clock in windows:
            return kind
    return None


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


def format_run_windows(windows: list[tuple[int, int]]) -> str:
    return ", ".join(f"{hour:02d}:{minute:02d}" for hour, minute in windows)


def within_run_window(value: datetime, windows: list[tuple[int, int]]) -> tuple[bool, tuple[int, int]]:
    current_minutes = value.hour * 60 + value.minute
    for window in windows:
        window_minutes = window[0] * 60 + window[1]
        if 0 <= current_minutes - window_minutes <= RUN_WINDOW_GRACE_MINUTES:
            return True, window
    return False, (0, 0)


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
