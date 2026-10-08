# ---
# created: 28 July 2026
# author: Lumo2.0, kaedonkers
# modified: 28 July 2026
# ---
"""Test downloader functionality without hitting the real server."""
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests
import concurrent.futures
from chessmet.download import (
    ChessMetDownloader,
    ChessMetConfig,
    DownloadResult,
    MIN_COMPLETE_BYTES,
)

class TestDownloadVar:
    """Test download_var method."""
    
    def test_build_url(self, downloader):
        """Test URL building for different dates."""
        url = downloader._build_url("tas", 1991, 1)
        assert "tas" in url
        assert "19910101-19910131" in url
        assert "https://catalogue.ceh.ac.uk" in url
        
        url = downloader._build_url("precip", 2000, 2)
        assert "20000201-20000229" in url  # Leap year February
    
    def test_file_exists_check(self, downloader, temp_dir):
        """Test _file_exists_locally returns correct values."""
        filepath = temp_dir / "test.nc"
        
        # Non-existent file
        assert not downloader._file_exists_locally(filepath)
        
        # Small file (incomplete)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_bytes(b"x" * 1000)
        assert not downloader._file_exists_locally(filepath)
        
        # Partial download just under the threshold is still incomplete
        filepath.write_bytes(b"x" * (MIN_COMPLETE_BYTES - 1))
        assert not downloader._file_exists_locally(filepath)
        
        # Complete file (>= threshold)
        filepath.write_bytes(b"x" * MIN_COMPLETE_BYTES)
        assert downloader._file_exists_locally(filepath)
    
    def test_prepare_filepath(self, downloader, temp_dir):
        """Test filepath preparation creates correct paths."""
        filepath = downloader._prepare_filepath(temp_dir, "tas", 1991, 1)
        
        assert filepath.parent == temp_dir / "tas"
        assert "chess-met_tas_gb_1km_daily_19910101-19910131.nc" in str(filepath)
        assert filepath.parent.exists()

    @patch("requests.Session.get")
    def test_download_var_serial_success(self, mock_get, downloader, temp_dir, mock_response):
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
        )
        
        assert len(results) == 1
        assert results[0].success is True
        assert results[0].filepath.exists()
        assert results[0].size_bytes > 0
    
    @patch("requests.Session.get")
    def test_download_var_serial_skip_existing(self, mock_get, downloader, temp_dir):
        """Test existing files are skipped."""
        # Pre-create a large file
        filepath = temp_dir / "tas" / "chess-met_tas_gb_1km_daily_19910101-19910131.nc"
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_bytes(b"x" * MIN_COMPLETE_BYTES)
        
        urls_and_paths = [
            ("https://test.url/file.nc", filepath)
        ]
        
        results = downloader.download_var_serial(
            "tas",
            urls_and_paths,
            temp_dir,
        )
        
        assert len(results) == 1
        assert results[0].success is True
        mock_get.assert_not_called()  # Download should be skipped
    
    @patch("requests.Session.get")
    def test_download_var_serial_failure(self, mock_get, downloader, temp_dir, caplog):
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
        )
        
        assert len(results) == 1
        assert results[0].success is False
        assert results[0].error is not None
        assert "Connection refused" in results[0].error
        
        # Verify partial file was cleaned up
        assert not (temp_dir / "tas" / "test.nc").exists()

    @patch("requests.Session.get")
    def test_download_all_vars(self, mock_get, downloader, temp_dir, mock_response):
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


class TestParallelDownload:
    """Test parallel download functionality."""
    
    @patch("requests.Session.get")
    def test_parallel_vs_serial_same_results(self, mock_get, downloader, temp_dir, mock_response):
        """Test parallel downloads produce same results as serial."""
        mock_get.return_value = mock_response
        
        # Serial
        serial_results = downloader.download_var_serial(
            "tas",
            [("https://test.url/file.nc", temp_dir / "tas" / "test.nc")],
            temp_dir,
        )
        
        # Reset path for parallel
        filepath = temp_dir / "tas" / "test_parallel.nc"
        
        # Parallel
        parallel_results = downloader.download_var_parallel(
            "tas",
            [("https://test.url/file.nc", filepath)],
            temp_dir,
            num_workers=1,
        )
        
        assert serial_results[0].success == parallel_results[0].success

    @patch("requests.Session.get")
    def test_parallel_with_multiple_workers(self, mock_get, downloader, temp_dir, mock_response):
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