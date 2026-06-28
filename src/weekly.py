"""주간 흐름 리포트 엔진 (FEAT-09). 일간 데이터 → 정량 신호 → Claude 합성 → 전문 저장."""
import logging
import re
import datetime
from collections import defaultdict
from datetime import timedelta

from src.config_loader import load_config, load_secrets, load_operator_profile
from src.storage import init_db, get_articles_by_range, get_meta, set_meta
from src.analyzer import synthesize_weekly
from src.reporter import build_weekly_report, save_bucket_report

# 정량 신호 임계값 — 라이브 시스템은 importance/relevance 0~10 스케일(스펙 확정).
# 0~5 스케일로 회귀하면 아래 상수만 조정하면 된다(집계 로직 불변).
HIGH_IMPORTANCE = 8        # 고중요 기준
WORKSHOP_RELEVANCE = 7     # 공방 관련 픽 기준
PERSIST_DAYS = 5           # 5일 이상 등장 = 지속 줄기
BURST_MAX_DAYS = 2         # 저지속(단발 버스트) 상한
NOISE_MIN_COUNT = 5        # 노이즈 후보 최소 볼륨
NOISE_AVG_IMPORTANCE = 3.0 # 노이즈 후보 평균 중요도 상한(미만)
SPARSE_DAY_MAX = 1         # 표본 빈약일(수집 ≤1건 = 전소스 실패 의심, 이슈 #13)


def target_iso_week(now: datetime.datetime) -> tuple:
    """막 끝난(=이번 일요일에 닫히는) ISO 주차를 산정한다.

    anchor = now - 2일. 금요일(anchor)과 그 주 일요일은 같은 ISO 주(월~일)에 속하므로
    일요일 04:40 정시 실행은 그 주(W##)를 가리킨다. 늦게 깨어나도 자가치유:
      Sun  on-time : anchor=Fri → 같은 주
      Mon  +1일    : anchor=Sat → 같은 주
      Tue  +2일    : anchor=Sun → 같은 주
    (Wed 이후 = ~3일 초과 지연 시 다음 주로 넘어가며, 커서가 중복을 막는다.)
    반환: (iso_year, iso_week) — isocalendar의 ISO 주차연도(연말연초 어긋남 대응).
    """
    iso = (now - timedelta(days=2)).isocalendar()
    return iso[0], iso[1]


def parse_week_arg(s: str) -> tuple:
    """'2026-W26' → (2026, 26). 형식 오류 시 ValueError."""
    m = re.fullmatch(r"(\d{4})-W(\d{1,2})", s.strip())
    if not m:
        raise ValueError(f"--week 형식은 YYYY-W## 이어야 함: {s}")
    return int(m.group(1)), int(m.group(2))


def iso_week_bounds(iso_year: int, iso_week: int) -> tuple:
    """ISO 주차의 월요일·일요일 date를 반환(date.fromisocalendar, Py3.8+)."""
    monday = datetime.date.fromisocalendar(iso_year, iso_week, 1)
    sunday = datetime.date.fromisocalendar(iso_year, iso_week, 7)
    return monday, sunday


def _day_of(collected_at: str) -> str:
    """collected_at(ISO8601 문자열) → 'YYYY-MM-DD' (앞 10자)."""
    return (collected_at or "")[:10]


def aggregate_week(articles: list, monday: datetime.date, sunday: datetime.date) -> dict:
    """정량 신호 5종을 계산한다(스펙 C). Claude 합성 근거 + 전문 표 재료."""
    # 1) 일자별 강도 추이 (월~일 7행 고정)
    days = [(monday + timedelta(days=i)).isoformat() for i in range(7)]
    by_day = defaultdict(list)
    for a in articles:
        by_day[_day_of(a.get("collected_at"))].append(a)
    daily_intensity = []
    for d in days:
        items = by_day.get(d, [])
        imps = [int(x.get("importance", 0) or 0) for x in items]
        daily_intensity.append({
            "date": d,
            "count": len(items),
            "high_count": sum(1 for v in imps if v >= HIGH_IMPORTANCE),
            "avg_importance": round(sum(imps) / len(imps), 2) if imps else 0.0,
            "sparse": len(items) <= SPARSE_DAY_MAX,  # 데이터 품질 경고(전소스 실패 의심)
        })
    total = len(articles)
    peak = max(daily_intensity, key=lambda r: r["count"]) if daily_intensity else None
    sparse_days = [r["date"] for r in daily_intensity if r["sparse"]]

    # 2) 태그 빈도 × 지속성
    tag_total = defaultdict(int)
    tag_days = defaultdict(set)
    for a in articles:
        d = _day_of(a.get("collected_at"))
        for t in (a.get("tags") or []):
            key = str(t).lower().strip()
            if key:
                tag_total[key] += 1
                tag_days[key].add(d)
    tags = []
    for k in tag_total:
        span = len(tag_days[k])
        tags.append({
            "tag": k, "count": tag_total[k], "day_span": span,
            "persistent": span >= PERSIST_DAYS,                       # 지속 줄기
            "burst": tag_total[k] >= PERSIST_DAYS and span <= BURST_MAX_DAYS,  # 단발 버스트
        })
    tags.sort(key=lambda x: (x["day_span"], x["count"]), reverse=True)
    persistent_stems = [t for t in tags if t["persistent"]]
    bursts = [t for t in tags if t["burst"]]

    # 3) 소스 신호 품질
    src_count = defaultdict(int)
    src_imp = defaultdict(list)
    for a in articles:
        s = a.get("source", "?")
        src_count[s] += 1
        src_imp[s].append(int(a.get("importance", 0) or 0))
    sources = []
    for s in src_count:
        avg = round(sum(src_imp[s]) / len(src_imp[s]), 2) if src_imp[s] else 0.0
        sources.append({
            "source": s, "count": src_count[s], "avg_importance": avg,
            "noise_candidate": src_count[s] >= NOISE_MIN_COUNT and avg < NOISE_AVG_IMPORTANCE,
        })
    sources.sort(key=lambda x: x["count"], reverse=True)
    noise_sources = [s for s in sources if s["noise_candidate"]]

    # 4) 공방 관련 픽 (relevance >= 7)
    workshop_picks = sorted(
        [a for a in articles if int(a.get("relevance", 0) or 0) >= WORKSHOP_RELEVANCE],
        key=lambda x: (int(x.get("relevance", 0) or 0), int(x.get("importance", 0) or 0)),
        reverse=True,
    )

    return {
        "total": total,
        "daily_intensity": daily_intensity,
        "peak_day": peak,
        "sparse_days": sparse_days,
        "tags": tags,
        "persistent_stems": persistent_stems,
        "bursts": bursts,
        "sources": sources,
        "noise_sources": noise_sources,
        "workshop_picks": workshop_picks,
    }


