from datetime import datetime, timedelta
import pytest
from app.formatting import format_size, format_age, format_local_time, format_local_datetime


@pytest.mark.parametrize("n,expected", [
    (0, "0 B"),
    (512, "512 B"),
    (1024, "1.0 KB"),
    (1536, "1.5 KB"),
    (1_048_576, "1.0 MB"),
    (19_519_304, "18.6 MB"),
    (1_073_741_824, "1.0 GB"),
    (None, "—"),
])
def test_format_size(n, expected):
    assert format_size(n) == expected


def test_format_age_buckets():
    now = datetime(2026, 4, 23, 12, 0, 0)
    assert format_age((now - timedelta(seconds=30)).isoformat(), now) == "just now"
    assert format_age((now - timedelta(minutes=5)).isoformat(), now) == "5m ago"
    assert format_age((now - timedelta(hours=3)).isoformat(), now) == "3h ago"
    assert format_age((now - timedelta(days=2)).isoformat(), now) == "2d ago"
    assert format_age((now - timedelta(days=10)).isoformat(), now) == "1w ago"
    assert format_age((now - timedelta(days=90)).isoformat(), now) == "3mo ago"
    assert format_age((now - timedelta(days=800)).isoformat(), now) == "2y ago"


def test_format_age_empty():
    assert format_age(None) == "—"
    assert format_age("") == "—"


def test_format_age_malformed():
    assert format_age("not-a-date") == "not-a-date"


def test_format_local_time_utc_to_central():
    # 2026-04-26T05:00:00Z is 00:00 CDT (UTC-5 during DST)
    assert format_local_time("2026-04-26T05:00:00Z") == "00:00:00"
    # 2026-04-26T18:30:45Z is 13:30 CDT
    assert format_local_time("2026-04-26T18:30:45Z") == "13:30:45"


def test_format_local_time_handles_empty_and_malformed():
    assert format_local_time(None) == "—"
    assert format_local_time("") == "—"
    assert format_local_time("not-a-date") == "—"


def test_format_local_datetime_utc_to_central():
    assert format_local_datetime("2026-04-26T18:30:45Z") == "2026-04-26 13:30:45"
