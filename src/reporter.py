"""
reporter.py — Markdown 일일 리포트 생성기 (FEAT-05)

build_report : 분석 결과 + 메타 정보 → Markdown 문자열 반환
save_report  : Markdown 문자열 → reports/<date>/report.md 저장
"""

import os


def _section(title, items):
    """섹션 블록 생성. items가 없으면 '_해당 없음_' 반환."""
    if not items:
        return f"## {title}\n\n_해당 없음_\n"
    return f"## {title}\n\n" + "\n".join(f"- {x}" for x in items) + "\n"


def _by_tags(articles, keys):
    """tags에 keys 중 하나라도 포함된 기사를 Markdown 링크 형태로 반환."""
    return [
        f"[{a['title']}]({a['url']}) — {a.get('summary', '')}"
        for a in articles
        if set(k.lower() for k in a.get("tags", [])) & set(keys)
    ]


def build_report(
    date,
    range_start,
    range_end,
    articles,
    briefing,
    mode,
    catchup=False,
    failed_sources=None,
    new_count=0,
    profile_is_default=False,
) -> str:
    """
    분석 결과와 메타 정보를 받아 7개 섹션 Markdown 리포트 문자열을 반환한다.

    Parameters
    ----------
    date            : str  — YYYY-MM-DD 형식 날짜
    range_start     : str  — 수집 범위 시작 (ISO8601)
    range_end       : str  — 수집 범위 종료 (ISO8601)
    articles        : list[dict] — url/title/source/published_at/summary/tags/importance/relevance
    briefing        : dict — headline_changes/sangsang_ideas/action_items/summary_text
    mode            : str  — 'claude' | 'fallback'
    catchup         : bool — 보완 수집(catch-up) 여부
    failed_sources  : list[dict] | None — 수집 실패 소스 목록 (source 키 포함)
    new_count       : int  — 신규 수집 건수
    profile_is_default : bool — 운영자 프로필 부재 여부
    """
    failed_sources = failed_sources or []
    important = sum(1 for a in articles if a.get("importance", 0) >= 4)

    # 헤더 블록
    head = [f"# AI Morning Brief — {date}", ""]

    if mode == "fallback":
        head.append("> **AI 분석 실패 / 기본 리포트 생성**")
    if catchup:
        head.append(f"> 보완 수집(catch-up): {range_start} ~ {range_end}")
    if profile_is_default:
        head.append("> ⚠ 운영자 프로필 없음 — 기본 프로필로 분석")
    if failed_sources:
        head.append(f"> 수집 실패 소스: {[f['source'] for f in failed_sources]}")

    head += [
        "",
        f"- 수집 범위: {range_start} ~ {range_end}",
        f"- 신규 수집: {new_count}건 / 중요(≥4): {important}건",
        f"- 분석 모드: {mode}",
        "",
    ]

    # 7개 섹션
    body = [
        _section("오늘의 핵심 변화", briefing.get("headline_changes", [])),
        _section(
            "Claude / Claude Code 관련 소식",
            _by_tags(articles, ["claude", "anthropic", "mcp"]),
        ),
        _section(
            "OpenAI / Codex / GPT 관련 소식",
            _by_tags(articles, ["openai", "gpt", "codex"]),
        ),
        _section(
            "MCP / Agent / 자동화 관련 소식",
            _by_tags(articles, ["mcp", "agent", "automation", "n8n", "github actions"]),
        ),
        _section("상상공방에 적용할 수 있는 아이디어", briefing.get("sangsang_ideas", [])),
        _section("나중에 실험해볼 액션 아이템", briefing.get("action_items", [])),
        _section(
            "저장해둘 원문 링크",
            [
                f"[{a['title']}]({a['url']})"
                for a in sorted(
                    articles, key=lambda x: x.get("importance", 0), reverse=True
                )
            ],
        ),
    ]

    return "\n".join(head) + "\n" + "\n".join(body)


def save_report(reports_dir, date, content) -> str:
    """
    content를 reports/<date>/report.md 에 저장한다.
    같은 날 재실행 시 덮어쓴다.

    Parameters
    ----------
    reports_dir : str — reports 루트 디렉토리 경로
    date        : str — YYYY-MM-DD
    content     : str — Markdown 문자열

    Returns
    -------
    str — 저장된 파일의 절대(또는 상대) 경로
    """
    d = os.path.join(reports_dir, date)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "report.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path
