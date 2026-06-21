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
        report_path: 로컬 리포트 경로 (비어 있으면 출력 안 함)
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
