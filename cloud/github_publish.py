"""GitHub Contents API로 리포트 파일을 레포에 커밋한다.

설계 근거: 03-설계/설계서.md "(인프라 이관) Cloud Run 마이그레이션 설계" N-H절.
- 리포트 파일 1~수개만 다루는 용도라 git clone/push보다 Contents API가 더 단순하다
  (추가 의존성 없음 — requests만 사용, 컨테이너에 git 바이너리 불요, 레포 히스토리
  전체를 내려받을 필요도 없어 콜드스타트가 가볍다).
- GitHub 레포의 reports/ 경로는 현재 완전히 비어있다(로컬 .gitignore로 한 번도
  커밋된 적 없음) — Contents API는 로컬 git 상태와 무관하게 원격에 직접 커밋하므로
  이 사실은 구현에 영향을 주지 않는다.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

GITHUB_API_ROOT = "https://api.github.com"


class GithubPublishError(RuntimeError):
    """GitHub Contents API 호출이 실패했을 때."""


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _get_existing_sha(repo: str, repo_path: str, token: str, branch: str) -> str | None:
    """repo_path가 이미 존재하면 그 blob sha를(갱신용 프리컨디션), 없으면 None을 반환한다."""
    url = f"{GITHUB_API_ROOT}/repos/{repo}/contents/{repo_path}"
    resp = requests.get(url, headers=_headers(token), params={"ref": branch}, timeout=15)
    if resp.status_code == 200:
        return resp.json()["sha"]
    if resp.status_code == 404:
        return None
    raise GithubPublishError(f"GitHub 파일 조회 실패 {resp.status_code}: {resp.text[:200]}")


def publish_file(
    local_path: str,
    repo: str,
    repo_path: str,
    token: str,
    branch: str = "main",
    message: str | None = None,
) -> str:
    """단일 파일을 Contents API로 생성/갱신한다. 반환값: 커밋된 파일의 html_url."""
    content_b64 = base64.b64encode(Path(local_path).read_bytes()).decode("ascii")
    sha = _get_existing_sha(repo, repo_path, token, branch)

    payload = {
        "message": message or f"chore(report): {repo_path} 자동 갱신 (Cloud Run Job)",
        "content": content_b64,
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha

    url = f"{GITHUB_API_ROOT}/repos/{repo}/contents/{repo_path}"
    resp = requests.put(url, headers=_headers(token), json=payload, timeout=30)
    if resp.status_code not in (200, 201):
        raise GithubPublishError(f"GitHub 커밋 실패 {resp.status_code}: {resp.text[:300]}")

    html_url = resp.json()["content"]["html_url"]
    logger.info("리포트 커밋 완료: %s -> %s", local_path, html_url)
    return html_url


def publish_reports_dir(
    local_dir: str,
    repo: str,
    token: str,
    base_path: str = "reports",
    branch: str = "main",
) -> list[str]:
    """local_dir 아래 모든 리포트 파일을 base_path/<상대경로>로 커밋한다.

    local_dir이 없거나 비어 있으면(이번 실행에서 리포트가 생성되지 않음) 빈 목록을 반환한다.
    반환값: 커밋된 각 파일의 html_url 목록.
    """
    local_root = Path(local_dir)
    if not local_root.is_dir():
        logger.info("업로드할 리포트 디렉터리 없음: %s (스킵)", local_dir)
        return []

    urls: list[str] = []
    for path in sorted(local_root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(local_root).as_posix()
        repo_path = f"{base_path.rstrip('/')}/{rel}"
        urls.append(publish_file(str(path), repo, repo_path, token, branch=branch))

    return urls
