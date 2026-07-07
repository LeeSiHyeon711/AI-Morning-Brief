"""cloud/entrypoint.py 단위테스트 — GCS 호출(download_db/upload_dir/upload_db)과
subprocess.run을 전부 모킹한다(hermetic). 실제 GCS·실제 main.py 실행 없음.
"""

from unittest.mock import MagicMock, patch

import pytest

from cloud import entrypoint
from cloud.gcs_sync import UploadConflictError
from cloud.github_publish import GithubPublishError


@pytest.fixture(autouse=True)
def _base_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GCS_BUCKET", "test-bucket")
    monkeypatch.setenv("CLOUD_RUN_TMP_ROOT", str(tmp_path))
    yield


def _patched(**overrides):
    """entrypoint 모듈이 참조하는 GCS/GitHub 함수 + subprocess.run을 한번에 패치."""
    defaults = dict(
        download_db=MagicMock(return_value=42),
        upload_dir=MagicMock(return_value=[]),
        upload_db=MagicMock(return_value=None),
        publish_reports_dir=MagicMock(return_value=[]),
        subprocess_run=MagicMock(return_value=MagicMock(returncode=0)),
    )
    defaults.update(overrides)
    return defaults


def _run_with_patches(p, argv):
    with patch.object(entrypoint, "download_db", p["download_db"]), \
         patch.object(entrypoint, "upload_dir", p["upload_dir"]), \
         patch.object(entrypoint, "upload_db", p["upload_db"]), \
         patch.object(entrypoint, "publish_reports_dir", p["publish_reports_dir"]), \
         patch.object(entrypoint.subprocess, "run", p["subprocess_run"]):
        return entrypoint.main(argv)


class TestSuccessPath:
    def test_uploads_raw_and_db_on_success(self, tmp_path):
        p = _patched()
        code = _run_with_patches(p, [])

        assert code == 0
        p["download_db"].assert_called_once_with("test-bucket", "data/morning_brief.db", str(tmp_path / "morning_brief.db"))
        p["upload_dir"].assert_called_once_with("test-bucket", "data/raw", str(tmp_path / "data" / "raw"))
        p["upload_db"].assert_called_once_with(
            "test-bucket", "data/morning_brief.db", str(tmp_path / "morning_brief.db"), expected_generation=42
        )

    def test_forwards_args_and_env_paths_to_subprocess(self, tmp_path):
        p = _patched()
        _run_with_patches(p, ["--weekly"])

        args, kwargs = p["subprocess_run"].call_args
        cmd = args[0]
        assert cmd[-1] == "--weekly"
        assert str(entrypoint.REPO_ROOT / "main.py") in cmd

        env = kwargs["env"]
        assert env["MORNINGBRIEF_DB"] == str(tmp_path / "morning_brief.db")
        assert env["MORNINGBRIEF_RAW_DIR"] == str(tmp_path / "data" / "raw")
        assert env["MORNINGBRIEF_REPORTS_DIR"] == str(tmp_path / "reports")


class TestFailurePath:
    def test_pipeline_failure_skips_upload_and_propagates_exit_code(self):
        p = _patched(subprocess_run=MagicMock(return_value=MagicMock(returncode=7)))
        code = _run_with_patches(p, [])

        assert code == 7
        p["upload_dir"].assert_not_called()
        p["upload_db"].assert_not_called()
        p["publish_reports_dir"].assert_not_called()


class TestUploadConflict:
    def test_db_conflict_returns_1_but_raw_already_uploaded(self):
        p = _patched(upload_db=MagicMock(side_effect=UploadConflictError("conflict")))
        code = _run_with_patches(p, [])

        assert code == 1
        p["upload_dir"].assert_called_once()  # raw는 DB 충돌과 무관하게 이미 올라감
        p["publish_reports_dir"].assert_not_called()  # DB 충돌 시 리포트 업로드까지 가지 않음


class TestMissingEnv:
    def test_missing_bucket_env_raises(self, monkeypatch):
        monkeypatch.delenv("GCS_BUCKET", raising=False)
        with pytest.raises(RuntimeError):
            entrypoint.main([])


class TestGithubPublish:
    def test_skipped_when_repo_or_token_missing(self, monkeypatch):
        monkeypatch.delenv("GITHUB_REPO", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        p = _patched()

        code = _run_with_patches(p, [])

        assert code == 0
        p["publish_reports_dir"].assert_not_called()

    def test_called_with_reports_dir_when_configured(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GITHUB_REPO", "owner/repo")
        monkeypatch.setenv("GITHUB_TOKEN", "dummy-token")
        p = _patched()

        code = _run_with_patches(p, [])

        assert code == 0
        p["publish_reports_dir"].assert_called_once_with(
            str(tmp_path / "reports"), "owner/repo", "dummy-token"
        )

    def test_publish_failure_does_not_fail_whole_run(self, monkeypatch):
        monkeypatch.setenv("GITHUB_REPO", "owner/repo")
        monkeypatch.setenv("GITHUB_TOKEN", "dummy-token")
        p = _patched(publish_reports_dir=MagicMock(side_effect=GithubPublishError("boom")))

        code = _run_with_patches(p, [])

        assert code == 0  # DB/raw는 이미 반영됐으므로 GitHub 실패로 전체를 실패시키지 않음
