"""Claude 분석기 + fallback (FEAT-04)

기사 목록을 Claude API로 분석해 기사별 메타(summary/tags/importance/relevance)와
전체 브리핑 JSON을 반환한다. operator_profile을 시스템 프롬프트 렌즈로 주입.

모든 예외(모델명 오류·미존재 모델·API 키 없음·API 실패·JSON 파싱 실패)는
_fallback_analyze로 흡수 → 파이프라인 절대 중단 없음.
"""

import json
import logging
import re

FALLBACK_KEYWORDS = [
    "claude", "anthropic", "openai", "gpt", "codex", "mcp", "agent",
    "gemini", "cursor", "automation", "github actions", "n8n",
]

# ── 출력 스키마 (tool-use 강제) ────────────────────────────────────────────────
# 자유 텍스트 JSON을 프롬프트로 "부탁"하고 find-brace+json.loads로 파싱하던 방식은
# 모델 출력에 문법 오류가 하나라도 있으면 통째로 fallback으로 강등됐다(2026-07-23 사고).
# Anthropic tool-use(tool_choice 강제)로 출력 구조를 못박아 파싱 취약성을 제거한다.
_STR_ARRAY = {"type": "array", "items": {"type": "string"}}

DAILY_SCHEMA = {
    "type": "object",
    "properties": {
        "articles": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "summary": {"type": "string"},
                    "tags": _STR_ARRAY,
                    "importance": {"type": "integer"},
                    "relevance": {"type": "integer"},
                },
                "required": ["url", "summary", "tags", "importance", "relevance"],
            },
        },
        "briefing": {
            "type": "object",
            "properties": {
                "headline_changes": _STR_ARRAY,
                "sangsang_ideas": _STR_ARRAY,
                "action_items": _STR_ARRAY,
                "summary_text": {"type": "string"},
            },
            "required": ["headline_changes", "sangsang_ideas", "action_items", "summary_text"],
        },
    },
    "required": ["articles", "briefing"],
}

WEEKLY_SCHEMA = {
    "type": "object",
    "properties": {
        "one_line_summary": {"type": "string"},
        "flow_themes": _STR_ARRAY,
        "notable_events": _STR_ARRAY,
        "workshop_actions": _STR_ARRAY,
        "next_week_watch": _STR_ARRAY,
        "narrative": {"type": "string"},
    },
    "required": ["one_line_summary", "flow_themes", "notable_events",
                 "workshop_actions", "next_week_watch", "narrative"],
}

MONTHLY_SCHEMA = {
    "type": "object",
    "properties": {
        "one_line_summary": {"type": "string"},
        "flow_themes": _STR_ARRAY,
        "notable_events": _STR_ARRAY,
        "workshop_actions": _STR_ARRAY,
        "next_month_watch": _STR_ARRAY,
        "narrative": {"type": "string"},
    },
    "required": ["one_line_summary", "flow_themes", "notable_events",
                 "workshop_actions", "next_month_watch", "narrative"],
}


def analyze(
    articles: list,
    operator_profile: str,
    api_key: str | None = None,
    model: str = "claude-sonnet-4-5",
    max_output_tokens: int = 4000,
    max_articles: int = 40,
    force_fallback: bool = False,
) -> dict:
    """기사 목록을 분석해 mode/articles/briefing dict를 반환한다.

    Args:
        articles: 수집된 기사 dict 리스트 (설계서 3장 표준 형태).
        operator_profile: operator_profile.md 텍스트 (시스템 프롬프트 렌즈).
        api_key: Anthropic API 키. None이면 즉시 fallback.
        model: Claude 모델명. 잘못된 모델명이면 예외 → fallback.
        max_output_tokens: 최대 출력 토큰 수.
        max_articles: 분석에 포함할 최대 기사 수.
        force_fallback: True이면 API 키 유무와 무관하게 즉시 fallback.

    Returns:
        {
            "mode": "claude" | "fallback",
            "articles": [{"url", "summary", "tags": list, "importance": int, "relevance": int}],
            "briefing": {"headline_changes": [...], "sangsang_ideas": [...],
                         "action_items": [...], "summary_text": str},
        }
    """
    if force_fallback or not api_key:
        logging.warning(
            "fallback 분석 사용 (force=%s, key=%s)", force_fallback, bool(api_key)
        )
        return _fallback_analyze(articles)

    try:
        prompt = _build_prompt(articles[:max_articles], operator_profile)
        data = _call_claude_json(
            prompt, api_key, model, max_output_tokens,
            tool_name="emit_daily_brief",
            description="기사별 평가와 전체 브리핑을 지정 스키마로 반환한다.",
            schema=DAILY_SCHEMA,
        )
        result = _parse_response(data, articles)
        result["mode"] = "claude"
        return result
    except Exception as ex:
        # API 오류/네트워크/타임아웃/모델명 오류(미존재 모델)/JSON 파싱 실패 모두 여기로
        logging.error("Claude 분석 실패 → fallback: %s", ex)
        return _fallback_analyze(articles)


