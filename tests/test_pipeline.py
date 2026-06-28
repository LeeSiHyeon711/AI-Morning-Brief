"""통합 테스트 — AI-Morning-Brief (FEAT-08)

pytest -q tests/test_pipeline.py で QA-1~6 를 일괄 검증한다.

격리 원칙:
  pytest tmp_path fixture 로 OS 임시 디렉토리에 테스트 전용 경로를 격리한다.
  monkeypatch 로 MORNINGBRIEF_DB / MORNINGBRIEF_RAW_DIR / MORNINGBRIEF_REPORTS_DIR 를 주입해
  운영 data/morning_brief.db · reports/ 를 절대 건드리지 않는다.
  tmp_path 는 pytest 가 테스트별로 자동 생성·정리 — data/raw/ 등 빈 디렉토리 잔류 없음.
  --force-fallback 을 기본 포함해 실제 Claude API 호출 없이 hermetic 실행
  — 과금·네트워크 의존 없이 수 초 내 완료.
"""

import datetime
import os
import sqlite3
import subprocess
import sys

import pytest

# 05-개발 디렉토리 (main.py 위치)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# src 패키지 import 보장 (pytest 실행 위치와 무관하게 동작)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from src.pipeline import _should_advance_cursor  # noqa: E402

TODAY = datetime.date.today().isoformat()


def _env():
    """테스트용 환경변수 dict — clean fixture 가 monkeypatch 로 주입한 경로를 그대로 전달."""
    return os.environ.copy()


