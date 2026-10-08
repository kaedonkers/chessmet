# ---
# created: 08 October 2026
# author: kaedonkers, Claude Sonnet 5.5
# modified: 08 October 2026
# ---
"""LIVE smoke test: does our token still work against the real EIDC server?

WHAT IT DOES
    Sends ONE request to the real EIDC server, using the real token from the
    repository's `.env`, asking for only the first 1 KB of one data file
    (HTTP `Range: bytes=0-1023`), and checks that the bytes are a NetCDF-4 header.
    Nothing is downloaded in full and nothing is written to disk.

WHEN TO USE IT
    Every other test uses mocks, so none of them can notice if EIDC changes how it
    authenticates or serves files. Run this before a release, or when downloads
    suddenly start failing with 401/403:

        pytest -m live

    A plain `pytest` never runs it: `pyproject.toml` sets `-m "not live"` by default.
    Do not run it in CI on every push (it needs a secret and a working network).

WHY IT FAILS OR SKIPS
    FAILS  (a real problem to look at):
        - 401/403 or any other status than 206: the token was rejected, expired or
          revoked, the licence was not accepted, or EIDC changed its API.
        - the bytes are not an HDF5 header: the server is returning something else.
    SKIPS  (says nothing about our code):
        - no EIDC_TOKEN in the repository `.env`.
        - EIDC unreachable, timed out, or answering with a 5xx error.

SAFETY
    The token is never printed or put in an assertion message. At most 1 KB is read.
    The request has an 8 s timeout so it stays under the 10 s slow-test guard in conftest.
"""
from pathlib import Path

import pytest
import requests
from dotenv import dotenv_values

from chessmet.download import ChessMetConfig, ChessMetDownloader

REPO_ROOT = Path(__file__).resolve().parents[1]
HDF5_SIGNATURE = b"\x89HDF\r\n\x1a\n"

pytestmark = pytest.mark.live


def test_token_can_fetch_first_kb():
    # conftest's autouse fixture hides EIDC_TOKEN from os.environ and moves into a temp
    # directory, so read the real token straight from the repo's .env (a plain dict:
    # dotenv_values does not touch os.environ).
    token = (dotenv_values(REPO_ROOT / ".env").get("EIDC_TOKEN") or "").strip()
    if not token:
        pytest.skip("no EIDC_TOKEN in .env, so there is nothing to test with")

    downloader = ChessMetDownloader(ChessMetConfig(token=token))
    url = downloader._build_url("tas", 2000, 1)

    try:
        response = downloader._create_session_with_auth().get(
            url, headers={"Range": "bytes=0-1023"}, timeout=8, stream=True
        )
    except (requests.ConnectionError, requests.Timeout) as exc:
        pytest.skip(f"EIDC unreachable: {type(exc).__name__}")

    with response:
        if response.status_code >= 500:
            pytest.skip(f"EIDC server error {response.status_code}")
        # 206 = partial content: token accepted and the Range header honoured.
        assert response.status_code == 206, (
            f"got HTTP {response.status_code}, expected 206: token rejected/expired, "
            "licence not accepted, or the EIDC API changed"
        )
        assert response.raw.read(8) == HDF5_SIGNATURE, "response is not a NetCDF-4/HDF5 file"
