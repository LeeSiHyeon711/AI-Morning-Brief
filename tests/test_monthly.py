"""월간보고 신호·필터 엔진 단위 테스트 (FEAT-11, #12) + 엔드투엔드·Discord 통합 테스트 (FEAT-13, #13).

FEAT-11 순수 함수 테스트는 위쪽, FEAT-13 엔드투엔드/다이제스트 테스트는 파일 하단에 이어 붙인다.
tmp_path + env override로 운영 DB·리포트와 완전히 격리되고, --force-fallback으로 Claude API를
호출하지 않는다(hermetic).
"""
import datetime
import os
import sqlite3
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.monthly import (  # noqa: E402
    aggregate_month,
    cap_source_bias,
    classify_noise,
    collect_monthly_ideas,
    first_stage_filter,
    month_bounds,
    month_key,
    parse_month_arg,
    partition_by_period,
    select_top_articles_monthly,
    source_stats,
    target_month,
    NOISE_MIN_COUNT,
    SOURCE_CAP_RATIO,
)


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    """운영 DB·리포트와 완전히 격리된 tmp_path 경로로 override (FEAT-13)."""
    monkeypatch.setenv("MORNINGBRIEF_DB", str(tmp_path / "mo.db"))
    monkeypatch.setenv("MORNINGBRIEF_RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("MORNINGBRIEF_REPORTS_DIR", str(tmp_path / "reports"))
    yield


def _article(url, title, source, published_at, collected_at=None, tags=None,
             importance=0, relevance=0, summary=""):
    return {
        "url": url, "title": title, "source": source,
        "published_at": published_at,
        "collected_at": collected_at or (published_at + "T08:00:00"),
        "summary": summary, "tags": tags or [], "importance": importance,
        "relevance": relevance, "analyzed": 1,
    }


# ── 기간 ─────────────────────────────────────────────────────────────────────

def test_target_month_normal_and_year_rollover():
    assert target_month(datetime.datetime(2026, 7, 1, 4, 50)) == (2026, 6)
    assert target_month(datetime.datetime(2026, 1, 3, 9, 0)) == (2025, 12)


def test_parse_month_arg_valid_and_invalid():
    assert parse_month_arg("2026-06") == (2026, 6)
    assert parse_month_arg(" 2026-6 ") == (2026, 6)
    with pytest.raises(ValueError):
        parse_month_arg("2026/06")
    with pytest.raises(ValueError):
        parse_month_arg("2026-13")


def test_month_bounds_30_and_31_day_months():
    first, last = month_bounds(2026, 6)
    assert (first.day, last.day) == (1, 30)
    first, last = month_bounds(2026, 7)
    assert (first.day, last.day) == (1, 31)


def test_month_key_format():
    assert month_key(2026, 6) == "2026-06"


# ── 기간 경계 분리 (#12-1) ─────────────────────────────────────────────────────

def test_partition_by_period_in_out_and_fallback():
    first, last = month_bounds(2026, 6)
    arts = [
        _article("u1", "in range", "A", "2026-06-15"),
        _article("u2", "out of range (old)", "A", "2026-04-01", collected_at="2026-06-01T08:00:00"),
        _article("u3", "null published fallback", "B", None, collected_at="2026-06-20T08:00:00"),
        _article("u4", "null published out", "B", None, collected_at="2026-07-01T08:00:00"),
        _article("u5", "boundary first day", "A", "2026-06-01"),
        _article("u6", "boundary last day", "A", "2026-06-30"),
    ]
    in_p, out_p = partition_by_period(arts, first, last)
    in_urls = {a["url"] for a in in_p}
    out_urls = {a["url"] for a in out_p}
    assert in_urls == {"u1", "u3", "u5", "u6"}
    assert out_urls == {"u2", "u4"}


def test_partition_by_period_empty_input():
    first, last = month_bounds(2026, 6)
    in_p, out_p = partition_by_period([], first, last)
    assert in_p == [] and out_p == []


# ── 1차 필터 + 노이즈 분류 (#12-3, #12-7) ──────────────────────────────────────

def test_first_stage_filter_keyword_and_signal_pass():
    arts = [
        _article("u1", "MCP agent release", "A", "2026-06-10", tags=["mcp", "agent"],
                  importance=0, relevance=0),
        _article("u2", "city council election results", "B", "2026-06-11",
                  importance=0, relevance=0),
        _article("u3", "random topic", "C", "2026-06-12", importance=5, relevance=0),
    ]
    passed, rejected = first_stage_filter(arts)
    passed_urls = {a["url"] for a in passed}
    rejected_urls = {a["url"] for a in rejected}
    assert passed_urls == {"u1", "u3"}
    assert rejected_urls == {"u2"}


def test_classify_noise_types_and_total():
    rejected = [
        _article("u1", "national election policy debate", "A", "2026-06-01"),
        _article("u2", "new data center investment billion dollar campus", "B", "2026-06-02"),
        _article("u3", "totally unrelated fluff piece", "C", "2026-06-03"),
    ]
    result = classify_noise(rejected)
    assert result["정치·사회"] == 1
    assert result["인프라투자·부동산"] == 1
    assert result["기타"] == 1
    assert result["_total"] == 3


def test_classify_noise_empty():
    result = classify_noise([])
    assert result == {"_total": 0}


# ── 소스 편중 (#12-5, #12-6) ───────────────────────────────────────────────────

def test_source_stats_share_and_noise_candidate():
    arts = [_article(f"u{i}", f"t{i}", "Noisy", "2026-06-01", importance=1)
            for i in range(NOISE_MIN_COUNT)]
    arts += [_article("v1", "high value", "Good", "2026-06-02", importance=9)]
    stats = source_stats(arts)
    noisy = next(s for s in stats if s["source"] == "Noisy")
    good = next(s for s in stats if s["source"] == "Good")
    assert noisy["count"] == NOISE_MIN_COUNT
    assert noisy["noise_candidate"] is True
    assert good["noise_candidate"] is False
    assert stats[0]["source"] == "Noisy"  # count desc 정렬


def test_cap_source_bias_applies_cap():
    arts = [_article(f"u{i}", f"t{i}", "Dominant", "2026-06-01", importance=i)
            for i in range(20)]
    capped = cap_source_bias(arts, SOURCE_CAP_RATIO)
    cap = max(3, round(20 * SOURCE_CAP_RATIO))
    assert len(capped) == cap
    # importance desc 상위만 남음
    assert all(a["importance"] >= 20 - cap for a in capped)


def test_cap_source_bias_small_pool_uses_minimum_cap():
    arts = [_article(f"u{i}", f"t{i}", "OnlySrc", "2026-06-01", importance=i)
            for i in range(2)]
    capped = cap_source_bias(arts, SOURCE_CAP_RATIO)
    assert len(capped) == 2  # cap = max(3, ...) 이지만 풀 자체가 2건뿐


# ── 집계 ──────────────────────────────────────────────────────────────────────

def test_aggregate_month_basic_totals_and_tags():
    first, last = month_bounds(2026, 6)
    arts = []
    for i in range(10):
        d = (first + datetime.timedelta(days=i)).isoformat()
        arts.append(_article(f"u{i}", f"MCP agent {i}", "SrcA", d,
                              tags=["mcp", "agent"], importance=9, relevance=8))
    sig = aggregate_month(arts, first, last)
    assert sig["total"] == 10
    assert sig["span_days"] == 30
    assert len(sig["daily_intensity"]) == 30
    # mcp: 10일 등장 >= PERSIST_DAYS(8) → persistent
    assert any(t["tag"] == "mcp" and t["persistent"] for t in sig["persistent_stems"])
    # relevance=8 >= WORKSHOP_RELEVANCE(7) → 전부 workshop_picks
    assert len(sig["workshop_picks"]) == 10
    assert "cross_source" in sig


def test_aggregate_month_tag_count_is_unique_source_day_pairs():
    """한 소스가 같은 날 여러 건 도배해도 태그 count 는 (source, day) 고유조합 1로 정규화 (#12-6)."""
    first, last = month_bounds(2026, 6)
    d = first.isoformat()
    arts = [_article(f"u{i}", f"t{i}", "SrcA", d, tags=["burstword"], importance=5)
            for i in range(5)]
    sig = aggregate_month(arts, first, last)
    burstword = next(t for t in sig["tags"] if t["tag"] == "burstword")
    assert burstword["count"] == 1  # 같은 (SrcA, day) 조합이라 1로 정규화
    assert burstword["day_span"] == 1


def test_aggregate_month_empty_articles_safe():
    """빈 월: total=0, 전 일자 sparse, peak_day는 count=0인 첫 날짜(daily_intensity 비어있지 않음)."""
    first, last = month_bounds(2026, 6)
    sig = aggregate_month([], first, last)
    assert sig["total"] == 0
    assert sig["peak_day"]["count"] == 0
    assert len(sig["sparse_days"]) == 30
    assert sig["tags"] == []
    assert sig["cross_source"] == {}


# ── 대표 압축 + 아이디어 (#12-4) ───────────────────────────────────────────────

def test_select_top_articles_monthly_dedupe_cap_and_cross_source():
    arts = [
        _article("u1", "Same Event", "A", "2026-06-01", importance=9, relevance=8),
        _article("u2", "same event", "B", "2026-06-01", importance=5, relevance=5),
        _article("u3", "Different Event", "A", "2026-06-02", importance=7, relevance=6),
    ]
    top = select_top_articles_monthly(arts, limit=40)
    titles = [_a["title"] for _a in top]
    # 'Same Event' / 'same event' 는 정규화 후 동일 키 → importance 높은 u1만 남음
    assert titles.count("Same Event") == 1
    assert "same event" not in titles
    picked = next(a for a in top if a["title"] == "Same Event")
    assert picked["cross_source_count"] == 2  # A, B 두 소스가 같은 사건 다룸


def test_select_top_articles_monthly_limit_respected():
    arts = [_article(f"u{i}", f"title {i}", "A", "2026-06-01", importance=i)
            for i in range(10)]
    top = select_top_articles_monthly(arts, limit=3)
    assert len(top) == 3


def test_select_top_articles_monthly_empty():
    assert select_top_articles_monthly([], limit=40) == []


# ── 아이디어 (weekly.collect_daily_ideas 재사용) ───────────────────────────────

def test_collect_monthly_ideas_reads_daily_reports(tmp_path):
    reports_dir = tmp_path / "reports"
    first, last = month_bounds(2026, 6)
    section = "## 상상공방에 적용할 수 있는 아이디어"
    for i in range(3):
        d = first + datetime.timedelta(days=i)
        p = reports_dir / f"{d.year:04d}" / f"{d.month:02d}"
        p.mkdir(parents=True, exist_ok=True)
        (p / f"{d.day:02d}.md").write_text(
            f"{section}\n- 반복 아이디어\n- 단발 아이디어 {i}\n", encoding="utf-8"
        )
    ideas = collect_monthly_ideas(str(reports_dir), first, last)
    rep = next(i for i in ideas if i["text"] == "반복 아이디어")
    assert rep["days"] == 3


def test_collect_monthly_ideas_missing_files_skip(tmp_path):
    first, last = month_bounds(2026, 6)
    ideas = collect_monthly_ideas(str(tmp_path / "nope"), first, last)
    assert ideas == []


# ── 엔드투엔드 통합 테스트 (FEAT-13, #13) ─────────────────────────────────────
# run_monthly 전 구간(트리거→경계→2단계필터→집계→합성→전문 저장→커서→Discord hook)을
# tmp_path + env override로 격리해 검증한다. --force-fallback·--no-discord로 Claude/네트워크
# 호출 없이 수 초 내 완료(hermetic).

def _seed_month(db, y=2026, m=6):
    from src.storage import init_db, insert_article, update_analysis
    c = init_db(db)
    first = datetime.date(y, m, 1)
    for i in range(28):
        d = (first + datetime.timedelta(days=i)).isoformat()
        for j in range(4):
            u = f"u{i}-{j}"
            src = "VentureBeat AI" if j == 3 else ("OpenAI" if j == 0 else "Google")
            insert_article(c, {"url": u, "title": f"MCP agent update {i}-{j}", "source": src,
                               "published_at": d, "collected_at": d + "T08:00:00", "raw_excerpt": "x"})
            update_analysis(c, u, "요약", ["mcp", "agent"], 9 if j == 0 else 2, 8 if j == 0 else 1, 1)
    # 범위 밖(4월) + 관련성 없는(정치) 기사 — 경계 분리·1차 필터 검증용
    insert_article(c, {"url": "old", "title": "old news", "source": "X",
                       "published_at": "2026-04-01", "collected_at": "2026-06-02T08:00:00", "raw_excerpt": "x"})
    update_analysis(c, "old", "s", ["ai"], 2, 1, 1)
    insert_article(c, {"url": "pol", "title": "city election policy", "source": "Y",
                       "published_at": "2026-06-05", "collected_at": "2026-06-05T08:00:00", "raw_excerpt": "x"})
    update_analysis(c, "pol", "s", [], 0, 0, 1)
    return c


def _run_monthly(*flags):
    return subprocess.run(
        [sys.executable, "main.py", "--monthly", "--no-discord", "--force-fallback", *flags],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )


def test_report_created_and_sections():
    _seed_month(os.environ["MORNINGBRIEF_DB"])
    r = _run_monthly("--month", "2026-06")
    assert r.returncode == 0, r.stderr
    p = os.path.join(os.environ["MORNINGBRIEF_REPORTS_DIR"], "2026", "monthly", "M06.md")
    assert os.path.exists(p)
    md = open(p, encoding="utf-8").read()
    assert "수집" in md and ("[확정]" in md or "[추정]" in md)   # #12-7 기반 + #12-2 라벨
    assert "점유율" in md or "편중" in md                        # #12-5
    assert "범위 외" in md                                       # #12-1


def test_cursor_idempotent():
    _seed_month(os.environ["MORNINGBRIEF_DB"])
    r1 = _run_monthly("--month", "2026-06")
    assert r1.returncode == 0, r1.stderr
    db = os.environ["MORNINGBRIEF_DB"]
    v = sqlite3.connect(db).execute("SELECT value FROM meta WHERE key='last_monthly_ym'").fetchone()
    assert v and v[0] == "2026-06"
    # 동일 달 재실행(포워스 없이) → skip, 커서 유지, 종료코드 0
    r2 = _run_monthly()
    assert r2.returncode == 0, r2.stderr


def test_monthly_run_no_discord_skips_send(monkeypatch):
    """--no-discord 지정 시 send_discord가 호출되지 않는다(네트워크 무접촉 확인, in-process)."""
    _seed_month(os.environ["MORNINGBRIEF_DB"])
    from src import monthly as monthly_mod
    calls = []
    monkeypatch.setattr(monthly_mod, "send_discord", lambda url, msg: calls.append((url, msg)))

    class Args:
        month = "2026-06"
        no_discord = True
        force_fallback = True

    rc = monthly_mod.run_monthly(Args())
    assert rc == 0
    assert calls == []


def test_run_monthly_calls_discord_hook_when_enabled(monkeypatch):
    """--no-discord 없이 실행하면 build_monthly_message 결과가 send_discord로 전달된다(in-process)."""
    _seed_month(os.environ["MORNINGBRIEF_DB"])
    from src import monthly as monthly_mod
    sent = {}

    def fake_send(url, msg):
        sent["url"] = url
        sent["msg"] = msg
        return True

    monkeypatch.setattr(monthly_mod, "send_discord", fake_send)

    class Args:
        month = "2026-06"
        no_discord = False
        force_fallback = True

    rc = monthly_mod.run_monthly(Args())
    assert rc == 0
    assert "msg" in sent
    assert "월간" in sent["msg"] and len(sent["msg"]) <= 2000


def test_digest_length():
    from src.notifier import build_monthly_message
    sig = {"peak_day": {"date": "2026-06-24", "count": 31},
           "persistent_stems": [{"tag": "mcp", "count": 34, "day_span": 12}],
           "sources": [{"source": "VentureBeat AI", "share": 0.21, "count": 60, "avg_importance": 2.3}],
           "noise_sources": [{"source": "VentureBeat AI", "count": 41, "avg_importance": 2.3}]}
    basis = {"collected": 287, "first_stage_passed": 188, "represented": 40,
             "out_of_period": 11, "noise": {"_total": 88}}
    syn = {"one_line_summary": "에이전트 인프라 전쟁 개막.",
           "flow_themes": ["[확정] MCP 표준화"], "notable_events": ["[확정] MCP RC"],
           "workshop_actions": ["MCP 서버 구축"], "next_month_watch": ["MCP 최종 스펙"],
           "narrative": "[확정] ... [추정] ..."}
    m = build_monthly_message("2026-06", datetime.date(2026, 6, 1), datetime.date(2026, 6, 30),
                              sig, basis, syn, "reports/2026/monthly/M06.md")
    assert len(m) <= 2000 and "분석 기반" in m and "신뢰도" in m


def test_digest_length_dense_synthesis_still_fits():
    """블록이 모두 최대치로 채워져도(경계값) 2000자 안전망이 지켜진다."""
    from src.notifier import build_monthly_message
    sig = {"peak_day": {"date": "2026-06-24", "count": 31},
           "persistent_stems": [{"tag": f"tag{i}", "count": 34, "day_span": 12} for i in range(6)],
           "sources": [{"source": "VentureBeat AI", "share": 0.21, "count": 60, "avg_importance": 2.3}],
           "noise_sources": [{"source": "VentureBeat AI", "count": 41, "avg_importance": 2.3}]}
    basis = {"collected": 287, "first_stage_passed": 188, "represented": 40,
             "out_of_period": 11, "noise": {"_total": 88}}
    syn = {"one_line_summary": "에이전트 인프라 전쟁이 전방위로 확산되며 표준화 경쟁이 격화된 한 달." * 2,
           "flow_themes": [f"[확정] 흐름 테마 {i} — 상세 설명이 붙은 긴 문장" for i in range(4)],
           "notable_events": [f"[추정] 주목 사건 {i} — 상세 설명이 붙은 긴 문장" for i in range(3)],
           "workshop_actions": [f"공방 액션 {i} — 상세 설명이 붙은 긴 문장" for i in range(3)],
           "next_month_watch": ["관전 포인트 A", "관전 포인트 B", "관전 포인트 C"],
           "narrative": "[확정] a [추정] b [주의] c"}
    m = build_monthly_message("2026-06", datetime.date(2026, 6, 1), datetime.date(2026, 6, 30),
                              sig, basis, syn, "reports/2026/monthly/M06.md")
    assert len(m) <= 2000
