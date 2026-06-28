"""주간보고 통합 테스트 (FEAT-10). tmp_path 격리 + env override + --force-fallback hermetic."""
import datetime
import os
import sqlite3
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.weekly import (  # noqa: E402
    aggregate_week,
    iso_week_bounds,
    parse_week_arg,
    select_top_articles,
    target_iso_week,
)
from src.storage import init_db, insert_article, update_analysis  # noqa: E402


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MORNINGBRIEF_DB", str(tmp_path / "wk.db"))
    monkeypatch.setenv("MORNINGBRIEF_RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("MORNINGBRIEF_REPORTS_DIR", str(tmp_path / "reports"))
    yield


def _seed(db, iso_year=2026, iso_week=26):
    """W26 7일에 걸쳐 기사 시드.

    - mcp/agent 태그: 7일 지속(day_span=7 >= PERSIST_DAYS=5 → persistent=True)
    - VentureBeat AI: 21건·avg_importance=2.0 < NOISE_AVG_IMPORTANCE=3.0 → noise_candidate=True
    - relevance=8(j==0) >= WORKSHOP_RELEVANCE=7 → workshop_picks = 7건(1건/일)
    - 총 21건(7일 × 3건/일)
    """
    c = init_db(db)
    mon = datetime.date.fromisocalendar(iso_year, iso_week, 1)
    for i in range(7):
        d = (mon + datetime.timedelta(days=i)).isoformat()
        for j in range(3):
            u = f"u{i}-{j}"
            insert_article(
                c,
                {
                    "url": u,
                    "title": f"MCP agent {i}-{j}",
                    "source": "VentureBeat AI",
                    "published_at": d,
                    "collected_at": d + "T08:00:00",
                    "raw_excerpt": "x",
                },
            )
            # importance=2(저중요) → VentureBeat AI avg_importance=2.0 < 3.0 = noise_candidate
            # relevance=8(j==0) → workshop_picks 자격 / relevance=1(j!=0)
            update_analysis(c, u, "요약", ["mcp", "agent"], 2, 8 if j == 0 else 1, 1)
    return c


def _run_weekly(*flags):
    return subprocess.run(
        [sys.executable, "main.py", "--weekly", "--no-discord", "--force-fallback", *flags],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )


# ── 순수 함수 ──────────────────────────────────────────────────────────────────

def test_iso_week_on_time_and_late():
    """일요일 정시(04:40)와 +1/+2일 지연 모두 같은 주(W26)를 가리킨다."""
    sun = datetime.datetime(2026, 6, 28, 4, 40)
    assert target_iso_week(sun) == (2026, 26)
    assert target_iso_week(sun + datetime.timedelta(days=1)) == (2026, 26)   # Mon
    assert target_iso_week(sun + datetime.timedelta(days=2)) == (2026, 26)   # Tue


def test_parse_week_arg():
    assert parse_week_arg("2026-W26") == (2026, 26)
    with pytest.raises(ValueError):
        parse_week_arg("2026/26")


def test_iso_week_bounds():
    mon, sun = iso_week_bounds(2026, 26)
    assert mon.isoweekday() == 1 and sun.isoweekday() == 7 and (sun - mon).days == 6


def test_aggregate_signals():
    c = _seed(os.environ["MORNINGBRIEF_DB"])
    from src.storage import get_articles_by_range
    mon, sun = iso_week_bounds(2026, 26)
    arts = get_articles_by_range(c, f"{mon}T00:00:00", f"{sun}T23:59:59")
    sig = aggregate_week(arts, mon, sun)
    assert sig["total"] == 21
    # mcp: 7일 지속 >= PERSIST_DAYS(5) → persistent=True
    assert any(t["tag"] == "mcp" and t["persistent"] for t in sig["persistent_stems"])
    # VentureBeat AI: 21건·avg=2.0 < NOISE_AVG_IMPORTANCE(3.0) → noise_candidate=True
    assert any(
        s["source"] == "VentureBeat AI" and s["noise_candidate"]
        for s in sig["noise_sources"]
    )
    # relevance=8 >= WORKSHOP_RELEVANCE(7): j==0 × 7일 = 7건
    assert len(sig["workshop_picks"]) == 7


def test_empty_week_safe():
    init_db(os.environ["MORNINGBRIEF_DB"])
    sig = aggregate_week([], *iso_week_bounds(2026, 26))
    assert sig["total"] == 0 and len(sig["sparse_days"]) == 7


# ── 통합(엔드투엔드, hermetic) ─────────────────────────────────────────────────

def test_report_created_and_path():
    _seed(os.environ["MORNINGBRIEF_DB"])
    r = _run_weekly("--week", "2026-W26")
    assert r.returncode == 0, r.stderr
    p = os.path.join(os.environ["MORNINGBRIEF_REPORTS_DIR"], "2026", "weekly", "W26.md")
    assert os.path.exists(p)


def test_cursor_idempotent():
    _seed(os.environ["MORNINGBRIEF_DB"])
    _run_weekly("--week", "2026-W26")             # 생성 + 커서=2026-W26
    db = os.environ["MORNINGBRIEF_DB"]
    v = sqlite3.connect(db).execute(
        "SELECT value FROM meta WHERE key='last_weekly_iso_week'"
    ).fetchone()
    assert v and v[0] == "2026-W26"
    # --week 없이 정시 산정 주차가 커서와 같으면 skip (다르면 신규 생성). 커서 자체는 유지.
