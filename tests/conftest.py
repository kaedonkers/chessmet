# ---
# created: 28 July 2026
# author: Lumo2.0, kaedonkers, Claude Sonnet 5.5
# modified: 08 October 2026
# ---
"""Pytest configuration and shared fixtures."""
import os
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from chessmet.download import ChessMetDownloader, ChessMetConfig, DownloadResult

def make_hdf5(eof: int, version: int = 0) -> bytes:
    """Minimal HDF5 superblock claiming a total file length of `eof` bytes, zero-padded."""
    sig = b"\x89HDF\r\n\x1a\n"
    if version == 0:
        # sig, versions(4), sizes: offset=8, length=8, reserved, group K(4), flags(4), base, free-space, EOF
        head = sig + bytes([0, 0, 0, 0, 0, 8, 8, 0]) + b"\x04\x00\x10\x00" + b"\x00" * 4
        head += (0).to_bytes(8, "little") * 2 + eof.to_bytes(8, "little")
    else:
        # v2: sig, version, offset size, length size, flags, base, extension, EOF
        head = sig + bytes([2, 8, 8, 0]) + (0).to_bytes(8, "little") * 2 + eof.to_bytes(8, "little")
    return head + b"\x00" * (eof - len(head))


@pytest.fixture
def temp_dir(tmp_path):
    """Create temporary directory for test downloads."""
    return tmp_path / "test_data"

@pytest.fixture
def valid_env_vars(monkeypatch):
    """Set up a valid EIDC token for testing."""
    monkeypatch.setenv("EIDC_TOKEN", "pat_test_token_abc123")

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
        "Content-Length": str(65536 * 153),
    }
    response.iter_content.return_value = [b"x" * 65536 for _ in range(153)]  # ~10MB
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response
