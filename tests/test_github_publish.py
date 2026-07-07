"""cloud/github_publish.py 단위테스트 — requests.get/put을 전부 모킹한다(hermetic).
실제 GitHub API 호출·실제 커밋 없음.
"""

from unittest.mock import MagicMock, patch

import pytest

from cloud import github_publish


def _resp(status_code, json_body=None, text=""):
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = json_body or {}
    r.text = text
    return r


class TestPublishFile:
    def test_creates_new_file_without_sha(self, tmp_path):
        local = tmp_path / "07.md"
        local.write_text("# 오늘의 리포트")

        get_resp = _resp(404)  # 원격에 아직 없음
        put_resp = _resp(201, {"content": {"html_url": "https://github.com/x/y/blob/main/reports/2026/07/07.md"}})

        with patch.object(github_publish.requests, "get", return_value=get_resp) as mock_get, \
             patch.object(github_publish.requests, "put", return_value=put_resp) as mock_put:
            url = github_publish.publish_file(str(local), "owner/repo", "reports/2026/07/07.md", "tok")

        assert url == "https://github.com/x/y/blob/main/reports/2026/07/07.md"
        mock_get.assert_called_once()
        put_kwargs = mock_put.call_args.kwargs
        assert "sha" not in put_kwargs["json"]
        assert put_kwargs["json"]["branch"] == "main"

    def test_updates_existing_file_with_sha(self, tmp_path):
        local = tmp_path / "07.md"
        local.write_text("# 갱신된 리포트")

        get_resp = _resp(200, {"sha": "abc123"})
        put_resp = _resp(200, {"content": {"html_url": "https://github.com/x/y/blob/main/reports/2026/07/07.md"}})

        with patch.object(github_publish.requests, "get", return_value=get_resp), \
             patch.object(github_publish.requests, "put", return_value=put_resp) as mock_put:
            github_publish.publish_file(str(local), "owner/repo", "reports/2026/07/07.md", "tok")

        put_kwargs = mock_put.call_args.kwargs
        assert put_kwargs["json"]["sha"] == "abc123"

    def test_get_error_raises(self, tmp_path):
        local = tmp_path / "07.md"
        local.write_text("x")
        get_resp = _resp(500, text="server error")

        with patch.object(github_publish.requests, "get", return_value=get_resp):
            with pytest.raises(github_publish.GithubPublishError):
                github_publish.publish_file(str(local), "owner/repo", "reports/2026/07/07.md", "tok")

    def test_put_error_raises(self, tmp_path):
        local = tmp_path / "07.md"
        local.write_text("x")
        get_resp = _resp(404)
        put_resp = _resp(422, text="validation failed")

        with patch.object(github_publish.requests, "get", return_value=get_resp), \
             patch.object(github_publish.requests, "put", return_value=put_resp):
            with pytest.raises(github_publish.GithubPublishError):
                github_publish.publish_file(str(local), "owner/repo", "reports/2026/07/07.md", "tok")


class TestPublishReportsDir:
    def test_uploads_all_files_with_base_path_prefix(self, tmp_path):
        (tmp_path / "2026" / "07").mkdir(parents=True)
        (tmp_path / "2026" / "07" / "07.md").write_text("daily")

        get_resp = _resp(404)
        put_resp = _resp(201, {"content": {"html_url": "https://example.com/x"}})

        with patch.object(github_publish.requests, "get", return_value=get_resp), \
             patch.object(github_publish.requests, "put", return_value=put_resp) as mock_put:
            urls = github_publish.publish_reports_dir(str(tmp_path), "owner/repo", "tok")

        assert urls == ["https://example.com/x"]
        called_url = mock_put.call_args.args[0]
        assert called_url.endswith("/repos/owner/repo/contents/reports/2026/07/07.md")

    def test_missing_local_dir_returns_empty(self, tmp_path):
        with patch.object(github_publish.requests, "get") as mock_get:
            urls = github_publish.publish_reports_dir(str(tmp_path / "no-such-dir"), "owner/repo", "tok")

        assert urls == []
        mock_get.assert_not_called()
