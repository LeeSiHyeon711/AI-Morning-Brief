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
        text = _call_claude(prompt, api_key, model, max_output_tokens)
        result = _parse_response(text, articles)
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


def _parse_response(text: str, articles: list) -> dict:
    """응답 텍스트에서 JSON을 추출·파싱하고 누락 필드를 기본값으로 보정한다.

    파싱 실패 시 예외를 올려 analyze가 fallback으로 흡수하게 한다.
    """
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e == -1:
        raise ValueError("응답에 JSON 객체({...})가 없음")
    data = json.loads(text[s : e + 1])

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
        text = _call_claude(prompt, api_key, model, max_output_tokens)  # 기존 일간 호출 재사용
        data = _parse_weekly_response(text)
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
        '"flow_themes":["주요 흐름 테마 제목 최대 4개"],'
        '"notable_events":["주목 사건 최대 3개"],'
        '"workshop_actions":["공방 즉시 착수 액션 최대 3개(반복 등장 아이디어 우선)"],'
        '"next_week_watch":["다음 주 관전 포인트"],'
        '"narrative":"<주간 흐름 서술, 한국어, 정량 신호에 정합>"}')
    return {"system": system, "user": user}


def _parse_weekly_response(text: str) -> dict:
    """첫 { ~ 마지막 } 슬라이스 후 json.loads + 누락 필드 기본값 보정."""
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e == -1:
        raise ValueError("주간 응답에 JSON 객체가 없음")
    data = json.loads(text[s:e + 1])
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
