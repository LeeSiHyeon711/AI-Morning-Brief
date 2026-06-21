"""
FEAT-03: RSS 수집기 + 소스 접근성 진단 + --test fixtures 지원

- collect(): feedparser 기반 RSS 수집, 발행 범위 필터, raw JSON 저장(save_raw 옵션)
- check_sources(): 읽기 전용 소스 접근성 진단 (DB/파일 쓰기 없음)
- format_diagnostics(): 콘솔 표 출력
- save_raw=False면 raw 파일을 단 하나도 쓰지 않음 (dry-run 무상태 보장)
"""

import os
import json
import logging
from datetime import datetime

import requests
import feedparser
from dateutil import parser as dtparser


def _to_iso(v):
    """타임스탬프 문자열/struct_time → ISO8601 문자열. 파싱 실패 시 None 반환."""
    try:
        if v:
            return dtparser.parse(str(v)).isoformat()
    except Exception:
        pass
    return None


def _in_range(pub_iso, start, end) -> bool:
    """published_at이 [start, end] 범위 내인지 확인. None이면 보호적 채택(True)."""
    if not pub_iso:
        return True
    return start <= pub_iso <= end


def save_raw_json(raw_dir, source, day, payload) -> str:
    """raw 수집 데이터를 data/raw/<date>/<source>.json에 저장하고 경로 반환."""
    d = os.path.join(raw_dir, day)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{source.replace(' ', '_')}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, default=str)
    return path


def collect(
    sources,
    range_start,
    range_end,
    raw_dir,
    timeout=15,
    test_fixtures=None,
    save_raw=True,
) -> tuple[list[dict], list[dict]]:
    """
    RSS 소스에서 기사를 수집하고 발행 범위로 필터링한다.

    Args:
        sources: list[{name, url, enabled}] — 소스 목록
        range_start: ISO8601 문자열 — 발행 범위 시작
        range_end:   ISO8601 문자열 — 발행 범위 끝
        raw_dir:     raw JSON 저장 루트 디렉토리
        timeout:     HTTP 요청 타임아웃 (초)
        test_fixtures: 지정 시 해당 JSON 파일을 로드해 수집 대체 (--test 경로)
        save_raw:    False면 raw 파일을 전혀 쓰지 않음 (dry-run 무상태 보장)

    Returns:
        (articles: list[dict], errors: list[dict])
        - article 키: url, title, published_at, source, collected_at, raw_excerpt
        - error 키: source, error
    """
    now = datetime.now().isoformat()
    day = now[:10]
    articles, errors = [], []

    # --test 경로: fixtures JSON 로드
    if test_fixtures:
        with open(test_fixtures, encoding="utf-8") as fh:
            raw = json.load(fh)
        if save_raw:
            save_raw_json(raw_dir, "fixtures", day, raw)
        for it in raw:
            art = {
                "url": it["url"],
                "title": it["title"],
                "published_at": _to_iso(it.get("published_at")),
                "source": it.get("source", "fixture"),
                "collected_at": now,
                "raw_excerpt": it.get("raw_excerpt", ""),
            }
            if art["url"] and _in_range(art["published_at"], range_start, range_end):
                articles.append(art)
        return articles, errors

    # 일반 경로: enabled 소스만 순회
    for s in sources:
        if not s.get("enabled", True):
            continue
        name = s["name"]
        try:
            resp = requests.get(
                s["url"],
                timeout=timeout,
                headers={"User-Agent": "AI-Morning-Brief/0.1"},
            )
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
            if save_raw:
                save_raw_json(raw_dir, name, day, feed.entries)
            for e in feed.entries:
                pub = _to_iso(
                    getattr(e, "published", None) or getattr(e, "updated", None)
                )
                art = {
                    "url": getattr(e, "link", ""),
                    "title": getattr(e, "title", ""),
                    "published_at": pub,
                    "source": name,
                    "collected_at": now,
                    "raw_excerpt": (getattr(e, "summary", "") or "")[:2000],
                }
                if art["url"] and _in_range(pub, range_start, range_end):
                    articles.append(art)
        except Exception as ex:
            logging.warning("소스 수집 실패 %s: %s", name, ex)
            errors.append({"source": name, "error": str(ex)})

    return articles, errors


def check_sources(sources, timeout=15) -> list[dict]:
    """
    소스 접근성 진단 — 읽기 전용. DB/파일/Discord에 아무것도 쓰지 않는다.

    설계서 9-1 항목:
      source_name, url, enabled, http_status, ok, timeout,
      parseable, entry_count, error, checked_at

    Args:
        sources: list[{name, url, enabled}]
        timeout: HTTP 요청 타임아웃 (초)

    Returns:
        list[dict] — 소스별 진단 결과 (enabled 여부와 무관하게 전체 처리)
    """
    out = []
    for s in sources:
        r = {
            "source_name": s["name"],
            "url": s["url"],
            "enabled": s.get("enabled", True),
            "http_status": None,
            "ok": False,
            "timeout": False,
            "parseable": False,
            "entry_count": 0,
            "error": "",
            "checked_at": datetime.now().isoformat(),
        }
        try:
            resp = requests.get(
                s["url"],
                timeout=timeout,
                headers={"User-Agent": "AI-Morning-Brief/0.1"},
            )
            r["http_status"] = resp.status_code
            feed = feedparser.parse(resp.content)
            r["entry_count"] = len(feed.entries)
            r["parseable"] = len(feed.entries) > 0
            r["ok"] = resp.ok and r["parseable"]
            if not resp.ok:
                r["error"] = f"HTTP {resp.status_code}"
            elif not r["parseable"]:
                r["error"] = "파싱 불가 또는 엔트리 없음"
        except requests.exceptions.Timeout:
            r["timeout"] = True
            r["error"] = "timeout"
        except Exception as ex:
            r["error"] = str(ex)
        out.append(r)
    return out


def format_diagnostics(rows) -> str:
    """
    check_sources 결과를 콘솔 표 문자열로 포맷한다.

    Returns:
        헤더 + 행 목록 + 요약 줄을 합친 문자열
    """
    lines = [
        f"{'SOURCE':22} {'EN':3} {'HTTP':5} {'OK':3} {'TO':3} {'PARSE':6} {'N':5} ERROR"
    ]
    ok_n = 0
    for r in rows:
        if r["ok"]:
            ok_n += 1
        lines.append(
            "{:22} {:3} {:5} {:3} {:3} {:6} {:5} {}".format(
                r["source_name"][:22],
                "y" if r["enabled"] else "n",
                str(r["http_status"] or "-"),
                "OK" if r["ok"] else "X",
                "y" if r["timeout"] else "n",
                "yes" if r["parseable"] else "no",
                r["entry_count"],
                r["error"],
            )
        )
    fails = [r["source_name"] for r in rows if not r["ok"]]
    lines.append(
        f"요약: 성공 {ok_n} / 실패 {len(rows) - ok_n}"
        + (f" → 실패: {fails}" if fails else "")
    )
    return "\n".join(lines)
