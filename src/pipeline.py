"""파이프라인 오케스트레이션 (FEAT-07)

FEAT-01~06 모듈을 end-to-end 실행 흐름으로 묶는다.

주요 분기:
- --check-sources : 소스 진단만 출력 후 종료, 파이프라인 미실행.
- --dry-run       : collect(save_raw=False) 후 계획/미리보기 출력 + return 0.
                   DB 파일 생성 자체를 건너뜀 (init_db 미호출).
                   DB·raw·report·Discord·meta 어떤 쓰기도 없는 무상태 보장.
- --test          : fixtures 사용, 30일(720h) 기본 lookback으로 fixture 날짜를 커버.
- --force-fallback: analyze에 force_fallback=True 전달, Claude 실패 검증용.
- 실패 복구       : 단일 소스 실패·Claude 실패·Discord 실패는 파이프라인 지속.
                   DB 쓰기 오류만 예외 전파 → 종료코드 1 + last_success_at 미갱신.
"""

import logging
from datetime import datetime, timedelta
from dateutil import parser as dtparser

from src.config_loader import load_config, load_sources, load_secrets, load_operator_profile
from src.storage import init_db, insert_article, update_analysis, get_meta, set_meta
from src.collector import collect, check_sources, format_diagnostics
from src.analyzer import analyze
from src.reporter import build_report, save_report
from src.notifier import build_briefing_message, send_discord


def _iso(dt) -> str:
    """datetime → ISO8601 문자열."""
    return dt.isoformat()


def _determine_range(args, conn, cfg) -> tuple:
    """수집 범위(start, end, catchup)를 결정한다.

    우선순위: --from/--to 명시 > last_success_at(meta) > lookback_hours 기본값.
    conn이 None인 경우(dry-run) last_success_at을 참조하지 않는다.
    --test 모드에서 명시적 범위가 없으면 lookback을 720h(30일)로 확장해
    fixtures의 날짜(~30일 내 분포)를 안정적으로 커버한다.

    Returns:
        (start: str, end: str, catchup: bool) — ISO8601 문자열 쌍
    """
    now = datetime.now()
    pipeline_cfg = cfg.get("pipeline", {})
    lookback = pipeline_cfg.get("lookback_hours", 24)

    # --test 모드: 기본 lookback을 30일로 확장 (fixtures 날짜 커버)
    if getattr(args, "test", False) and not args.from_dt and not args.to_dt:
        lookback = 720

    last = get_meta(conn, "last_success_at") if conn is not None else None

    if args.from_dt:
        start = dtparser.parse(args.from_dt).isoformat()
    elif last:
        start = last
    else:
        start = _iso(now - timedelta(hours=lookback))

    end = dtparser.parse(args.to_dt).isoformat() if args.to_dt else _iso(now)

    catchup = bool(args.from_dt or args.to_dt) or (
        bool(last) and dtparser.parse(last) < now - timedelta(hours=lookback)
    )
    return start, end, catchup


def _dispatch_check_sources(sources, timeout) -> int:
    """소스 접근성 진단만 실행하고 종료코드를 반환한다.

    파이프라인을 실행하지 않는다. DB/raw/Discord 쓰기 없음.

    Returns:
        0 — 모든 소스 OK
        1 — 하나 이상 실패 (진단 신호용, 예외 아님)
    """
    rows = check_sources(sources, timeout)
    print(format_diagnostics(rows))
    return 0 if all(r["ok"] for r in rows) else 1


