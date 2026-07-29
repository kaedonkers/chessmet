# ---
# created: 28 July 2026
# author: Lumo2.0, kaedonkers
# modified: 28 July 2026
# ---
"""Pytest configuration and shared fixtures."""
import os
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from chessmet.download import ChessMetDownloader, ChessMetConfig, DownloadResult

@pytest.fixture
def temp_dir(tmp_path):
    """Create temporary directory for test downloads."""
    return tmp_path / "test_data"

@pytest.fixture
def valid_env_vars(monkeypatch):
    """Set up valid EIDC credentials for testing."""
    monkeypatch.setenv("EIDC_USERNAME", "test@example.com")
    monkeypatch.setenv("EIDC_PASSWORD", "test_password_123")

@pytest.fixture
def config(valid_env_vars):
    """Create a ChessMetConfig with test credentials."""
    return ChessMetConfig(
        valid_vars=("precip", "tas", "rsds"),
        valid_years=(1961, 2019),
        default_start_year=1991,
        default_end_year=1992,
        max_workers=1,
        timeout_seconds=5,
        rate_limit_delay=0,
    )

@pytest.fixture
def downloader(config):
    """Create a ChessMetDownloader instance."""
    return ChessMetDownloader(config=config)

@pytest.fixture
def mock_response():
    """Mock requests.Response with NetCDF-like content."""
    response = MagicMock()
    response.status_code = 200
    response.headers = {
        "Content-Type": "application/octet-stream",
        "Content-Length": "10000000",
    }
    response.iter_content.return_value = [b"x" * 65536 for _ in range(153)]  # ~10MB
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response