def _build_prompt(articles: list, operator_profile: str) -> dict:
    """설계서 5-1. system/user 메시지 dict 반환."""
    items = [
        {
            "idx": i,
            "title": a["title"],
            "source": a["source"],
            "published_at": a.get("published_at"),
            "url": a["url"],
            "excerpt": (a.get("raw_excerpt") or "")[:800],
        }
        for i, a in enumerate(articles)
    ]
    system = (
        "당신은 IT상상공방 운영자의 'AI 동향 분석가'다. 아래 <운영자 프로필>을 분석의 "
        "렌즈로 삼아 각 기사의 중요도와 상상공방 적용 가능성을 평가하라. 반드시 지정된 "
        "JSON 스키마만 출력하라.\n\n<운영자 프로필>\n"
        + operator_profile
        + "\n</운영자 프로필>"
    )
    user = (
        "다음 기사들을 평가하고 전체 브리핑을 작성하라.\n<기사 목록 (JSON)>\n"
        + json.dumps(items, ensure_ascii=False)
        + "\n</기사 목록>\n\n출력 스키마:\n"
        '{"articles":[{"url":"<원본>","summary":"<한국어 2~3문장>","tags":[..],'
        '"importance":0,"relevance":0}],'
        '"briefing":{"headline_changes":[],"sangsang_ideas":[],"action_items":[],'
        '"summary_text":"<1800자 이내 한국어>"}}'
    )
    return {"system": system, "user": user}


def _call_claude(prompt: dict, api_key: str, model: str, max_tokens: int) -> str:
    """Anthropic SDK로 단일 메시지를 호출해 응답 텍스트를 반환한다.

    모델명이 잘못되면 이 호출에서 예외 발생 → analyze의 except가 fallback으로 강등.
    """
    from anthropic import Anthropic  # 런타임 임포트 (SDK 미설치 시 예외 → fallback)

    client = Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=prompt["system"],
        messages=[{"role": "user", "content": prompt["user"]}],
    )
    return "".join(getattr(b, "text", "") for b in msg.content)


def _call_claude_json(prompt: dict, api_key: str, model: str, max_tokens: int,
                      tool_name: str, description: str, schema: dict) -> dict:
    """tool-use로 출력 스키마를 강제해, 모델이 반환한 구조화 dict를 그대로 반환한다.

    tool_choice로 지정 툴 호출을 강제하므로 모델은 자유 텍스트가 아니라 스키마에 맞는
    구조화 입력(tool_use.input)을 낸다 → find-brace + json.loads 파싱이 불필요해지고,
    문자열 값의 이스케이프 오류 등으로 전체가 fallback되던 취약성이 사라진다.
    tool_use 블록이 없으면 예외 → 호출부의 except가 fallback으로 흡수한다.
    """
    from anthropic import Anthropic  # 런타임 임포트 (SDK 미설치 시 예외 → fallback)

    client = Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=prompt["system"],
        messages=[{"role": "user", "content": prompt["user"]}],
        tools=[{"name": tool_name, "description": description, "input_schema": schema}],
        tool_choice={"type": "tool", "name": tool_name},
    )
    for block in msg.content:
        if getattr(block, "type", None) == "tool_use":
            return dict(block.input)
    raise ValueError("tool_use 응답이 없음 (스키마 강제 실패)")


