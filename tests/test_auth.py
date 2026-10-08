# ---
# created: 08 October 2026
# author: kaedonkers, Claude Sonnet 5.5
# modified: 08 October 2026
# ---

"""Test token-only authentication and failure handling."""
from unittest.mock import MagicMock, patch

import pytest

from chessmet.download import (
    DATASET_URL,
    TOKENS_URL,
    AuthenticationError,
    ChessMetConfig,
    ChessMetDownloader,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ("EIDC_TOKEN",):
        monkeypatch.delenv(k, raising=False)


def test_token_gives_bearer_header(monkeypatch):
    monkeypatch.setenv("EIDC_TOKEN", " pat_tok123 ")
    s = ChessMetDownloader(ChessMetConfig())._create_session_with_auth()
    assert s.headers["Authorization"] == "Bearer pat_tok123"
    assert s.auth is None


def test_username_and_password_env_are_ignored(monkeypatch):
    monkeypatch.setenv("EIDC_USERNAME", "u")
    monkeypatch.setenv("EIDC_PASSWORD", "p")
    cfg = ChessMetConfig()
    assert cfg.token is None
    with pytest.raises(AuthenticationError, match="EIDC_TOKEN"):
        ChessMetDownloader(cfg)._create_session_with_auth()


def test_blank_token_ignored(monkeypatch):
    monkeypatch.setenv("EIDC_TOKEN", "   ")
    assert ChessMetConfig().token is None


def test_token_prefix_check():
    assert ChessMetConfig(token="pat_abc").token_looks_valid
    assert not ChessMetConfig(token="hunter2").token_looks_valid
    assert not ChessMetConfig(token=None).token_looks_valid


def test_token_not_in_repr():
    assert "pat_secret" not in repr(ChessMetConfig(token="pat_secret"))


def _mock_status(mock_get, status):
    resp = MagicMock(status_code=status, headers={})
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    mock_get.return_value = resp


@patch("chessmet.download.time.sleep")
@patch("requests.Session.get")
def test_401_fails_fast_without_retry_and_aborts_batch(mock_get, _sleep, tmp_path):
    _mock_status(mock_get, 401)
    dl = ChessMetDownloader(ChessMetConfig(token="pat_secret", rate_limit_delay=0))
    first = dl.download_worker((0, "http://x/a.nc", tmp_path / "a.nc"))[1]
    second = dl.download_worker((1, "http://x/b.nc", tmp_path / "b.nc"))[1]

    assert mock_get.call_count == 1
    assert not first.success and "expired" in first.error and TOKENS_URL in first.error
    assert "pat_secret" not in first.error
    assert not second.success and "Skipped" in second.error


@patch("requests.Session.get")
def test_403_points_to_licence_acceptance(mock_get, tmp_path):
    _mock_status(mock_get, 403)
    dl = ChessMetDownloader(ChessMetConfig(token="pat_secret", rate_limit_delay=0))
    res = dl.download_worker((0, "http://x/a.nc", tmp_path / "a.nc"))[1]

    assert mock_get.call_count == 1
    assert "licence" in res.error and DATASET_URL in res.error
