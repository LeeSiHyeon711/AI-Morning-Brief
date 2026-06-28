# src/notifier.py
import logging

import requests


def build_briefing_message(
    date,
    range_start,
    range_end,
    new_count,
    briefing,
    mode="claude",
    catchup=False,
    failed_sources=None,
    report_path="",
    profile_is_default=False,
    char_limit=2000,
    truncate_marker="…(이하 생략, 전체는 로컬 리포트 참조)",
) -> str:
    """브리핑·메타 정보를 char_limit 이내 Discord 메시지로 조립한다.

    Args:
        date: 브리핑 날짜 (예: "2026-06-21")
        range_start: 수집 범위 시작 ISO8601 문자열
        range_end: 수집 범위 끝 ISO8601 문자열
        new_count: 신규 수집 건수
        briefing: analyze() 반환 dict 내 briefing 딕셔너리
                  (headline_changes, sangsang_ideas 키 포함)
        mode: "claude" | "fallback"
        catchup: True면 catch-up 수집 안내 포함
        failed_sources: 수집 실패 소스 리스트 ({"source": "..."} dict 형태)
        report_path: 로컬 리포트 경로 (예: "reports/2026/06/21.md" — 비어 있으면 출력 안 함)
        profile_is_default: True면 운영자 프로필 부재 안내 포함
        char_limit: 최대 문자 수 (기본 2000)
        truncate_marker: 절삭 시 메시지 끝에 붙이는 마커

    Returns:
        길이 <= char_limit 인 Discord 메시지 문자열
    """
    failed_sources = failed_sources or []

    lines = [f"**AI Morning Brief — {date}**"]

    if mode == "fallback":
        lines.append("⚠ AI 분석 실패 / 기본 리포트 생성")

    if catchup:
        lines.append(f"↻ 보완 수집(catch-up): {range_start} ~ {range_end}")

    if profile_is_default:
        lines.append("⚠ 운영자 프로필 없음 — 기본 프로필로 분석")

    lines.append(f"수집 범위: {range_start} ~ {range_end}")
    lines.append(f"신규 수집: {new_count}건")

    hc = briefing.get("headline_changes", [])[:5]
    if hc:
        lines.append("\n__오늘의 핵심 변화__")
        lines += [f"• {x}" for x in hc]

    ideas = briefing.get("sangsang_ideas", [])
    lines.append(
        "\n__상상공방 적용__: "
        + ("\n• " + "\n• ".join(ideas) if ideas else "해당 없음")
    )

    if failed_sources:
        lines.append(f"\n수집 실패 소스: {[f['source'] for f in failed_sources]}")

    if report_path:
        lines.append(f"\n📄 리포트: {report_path}")

    msg = "\n".join(lines)

    if len(msg) > char_limit:
        msg = msg[: char_limit - len(truncate_marker)] + truncate_marker

    return msg