def _parse_response(data: dict, articles: list) -> dict:
    """tool-use로 강제된 응답 dict의 누락 필드를 기본값으로 방어 보정한다.

    (스키마 required로 대부분 채워지나, 서버측 검증이 느슨할 수 있어 기본값을 둔다.)
    """
    data.setdefault("articles", [])
    b = data.setdefault("briefing", {})
    for k in ("headline_changes", "sangsang_ideas", "action_items"):
        b.setdefault(k, [])
    b.setdefault("summary_text", "")

    for a in data["articles"]:
        a.setdefault("summary", "")
        a.setdefault("tags", [])
        a["importance"] = int(a.get("importance", 0))
        a["relevance"] = int(a.get("relevance", 0))

    return data


# ── 주간 합성 함수 (FEAT-09) — 기존 일간 코드 수정 없이 하단에 추가 ──────────────

def synthesize_weekly(signals, ideas, top_articles, operator_profile,
                      api_key=None, model="claude-sonnet-4-6",
                      max_output_tokens=16000, force_fallback=False) -> dict:
    """주간 흐름을 합성한다. 반환: {"mode": "claude"|"fallback", "synthesis": {...}}.

    synthesis 스키마:
      {"one_line_summary": str, "flow_themes": [str,...<=4],
       "notable_events": [str,...<=3], "workshop_actions": [str,...<=3],
       "next_week_watch": [str,...], "narrative": str}
    모든 예외(키 없음·모델명 오류·API 실패·JSON 파싱 실패)는 _fallback_weekly로 흡수.
    """
    if force_fallback or not api_key:
        return {"mode": "fallback", "synthesis": _fallback_weekly(signals, ideas, top_articles)}
    try:
        prompt = _build_weekly_prompt(signals, ideas, top_articles, operator_profile)
        raw = _call_claude_json(
            prompt, api_key, model, max_output_tokens,
            tool_name="emit_weekly_synthesis",
            description="주간 흐름 합성을 지정 스키마로 반환한다.",
            schema=WEEKLY_SCHEMA,
        )
        data = _parse_weekly_response(raw)
        return {"mode": "claude", "synthesis": data}
    except Exception as ex:
        logging.error("주간 합성 실패 → fallback: %s", ex)
        return {"mode": "fallback", "synthesis": _fallback_weekly(signals, ideas, top_articles)}


def _build_weekly_prompt(signals, ideas, top_articles, operator_profile) -> dict:
    """주간 합성용 system/user dict. 정량 신호를 '근거'로 명시 투입(서술이 숫자에 정합하도록)."""
    import json as _json
    sig = {
        "total": signals["total"],
        "daily_intensity": signals["daily_intensity"],
        "peak_day": signals["peak_day"],
        "sparse_days": signals["sparse_days"],
        "persistent_stems": [{"tag": t["tag"], "count": t["count"], "day_span": t["day_span"]}
                             for t in signals["persistent_stems"][:12]],
        "bursts": [{"tag": t["tag"], "count": t["count"], "day_span": t["day_span"]}
                   for t in signals["bursts"][:8]],
        "noise_sources": signals["noise_sources"],
    }
    arts = [{"title": a["title"], "source": a["source"], "summary": a.get("summary", ""),
             "tags": a.get("tags", []), "importance": a.get("importance", 0),
             "relevance": a.get("relevance", 0)} for a in top_articles]
    system = (
        "당신은 IT상상공방 운영자의 'AI 주간 흐름 분석가'다. 아래 <운영자 프로필>을 렌즈로,"
        " 제공된 <정량 신호>를 반드시 근거로 삼아(서술이 숫자와 모순되지 않게) 한 주의 흐름을"
        " 합성하라. 단발 버스트(노이즈)와 지속 줄기(구조적 흐름)를 구분하라. 지정된 JSON만 출력하라.\n\n"
        "<운영자 프로필>\n" + operator_profile + "\n</운영자 프로필>")
    user = (
        "<정량 신호 (JSON)>\n" + _json.dumps(sig, ensure_ascii=False) + "\n</정량 신호>\n\n"
        "<일간 아이디어 통합 (반복일수 days 포함)>\n" + _json.dumps(ideas, ensure_ascii=False) + "\n</일간 아이디어>\n\n"
        "<상위 기사 (JSON)>\n" + _json.dumps(arts, ensure_ascii=False) + "\n</상위 기사>\n\n"
        "출력 스키마:\n"
        '{"one_line_summary":"<이번 주 한 줄 요약>",'
        '"flow_themes":["주요 흐름 테마 제목 최대 4개, 각 항목 80자 이내"],'
        '"notable_events":["주목 사건 최대 3개, 각 항목 100자 이내"],'
        '"workshop_actions":["공방 즉시 착수 액션 최대 3개(반복 등장 아이디어 우선), 각 항목 100자 이내"],'
        '"next_week_watch":["다음 주 관전 포인트, 각 항목 80자 이내"],'
        '"narrative":"<주간 흐름 서술, 한국어, 정량 신호에 정합>"}')
    return {"system": system, "user": user}


