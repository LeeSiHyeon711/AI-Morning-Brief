"""월간 흐름 리포트 신호·필터 엔진 (FEAT-11, #12). 순수 함수만 — I/O·Claude 없음."""
import calendar
import datetime
import re
from collections import defaultdict
from datetime import timedelta

# 주간과 공유하는 헬퍼는 재구현하지 않고 import 재사용 (최소 변경 원칙)
from src.weekly import _day_of, _norm_title, collect_daily_ideas as _collect_daily_ideas

# ── 임계값(월간 스케일). 스케일 변경 시 이 상수만 조정 (집계 로직 불변) ──
HIGH_IMPORTANCE = 8         # 고중요 기준 (importance 0~10 스케일)
WORKSHOP_RELEVANCE = 7      # 공방 픽 기준
PERSIST_DAYS = 8           # 월간 지속 줄기: 서로 다른 8일 이상 등장 (주간은 5)
BURST_MAX_DAYS = 2         # 단발 버스트 상한
NOISE_MIN_COUNT = 15       # 노이즈 소스 후보 최소 볼륨 (월 스케일, 주간 5보다 상향)
NOISE_AVG_IMPORTANCE = 3.0 # 노이즈 소스 평균중요도 상한(미만)
SOURCE_CAP_RATIO = 0.15    # 대표 풀에서 한 소스가 차지할 상한 비율 (#12-6 지배 방지)
TOP_LIMIT = 40             # Claude 합성에 투입할 대표 기사 수 (#12-4 압축)
FIRST_STAGE_MIN_SIGNAL = 1 # 1차 통과 최소 신호: importance 또는 relevance 가 이 값 이상

# 1차 필터(#12-3) 관련성 화이트리스트 — 제목/태그/요약에 하나라도 있으면 AI/개발/에이전트 관련
FIRST_STAGE_KEYWORDS = (
    "ai", "llm", "gpt", "claude", "anthropic", "openai", "gemini", "google ai",
    "mcp", "agent", "에이전트", "codex", "copilot", "cursor", "rag", "vibe",
    "바이브", "코딩", "코드", "developer", "sdk", "api", "model", "모델",
    "automation", "자동화", "prompt", "프롬프트", "eval", "fine-tun", "инференс",
)
# 노이즈 유형 분류(#12-7) — 1차 탈락 기사를 유형별 건수로 집계 (best-effort 규칙)
NOISE_TYPE_KEYWORDS = {
    "정치·사회": ("election", "policy", "regulat", "정치", "선거", "규제", "정책", "safety act"),
    "인프라투자·부동산": ("data center", "데이터센터", "stargate", "investment", "투자", "billion", "campus"),
    "비AI소비자": ("shopping", "dating", "wellness", "쇼핑", "데이팅", "웰니스", "recipe"),
    "단순릴리스노트": ("alpha", "nightly", "changelog only", "no changes", "버전 태그"),
    "홍보성·파트너십": ("partnership", "partner with", "제휴", "도입 사례", "case study", "announces support"),
    "반도체·지정학": ("chip", "asml", "export control", "반도체", "수출 통제", "geopolit"),
    "AI윤리·논평": ("opinion", "essay", "칼럼", "논평", "philosophy", "친구가 아니다"),
}


def target_month(now: datetime.datetime) -> tuple[int, int]:
    """now 기준 '직전 달'(막 끝난 달)을 (year, month)로. 매월 1일 실행 → 전월.

    그 달 어느 날에 실행해도 now.month의 한 달 전을 가리켜, 지연 실행(1~며칠)에도
    같은 대상을 반환한다(자가치유). 커서 last_monthly_ym 가 중복 생성을 막는다.
    """
    y, m = now.year, now.month
    return (y - 1, 12) if m == 1 else (y, m - 1)


def parse_month_arg(s: str) -> tuple[int, int]:
    """'2026-06' → (2026, 6). 형식 오류 시 ValueError."""
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", s.strip())
    if not m:
        raise ValueError(f"--month 형식은 YYYY-MM 이어야 함: {s}")
    y, mo = int(m.group(1)), int(m.group(2))
    if not 1 <= mo <= 12:
        raise ValueError(f"월 범위 오류(1~12): {s}")
    return y, mo


def month_bounds(y: int, m: int) -> tuple[datetime.date, datetime.date]:
    """달력월의 1일·말일 date. (calendar.monthrange 로 말일 계산)"""
    last_day = calendar.monthrange(y, m)[1]
    return datetime.date(y, m, 1), datetime.date(y, m, last_day)


