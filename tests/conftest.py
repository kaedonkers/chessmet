# ---
# created: 28 July 2026
# author: Lumo2.0, kaedonkers, Claude Sonnet 5.5
# modified: 08 October 2026
# ---
"""Pytest configuration and shared fixtures."""
import signal
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from chessmet.download import ChessMetConfig, ChessMetDownloader

_HDF5_SIGNATURE = b"\x89HDF\r\n\x1a\n"


def _hdf5_bytes(eof: int, version: int, size: int) -> bytes:
    """HDF5 superblock claiming a file length of `eof`, zero-padded to `size` bytes."""
    if version in (0, 1):
        # sig, versions(4), sizes: offset=8, length=8, reserved, group K(4), flags(4), base, free-space, EOF
        head = _HDF5_SIGNATURE + bytes([version, 0, 0, 0, 0, 8, 8, 0]) + b"\x04\x00\x10\x00" + b"\x00" * 4
        if version == 1:
            head += b"\x00" * 4  # indexed-storage K + reserved
    elif version in (2, 3):
        # sig, version, offset size, length size, flags, base, extension, EOF
        head = _HDF5_SIGNATURE + bytes([version, 8, 8, 0])
    else:
        raise ValueError(f"unsupported superblock version {version}")
    head += (0).to_bytes(8, "little") * 2 + eof.to_bytes(8, "little")
    return (head + b"\x00" * max(0, size - len(head)))[:size]


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    """Keep the developer's real .env and shell settings out of every test."""
    for name in ("EIDC_TOKEN", "EIDC_BASE_URL", "RATE_LIMIT_DELAY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)  # the CLI looks for .env in the current directory


@pytest.fixture(autouse=True)
def sleep(monkeypatch):
    """Retry backoff never really waits. The mock is returned so tests can assert on the delays."""
    mock = MagicMock()
    monkeypatch.setattr("chessmet.download.time.sleep", mock)
    return mock


@pytest.fixture(autouse=True)
def slow_test_guard():
    """Fail any test that runs longer than 10 s (e.g. a stray real network call). Unix only."""
    if not hasattr(signal, "SIGALRM"):
        yield
        return

    def _timeout(signum, frame):
        raise TimeoutError("test exceeded 10 s; is something really sleeping or hitting the network?")

    previous = signal.signal(signal.SIGALRM, _timeout)
    signal.alarm(10)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


@pytest.fixture
def make_hdf5_file():
    """Factory: write a minimal HDF5 file; `size` below `eof` makes it a truncated download."""
    def _make(path, eof: int = 5000, version: int = 0, size: int = None):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_hdf5_bytes(eof, version, eof if size is None else size))
        return path
    return _make


@pytest.fixture
def temp_dir(tmp_path):
    """Download directory under tmp_path (not created until something writes to it)."""
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
    return ChessMetDownloader(config=config)


@pytest.fixture
def make_response():
    """Factory: mock streaming requests.Response. `content_length` defaults to the true byte count."""
    def _make(chunks=None, content_length=None):
        chunks = chunks if chunks is not None else [b"x" * 1024 for _ in range(3)]
        declared = sum(len(c) for c in chunks) if content_length is None else content_length
        response = MagicMock()
        response.status_code = 200
        response.headers = {"Content-Type": "application/octet-stream", "Content-Length": str(declared)}
        response.iter_content.return_value = chunks
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        return response
    return _make


@pytest.fixture
def mock_response(make_response):
    return make_response()


@pytest.fixture
def runner():
    return CliRunner()