def _parse_weekly_response(data: dict) -> dict:
    """tool-use로 강제된 주간 응답 dict의 누락 필드를 기본값으로 방어 보정한다."""
    data.setdefault("one_line_summary", "")
    for k in ("flow_themes", "notable_events", "workshop_actions", "next_week_watch"):
        data.setdefault(k, [])
    data.setdefault("narrative", "")
    return data


def _fallback_weekly(signals, ideas, top_articles) -> dict:
    """규칙기반 주간 합성(Claude 없이). 정량 신호에서 직접 문장을 만든다."""
    stems = [t["tag"] for t in signals["persistent_stems"][:6]]
    one = f"이번 주 총 {signals['total']}건. 지속 줄기: " + (", ".join(stems) if stems else "없음")
    themes = [a["title"] for a in top_articles[:4]]
    events = [a["title"] for a in top_articles[:3]]
    actions = [i["text"] for i in ideas[:3]] or [a["title"] for a in signals["workshop_picks"][:3]]
    watch = [t["tag"] for t in signals["persistent_stems"][:2]]
    narr = ("[기본 주간 리포트] " + one
            + (f" / 노이즈 후보: {[s['source'] for s in signals['noise_sources']]}"
               if signals["noise_sources"] else ""))
    return {"one_line_summary": one, "flow_themes": themes, "notable_events": events,
            "workshop_actions": actions, "next_week_watch": watch, "narrative": narr}


# ── 월간 합성 함수 (FEAT-12) — 기존 일간·주간 코드 수정 없이 하단에 추가 ──────────

def synthesize_monthly(signals, ideas, top_articles, basis, operator_profile,
                       api_key=None, model="claude-sonnet-4-6",
                       max_output_tokens=20000, force_fallback=False) -> dict:
    """월간 흐름 합성. 각 핵심 주장에 신뢰도 라벨을 부여(#12-2). 2차 필터(공방 적용성)를 랭킹으로 수행.

    반환: {"mode": "claude"|"fallback", "synthesis": {...}}.
    synthesis 스키마(라벨은 문자열 앞에 '[확정] '/'[추정] '/'[주의] ' 접두):
      {"one_line_summary": str,
       "flow_themes": [str,...<=4],       # 각 항목 라벨 접두
       "notable_events": [str,...<=3],    # 각 항목 라벨 접두
       "workshop_actions": [str,...<=3],  # 2차 필터 = 공방 적용성 상위
       "next_month_watch": [str,...<=3],
       "narrative": str}
    모든 예외(키없음·모델오류·API실패·JSON파싱실패)는 _fallback_monthly로 흡수.
    """
    if force_fallback or not api_key:
        return {"mode": "fallback",
                "synthesis": _fallback_monthly(signals, ideas, top_articles, basis)}
    try:
        prompt = _build_monthly_prompt(signals, ideas, top_articles, basis, operator_profile)
        raw = _call_claude_json(
            prompt, api_key, model, max_output_tokens,
            tool_name="emit_monthly_synthesis",
            description="월간 흐름 합성을 지정 스키마로 반환한다.",
            schema=MONTHLY_SCHEMA,
        )
        data = _parse_monthly_response(raw)
        return {"mode": "claude", "synthesis": data}
    except Exception as ex:
        logging.error("월간 합성 실패 → fallback: %s", ex)
        return {"mode": "fallback",
                "synthesis": _fallback_monthly(signals, ideas, top_articles, basis)}


