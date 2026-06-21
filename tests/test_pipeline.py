"""통합 테스트 — AI-Morning-Brief (FEAT-08)

pytest -q tests/test_pipeline.py で QA-1~6 를 일괄 검증한다.

격리 원칙:
  모든 테스트는 MORNINGBRIEF_DB / MORNINGBRIEF_RAW_DIR / MORNINGBRIEF_REPORTS_DIR 환경변수를
  테스트 전용 경로로 주입해 운영 data/morning_brief.db · reports/ 를 절대 건드리지 않는다.
"""

import datetime
import os
import shutil
import sqlite3
import subprocess
import sys

import pytest

# 05-개발 디렉토리 (main.py 위치)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TODAY = datetime.date.today().isoformat()

# 운영 경로와 분리된 테스트 전용 경로
TEST_DB = "data/test_morning_brief.db"
TEST_RAW = "data/raw/test"
TEST_REPORTS = "reports/test"


def _env():
    """테스트용 환경변수 dict — 운영 경로를 테스트 전용 경로로 override."""
    e = os.environ.copy()
    e["MORNINGBRIEF_DB"] = TEST_DB
    e["MORNINGBRIEF_RAW_DIR"] = TEST_RAW
    e["MORNINGBRIEF_REPORTS_DIR"] = TEST_REPORTS
    return e


def _run(*flags):
    """main.py --test --no-discord [*flags] 를 격리된 env로 실행하고 CompletedProcess 반환."""
    return subprocess.run(
        [sys.executable, "main.py", "--test", "--no-discord", *flags],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=_env(),
    )


@pytest.fixture(autouse=True)
def clean():
    """각 테스트 전·후에 테스트 전용 경로만 정리한다.

    운영 data/morning_brief.db · reports/ 는 절대 건드리지 않는다.
    """
    # 테스트 경로만 제거 (운영 경로 미접촉)
    for p in (os.path.join(ROOT, TEST_DB),):
        if os.path.exists(p):
            os.remove(p)
    for d in (os.path.join(ROOT, TEST_RAW), os.path.join(ROOT, TEST_REPORTS)):
        shutil.rmtree(d, ignore_errors=True)
    yield
    # teardown: 테스트 후에도 동일 경로만 정리
    for p in (os.path.join(ROOT, TEST_DB),):
        if os.path.exists(p):
            os.remove(p)
    for d in (os.path.join(ROOT, TEST_RAW), os.path.join(ROOT, TEST_REPORTS)):
        shutil.rmtree(d, ignore_errors=True)


def _test_db_path() -> str:
    """테스트 DB 절대경로."""
    return os.path.join(ROOT, TEST_DB)


def _test_report() -> str:
    """오늘 날짜의 테스트 리포트 절대경로."""
    return os.path.join(ROOT, TEST_REPORTS, TODAY, "report.md")


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
    """--test 실행 후 reports/test/<date>/report.md 가 생성돼야 한다."""
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
