# ---
# created: 08 October 2026
# author: kaedonkers, Claude Sonnet 5.5
# modified: 08 October 2026
# ---

"""Test the `status` CLI command without network access."""
from chessmet.cli import cli


def test_status_counts_present_incomplete_part_and_missing(runner, tmp_path, make_hdf5_file):
    tas = tmp_path / "tas"
    base = "chess-met_tas_gb_1km_daily_2000{m}01-2000{m}{d}.nc"
    make_hdf5_file(tas / base.format(m="01", d="31"))
    make_hdf5_file(tas / base.format(m="02", d="29"), size=4000)
    (tas / (base.format(m="03", d="31") + ".part")).write_bytes(b"partial")

    res = runner.invoke(cli, ["status", "-o", str(tmp_path), "--var", "tas", "-s", "2000", "-e", "2000"])
    assert res.exit_code == 0
    assert "1 present" in res.output
    assert "2 incomplete" in res.output
    assert "9 missing" in res.output


def test_status_counts_file_once_when_part_also_exists(runner, tmp_path, make_hdf5_file):
    name = "chess-met_tas_gb_1km_daily_20000101-20000131.nc"
    make_hdf5_file(tmp_path / "tas" / name)
    (tmp_path / "tas" / (name + ".part")).write_bytes(b"leftover")
    res = runner.invoke(cli, ["status", "-o", str(tmp_path), "--var", "tas", "-s", "2000", "-e", "2000"])
    assert "1 present" in res.output
    assert "0 incomplete" in res.output
    assert "11 missing" in res.output


def test_status_clamps_years_with_warning(runner, tmp_path):
    res = runner.invoke(cli, ["status", "--var", "tas", "-s", "1900", "-e", "2100", "-o", str(tmp_path)])
    assert res.exit_code == 0
    assert "start=1961" in res.output and "end=2019" in res.output
    assert "Years: 1961–2019" in res.output
