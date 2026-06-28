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


# ── 주간 리포트 함수 (FEAT-09) — 기존 save_report 패턴 일반화 + 주간 전문 빌더 ──

def save_bucket_report(reports_dir, year, bucket, name, content) -> str:
    """reports/<year>/<bucket>/<name>.md 로 저장(makedirs + 경로 조립).

    주간: bucket="weekly", name="W26" → reports/2026/weekly/W26.md
    (월간 #12 확장 예약: bucket="monthly", name="M06")
    기존 save_report(일간)와 동일 패턴이며 일간 경로(<YYYY>/<MM>/<DD>.md)는 변경하지 않는다.
    """
    d = os.path.join(reports_dir, f"{int(year):04d}", bucket)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{name}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def build_weekly_report(week_key, monday, sunday, signals, ideas, top, synthesis, mode) -> str:
    """주간 전문 Markdown. 표(강도/태그/소스)는 전문에만 둔다(Discord는 표 미렌더).

    섹션: 헤더(주차·범위·모드) → 한 줄 요약 → 강도 추이 표 → 지속 줄기 vs 단발 버스트 표
         → 소스 신호 품질 표(노이즈 경고) → 공방 관련 픽 → 주요 흐름 테마 → 주목 사건
         → 공방 즉시 착수 → 다음 주 관전 → 일간 아이디어 통합 → 주간 서술 → 상위 원문 링크.
    """
    lines = []

    # ── 헤더 ──
    lines.append(f"# AI Weekly Brief — {week_key}")
    lines.append(f"- 기간: {monday.isoformat()} ~ {sunday.isoformat()}")
    lines.append(f"- 분석 모드: {mode}")
    if mode == "fallback":
        lines.append("> **AI 합성 실패 / 기본 주간 리포트**")
    lines.append("")

    # ── 한 줄 요약 ──
    lines.append("## 이번 주 한 줄 요약")
    lines.append("")
    lines.append(synthesis.get("one_line_summary", ""))
    lines.append("")

    # ── 1. 일자별 강도 추이 표 ──
    lines.append("## 일자별 수집 강도 추이")
    lines.append("")
    lines.append("| 일자 | 건수 | 고중요(≥8) | 평균중요도 | 비고 |")
    lines.append("|------|------|-----------|-----------|------|")
    for row in signals.get("daily_intensity", []):
        note = " ⚠️ 데이터 빈약" if row.get("sparse") else ""
        lines.append(
            f"| {row['date']} | {row['count']} | {row['high_count']} "
            f"| {row['avg_importance']} |{note} |"
        )
    peak = signals.get("peak_day")
    if peak:
        lines.append(f"\n- 피크일: {peak['date']} ({peak['count']}건)")
    sparse = signals.get("sparse_days", [])
    if sparse:
        lines.append(f"- 데이터 빈약일(⚠️): {', '.join(sparse)}")
    lines.append("")

    # ── 2. 태그 지속성 표 ──
    lines.append("## 태그 빈도 × 지속성")
    lines.append("")
    lines.append("| 태그 | 등장 | 지속일수 | 구분 |")
    lines.append("|------|------|---------|------|")
    for t in signals.get("tags", [])[:20]:
        if t["persistent"]:
            label = "지속 줄기"
        elif t["burst"]:
            label = "단발 버스트"
        else:
            label = ""
        lines.append(f"| {t['tag']} | {t['count']} | {t['day_span']} | {label} |")
    stems = signals.get("persistent_stems", [])
    bursts = signals.get("bursts", [])
    if stems:
        lines.append(f"\n**지속 줄기(≥5일)**: {', '.join(t['tag'] for t in stems[:8])}")
    if bursts:
        lines.append(f"**단발 버스트(≤2일, ≥5건)**: {', '.join(t['tag'] for t in bursts[:8])}")
    lines.append("")

    # ── 3. 소스 신호 품질 표 ──
    lines.append("## 소스 신호 품질")
    lines.append("")
    lines.append("| 소스 | 건수 | 평균중요도 | 노이즈 후보 |")
    lines.append("|------|------|-----------|------------|")
    for s in signals.get("sources", []):
        noise = " ⚠️" if s.get("noise_candidate") else ""
        lines.append(
            f"| {s['source']} | {s['count']} | {s['avg_importance']} |{noise} |"
        )
    noise_srcs = signals.get("noise_sources", [])
    if noise_srcs:
        lines.append(f"\n> ⚠️ 노이즈 후보 소스: {[s['source'] for s in noise_srcs]}")
    lines.append("")

    # ── 4. 공방 관련 픽 ──
    picks = signals.get("workshop_picks", [])
    lines.append("## 공방 관련 픽 (relevance ≥ 7)")
    lines.append("")
    if picks:
        for a in picks[:10]:
            lines.append(
                f"- [{a.get('title', '')}]({a.get('url', '')}) "
                f"— 중요도 {a.get('importance', 0)}, 관련도 {a.get('relevance', 0)}"
            )
    else:
        lines.append("_해당 없음_")
    lines.append("")

    # ── 5. 주요 흐름 테마 ──
    lines.append("## 주요 흐름 테마")
    lines.append("")
    for theme in synthesis.get("flow_themes", []):
        lines.append(f"- {theme}")
    if not synthesis.get("flow_themes"):
        lines.append("_해당 없음_")
    lines.append("")

    # ── 6. 주목 사건 ──
    lines.append("## 주목 사건")
    lines.append("")
    for ev in synthesis.get("notable_events", []):
        lines.append(f"- {ev}")
    if not synthesis.get("notable_events"):
        lines.append("_해당 없음_")
    lines.append("")

    # ── 7. 공방 즉시 착수 ──
    lines.append("## 공방 즉시 착수")
    lines.append("")
    for act in synthesis.get("workshop_actions", []):
        lines.append(f"- {act}")
    if not synthesis.get("workshop_actions"):
        lines.append("_해당 없음_")
    lines.append("")

    # ── 8. 다음 주 관전 포인트 ──
    lines.append("## 다음 주 관전 포인트")
    lines.append("")
    for w in synthesis.get("next_week_watch", []):
        lines.append(f"- {w}")
    if not synthesis.get("next_week_watch"):
        lines.append("_해당 없음_")
    lines.append("")

    # ── 9. 일간 아이디어 통합 ──
    lines.append("## 일간 아이디어 통합 (7일)")
    lines.append("")
    if ideas:
        for idea in ideas:
            days_str = f"({idea['days']}일 반복)" if idea['days'] > 1 else "(1회)"
            lines.append(f"- {idea['text']} {days_str}")
    else:
        lines.append("_해당 없음_")
    lines.append("")

    # ── 10. 주간 서술 ──
    lines.append("## 주간 흐름 서술")
    lines.append("")
    lines.append(synthesis.get("narrative", ""))
    lines.append("")

    # ── 11. 상위 원문 링크 ──
    lines.append("## 상위 원문 링크")
    lines.append("")
    for a in top[:20]:
        lines.append(
            f"- [{a.get('title', '')}]({a.get('url', '')}) "
            f"— {a.get('source', '')} (중요도 {a.get('importance', 0)})"
        )
    if not top:
        lines.append("_해당 없음_")
    lines.append("")

    return "\n".join(lines)


# ── 일간 save_report (기존 — 수정 금지) ──────────────────────────────────────

def save_report(reports_dir, date, content) -> str:
    """
    content를 reports/<YYYY>/<MM>/<DD>.md 에 저장한다.
    같은 날 재실행 시 덮어쓴다.

    Parameters
    ----------
    reports_dir : str — reports 루트 디렉토리 경로
    date        : str — YYYY-MM-DD
    content     : str — Markdown 문자열

    Returns
    -------
    str — 저장된 파일의 절대(또는 상대) 경로 (예: reports/2026/06/21.md)
    """
    yyyy, mm, dd = date.split("-")
    d = os.path.join(reports_dir, yyyy, mm)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{dd}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path