def run(args) -> int:
    """파이프라인 end-to-end 실행. 종료코드(0=정상, 1=치명 오류)를 반환한다."""
    cfg = load_config()
    secrets = load_secrets()

    # 소스·프로필 경로: config에 paths.sources/operator_profile 없으면 기본값 사용
    sources = load_sources(cfg.get("paths", {}).get("sources", "config/sources.yaml"))
    profile, is_default = load_operator_profile(
        cfg.get("paths", {}).get("operator_profile", "config/operator_profile.md")
    )

    pipeline_cfg = cfg.get("pipeline", {})
    timeout = pipeline_cfg.get("request_timeout_sec", 15)

    # --check-sources: 진단만, 파이프라인 미실행
    if getattr(args, "check_sources", False):
        return _dispatch_check_sources(sources, timeout)

    # DB 초기화 (dry-run이면 건너뜀 — DB 파일 생성 자체를 막아 무상태 보장)
    conn = None
    if not args.dry_run:
        conn = init_db(cfg["paths"]["db"])

    start, end, catchup = _determine_range(args, conn, cfg)

    fixtures = "tests/fixtures/sample_articles.json" if getattr(args, "test", False) else None
    articles, errors = collect(
        sources, start, end, cfg["paths"]["raw_dir"], timeout,
        fixtures, save_raw=not args.dry_run,  # dry-run: raw 파일 저장 안 함
    )

    # ★ dry-run 무상태 보장: 이 지점까지 DB·raw·report·Discord·meta 쓰기 없음
    if args.dry_run:
        print(
            f"[dry-run] 범위 {start}~{end} catchup={catchup} "
            f"수집 {len(articles)}건 "
            f"실패소스 {[e['source'] for e in errors]} "
            f"(DB/raw/report/Discord 변경 없음)"
        )
        for a in articles[:5]:
            print(" -", a["title"])
        return 0

    # DB 저장 — 중복 URL은 무시(QA-2), 쓰기 오류는 예외 전파 → 종료코드 1
    new_count, new_articles = 0, []
    for a in articles:
        if insert_article(conn, a):
            new_count += 1
            new_articles.append(a)

    # Claude 분석 — 모든 실패(모델명 오류 포함)는 fallback으로 흡수, 파이프라인 중단 없음
    claude_cfg = cfg.get("claude", {})
    model = claude_cfg.get("model", "claude-sonnet-4-5")
    max_output_tokens = claude_cfg.get("max_tokens", 4000)
    max_articles_limit = pipeline_cfg.get("max_articles_per_source", 40)

    result = analyze(
        new_articles, profile,
        secrets.get("ANTHROPIC_API_KEY"),
        model, max_output_tokens, max_articles_limit,
        force_fallback=getattr(args, "force_fallback", False),
    )

    # 분석 결과 DB 반영
    by_url = {x["url"]: x for x in result["articles"]}
    merged = []
    for a in new_articles:
        an = by_url.get(a["url"], {})
        update_analysis(
            conn, a["url"],
            an.get("summary", ""),
            an.get("tags", []),
            an.get("importance", 0),
            an.get("relevance", 0),
            1 if result["mode"] == "claude" else 0,
        )
        merged.append({**a, **an})

    # 리포트 생성·저장
    date = datetime.now().strftime("%Y-%m-%d")
    md = build_report(
        date, start, end, merged, result["briefing"], result["mode"],
        catchup, errors, new_count, is_default,
    )
    path = save_report(cfg["paths"]["reports_dir"], date, md)

    # Discord 전송 (실패해도 파이프라인 계속 — meta는 정상 갱신)
    if not getattr(args, "no_discord", False):
        discord_cfg = cfg.get("discord", {})
        char_limit = discord_cfg.get("char_limit", 2000)
        truncate_marker = discord_cfg.get(
            "truncate_marker", "…(이하 생략, 전체는 로컬 리포트 참조)"
        )
        msg = build_briefing_message(
            date, start, end, new_count, result["briefing"],
            result["mode"], catchup, errors, path, is_default,
            char_limit, truncate_marker,
        )
        send_discord(secrets.get("DISCORD_WEBHOOK_URL"), msg)

    # 성공 meta 갱신 — 다음 실행의 catch-up 기준이 됨
    now_iso = datetime.now().isoformat()
    set_meta(conn, "last_success_at", now_iso)
    set_meta(conn, "last_report_range_start", start)
    set_meta(conn, "last_report_range_end", end)

    logging.info("완료: 신규 %d건, 모드 %s, 리포트 %s", new_count, result["mode"], path)
    return 0
