"""cloud/gcs_sync.py 단위테스트 — 실제 GCS를 호출하지 않고 google-cloud-storage 클라이언트를
전부 모킹한다(hermetic). 외부 네트워크/인증 없이 통과해야 한다.
"""

from unittest.mock import MagicMock, patch

import pytest
from google.api_core.exceptions import PreconditionFailed

from cloud import gcs_sync


def _mock_client(blob: MagicMock) -> MagicMock:
    client = MagicMock()
    client.bucket.return_value.blob.return_value = blob
    return client


class TestDownloadDb:
    def test_downloads_and_returns_generation(self, tmp_path):
        blob = MagicMock()
        blob.exists.return_value = True
        blob.generation = 12345

        dest = tmp_path / "sub" / "morning_brief.db"

        with patch.object(gcs_sync, "_client", return_value=_mock_client(blob)):
            generation = gcs_sync.download_db("test-bucket", "data/morning_brief.db", str(dest))

        assert generation == 12345
        blob.reload.assert_called_once()
        blob.download_to_filename.assert_called_once_with(str(dest))
        assert dest.parent.is_dir()  # 상위 디렉터리를 미리 만들어야 함

    def test_missing_object_returns_zero(self, tmp_path):
        blob = MagicMock()
        blob.exists.return_value = False

        dest = tmp_path / "morning_brief.db"

        with patch.object(gcs_sync, "_client", return_value=_mock_client(blob)):
            generation = gcs_sync.download_db("test-bucket", "data/morning_brief.db", str(dest))

        assert generation == 0
        blob.download_to_filename.assert_not_called()


class TestUploadDb:
    def test_uploads_with_generation_precondition(self, tmp_path):
        blob = MagicMock()
        src = tmp_path / "morning_brief.db"
        src.write_text("dummy")

        with patch.object(gcs_sync, "_client", return_value=_mock_client(blob)):
            gcs_sync.upload_db("test-bucket", "data/morning_brief.db", str(src), expected_generation=12345)

        blob.upload_from_filename.assert_called_once_with(str(src), if_generation_match=12345)

    def test_conflict_raises_upload_conflict_error(self, tmp_path):
        blob = MagicMock()
        blob.upload_from_filename.side_effect = PreconditionFailed("412")
        src = tmp_path / "morning_brief.db"
        src.write_text("dummy")

        with patch.object(gcs_sync, "_client", return_value=_mock_client(blob)):
            with pytest.raises(gcs_sync.UploadConflictError):
                gcs_sync.upload_db("test-bucket", "data/morning_brief.db", str(src), expected_generation=12345)


class TestUploadDir:
    def test_uploads_all_files_with_prefix(self, tmp_path):
        (tmp_path / "2026-07-07").mkdir()
        (tmp_path / "2026-07-07" / "GitHub_Blog.json").write_text("{}")
        (tmp_path / "2026-07-07" / "OpenAI_News.json").write_text("{}")

        blob_factory = MagicMock()
        client = MagicMock()
        client.bucket.return_value.blob = blob_factory

        with patch.object(gcs_sync, "_client", return_value=client):
            uploaded = gcs_sync.upload_dir("test-bucket", "data/raw", str(tmp_path))

        assert sorted(uploaded) == [
            "data/raw/2026-07-07/GitHub_Blog.json",
            "data/raw/2026-07-07/OpenAI_News.json",
        ]
        assert blob_factory.call_count == 2

    def test_missing_local_dir_returns_empty(self, tmp_path):
        client = MagicMock()

        with patch.object(gcs_sync, "_client", return_value=client):
            uploaded = gcs_sync.upload_dir("test-bucket", "data/raw", str(tmp_path / "does-not-exist"))

        assert uploaded == []
        client.bucket.assert_not_called()
