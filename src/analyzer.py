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
