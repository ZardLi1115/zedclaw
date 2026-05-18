from datetime import datetime
from zoneinfo import ZoneInfo

from oss_pr_agent import daily_review, runtime


def _cfg(**overrides):
    base = {
        "sleep_enabled": True,
        "timezone": "Asia/Shanghai",
        "sleep_start": "21:00",
        "sleep_end": "09:00",
    }
    base.update(overrides)
    return {"oss_pr_agent": base}


def _ts(value: str) -> float:
    return datetime.fromisoformat(value).timestamp()


def test_sleep_window_crosses_midnight_before_end():
    cfg = _cfg()
    ts = _ts("2026-05-18T08:30:00+08:00")

    sleep_end = runtime._sleep_end_timestamp(cfg, ts)

    assert datetime.fromtimestamp(sleep_end, ZoneInfo("Asia/Shanghai")).strftime("%H:%M") == "09:00"


def test_sleep_window_crosses_midnight_after_start():
    cfg = _cfg()
    ts = _ts("2026-05-18T22:00:00+08:00")

    sleep_end = runtime._sleep_end_timestamp(cfg, ts)

    assert datetime.fromtimestamp(sleep_end, ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M") == "2026-05-19 09:00"


def test_sleep_window_allows_workday_time():
    assert runtime._sleep_end_timestamp(_cfg(), _ts("2026-05-18T12:00:00+08:00")) is None


def test_sleep_window_can_be_disabled():
    cfg = _cfg(sleep_enabled=False)

    assert runtime._sleep_end_timestamp(cfg, _ts("2026-05-18T22:00:00+08:00")) is None


def test_apply_sleep_window_moves_wake_to_sleep_end():
    cfg = _cfg()
    target = _ts("2026-05-18T22:30:00+08:00")

    adjusted = runtime._apply_sleep_window(cfg, target)

    assert datetime.fromtimestamp(adjusted, ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M") == "2026-05-19 09:00"


def test_daily_review_due_one_minute_before_sleep():
    cfg = _cfg(sleep_start="22:00")

    assert daily_review.due(cfg, _ts("2026-05-18T21:59:00+08:00"))
    assert not daily_review.due(cfg, _ts("2026-05-18T21:58:59+08:00"))