def month_key(y: int, m: int) -> str:
    """커서/파일명용. (커서: 'YYYY-MM', 파일 bucket name: 'M##')"""
    return f"{y:04d}-{m:02d}"


def _pub_or_collect_day(a: dict) -> str | None:
    """경계 판정용 대표 일자: published_at 우선(앞 10자), 없으면 collected_at 폴백."""
    for key in ("published_at", "collected_at"):
        v = (a.get(key) or "")[:10]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            return v
    return None


def partition_by_period(articles: list, first: datetime.date, last: datetime.date) -> tuple[list, list]:
    """월 경계로 (in_period, out_of_period) 분리. #12-1.

    - published_at 이 [first,last] 이면 채택.
    - published_at 범위 밖이면 out_of_period('범위 외 참고 항목').
    - published_at NULL/파싱불가 → collected_at 이 월내이면 채택(폴백), 아니면 out.
    """
    lo, hi = first.isoformat(), last.isoformat()
    in_p, out_p = [], []
    for a in articles:
        d = _pub_or_collect_day(a)
        (in_p if (d is not None and lo <= d <= hi) else out_p).append(a)
    return in_p, out_p


def _text_blob(a: dict) -> str:
    return " ".join([
        str(a.get("title", "")), str(a.get("summary", "")),
        " ".join(a.get("tags") or []),
    ]).lower()


def first_stage_filter(articles: list) -> tuple[list, list]:
    """1차: AI/개발/에이전트 관련성. #12-3.

    통과 조건(OR): ① 키워드 화이트리스트 매칭 ② importance≥MIN 또는 relevance≥MIN.
    반환 (passed, rejected). 2차(공방 적용성)는 코드가 아니라 FEAT-12 Claude 합성이 랭킹으로 수행.
    """
    passed, rejected = [], []
    for a in articles:
        blob = _text_blob(a)
        kw = any(k in blob for k in FIRST_STAGE_KEYWORDS)
        sig = (int(a.get("importance", 0) or 0) >= FIRST_STAGE_MIN_SIGNAL
               or int(a.get("relevance", 0) or 0) >= FIRST_STAGE_MIN_SIGNAL)
        (passed if (kw or sig) else rejected).append(a)
    return passed, rejected


def classify_noise(rejected: list) -> dict:
    """1차 탈락 기사를 노이즈 유형별 건수로 집계. #12-7 상단 명시 재료.

    반환: {"정치·사회": n, ..., "기타": m, "_total": 총건수}. 매칭 안 되면 '기타'.
    """
    counts = defaultdict(int)
    for a in rejected:
        blob = _text_blob(a)
        hit = next((label for label, kws in NOISE_TYPE_KEYWORDS.items()
                    if any(k in blob for k in kws)), "기타")
        counts[hit] += 1
    counts["_total"] = len(rejected)
    return dict(counts)


def source_stats(articles: list) -> list:
    """소스별 (건수·점유율·평균중요도·노이즈여부). #12-5 편중도 표시.
    반환은 count desc 정렬 리스트. 점유율은 0~1 실수(리포트에서 %로 렌더).
    """
    total = len(articles) or 1
    cnt = defaultdict(int)
    imp = defaultdict(list)
    for a in articles:
        s = a.get("source", "?")
        cnt[s] += 1
        imp[s].append(int(a.get("importance", 0) or 0))
    out = []
    for s in cnt:
        avg = round(sum(imp[s]) / len(imp[s]), 2) if imp[s] else 0.0
        out.append({
            "source": s, "count": cnt[s], "share": round(cnt[s] / total, 3),
            "avg_importance": avg,
            "noise_candidate": cnt[s] >= NOISE_MIN_COUNT and avg < NOISE_AVG_IMPORTANCE,
        })
    out.sort(key=lambda x: x["count"], reverse=True)
    return out


def cap_source_bias(articles: list, cap_ratio: float = SOURCE_CAP_RATIO) -> list:
    """소스 지배 방지: 한 소스가 대표 후보의 상한(cap)을 넘으면 초과분을 후순위로 제외. #12-6.

    cap = max(3, round(len(articles) * cap_ratio)). 각 소스 내부는 importance desc 로
    상위 cap 건만 남긴다(초과분은 대표 풀에서 제외 — 전문 부록엔 FEAT-12가 별도 표기).
    """
    cap = max(3, round(len(articles) * cap_ratio))
    by_src = defaultdict(list)
    for a in articles:
        by_src[a.get("source", "?")].append(a)
    kept = []
    for s, items in by_src.items():
        items.sort(key=lambda x: int(x.get("importance", 0) or 0), reverse=True)
        kept.extend(items[:cap])
    return kept


