"""GCS 동기화 헬퍼 — Cloud Run Job 실행 중 DB/raw JSON을 GCS와 주고받는다.

설계 근거: 03-설계/설계서.md "(인프라 이관) Cloud Run 마이그레이션 설계" N-D절.
- DB: 다운로드 시점의 generation을 기억해뒀다가, 재업로드 시 `if_generation_match`로
  그 사이 다른 실행이 먼저 쓰지 않았는지 검증한다(compare-and-swap). 겹침 실행이 있었다면
  조용히 덮어쓰는 대신 UploadConflictError를 던진다.
- raw JSON: 쓰기 전용이라 다운로드 없이 그대로 올린다(다른 실행과 충돌할 여지가 없음).
"""

from __future__ import annotations

import logging
from pathlib import Path

from google.api_core.exceptions import PreconditionFailed
from google.cloud import storage

logger = logging.getLogger(__name__)


class UploadConflictError(RuntimeError):
    """DB 재업로드 시 GCS의 generation이 다운로드 시점과 달라졌을 때(겹침 실행 감지)."""


def _client() -> storage.Client:
    return storage.Client()


def download_db(bucket_name: str, object_path: str, dest_path: str) -> int:
    """GCS의 DB 객체를 dest_path로 내려받고, 그 시점의 generation을 반환한다.

    반환값은 upload_db()의 expected_generation 인자로 그대로 넘겨 compare-and-swap에 쓴다.
    객체가 아직 없으면(버킷에 DB가 없는 최초 상태) generation 0을 반환한다 — GCS의
    `if_generation_match=0`은 "그 객체가 존재하지 않을 때만 성공"을 뜻하므로, 이 0을
    그대로 upload_db에 넘기면 자연스럽게 "최초 생성" 프리컨디션이 된다.
    """
    client = _client()
    blob = client.bucket(bucket_name).blob(object_path)

    Path(dest_path).parent.mkdir(parents=True, exist_ok=True)

    if not blob.exists():
        logger.warning(
            "gs://%s/%s 객체 없음 — 최초 실행으로 간주(generation=0)", bucket_name, object_path
        )
        return 0

    blob.reload()
    generation = blob.generation
    blob.download_to_filename(dest_path)
    logger.info(
        "DB 다운로드 완료: gs://%s/%s (generation=%s) -> %s",
        bucket_name, object_path, generation, dest_path,
    )
    return generation


def upload_db(bucket_name: str, object_path: str, src_path: str, expected_generation: int) -> None:
    """다운로드 시점(expected_generation) 그대로일 때만 GCS에 덮어쓴다.

    그 사이 다른 실행이 먼저 업로드해 generation이 바뀌었다면 GCS가 412(Precondition
    Failed)를 반환하고, 이를 UploadConflictError로 감싸 올린다 — 겹침 실행을 조용한
    데이터 유실이 아니라 즉시 드러나는 에러로 바꾸는 것이 이 함수의 핵심 목적이다.
    """
    client = _client()
    blob = client.bucket(bucket_name).blob(object_path)

    try:
        blob.upload_from_filename(src_path, if_generation_match=expected_generation)
    except PreconditionFailed as exc:
        raise UploadConflictError(
            f"gs://{bucket_name}/{object_path} 가 다운로드 시점"
            f"(generation={expected_generation}) 이후 다른 실행에 의해 변경됨 — "
            "겹침 실행 의심, 이번 실행분은 업로드하지 않음"
        ) from exc

    logger.info("DB 업로드 완료: %s -> gs://%s/%s", src_path, bucket_name, object_path)


def upload_dir(bucket_name: str, prefix: str, local_dir: str) -> list[str]:
    """local_dir 아래 모든 파일을 gs://bucket/prefix/<상대경로>로 올린다.

    쓰기 전용(다운로드 없음)이라 다른 실행과 경합할 여지가 없다. local_dir이 없으면
    (그날 raw 산출물이 없는 경우) 빈 목록을 반환하고 조용히 넘어간다.
    반환값: 업로드된 GCS 객체 경로 목록.
    """
    local_root = Path(local_dir)

    if not local_root.is_dir():
        logger.info("업로드할 로컬 디렉터리 없음: %s (스킵)", local_dir)
        return []

    bucket = _client().bucket(bucket_name)
    uploaded: list[str] = []
    for path in sorted(local_root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(local_root).as_posix()
        object_path = f"{prefix.rstrip('/')}/{rel}"
        bucket.blob(object_path).upload_from_filename(str(path))
        uploaded.append(object_path)

    logger.info(
        "raw 업로드 완료: %s -> gs://%s/%s (%d개 파일)",
        local_dir, bucket_name, prefix, len(uploaded),
    )
    return uploaded
