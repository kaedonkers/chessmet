# ---
# created: 08 October 2026
# author: kaedonkers, Claude Sonnet 5.5
# modified: 08 October 2026
# ---

"""Test token-only authentication, failure handling and where the token may travel."""
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

import pytest
import requests
from requests.adapters import BaseAdapter

from chessmet.download import (
    ALLOWED_HOST,
    DATASET_URL,
    TOKENS_URL,
    AuthenticationError,
    ChessMetConfig,
    ChessMetDownloader,
)


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


@patch("chessmet.download.time.sleep")
@patch("requests.Session.get")
def test_401_in_parallel_run_stops_remaining_files(mock_get, _sleep, tmp_path):
    from rich.console import Console
    from rich.progress import Progress

    _mock_status(mock_get, 401)
    dl = ChessMetDownloader(ChessMetConfig(token="pat_secret", rate_limit_delay=0))
    todo = [(f"http://x/{i}.nc", tmp_path / f"{i}.nc") for i in range(6)]
    results = dl.download_var_parallel(
        "tas", todo, tmp_path, num_workers=1, progress=Progress(console=Console(quiet=True))
    )

    assert mock_get.call_count == 1  # one worker: only the first file reaches the server
    assert not any(r.success for r in results)
    assert "expired" in results[0].error
    assert all("Skipped" in r.error for r in results[1:])


# ── token scope ────────────────────────────────────────────────

TOKEN = "pat_secret123"


class RecordingAdapter(BaseAdapter):
    """Fake transport: records every request and redirects the first one to `redirect_to`."""

    def __init__(self, redirect_to=None):
        super().__init__()
        self.redirect_to = redirect_to
        self.seen = []

    def send(self, request, **kwargs):
        self.seen.append(request)
        response = requests.Response()
        response.request = request
        response.url = request.url
        if self.redirect_to and len(self.seen) == 1:
            response.status_code = 302
            response.headers["Location"] = self.redirect_to
        else:
            response.status_code = 200
            response._content = b""
        return response

    def close(self):
        pass


@pytest.fixture
def dl():
    return ChessMetDownloader(config=ChessMetConfig(token=TOKEN))


def test_every_built_url_is_on_the_allowed_host_and_has_no_token(dl):
    for var in ("tas", "precip"):
        for month in (1, 12):
            url = dl._build_url(var, 2000, month)
            parsed = urlparse(url)
            assert parsed.scheme == "https"
            assert parsed.hostname == ALLOWED_HOST
            assert TOKEN not in url
            assert not parsed.username and not parsed.query


def test_authorization_is_dropped_when_redirected_to_another_host(dl):
    session = dl._create_session_with_auth()
    adapter = RecordingAdapter(redirect_to="https://evil.example.com/file.nc")
    session.mount("https://", adapter)
    session.get(dl._build_url("tas", 2000, 1))
    first, second = adapter.seen
    assert first.headers["Authorization"] == f"Bearer {TOKEN}"
    assert urlparse(second.url).hostname == "evil.example.com"
    assert "Authorization" not in second.headers


def test_authorization_is_kept_for_redirect_within_same_host(dl):
    session = dl._create_session_with_auth()
    adapter = RecordingAdapter(redirect_to=f"https://{ALLOWED_HOST}/elsewhere/file.nc")
    session.mount("https://", adapter)
    session.get(dl._build_url("tas", 2000, 1))
    assert adapter.seen[1].headers["Authorization"] == f"Bearer {TOKEN}"
