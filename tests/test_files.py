# ---
# created: 08 October 2026
# author: kaedonkers, Claude Sonnet 5.5
# modified: 08 October 2026
# ---

"""Test downloaded files on disk: NetCDF completeness detection and atomic (.part) writes."""
from unittest.mock import patch

import pytest

from chessmet.download import ChessMetConfig, ChessMetDownloader, is_complete_netcdf


@pytest.mark.parametrize("version", [0, 1, 2, 3])
def test_is_complete_netcdf(tmp_path, make_hdf5_file, version):
    assert is_complete_netcdf(make_hdf5_file(tmp_path / "ok.nc", eof=10_000, version=version))
    assert not is_complete_netcdf(make_hdf5_file(tmp_path / "short.nc", eof=10_000, version=version, size=9_999))


def test_not_hdf5_or_missing_is_incomplete(tmp_path):
    p = tmp_path / "f.nc"
    assert not is_complete_netcdf(p)
    p.write_bytes(b"CDF\x01" + b"\x00" * 1000)  # classic NetCDF-3: not what this dataset ships
    assert not is_complete_netcdf(p)
    p.write_bytes(b"")
    assert not is_complete_netcdf(p)


@patch("requests.Session.get")
def test_short_download_leaves_no_files(mock_get, tmp_path, make_response):
    mock_get.return_value = make_response([b"x" * 100], content_length=500)
    dl = ChessMetDownloader(ChessMetConfig(token="pat_t", rate_limit_delay=0))
    target = tmp_path / "tas" / "a.nc"
    _, res = dl.download_worker((0, "http://x/a.nc", target), retry_attempts=1)

    assert not res.success and "Incomplete download" in res.error
    assert not target.exists()
    assert not list(tmp_path.rglob("*.part"))


@patch("requests.Session.get")
def test_good_download_is_renamed_and_part_removed(mock_get, tmp_path, make_response):
    mock_get.return_value = make_response([b"x" * 100], content_length=100)
    dl = ChessMetDownloader(ChessMetConfig(token="pat_t", rate_limit_delay=0))
    target = tmp_path / "tas" / "a.nc"
    _, res = dl.download_worker((0, "http://x/a.nc", target), retry_attempts=1)

    assert res.success and res.size_bytes == 100
    assert target.read_bytes() == b"x" * 100
    assert not list(tmp_path.rglob("*.part"))


@patch("requests.Session.get")
def test_failed_overwrite_keeps_existing_file(mock_get, tmp_path, make_response):
    target = tmp_path / "a.nc"
    target.write_bytes(b"old")
    mock_get.return_value = make_response([b"x" * 10], content_length=500)
    dl = ChessMetDownloader(ChessMetConfig(token="pat_t", rate_limit_delay=0))
    dl.download_worker((0, "http://x/a.nc", target), retry_attempts=1)
    assert target.read_bytes() == b"old"
