"""Retry/backoff behaviour of download_worker (no real sleeping, no network)."""
from unittest.mock import MagicMock, patch

import pytest
import requests

from chessmet.download import ChessMetConfig, ChessMetDownloader


@pytest.fixture
def dl():
    return ChessMetDownloader(ChessMetConfig(token="pat_t", rate_limit_delay=0))


@pytest.fixture(autouse=True)
def no_sleep():
    with patch("chessmet.download.time.sleep") as sleep:
        yield sleep


def _task(tmp_path):
    return (0, "http://x/a.nc", tmp_path / "tas" / "a.nc")


@pytest.mark.parametrize("exc", [requests.ConnectionError("boom"), requests.Timeout("slow")])
@patch("requests.Session.get")
def test_transient_error_is_retried_then_succeeds(mock_get, exc, dl, tmp_path, make_response, no_sleep):
    mock_get.side_effect = [exc, make_response()]
    _, res = dl.download_worker(_task(tmp_path))

    assert res.success
    assert mock_get.call_count == 2
    no_sleep.assert_called_once_with(5)
    assert res.filepath.exists()


@patch("requests.Session.get")
def test_gives_up_after_max_attempts_with_growing_backoff(mock_get, dl, tmp_path, no_sleep):
    mock_get.side_effect = requests.ConnectionError("down")
    _, res = dl.download_worker(_task(tmp_path), retry_attempts=3)

    assert not res.success and "down" in res.error
    assert mock_get.call_count == 3
    assert [c.args[0] for c in no_sleep.call_args_list] == [5, 10]
    assert not res.filepath.exists()
    assert not list(tmp_path.rglob("*.part"))


@patch("requests.Session.get")
def test_http_error_is_not_retried(mock_get, dl, tmp_path, no_sleep):
    resp = MagicMock(status_code=500, headers={})
    resp.raise_for_status.side_effect = requests.HTTPError("500 Server Error")
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    mock_get.return_value = resp
    _, res = dl.download_worker(_task(tmp_path))

    assert not res.success and "500" in res.error
    assert mock_get.call_count == 1
    no_sleep.assert_not_called()


@patch("requests.Session.get")
def test_html_login_page_is_an_error_not_a_file(mock_get, dl, tmp_path, make_response):
    resp = make_response()
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    mock_get.return_value = resp
    _, res = dl.download_worker(_task(tmp_path))

    assert not res.success and "HTML" in res.error
    assert not res.filepath.exists()
