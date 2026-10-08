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

@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    """Keep the developer's real .env and shell settings out of every test."""
    for name in ("EIDC_TOKEN", "EIDC_BASE_URL", "RATE_LIMIT_DELAY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)  # the CLI looks for .env in the current directory




@pytest.fixture
def temp_dir(tmp_path):
    """Create temporary directory for test downloads."""
    return tmp_path / "test_data"


@pytest.fixture
def token_env(monkeypatch):
    """Provide a valid-looking EIDC token."""
    monkeypatch.setenv("EIDC_TOKEN", "pat_test_token_abc123")


@pytest.fixture
def config(token_env):
    """ChessMetConfig with a test token and no rate-limit delay."""
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
    """Mock requests.Response streaming a few small chunks."""
    chunks = [b"x" * 1024 for _ in range(3)]
    response = MagicMock()
    response.status_code = 200
    response.headers = {
        "Content-Type": "application/octet-stream",
        "Content-Length": str(sum(len(c) for c in chunks)),
    }
    response.iter_content.return_value = chunks
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response
