"""Duration parsing, formatting, and datetime helpers.

Consolidated here because the same logic was previously duplicated across
the timer and tracker cogs and the database layer.
"""

import re
from datetime import datetime, timezone
from typing import Optional

# Maps every accepted unit spelling to its multiplier in seconds.
UNIT_MAP = {
    # Seconds
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    # Minutes
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60, "minut": 60,
    # Hours
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600
}


def aware(dt: datetime) -> datetime:
    """Coerce a naive datetime to UTC-aware.

    MongoDB hands back naive datetimes, so anything read from the database
    has to pass through here before being compared against datetime.now(utc).
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def compute_duration(joined_at: datetime, end_time: Optional[datetime] = None) -> int:
    """Seconds elapsed between joined_at and end_time (default: now).

    Never returns a negative value - reconciliation settles sessions against
    the last heartbeat, which can predate a session that started after it.
    """
    end = aware(end_time) if end_time is not None else datetime.now(timezone.utc)
    return max(0, int((end - aware(joined_at)).total_seconds()))


def parse_duration(duration_str: str) -> Optional[int]:
    """Parse duration string to seconds.

    Supported formats:
    - Pure number: 90 (seconds)
    - Short: 30s, 5m, 1h
    - Full: 30sec, 30secs, 30second, 30seconds
    - Full: 5min, 5mins, 5minute, 5minutes
    - Full: 1hr, 1hrs, 1hour, 1hours
    """
    duration_str = duration_str.strip().lower()

    # Pure number = seconds
    if duration_str.isdigit():
        return int(duration_str)

    # Match number + unit
    match = re.match(r"^(\d+)\s*([a-z]+)$", duration_str)
    if not match:
        return None

    value, unit = int(match.group(1)), match.group(2)

    if unit not in UNIT_MAP:
        return None

    return value * UNIT_MAP[unit]


def format_duration(seconds: int) -> str:
    """Format seconds into human readable string."""
    if seconds < 60:
        return f"{seconds}s"

    mins, secs = divmod(seconds, 60)
    hours, mins = divmod(mins, 60)
    days, hours = divmod(hours, 24)

    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if mins:
        parts.append(f"{mins}m")
    if secs and not days:  # Skip seconds if showing days
        parts.append(f"{secs}s")

    return " ".join(parts) if parts else "0s"