def collect_daily_ideas(reports_dir: str, monday: datetime.date, sunday: datetime.date) -> list:
    """7개 일간 .md의 '## 상상공방에 적용할 수 있는 아이디어' 불릿을 추출·빈도 집계.

    반환: [{"text": str, "days": int}, ...] — 여러 날 반복(days>=2)을 우선(=즉시 착수),
          1회성은 하위. 파일 없음/섹션 없음은 조용히 skip.
    """
    section = "상상공방에 적용할 수 있는 아이디어"
    norm_count = defaultdict(int)
    norm_text = {}
    n = (sunday - monday).days + 1
    for i in range(n):
        d = monday + timedelta(days=i)
        path = f"{reports_dir}/{d.year:04d}/{d.month:02d}/{d.day:02d}.md"
        try:
            md = open(path, encoding="utf-8").read()
        except OSError:
            continue
        bullets = _extract_section_bullets(md, section)
        seen = set()
        for b in bullets:
            key = re.sub(r"\s+", " ", b.lower()).strip()
            if not key or key == "_해당 없음_" or key in seen:
                continue
            seen.add(key)
            norm_count[key] += 1
            norm_text.setdefault(key, b)
    out = [{"text": norm_text[k], "days": norm_count[k]} for k in norm_count]
    out.sort(key=lambda x: x["days"], reverse=True)
    return out


def _extract_section_bullets(md: str, title: str) -> list:
    """'## {title}' 헤더 ~ 다음 '## ' 사이의 '- ' 불릿 텍스트 리스트."""
    lines = md.splitlines()
    out, capture = [], False
    for ln in lines:
        if ln.startswith("## "):
            capture = (ln[3:].strip() == title)
            continue
        if capture and ln.lstrip().startswith("- "):
            out.append(ln.lstrip()[2:].strip())
    return out


def _norm_title(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").lower()).strip()


def select_top_articles(articles: list, limit: int = 35) -> list:
    """제목 정규화 중복제거 후 importance desc, relevance desc 상위 N."""
    best = {}
    for a in articles:
        k = _norm_title(a.get("title"))
        cur = best.get(k)
        if cur is None or int(a.get("importance", 0) or 0) > int(cur.get("importance", 0) or 0):
            best[k] = a
    uniq = list(best.values())
    uniq.sort(key=lambda x: (int(x.get("importance", 0) or 0), int(x.get("relevance", 0) or 0)),
              reverse=True)
    return uniq[:limit]


def run_weekly(args) -> int:
    """주간보고 엔진 진입점. 트리거→집계→합성→전문 저장→커서 갱신. 종료코드 반환.

    ★ Discord 전송은 FEAT-10이 10번 단계(hook)에 추가한다. FEAT-09는 전송하지 않는다.
    """
    cfg = load_config()
    secrets = load_secrets()
    profile, _is_default = load_operator_profile(
        cfg.get("paths", {}).get("operator_profile", "config/operator_profile.md"))
    conn = init_db(cfg["paths"]["db"])
    now = datetime.datetime.now()

    if getattr(args, "week", None):
        iso_year, iso_week = parse_week_arg(args.week)
        forced = True
    else:
        iso_year, iso_week = target_iso_week(now)
        forced = False
    week_key = f"{iso_year}-W{iso_week:02d}"

    if not forced and get_meta(conn, "last_weekly_iso_week") == week_key:
        logging.info("주간보고 skip — 이미 생성됨: %s", week_key)
        return 0

    monday, sunday = iso_week_bounds(iso_year, iso_week)
    articles = get_articles_by_range(conn, f"{monday}T00:00:00", f"{sunday}T23:59:59")
    signals = aggregate_week(articles, monday, sunday)
    ideas = collect_daily_ideas(cfg["paths"]["reports_dir"], monday, sunday)
    top = select_top_articles(articles, 35)

    claude = cfg.get("claude", {})
    result = synthesize_weekly(
        signals, ideas, top, profile,
        secrets.get("ANTHROPIC_API_KEY"),
        claude.get("model", "claude-sonnet-4-6"),
        claude.get("max_tokens", 16000),
        force_fallback=getattr(args, "force_fallback", False),
    )
    md = build_weekly_report(week_key, monday, sunday, signals, ideas, top,
                             result["synthesis"], result["mode"])
    path = save_bucket_report(cfg["paths"]["reports_dir"], iso_year, "weekly",
                              f"W{iso_week:02d}", md)

    # ── (FEAT-10 hook) Discord 다이제스트 전송 자리. FEAT-09에서는 비워 둔다. ──

    set_meta(conn, "last_weekly_iso_week", week_key)
    logging.info("주간보고 생성: %s → %s (mode=%s)", week_key, path, result["mode"])
    return 0