def _build_monthly_prompt(signals, ideas, top_articles, basis, operator_profile) -> dict:
    """월간 합성 system/user. 신뢰도 라벨 규칙 + 2단계 필터(2차=공방 적용성) + 정량 신호 근거 투입."""
    sig = {
        "total": signals["total"], "span_days": signals["span_days"],
        "peak_day": signals["peak_day"], "sparse_days": signals["sparse_days"],
        "persistent_stems": [{"tag": t["tag"], "count": t["count"], "day_span": t["day_span"]}
                             for t in signals["persistent_stems"][:14]],
        "bursts": [{"tag": t["tag"], "count": t["count"], "day_span": t["day_span"]}
                   for t in signals["bursts"][:8]],
        "sources_top": signals["sources"][:8],       # 편중도 (#12-5)
        "noise_sources": signals["noise_sources"],    # (#12-6)
    }
    # 각 대표 기사에 교차출처수 동봉 → 신뢰도 라벨 판정 근거 (#12-2)
    arts = [{"title": a["title"], "source": a["source"], "summary": a.get("summary", ""),
             "tags": a.get("tags", []), "importance": a.get("importance", 0),
             "relevance": a.get("relevance", 0),
             "cross_source_count": a.get("cross_source_count", 1)} for a in top_articles]
    system = (
        "당신은 IT상상공방 운영자의 'AI 월간 흐름 분석가'다. 아래 <운영자 프로필>을 렌즈로,"
        " <정량 신호>와 <대표 기사>를 반드시 근거로 삼아(서술이 숫자·출처와 모순되지 않게)"
        " 한 달의 구조적 흐름을 합성하라.\n"
        "■ 2단계 필터: 1차 관련성 통과분만 받았다. 당신은 2차로 '상상공방 적용 가능성'을 기준으로"
        " 흐름·액션의 우선순위를 정하라(무관한 것은 뒤로 미루되 삭제하지 말 것).\n"
        "■ 신뢰도 라벨(필수): flow_themes·notable_events의 각 항목과 narrative의 각 핵심 주장 앞에"
        " 다음 규칙으로 라벨을 접두하라 —\n"
        "  [확정] : 교차출처(cross_source_count)≥2 이거나 공식 릴리스/공식 블로그로 확인된 사실.\n"
        "  [추정] : 단일 출처·해석·전망(추론이 포함된 서술).\n"
        "  [주의] : 미확인·논란·반박 가능성이 있는 주장.\n"
        " 지정된 JSON만 출력하라.\n\n"
        "<운영자 프로필>\n" + operator_profile + "\n</운영자 프로필>")
    user = (
        "<분석 기반>\n" + json.dumps(basis, ensure_ascii=False) + "\n</분석 기반>\n\n"
        "<정량 신호 (JSON)>\n" + json.dumps(sig, ensure_ascii=False) + "\n</정량 신호>\n\n"
        "<일간 아이디어 통합 (반복일수 days 포함)>\n" + json.dumps(ideas, ensure_ascii=False) + "\n</일간 아이디어>\n\n"
        "<대표 기사 (JSON, cross_source_count 포함)>\n" + json.dumps(arts, ensure_ascii=False) + "\n</대표 기사>\n\n"
        "출력 스키마:\n"
        '{"one_line_summary":"<이번 달 한 줄 요약>",'
        '"flow_themes":["[라벨] 주요 흐름 테마 최대 4개, 각 항목 100자 이내([라벨] 포함)"],'
        '"notable_events":["[라벨] 주목 사건 최대 3개, 각 항목 100자 이내([라벨] 포함)"],'
        '"workshop_actions":["공방 즉시 착수 액션 최대 3개(2차=공방 적용성 상위, 반복 아이디어 우선), 각 항목 100자 이내"],'
        '"next_month_watch":["다음 달 관전 포인트 최대 3개, 각 항목 80자 이내"],'
        '"narrative":"<월간 흐름 서술, 한국어, 핵심 주장마다 [라벨] 접두, 정량 신호에 정합>"}')
    return {"system": system, "user": user}


