# ---
# created: 28 July 2026
# author: Lumo2.0, kaedonkers, Claude Sonnet 5.5
# modified: 08 October 2026
# ---
"""Test downloader behaviour (URLs, serial/parallel runs, retries) without hitting the real server."""
import concurrent.futures
from unittest.mock import MagicMock, patch

import pytest
import requests
from rich.console import Console
from rich.progress import Progress


def test_build_url(downloader):
    """Test URL building for different dates."""
    url = downloader._build_url("tas", 1991, 1)
    assert "tas" in url
    assert "19910101-19910131" in url
    assert "https://catalogue.ceh.ac.uk" in url
    
    url = downloader._build_url("precip", 2000, 2)
    assert "20000201-20000229" in url  # Leap year February


def test_prepare_filepath(downloader, temp_dir):
    """Test filepath preparation creates correct paths."""
    filepath = downloader._prepare_filepath(temp_dir, "tas", 1991, 1)
    
    assert filepath.parent == temp_dir / "tas"
    assert "chess-met_tas_gb_1km_daily_19910101-19910131.nc" in str(filepath)
    assert filepath.parent.exists()

@patch("requests.Session.get")
def test_download_var_serial_success(mock_get, downloader, temp_dir, mock_response):
    """Test serial download succeeds with mocked response."""
    mock_get.return_value = mock_response
    
    # Create URL/path pairs
    urls_and_paths = [
        (
            "https://test.url/file.nc",
            temp_dir / "tas" / "test.nc"
        )
    ]
    
    results = downloader.download_var_serial(
        "tas",
        urls_and_paths,
        temp_dir,
        progress=Progress(console=Console(quiet=True)),
    )
    
    assert len(results) == 1
    assert results[0].success is True
    assert results[0].filepath.exists()
    assert results[0].size_bytes > 0

@patch("requests.Session.get")
def test_download_var_serial_skip_existing(mock_get, downloader, temp_dir, make_hdf5_file):
    """Test existing files are skipped."""
    filepath = make_hdf5_file(temp_dir / "tas" / "chess-met_tas_gb_1km_daily_19910101-19910131.nc")
    
    urls_and_paths = [
        ("https://test.url/file.nc", filepath)
    ]
    
    results = downloader.download_var_serial(
        "tas",
        urls_and_paths,
        temp_dir,
        progress=Progress(console=Console(quiet=True)),
    )
    
    assert len(results) == 1
    assert results[0].success is True
    mock_get.assert_not_called()  # Download should be skipped

@patch("requests.Session.get")
def test_download_var_serial_failure(mock_get, downloader, temp_dir, caplog):
    """Test failed downloads are reported correctly."""
    # Mock to raise exception when entering context manager
    mock_response = MagicMock()
    mock_response.__enter__ = MagicMock(side_effect=requests.RequestException("Connection refused"))
    mock_response.__exit__ = MagicMock(return_value=False)
    mock_get.return_value = mock_response
    
    urls_and_paths = [
        (
            "https://test.url/file.nc",
            temp_dir / "tas" / "test.nc"
        )
    ]
    
    results = downloader.download_var_serial(
        "tas",
        urls_and_paths,
        temp_dir,
        progress=Progress(console=Console(quiet=True)),
    )
    
    assert len(results) == 1
    assert results[0].success is False
    assert results[0].error is not None
    assert "Connection refused" in results[0].error
    
    # Verify partial file was cleaned up
    assert not (temp_dir / "tas" / "test.nc").exists()

@patch("requests.Session.get")
def test_download_all_vars(mock_get, downloader, temp_dir, mock_response):
    """Test download_all_vars processes multiple variables."""
    mock_get.return_value = mock_response
    
    results = downloader.download_all_vars(
        vars_=["tas", "precip"],
        start_year=1991,
        end_year=1991,
        outdir=temp_dir,
    )
    
    assert "tas" in results
    assert "precip" in results
    assert all(r.success for var_results in results.values() for r in var_results)


@patch("requests.Session.get")
def test_parallel_vs_serial_same_results(mock_get, downloader, temp_dir, mock_response):
    """Test parallel downloads produce same results as serial."""
    mock_get.return_value = mock_response
    
    # Serial
    serial_results = downloader.download_var_serial(
        "tas",
        [("https://test.url/file.nc", temp_dir / "tas" / "test.nc")],
        temp_dir,
        progress=Progress(console=Console(quiet=True)),
    )
    
    # Reset path for parallel
    filepath = temp_dir / "tas" / "test_parallel.nc"
    
    # Parallel
    parallel_results = downloader.download_var_parallel(
        "tas",
        [("https://test.url/file.nc", filepath)],
        temp_dir,
        num_workers=1,
        progress=Progress(console=Console(quiet=True)),
    )
    
    assert serial_results[0].success and parallel_results[0].success
    assert serial_results[0].size_bytes == parallel_results[0].size_bytes
    assert serial_results[0].filepath.read_bytes() == parallel_results[0].filepath.read_bytes()

@patch("requests.Session.get")
def test_parallel_with_multiple_workers(mock_get, downloader, temp_dir, mock_response):
    """Test parallel downloads with multiple workers."""
    mock_get.return_value = mock_response
    
    # Create 5 file tasks
    tasks = [
        (i, f"https://test.url/file{i}.nc", temp_dir / f"file{i}.nc")
        for i in range(5)
    ]
    
    results_map = {}
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(downloader.download_worker, task_item): task_item[0]
            for task_item in tasks
        }
        
        for future in concurrent.futures.as_completed(futures):
            idx, result = future.result()
            results_map[idx] = result
    
    assert len(results_map) == 5
    assert all(result.success for result in results_map.values())


# ── retries and bad responses ──────────────────────────────────

def _task(tmp_path):
    return (0, "http://x/a.nc", tmp_path / "tas" / "a.nc")


@pytest.mark.parametrize("exc", [requests.ConnectionError("boom"), requests.Timeout("slow")])
@patch("requests.Session.get")
def test_transient_error_is_retried_then_succeeds(mock_get, exc, downloader, tmp_path, make_response, sleep):
    mock_get.side_effect = [exc, make_response()]
    _, res = downloader.download_worker(_task(tmp_path))

    assert res.success
    assert mock_get.call_count == 2
    sleep.assert_called_once_with(5)
    assert res.filepath.exists()


@patch("requests.Session.get")
def test_gives_up_after_max_attempts_with_growing_backoff(mock_get, downloader, tmp_path, sleep):
    mock_get.side_effect = requests.ConnectionError("down")
    _, res = downloader.download_worker(_task(tmp_path), retry_attempts=3)

    assert not res.success and "down" in res.error
    assert mock_get.call_count == 3
    assert [c.args[0] for c in sleep.call_args_list] == [5, 10]
    assert not res.filepath.exists()
    assert not list(tmp_path.rglob("*.part"))


@patch("requests.Session.get")
def test_http_error_is_not_retried(mock_get, downloader, tmp_path, sleep):
    resp = MagicMock(status_code=500, headers={})
    resp.raise_for_status.side_effect = requests.HTTPError("500 Server Error")
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    mock_get.return_value = resp
    _, res = downloader.download_worker(_task(tmp_path))

    assert not res.success and "500" in res.error
    assert mock_get.call_count == 1
    sleep.assert_not_called()


@patch("requests.Session.get")
def test_html_login_page_is_an_error_not_a_file(mock_get, downloader, tmp_path, make_response):
    resp = make_response()
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    mock_get.return_value = resp
    _, res = downloader.download_worker(_task(tmp_path))

    assert not res.success and "HTML" in res.error
    assert not res.filepath.exists()
