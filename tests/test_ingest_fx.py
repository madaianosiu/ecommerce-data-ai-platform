from datetime import date, timedelta

import pytest

from ingest.ingest_fx import WINDOW_DAYS, date_windows, flatten_rates


def test_windows_cover_range_without_gaps_or_overlap():
    start, end = date(2016, 9, 1), date(2018, 10, 31)
    wins = list(date_windows(start, end))
    assert wins[0][0] == start
    assert wins[-1][1] == end
    for (_, prev_end), (nxt_start, _) in zip(wins, wins[1:]):
        assert nxt_start - prev_end == timedelta(days=1)
    total_days = sum((e - s).days + 1 for s, e in wins)
    assert total_days == (end - start).days + 1


def test_windows_at_most_90_days():
    wins = list(date_windows(date(2016, 9, 1), date(2018, 10, 31)))
    assert WINDOW_DAYS == 90
    assert all((e - s).days + 1 <= 90 for s, e in wins)
    assert len(wins) == 9  # 791 zile -> 8 ferestre pline + una parțială


def test_exact_multiple_of_window_size():
    start = date(2017, 1, 1)
    wins = list(date_windows(start, start + timedelta(days=179)))
    assert len(wins) == 2
    assert wins[1] == (start + timedelta(days=90), start + timedelta(days=179))


def test_single_day_window():
    d = date(2017, 1, 1)
    assert list(date_windows(d, d)) == [(d, d)]


def test_start_after_end_yields_nothing():
    assert list(date_windows(date(2017, 1, 2), date(2017, 1, 1))) == []


def test_invalid_size_raises():
    with pytest.raises(ValueError):
        list(date_windows(date(2017, 1, 1), date(2017, 1, 5), size=0))


def test_flatten_rates():
    payload = {
        "amount": 1.0,
        "base": "BRL",
        "start_date": "2017-01-02",
        "end_date": "2017-01-03",
        "rates": {"2017-01-02": {"EUR": 0.29}, "2017-01-03": {"EUR": 0.30}},
    }
    assert flatten_rates(payload, "BRL") == [
        (date(2017, 1, 2), "BRL", "EUR", 0.29),
        (date(2017, 1, 3), "BRL", "EUR", 0.30),
    ]


def test_flatten_multiple_quotes_per_day():
    payload = {"rates": {"2017-01-02": {"EUR": 0.29, "USD": 0.31}}}
    assert sorted(flatten_rates(payload, "BRL")) == [
        (date(2017, 1, 2), "BRL", "EUR", 0.29),
        (date(2017, 1, 2), "BRL", "USD", 0.31),
    ]


def test_flatten_clips_to_requested_window():
    # Fereastră cerută de duminică 2017-08-27; API-ul întoarce și vineri 2017-08-25.
    payload = {
        "start_date": "2017-08-25",
        "rates": {"2017-08-25": {"EUR": 0.27}, "2017-08-28": {"EUR": 0.26}},
    }
    assert flatten_rates(payload, "BRL", date(2017, 8, 27), date(2017, 11, 24)) == [
        (date(2017, 8, 28), "BRL", "EUR", 0.26),
    ]


def test_flatten_empty_payload():
    assert flatten_rates({}, "BRL") == []
    assert flatten_rates({"rates": {}}, "BRL") == []
