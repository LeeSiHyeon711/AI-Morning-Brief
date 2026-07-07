"""Cloud Run Job 컨테이너 실행 진입점.

흐름(설계서 03-설계/설계서.md "(인프라 이관) Cloud Run 마이그레이션 설계" N-C절):
  1. GCS에서 DB를 /tmp로 다운로드(그 시점 generation 기억)
  2. 기존 main.py를 서브프로세스로 그대로 호출 — 파이프라인 로직(pipeline/collector/
     analyzer/reporter/notifier/storage/weekly/monthly)은 단 한 줄도 건드리지 않는다.
     경로만 MORNINGBRIEF_DB/RAW_DIR/REPORTS_DIR env override(FEAT-01)로 /tmp 하위를 가리킨다.
  3. 성공(exit 0) 시에만 raw JSON + DB를 GCS로 재업로드(DB는 generation 프리컨디션)
  4. 실패 시 DB 업로드를 생략한다 — meta.last_success_at이 갱신되지 않았으므로 다음
     실행이 기존 catch-up 로직으로 알아서 복구한다(기존 실패복구 불변식과 동형).
  5. (성공 시) 생성된 리포트 파일을 GitHub 레포에 커밋한다(cloud/github_publish.py).
     GITHUB_REPO/GITHUB_TOKEN이 없거나 GitHub API가 실패해도 이 단계는 파이프라인
     전체를 실패시키지 않는다 — DB/raw는 이미 GCS에 안전하게 반영된 뒤라, Discord
     전송 실패를 다루는 기존 방식과 동일하게 "핵심 상태는 지키고 부가 단계만 경고"한다.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cloud.gcs_sync import UploadConflictError, download_db, upload_db, upload_dir  # noqa: E402
from cloud.github_publish import GithubPublishError, publish_reports_dir  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("cloud.entrypoint")


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"필수 환경변수 {name} 없음")
    return value


def main(argv: list[str] | None = None) -> int:
    """Cloud Run Job 실행 1회의 전체 흐름. 반환값을 프로세스 종료 코드로 그대로 쓴다.

    argv는 Cloud Run Job에 설정된 컨테이너 인자(daily=없음/weekly=--weekly/monthly=--monthly)이며,
    main.py에 그대로 전달한다. 테스트에서 주입할 수 있도록 None이면 sys.argv[1:]를 쓴다.
    """
    bucket = _require_env("GCS_BUCKET")
    db_object_path = os.environ.get("GCS_DB_OBJECT_PATH", "data/morning_brief.db")
    raw_prefix = os.environ.get("GCS_RAW_PREFIX", "data/raw")

    # CLOUD_RUN_TMP_ROOT는 테스트가 실제 /tmp를 건드리지 않도록 하는 override용(운영은 기본값 /tmp 사용)
    tmp_root = Path(os.environ.get("CLOUD_RUN_TMP_ROOT", "/tmp"))
    db_local_path = tmp_root / "morning_brief.db"
    raw_local_dir = tmp_root / "data" / "raw"
    reports_local_dir = tmp_root / "reports"

    logger.info("=== Cloud Run entrypoint 시작 (bucket=%s) ===", bucket)

    generation = download_db(bucket, db_object_path, str(db_local_path))

    env = os.environ.copy()
    env["MORNINGBRIEF_DB"] = str(db_local_path)
    env["MORNINGBRIEF_RAW_DIR"] = str(raw_local_dir)
    env["MORNINGBRIEF_REPORTS_DIR"] = str(reports_local_dir)

    forwarded_args = sys.argv[1:] if argv is None else argv
    cmd = [sys.executable, str(REPO_ROOT / "main.py"), *forwarded_args]
    logger.info("main.py 실행: %s", " ".join(cmd))

    result = subprocess.run(cmd, cwd=str(REPO_ROOT), env=env)

    if result.returncode != 0:
        logger.error(
            "main.py 실패(exit=%s) — DB 업로드 생략(부분 상태 미반영). "
            "meta.last_success_at 미갱신이므로 다음 실행이 catch-up으로 복구.",
            result.returncode,
        )
        return result.returncode

    upload_dir(bucket, raw_prefix, str(raw_local_dir))

    try:
        upload_db(bucket, db_object_path, str(db_local_path), expected_generation=generation)
    except UploadConflictError:
        logger.error(
            "DB 업로드 충돌 — 다운로드 시점 이후 다른 실행이 먼저 씀. "
            "raw JSON은 이미 올라갔으나 DB 상태는 반영되지 않음(겹침 실행 의심, 조사 필요)."
        )
        return 1

    github_repo = os.environ.get("GITHUB_REPO")
    github_token = os.environ.get("GITHUB_TOKEN")
    if github_repo and github_token:
        try:
            publish_reports_dir(str(reports_local_dir), github_repo, github_token)
        except GithubPublishError as exc:
            logger.error(
                "GitHub 리포트 업로드 실패(DB/raw는 이미 GCS에 반영됨, 계속 진행): %s", exc
            )
    else:
        logger.warning("GITHUB_REPO/GITHUB_TOKEN 미설정 — 리포트 GitHub 업로드 생략")

    logger.info("=== Cloud Run entrypoint 완료 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
