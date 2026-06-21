"""AI-Morning-Brief CLI 진입점 (FEAT-01)

사용법:
    python main.py --help
    python main.py --test
    python main.py --dry-run --no-discord
    python main.py --from 2026-06-20 --to 2026-06-21
    python main.py --check-sources
"""

import argparse
import logging
import sys


def build_parser() -> argparse.ArgumentParser:
    """CLI 옵션 파서를 생성한다."""
    p = argparse.ArgumentParser(
        description="AI-Morning-Brief 파이프라인",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""예시:
  python main.py --test                  # 파이프라인 테스트 실행
  python main.py --dry-run               # 실제 저장/전송 없이 실행 계획·미리보기만 출력
  python main.py --no-discord            # Discord 전송 생략
  python main.py --force-fallback        # Claude 분석을 강제로 fallback(기본 요약) 모드로 실행
  python main.py --from 2026-06-20       # 특정 날짜부터 수집
  python main.py --check-sources         # RSS 소스 접근성 진단만 실행
""",
    )
    p.add_argument(
        "--test",
        action="store_true",
        help="파이프라인 전체를 테스트 모드로 실행 (운영 데이터 비변경)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="실제 저장/전송 없이 실행 계획과 리포트 미리보기만 출력 (DB·raw·리포트·Discord 무변경)",
    )
    p.add_argument(
        "--no-discord",
        action="store_true",
        help="Discord 웹훅 전송을 생략하고 로컬 파일만 저장",
    )
    p.add_argument(
        "--force-fallback",
        action="store_true",
        help="Claude 분석을 강제로 fallback(기본 요약) 모드로 실행 (Claude 연동 실패 검증용)",
    )
    p.add_argument(
        "--from",
        dest="from_dt",
        default=None,
        metavar="YYYY-MM-DD",
        help="수집 시작 날짜 (기본: lookback_hours 기준 자동 계산)",
    )
    p.add_argument(
        "--to",
        dest="to_dt",
        default=None,
        metavar="YYYY-MM-DD",
        help="수집 종료 날짜 (기본: 현재 시각)",
    )
    p.add_argument(
        "--check-sources",
        action="store_true",
        help="RSS 소스 접근성 진단만 실행하고 종료",
    )
    return p


def main() -> int:
    """CLI 진입점. argparse로 옵션을 파싱하고 pipeline.run(args)에 위임한다."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    args = build_parser().parse_args()

    try:
        from src.pipeline import run  # type: ignore[import]
    except ImportError:
        # FEAT-07(파이프라인 구현) 전까지는 옵션 파싱만 확인하고 종료
        print("pipeline 미구현 (FEAT-07 예정). 파싱된 옵션:", vars(args))
        return 0

    return run(args)


if __name__ == "__main__":
    sys.exit(main())
