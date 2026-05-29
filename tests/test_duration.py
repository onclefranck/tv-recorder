from datetime import datetime, timedelta

from tv_recorder.duration import parse_duration, parse_start, seconds_until


def test_parse_duration_units():
    assert parse_duration("90s") == 90
    assert parse_duration("30m") == 1800
    assert parse_duration("2h") == 7200
    assert parse_duration("2h30m") == 9000


def test_parse_duration_clock_values():
    assert parse_duration("10:05") == 605
    assert parse_duration("01:30:00") == 5400


def test_parse_start_now():
    now = datetime(2026, 5, 24, 20, 0, 0)
    assert parse_start("now", now=now) == now


def test_seconds_until_never_negative():
    now = datetime(2026, 5, 24, 20, 0, 0)
    assert seconds_until(now - timedelta(minutes=1), now=now) == 0
    assert seconds_until(now + timedelta(seconds=30), now=now) == 30