def _run(*flags):
    """main.py --test --no-discord --force-fallback [*flags] 를 격리된 env로 실행.

    --force-fallback 을 기본 포함해 실제 Claude API 를 호출하지 않는다.
    QA-5 처럼 --force-fallback 을 추가로 전달해도 store_true 중복이라 무해하다.
    """
    return subprocess.run(
        [sys.executable, "main.py", "--test", "--no-discord", "--force-fallback", *flags],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=_env(),
    )


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    """테스트 전용 경로를 pytest tmp_path(OS 임시 디렉토리)에 격리한다.

    운영 data/morning_brief.db · reports/ 는 절대 건드리지 않는다.
    tmp_path 는 pytest 가 테스트별로 자동 생성·정리 — 명시적 teardown 불필요.
    data/raw/ 등 프로젝트 경로에 빈 디렉토리를 남기지 않는다.
    """
    monkeypatch.setenv("MORNINGBRIEF_DB", str(tmp_path / "morning_brief.db"))
    monkeypatch.setenv("MORNINGBRIEF_RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("MORNINGBRIEF_REPORTS_DIR", str(tmp_path / "reports"))
    yield
    # tmp_path 정리는 pytest 가 자동 수행 — 별도 teardown 없음


def _test_db_path() -> str:
    """테스트 DB 절대경로 (clean fixture 가 monkeypatch 로 주입한 경로)."""
    return os.environ["MORNINGBRIEF_DB"]


def _test_report() -> str:
    """오늘 날짜의 테스트 리포트 절대경로 (reports/<YYYY>/<MM>/<DD>.md)."""
    yyyy, mm, dd = TODAY.split("-")
    return os.path.join(os.environ["MORNINGBRIEF_REPORTS_DIR"], yyyy, mm, f"{dd}.md")


# ──────────────────────────────────────────────
# QA-1: 샘플 기사가 테스트 DB에 저장되는가
# ──────────────────────────────────────────────
def test_qa1_db_insert():
    """--test 실행 후 테스트 DB의 articles 테이블에 1건 이상 저장돼야 한다."""
    result = _run()
    assert result.returncode == 0, f"파이프라인 비정상 종료:\n{result.stderr}"
    n = (
        sqlite3.connect(_test_db_path())
        .execute("SELECT COUNT(*) FROM articles")
        .fetchone()[0]
    )
    assert n > 0, f"articles COUNT={n}, 기사가 저장되지 않음"


# ──────────────────────────────────────────────
# QA-2: 중복 URL 이 두 번 저장되지 않는가
# ──────────────────────────────────────────────
def test_qa2_dedup():
    """동일 파이프라인을 2회 실행해도 url이 중복 저장되지 않아야 한다."""
    _run()
    _run()
    dups = (
        sqlite3.connect(_test_db_path())
        .execute("SELECT url, COUNT(*) c FROM articles GROUP BY url HAVING c > 1")
        .fetchall()
    )
    assert dups == [], f"중복 URL 발견: {dups}"


# ──────────────────────────────────────────────
# QA-3: Markdown 리포트가 테스트 경로에 생성되는가
# ──────────────────────────────────────────────
def test_qa3_report_created():
    """--test 실행 후 reports/<YYYY>/<MM>/<DD>.md 가 생성돼야 한다."""
    result = _run()
    assert result.returncode == 0, f"파이프라인 비정상 종료:\n{result.stderr}"
    assert os.path.exists(_test_report()), f"리포트 미생성: {_test_report()}"


# ──────────────────────────────────────────────
# QA-4: --no-discord 에서도 오류 없이 완료되는가
# ──────────────────────────────────────────────
def test_qa4_no_discord_exit0():
    """--no-discord 옵션으로 실행 시 종료코드 0 이어야 한다."""
    result = _run()
    assert result.returncode == 0, (
        f"종료코드 {result.returncode}:\n"
        f"stdout: {result.stdout[:500]}\n"
        f"stderr: {result.stderr[:500]}"
    )


# ──────────────────────────────────────────────
# QA-5: --force-fallback 시 fallback 문구가 리포트에 포함되는가
# ──────────────────────────────────────────────
def test_qa5_fallback():
    """--force-fallback 실행 후 리포트에 'AI 분석 실패 / 기본 리포트 생성' 이 포함돼야 한다."""
    result = _run("--force-fallback")
    assert result.returncode == 0, f"파이프라인 비정상 종료:\n{result.stderr}"
    md = open(_test_report(), encoding="utf-8").read()
    assert "AI 분석 실패 / 기본 리포트 생성" in md, (
        "fallback 문구 미포함. 리포트 앞부분:\n" + md[:300]
    )


# ──────────────────────────────────────────────
# QA-6: --from/--to 범위가 리포트에 명시되는가
# ──────────────────────────────────────────────
def test_qa6_range():
    """--from/--to 지정 시 시작 날짜 문자열이 리포트에 포함돼야 한다."""
    result = _run("--from", "2026-06-01T00:00:00", "--to", "2026-06-21T23:59:59")
    assert result.returncode == 0, f"파이프라인 비정상 종료:\n{result.stderr}"
    md = open(_test_report(), encoding="utf-8").read()
    assert "2026-06-01" in md, (
        "수집 범위 시작 날짜 '2026-06-01' 미포함. 리포트 앞부분:\n" + md[:300]
    )


# ──────────────────────────────────────────────
# 이슈 #13: 전 소스 수집 실패 시 catch-up 커서(last_success_at) 보존
# ──────────────────────────────────────────────
def test_issue13_all_sources_failed_keeps_cursor():
    """전 소스 실패(신규 0건, 성공 소스 0개) → 커서를 전진시키지 않는다."""
    # enabled 14개 전부 실패, 신규 0건
    assert _should_advance_cursor(new_count=0, enabled_count=14, failed_count=14) is False


def test_issue13_weekend_zero_new_advances_cursor():
    """정상 0건(소스는 응답, 신규 기사만 없음 — 주말 등) → 커서를 전진시킨다."""
    # 14개 소스 모두 성공(실패 0), 신규만 0건
    assert _should_advance_cursor(new_count=0, enabled_count=14, failed_count=0) is True


def test_issue13_partial_failure_advances_cursor():
    """일부 소스만 실패해도 성공 소스가 있으면(신규 0건이라도) 전진한다."""
    assert _should_advance_cursor(new_count=0, enabled_count=14, failed_count=13) is True


def test_issue13_normal_run_advances_cursor():
    """신규 기사가 있으면 실패 소스 수와 무관하게 전진한다."""
    assert _should_advance_cursor(new_count=5, enabled_count=14, failed_count=14) is True


def test_issue13_happy_path_cursor_set():
    """회귀: 정상 --test 실행은 기존대로 last_success_at 을 갱신해야 한다."""
    result = _run()
    assert result.returncode == 0, f"파이프라인 비정상 종료:\n{result.stderr}"
    row = (
        sqlite3.connect(_test_db_path())
        .execute("SELECT value FROM meta WHERE key = 'last_success_at'")
        .fetchone()
    )
    assert row is not None and row[0], "정상 실행인데 last_success_at 이 갱신되지 않음"
