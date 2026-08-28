"""report 聚合层的单元测试:钉死跨度切分、合并、切换计数等边界逻辑。

运行:python -m pytest tests -q
夹具按真实心跳节奏(60s 一条)构造,与采集器的落库策略一致。
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from report import MAX_SAMPLE_SPAN, _clip_sessions, aggregate, focus_score, \
    reconstruct_sessions

D0 = (2026, 8, 27)
TS = lambda *a: datetime(*a).timestamp()  # noqa: E731

CAT = {"code.exe": "工作", "chrome.exe": "浏览"}


def hb(day, h0, m0, h1, m1, exe, focused=1, step=60):
    """按 step 秒一条构造 [t0, t1] 的心跳样本(含端点)。"""
    t0 = datetime(*day, h0, m0)
    t1 = datetime(*day, h1, m1)
    out = []
    t = t0
    while t <= t1:
        out.append((t.timestamp(), exe, f"{exe} 标题", focused))
        t += timedelta(seconds=step)
    return out


# ---------------------------------------------------------------- 会话重构

def test_merge_contiguous_heartbeats():
    rows = hb(D0, 10, 0, 10, 2, "code.exe")
    sessions = reconstruct_sessions(rows)
    assert len(sessions) == 1
    assert sessions[0][1] - sessions[0][0] == 180


def test_cap_long_gap_after_shutdown():
    # 最后一个样本后进入关机:有效期只延伸 HEARTBEAT 间隔,而不是吃掉整夜
    rows = hb(D0, 22, 0, 22, 0, "code.exe")
    sessions = reconstruct_sessions(rows)
    assert sessions[0][1] - sessions[0][0] == 60


def test_cap_samples_span():
    # 样本间隔超过 MAX_SAMPLE_SPAN(如休眠 1 小时)时,单样本按 180s 封顶
    rows = [(TS(*D0, 10, 0), "code.exe", "t", 1),
            (TS(*D0, 11, 0), "chrome.exe", "t", 1)]
    sessions = reconstruct_sessions(rows)
    assert sessions[0][1] - sessions[0][0] == MAX_SAMPLE_SPAN


def test_focused_transition_breaks_session():
    # 同一窗口从专注转为离开:不能合并成一段
    rows = hb(D0, 10, 0, 10, 1, "code.exe", 1) + hb(D0, 10, 2, 10, 3, "code.exe", 0)
    sessions = reconstruct_sessions(rows)
    assert len(sessions) == 2
    assert sessions[0][4] == 1 and sessions[1][4] == 0


# ---------------------------------------------------------------- 聚合切分

def test_hour_boundary_split():
    # 10:50 ~ 11:10 的连续会话应切成 10 点 600 秒 + 11 点 600 秒
    rows = hb(D0, 10, 50, 10, 59, "code.exe") + hb(D0, 11, 0, 11, 10, "code.exe")
    sessions = reconstruct_sessions(rows)
    assert len(sessions) == 1
    agg = aggregate(sessions, CAT)
    assert agg["heat"][("2026-08-27", 10)] == pytest.approx(600)
    # 会话尾部 = 最后一条心跳样本 + 60s 有效期,延伸到 11:11
    assert agg["heat"][("2026-08-27", 11)] == pytest.approx(660)


def test_day_boundary_split():
    # 23:50 ~ 次日 00:10:两天的日合计各得 600 秒,谁也不吃掉谁
    rows = hb((2026, 8, 27), 23, 50, 23, 59, "code.exe") + \
        hb((2026, 8, 28), 0, 0, 0, 10, "code.exe")
    sessions = reconstruct_sessions(rows)
    agg = aggregate(sessions, CAT)
    assert agg["per_day"]["2026-08-27"]["total"] == pytest.approx(600)
    assert agg["per_day"]["2026-08-28"]["total"] == pytest.approx(660)


def test_switch_count():
    rows = hb(D0, 10, 0, 10, 4, "code.exe") + hb(D0, 10, 5, 10, 9, "chrome.exe")
    sessions = reconstruct_sessions(rows)
    agg = aggregate(sessions, CAT)
    assert agg["per_day"]["2026-08-27"]["switch"] == 1


def test_switch_not_counted_within_split_segments():
    # 同一会话被小时边界切开,多段内部不应重复计切换
    rows = hb(D0, 10, 55, 10, 59, "code.exe") + hb(D0, 11, 0, 11, 5, "code.exe")
    sessions = reconstruct_sessions(rows)
    agg = aggregate(sessions, CAT)
    assert agg["per_day"]["2026-08-27"]["switch"] == 0


def test_away_resets_switch_chain():
    rows = hb(D0, 10, 0, 10, 4, "code.exe", 1) + hb(D0, 10, 5, 10, 7, "code.exe", 0) + \
        hb(D0, 10, 8, 10, 10, "code.exe", 1)
    sessions = reconstruct_sessions(rows)
    agg = aggregate(sessions, CAT)
    # 从离开中回来不算一次窗口切换
    assert agg["per_day"]["2026-08-27"]["switch"] == 0
    assert agg["per_day"]["2026-08-27"]["away"] == pytest.approx(180)


def test_longest_per_day():
    rows = hb(D0, 10, 0, 10, 10, "code.exe") + hb(D0, 11, 0, 11, 3, "chrome.exe")
    sessions = reconstruct_sessions(rows)
    agg = aggregate(sessions, CAT)
    # 10:00~10:10 的心跳构成 660s 会话;末样本因下一条间隔过大,再延长 120s 封顶 → 780
    assert agg["per_day"]["2026-08-27"]["longest"] == pytest.approx(780)


def test_categories_rollup():
    rows = hb(D0, 10, 0, 10, 5, "code.exe") + hb(D0, 10, 6, 10, 9, "chrome.exe")
    sessions = reconstruct_sessions(rows)
    agg = aggregate(sessions, CAT)
    assert agg["cats"]["工作"] == pytest.approx(360)
    assert agg["cats"]["浏览"] == pytest.approx(240)


# ---------------------------------------------------------------- 评分与裁剪

def test_focus_score_saturation():
    assert focus_score(10 * 3600, 90 * 60)[0] == 99
    assert focus_score(0, 0)[0] == 0
    score, time_part, depth_part = focus_score(3 * 3600, 45 * 60)
    assert score == time_part + depth_part
    assert time_part == 30 and depth_part == 40


def test_clip_sessions():
    start = TS(*D0, 9, 0)
    sessions = [[start - 600, start + 600, "code.exe", "t", 1]]
    clipped = list(_clip_sessions(sessions, start))
    assert clipped[0][0] == start and clipped[0][1] == start + 600


def test_clip_session_ending_before_range_dropped():
    start = TS(*D0, 9, 0)
    sessions = [[start - 3600, start - 1800, "code.exe", "t", 1]]
    assert list(_clip_sessions(sessions, start)) == []


def test_prev_period_delta_inputs():
    # 前一周期的聚合只取三个总量,确保与 cur 结构解耦
    rows = hb(D0, 10, 0, 10, 2, "code.exe")
    sessions = list(_clip_sessions(reconstruct_sessions(rows), datetime(*D0).timestamp()))
    agg = aggregate(sessions, CAT)
    prev_tot = {"total": sum(d["total"] for d in agg["per_day"].values()),
                "away": sum(d["away"] for d in agg["per_day"].values()),
                "switch": sum(d["switch"] for d in agg["per_day"].values())}
    assert prev_tot["total"] == pytest.approx(180)
    assert prev_tot["away"] == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