def _cross_source_count(articles: list) -> dict:
    """제목 정규화 키 → 그 사건을 다룬 '고유 소스 수'(신뢰도 라벨 근거). #12-2 입력."""
    by_title = defaultdict(set)
    for a in articles:
        by_title[_norm_title(a.get("title"))].add(a.get("source", "?"))
    return {k: len(v) for k, v in by_title.items()}


def aggregate_month(articles: list, first: datetime.date, last: datetime.date) -> dict:
    """월간 정량 신호. 주간 aggregate_week 를 월 스케일로 확장 + 편중 정규화 태그.

    반환 signals(dict):
      total, span_days, daily_intensity[], peak_day, sparse_days[],
      tags[], persistent_stems[], bursts[],       # 태그 count = 고유 (source,day) 조합 (편중 정규화)
      sources[](=source_stats), noise_sources[],  # #12-5,6
      workshop_picks[], cross_source[]             # #12-2 신뢰도 입력
    """
    span = (last - first).days + 1
    days = [(first + timedelta(days=i)).isoformat() for i in range(span)]
    by_day = defaultdict(list)
    for a in articles:
        by_day[_day_of(a.get("collected_at"))].append(a)
    daily = []
    for d in days:
        items = by_day.get(d, [])
        imps = [int(x.get("importance", 0) or 0) for x in items]
        daily.append({"date": d, "count": len(items),
                      "high_count": sum(1 for v in imps if v >= HIGH_IMPORTANCE),
                      "avg_importance": round(sum(imps) / len(imps), 2) if imps else 0.0,
                      "sparse": len(items) <= 1})
    peak = max(daily, key=lambda r: r["count"]) if daily else None
    sparse_days = [r["date"] for r in daily if r["sparse"]]

    # 태그: count = 고유 (source, day) 조합 수 → 한 소스가 하루 도배해도 1로 정규화 (#12-6)
    tag_pairs = defaultdict(set)   # tag -> {(source, day)}
    tag_days = defaultdict(set)    # tag -> {day}
    for a in articles:
        d = _day_of(a.get("collected_at"))
        s = a.get("source", "?")
        for t in (a.get("tags") or []):
            k = str(t).lower().strip()
            if k:
                tag_pairs[k].add((s, d))
                tag_days[k].add(d)
    tags = []
    for k in tag_pairs:
        span_days = len(tag_days[k])
        cnt = len(tag_pairs[k])
        tags.append({"tag": k, "count": cnt, "day_span": span_days,
                     "persistent": span_days >= PERSIST_DAYS,
                     "burst": cnt >= NOISE_MIN_COUNT // 3 and span_days <= BURST_MAX_DAYS})
    tags.sort(key=lambda x: (x["day_span"], x["count"]), reverse=True)

    srcs = source_stats(articles)
    picks = sorted([a for a in articles if int(a.get("relevance", 0) or 0) >= WORKSHOP_RELEVANCE],
                   key=lambda x: (int(x.get("relevance", 0) or 0), int(x.get("importance", 0) or 0)),
                   reverse=True)
    return {
        "total": len(articles), "span_days": span, "daily_intensity": daily,
        "peak_day": peak, "sparse_days": sparse_days,
        "tags": tags,
        "persistent_stems": [t for t in tags if t["persistent"]],
        "bursts": [t for t in tags if t["burst"]],
        "sources": srcs,
        "noise_sources": [s for s in srcs if s["noise_candidate"]],
        "workshop_picks": picks,
        "cross_source": _cross_source_count(articles),
    }


def select_top_articles_monthly(articles: list, limit: int = TOP_LIMIT) -> list:
    """제목 정규화 중복제거 → 소스 상한(cap_source_bias) → 정렬 상위 limit. #12-4.

    정렬키: importance desc, relevance desc, 교차출처수 desc(신뢰 높은 사건 우선).
    각 반환 항목에 'cross_source_count' 키를 부가(FEAT-12 신뢰도 라벨 근거).
    """
    xsrc = _cross_source_count(articles)
    best = {}
    for a in articles:
        k = _norm_title(a.get("title"))
        cur = best.get(k)
        if cur is None or int(a.get("importance", 0) or 0) > int(cur.get("importance", 0) or 0):
            best[k] = a
    capped = cap_source_bias(list(best.values()), SOURCE_CAP_RATIO)
    for a in capped:
        a2 = a  # dict 참조; 부가 필드만 세팅
        a2["cross_source_count"] = xsrc.get(_norm_title(a.get("title")), 1)
    capped.sort(key=lambda x: (int(x.get("importance", 0) or 0),
                               int(x.get("relevance", 0) or 0),
                               int(x.get("cross_source_count", 1))), reverse=True)
    return capped[:limit]


def collect_monthly_ideas(reports_dir: str, first: datetime.date, last: datetime.date) -> list:
    """월 일자별 일간 .md의 '상상공방 적용 아이디어'를 통합. weekly.collect_daily_ideas 재사용.

    반환: [{"text": str, "days": int}, ...] 반복일수 desc. (파일 없음/섹션 없음은 skip)
    """
    return _collect_daily_ideas(reports_dir, first, last)


# ── 오케스트레이션 (FEAT-12, #12) — run_monthly: 트리거→경계→2단계필터→집계→합성→저장→커서 ──

import logging as _logging  # noqa: E402  (기존 상단 import 유지, 오케스트레이션 전용 별칭)

from src.config_loader import load_config, load_secrets, load_operator_profile  # noqa: E402
from src.storage import init_db, get_articles_by_range, get_meta, set_meta  # noqa: E402
from src.analyzer import synthesize_monthly  # noqa: E402
from src.reporter import build_monthly_report, save_bucket_report  # noqa: E402
from src.notifier import send_discord  # noqa: E402  (FEAT-13)


def run_monthly(args) -> int:
    """월간보고 엔진 진입점. 트리거→경계→2단계필터→집계→합성(신뢰도 라벨)→전문 저장→커서→Discord 전송."""
    cfg = load_config()
    secrets = load_secrets()
    profile, _is_default = load_operator_profile(
        cfg.get("paths", {}).get("operator_profile", "config/operator_profile.md"))
    conn = init_db(cfg["paths"]["db"])
    now = datetime.datetime.now()

    if getattr(args, "month", None):
        y, m = parse_month_arg(args.month)
        forced = True
    else:
        y, m = target_month(now)
        forced = False
    ym = month_key(y, m)

    if not forced and get_meta(conn, "last_monthly_ym") == ym:
        _logging.info("월간보고 skip — 이미 생성됨: %s", ym)
        return 0

    first, last = month_bounds(y, m)
    raw = get_articles_by_range(conn, f"{first}T00:00:00", f"{last}T23:59:59")
    in_p, out_p = partition_by_period(raw, first, last)          # #12-1
    passed, rejected = first_stage_filter(in_p)                  # #12-3
    noise = classify_noise(rejected)                             # #12-7
    signals = aggregate_month(passed, first, last)              # 정량 신호 + 편중(#12-5,6)
    top = select_top_articles_monthly(passed, TOP_LIMIT)        # #12-4 대표 압축
    ideas = collect_monthly_ideas(cfg["paths"]["reports_dir"], first, last)
    basis = {"collected": len(raw), "in_period": len(in_p), "out_of_period": len(out_p),
             "first_stage_passed": len(passed), "noise": noise, "represented": len(top)}

    claude = cfg.get("claude", {})
    result = synthesize_monthly(
        signals, ideas, top, basis, profile,
        secrets.get("ANTHROPIC_API_KEY"),
        claude.get("model", "claude-sonnet-4-6"),
        claude.get("max_tokens", 20000),
        force_fallback=getattr(args, "force_fallback", False),
    )
    md = build_monthly_report(ym, first, last, signals, ideas, top, out_p, basis,
                              result["synthesis"], result["mode"])
    path = save_bucket_report(cfg["paths"]["reports_dir"], y, "monthly", f"M{m:02d}", md)

    if not getattr(args, "no_discord", False):
        from src.notifier import build_monthly_message
        msg = build_monthly_message(ym, first, last, signals, basis,
                                    result["synthesis"], path)
        send_discord(secrets.get("DISCORD_WEBHOOK_URL"), msg)

    set_meta(conn, "last_monthly_ym", ym)
    _logging.info("월간보고 생성: %s → %s (mode=%s)", ym, path, result["mode"])
    return 0
