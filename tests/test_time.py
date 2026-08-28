from datetime import datetime, timedelta, timezone

import pytest

from bot.utils.time import UNIT_MAP, aware, compute_duration, format_duration, parse_duration


class TestParseDuration:
    def test_bare_number_is_seconds(self):
        assert parse_duration("90") == 90
        assert parse_duration("0") == 0

    def test_zero_is_not_none(self):
        # The dc command distinguishes "no duration" from "zero" with a falsy
        # check, so this must be 0 rather than None.
        assert parse_duration("0") == 0

    @pytest.mark.parametrize("unit,multiplier", sorted(UNIT_MAP.items()))
    def test_every_supported_unit(self, unit, multiplier):
        assert parse_duration(f"5{unit}") == 5 * multiplier

    def test_short_units(self):
        assert parse_duration("30s") == 30
        assert parse_duration("5m") == 300
        assert parse_duration("1h") == 3600

    def test_long_units(self):
        assert parse_duration("30seconds") == 30
        assert parse_duration("5minutes") == 300
        assert parse_duration("2hours") == 7200

    def test_case_and_surrounding_whitespace(self):
        assert parse_duration("  5M  ") == 300
        assert parse_duration("1H") == 3600

    def test_internal_whitespace(self):
        assert parse_duration("5 m") == 300
        assert parse_duration("30 sec") == 30

    @pytest.mark.parametrize("value", ["", "abc", "-5m", "5x", "m5", "5m30s", "1.5h", "m"])
    def test_rejects_junk(self, value):
        assert parse_duration(value) is None


class TestFormatDuration:
    def test_seconds_only(self):
        assert format_duration(0) == "0s"
        assert format_duration(1) == "1s"
        assert format_duration(59) == "59s"

    def test_exact_minute(self):
        assert format_duration(60) == "1m"

    def test_minutes_and_seconds(self):
        assert format_duration(90) == "1m 30s"

    def test_exact_hour(self):
        assert format_duration(3600) == "1h"

    def test_hours_minutes_seconds(self):
        assert format_duration(3661) == "1h 1m 1s"

    def test_exact_day(self):
        assert format_duration(86400) == "1d"

    def test_days_suppress_seconds(self):
        # Once days are in play the seconds component is deliberately dropped.
        assert format_duration(86400 + 3600 + 60 + 30) == "1d 1h 1m"

    def test_days_without_smaller_parts(self):
        assert format_duration(86400 * 3) == "3d"


class TestAware:
    def test_naive_becomes_utc(self):
        naive = datetime(2026, 1, 1, 12, 0, 0)
        assert aware(naive).tzinfo == timezone.utc

    def test_already_aware_is_unchanged(self):
        original = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        assert aware(original) is original


class TestComputeDuration:
    def test_naive_joined_at(self):
        # MongoDB hands back naive datetimes; they must be treated as UTC.
        joined = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=120)
        assert compute_duration(joined) == pytest.approx(120, abs=2)

    def test_aware_joined_at(self):
        joined = datetime.now(timezone.utc) - timedelta(seconds=45)
        assert compute_duration(joined) == pytest.approx(45, abs=2)

    def test_explicit_end_time(self):
        joined = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 1, 1, 13, 30, 0, tzinfo=timezone.utc)
        assert compute_duration(joined, end) == 5400

    def test_naive_end_time(self):
        joined = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 1, 1, 12, 0, 30)
        assert compute_duration(joined, end) == 30

    def test_never_negative(self):
        # Reconciliation settles sessions against the last heartbeat, which can
        # predate a session that started after it.
        joined = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        cutoff = datetime(2026, 1, 1, 11, 0, 0, tzinfo=timezone.utc)
        assert compute_duration(joined, cutoff) == 0

    def test_offline_gap_is_not_credited(self):
        # The bug this guards: a session left open across a three day outage
        # must be settled at the heartbeat, not at now.
        joined = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        heartbeat = datetime(2026, 1, 1, 12, 5, 0, tzinfo=timezone.utc)
        assert compute_duration(joined, heartbeat) == 300
