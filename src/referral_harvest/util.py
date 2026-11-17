"""Small shared helpers."""

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Current UTC time as a tz-naive datetime.

    Convention: every timestamp column in this project stores naive UTC.
    All comparisons use this helper so SQLite (which drops tzinfo) and
    Postgres behave identically.
    """
    return datetime.now(UTC).replace(tzinfo=None)


def parse_hhmm(value: str) -> tuple[int, int]:
    """Parse 'HH:MM' into (hour, minute); raises ValueError on bad input."""
    hour_s, _, minute_s = value.partition(":")
    hour, minute = int(hour_s), int(minute_s)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"bad HH:MM value: {value!r}")
    return hour, minute