def build_weekly_message(week_key, monday, sunday, signals, synthesis,
                         report_path, char_limit=2000) -> str:
    """주간 다이제스트(9블록, plain content, char_limit 이내 완결).

    절삭이 아니라 의도적 요약 — 9블록 항목 수를 제한해 2000자 내 완결.

    블록 순서(스펙 E):
      1) 주차 + 한 줄 요약(synthesis['one_line_summary'])
      2) 강도: 총 건수 · 피크일 · 단절일(sparse_days)
      3) 지속 줄기 태그 Top6  ("태그 N회·M일" 인라인)
      4) 노이즈 소스 1줄(noise_sources Top1: "소스 N건·평균 X")
      5) 주요 흐름 테마 제목 4줄(synthesis['flow_themes'][:4])
      6) 주목 사건 Top3(synthesis['notable_events'][:3])
      7) 공방 즉시 착수 액션 Top3(synthesis['workshop_actions'][:3])
      8) 다음 주 관전 포인트(synthesis['next_week_watch'])
      9) 전체 리포트 경로(report_path)

    Args:
        week_key: ISO 주차 키 (예: "2026-W26")
        monday: 해당 주 월요일 date
        sunday: 해당 주 일요일 date
        signals: aggregate_week() 반환 dict
        synthesis: synthesize_weekly() 반환의 synthesis dict
        report_path: 전체 리포트 파일 경로 문자열
        char_limit: 최대 문자 수 (기본 2000)

    Returns:
        길이 <= char_limit 인 Discord 메시지 문자열
    """
    peak = signals.get("peak_day") or {"date": "-", "count": 0}
    sparse = signals.get("sparse_days") or []
    stems = signals.get("persistent_stems", [])[:6]
    noise = signals.get("noise_sources") or []

    L = []
    # 블록 1: 주차 + 한 줄 요약
    L.append(
        f"📅 **AI Morning Brief 주간 — {week_key} "
        f"({monday.month}/{monday.day}~{sunday.month}/{sunday.day})**"
    )
    one_line = (synthesis.get("one_line_summary") or "").strip()
    if one_line:
        L.append(one_line)

    # 블록 2: 강도
    peak_date = (peak.get("date") or "-")[5:] if peak.get("date") != "-" else "-"
    intensity = (
        f"\n📊 강도: 총 {signals['total']}건 · 피크 {peak_date} {peak.get('count', 0)}건"
    )
    if sparse:
        intensity += f" · ⚠️ 단절일 {len(sparse)}일"
    L.append(intensity)

    # 블록 3: 지속 줄기 태그
    if stems:
        L.append(
            "🌳 지속 줄기: "
            + " · ".join(f"{t['tag']} {t['count']}회·{t['day_span']}일" for t in stems)
        )

    # 블록 4: 노이즈 소스
    if noise:
        n = noise[0]
        L.append(f"⚠️ 노이즈 소스: {n['source']} {n['count']}건·평균 {n['avg_importance']}")

    # 블록 5: 주요 흐름 테마
    if synthesis.get("flow_themes"):
        L.append(
            "🧵 주요 흐름:\n"
            + "\n".join(f"• {x}" for x in synthesis["flow_themes"][:4])
        )

    # 블록 6: 주목 사건
    if synthesis.get("notable_events"):
        L.append(
            "⭐ 주목 사건:\n"
            + "\n".join(
                f"{i + 1}. {x}"
                for i, x in enumerate(synthesis["notable_events"][:3])
            )
        )

    # 블록 7: 공방 즉시 착수 액션
    if synthesis.get("workshop_actions"):
        L.append(
            "🎯 공방 즉시 착수:\n"
            + "\n".join(
                f"{i + 1}. {x}"
                for i, x in enumerate(synthesis["workshop_actions"][:3])
            )
        )

    # 블록 8: 다음 주 관전 포인트
    if synthesis.get("next_week_watch"):
        L.append(
            "🔭 다음 주 관전: "
            + " / ".join(synthesis["next_week_watch"][:3])
        )

    # 블록 9: 전체 리포트 경로
    L.append(f"📄 전체 리포트: {report_path}")

    msg = "\n".join(s for s in L if s)
    if len(msg) > char_limit:          # 안전망 — 설계상 9블록은 2000자 내 완결
        msg = msg[: char_limit - 1] + "…"
    return msg


def send_discord(webhook_url, message) -> bool:
    """Discord Webhook으로 메시지를 전송한다.

    전송 실패(URL 없음 / HTTP 비2xx / 예외)는 WARNING/ERROR 로그만 남기고
    False 반환 — 파이프라인을 절대 중단시키지 않는다.

    Args:
        webhook_url: Discord Webhook URL (None 또는 빈 문자열이면 전송 생략)
        message: 전송할 메시지 문자열

    Returns:
        True(전송 성공) / False(생략 또는 실패)
    """
    if not webhook_url:
        logging.warning("DISCORD_WEBHOOK_URL 없음 — 전송 생략")
        return False
    try:
        r = requests.post(webhook_url, json={"content": message}, timeout=15)
        if r.status_code // 100 == 2:
            return True
        logging.error(
            "Discord 전송 실패 HTTP %s: %s", r.status_code, r.text[:200]
        )
        return False
    except Exception as ex:
        logging.error("Discord 전송 예외: %s", ex)
        return False