def _parse_monthly_response(data: dict) -> dict:
    """tool-use로 강제된 월간 응답 dict의 누락 필드를 기본값으로 방어 보정한다."""
    data.setdefault("one_line_summary", "")
    for k in ("flow_themes", "notable_events", "workshop_actions", "next_month_watch"):
        data.setdefault(k, [])
    data.setdefault("narrative", "")
    return data


def _label_by_cross_source(article) -> str:
    """fallback용 규칙 라벨: 교차출처≥2 → [확정], ==1 → [추정]. (코드 기계 부여)"""
    return "[확정]" if int(article.get("cross_source_count", 1)) >= 2 else "[추정]"


def _fallback_monthly(signals, ideas, top_articles, basis) -> dict:
    """규칙기반 월간 합성(Claude 없이). 신뢰도 라벨을 교차출처수로 기계 부여(#12-2)."""
    stems = [t["tag"] for t in signals["persistent_stems"][:6]]
    one = (f"이번 달 대표 {basis.get('represented', 0)}건(수집 {basis.get('collected', 0)}·"
           f"1차통과 {basis.get('first_stage_passed', 0)}). 지속 줄기: "
           + (", ".join(stems) if stems else "없음"))
    themes = [f"{_label_by_cross_source(a)} {a['title']}" for a in top_articles[:4]]
    events = [f"{_label_by_cross_source(a)} {a['title']}" for a in top_articles[:3]]
    actions = [i["text"] for i in ideas[:3]] or [a["title"] for a in signals["workshop_picks"][:3]]
    watch = [t["tag"] for t in signals["persistent_stems"][:3]]
    noise = basis.get("noise", {})
    narr = ("[확정] [기본 월간 리포트] " + one
            + (f" / 노이즈 제외 {noise.get('_total', 0)}건" if noise else "")
            + (f" / 범위 외 {basis.get('out_of_period', 0)}건" if basis.get("out_of_period") else ""))
    return {"one_line_summary": one, "flow_themes": themes, "notable_events": events,
            "workshop_actions": actions, "next_month_watch": watch, "narrative": narr}


# ── 일간 fallback (기존 — 수정 금지) ──────────────────────────────────────────

def _fallback_analyze(articles: list) -> dict:
    """규칙 기반 fallback 분석. 항상 mode='fallback'을 반환한다.

    Claude API 없이 키워드 매칭으로 tags를 추출하고 excerpt 앞 2문장을 summary로 사용.
    """
    out_articles = []
    for a in articles:
        ex = (a.get("raw_excerpt") or "").strip()
        sents = re.split(r"(?<=[.!?。])\s+", ex)
        summary = " ".join(sents[:2]) if ex else a["title"]
        text = (a["title"] + " " + ex).lower()
        tags = [k for k in FALLBACK_KEYWORDS if k in text]
        out_articles.append(
            {
                "url": a["url"],
                "summary": summary,
                "tags": tags,
                "importance": 0,
                "relevance": 0,
            }
        )

    hot = [
        a["title"]
        for a in articles
        if any(
            k in (a["title"] + " " + (a.get("raw_excerpt") or "")).lower()
            for k in FALLBACK_KEYWORDS
        )
    ][:5]

    briefing = {
        "headline_changes": hot,
        "sangsang_ideas": [],
        "action_items": [],
        "summary_text": (
            ("[기본 리포트] 신규 %d건. 키워드 매칭 주요 제목:\n- " % len(articles))
            + "\n- ".join(hot)
            if hot
            else "[기본 리포트] 키워드 매칭 없음"
        ),
    }
    return {"mode": "fallback", "articles": out_articles, "briefing": briefing}
