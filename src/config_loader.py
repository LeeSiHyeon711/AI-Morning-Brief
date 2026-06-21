"""설정·소스·비밀값·운영자 프로필 로더 (FEAT-01)"""

import os
import yaml
import logging

# 운영자 프로필 기본값 — operator_profile.md 부재 시 사용
DEFAULT_PROFILE: str = """# 운영자 프로필 — IT상상공방 (기본값)

## 직무 / 역할
- 소프트웨어 개발자 / 프로덕트 매니저

## 주요 관심 분야
- AI·머신러닝
- 기술·스타트업
- 국내외 경제 동향
- 클라우드·인프라

## 관심 키워드
- Claude, Anthropic, OpenAI
- Python, 쿠버네티스
- 스타트업 트렌드, 시리즈 투자

## 브리핑 스타일 선호
- 분량: 소스당 3~5줄 핵심 요약
- 형식: 불릿 포인트, 실용적 시사점 포함
- 언어 수준: 전문 용어 허용
- 분량: 전체 1,000자 이내

## 기타 참고사항
- 아침 브리핑 용도 (출근 전 5분 이내 소화 가능한 분량)
- 해외 뉴스는 한국어로 번역해서 정리
"""

# 경로 env override 매핑
# 테스트가 운영 데이터를 건드리지 않도록 환경변수로 경로를 격리할 수 있음
_PATH_ENV = {
    "db": "MORNINGBRIEF_DB",
    "raw_dir": "MORNINGBRIEF_RAW_DIR",
    "reports_dir": "MORNINGBRIEF_REPORTS_DIR",
}


def _apply_path_overrides(cfg: dict) -> dict:
    """config dict의 paths 섹션에 환경변수 override를 적용한다."""
    paths = cfg.setdefault("paths", {})
    for key, env in _PATH_ENV.items():
        val = os.environ.get(env)
        if val:
            paths[key] = val
            logging.info("경로 override: %s = %s (env %s)", key, val, env)
    return cfg


def load_config(path: str = "config/config.yaml") -> dict:
    """config.yaml을 읽어 env override가 적용된 dict를 반환한다.

    Args:
        path: config.yaml 경로 (기본값: config/config.yaml)

    Returns:
        설정 dict (paths 섹션에 env override 적용됨)

    Raises:
        FileNotFoundError: config.yaml 파일이 없을 때 (설정은 필수)
    """
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    return _apply_path_overrides(cfg)


def load_sources(path: str = "config/sources.yaml") -> list:
    """소스 설정을 읽어 리스트로 반환한다.

    sources.yaml 우선. 없으면 sources.example.yaml fallback + WARNING.
    둘 다 없으면 빈 리스트 + ERROR 로그.

    Args:
        path: sources.yaml 경로 (기본값: config/sources.yaml)

    Returns:
        소스 dict 리스트. enabled 필드가 없으면 True로 기본 설정됨.
    """
    example_path = os.path.join(os.path.dirname(path), "sources.example.yaml")
    candidates = [path, example_path]

    for i, p in enumerate(candidates):
        if os.path.exists(p):
            if i > 0:
                logging.warning(
                    "sources.yaml 없음 — %s 사용. "
                    "cp config/sources.example.yaml config/sources.yaml 권장",
                    p,
                )
            with open(p, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            srcs = data.get("sources", []) or []
            for s in srcs:
                s.setdefault("enabled", True)
            return srcs

    logging.error("소스 설정 파일이 없습니다 (%s)", candidates)
    return []


def load_secrets() -> dict:
    """.env 파일을 읽어 os.environ을 보강하고, 필요한 비밀값 dict를 반환한다.

    .env 파일이 없으면 기존 환경변수만 참조한다.
    키가 없으면 None을 반환한다.

    Returns:
        {"ANTHROPIC_API_KEY": str|None, "DISCORD_WEBHOOK_URL": str|None}
    """
    if os.path.exists(".env"):
        with open(".env", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

    return {
        "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY"),
        "DISCORD_WEBHOOK_URL": os.environ.get("DISCORD_WEBHOOK_URL"),
    }


def load_operator_profile(path: str = "config/operator_profile.md") -> tuple:
    """운영자 프로필 마크다운을 읽어 (text, is_default) 튜플로 반환한다.

    파일이 없거나 비어 있으면 DEFAULT_PROFILE을 사용하고 WARNING을 남긴다.

    Args:
        path: operator_profile.md 경로 (기본값: config/operator_profile.md)

    Returns:
        (profile_text: str, is_default: bool)
        is_default=True 이면 기본 프로필을 사용 중임을 의미
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read().strip()
        if not text:
            logging.warning("operator_profile 비어있음 (%s) — 기본 프로필 사용", path)
            return DEFAULT_PROFILE, True
        return text, False
    except FileNotFoundError:
        logging.warning("operator_profile 없음 (%s) — 기본 프로필 사용", path)
        return DEFAULT_PROFILE, True
