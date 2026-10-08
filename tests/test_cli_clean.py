# ---
# created: 08 October 2026
# author: kaedonkers, Claude Sonnet 5.5
# modified: 08 October 2026
# ---

"""Test the `clean` CLI command without network access."""
import pytest

from chessmet.cli import cli


@pytest.fixture
def populated(tmp_path, make_hdf5_file):
    """tas: 1 complete, 1 truncated, 1 .part, plus a stray file; precip: 1 complete."""
    tas = tmp_path / "tas"
    precip = tmp_path / "precip"
    make_hdf5_file(tas / "a.nc")
    make_hdf5_file(tas / "b.nc", size=4000)
    (tas / "c.nc.part").write_bytes(b"partial")
    (tas / ".DS_Store").write_bytes(b"x")
    make_hdf5_file(precip / "d.nc")
    return tmp_path


def names(path):
    return sorted(p.name for p in path.iterdir())


def test_clean_dry_run_deletes_nothing(runner, populated):
    res = runner.invoke(cli, ["clean", "-o", str(populated), "--dry-run"])
    assert res.exit_code == 0
    assert "Dry run" in res.output and "4 files" in res.output
    assert len(names(populated / "tas")) == 4
    assert len(names(populated / "precip")) == 1


def test_clean_removes_data_files_but_keeps_folders_by_default(runner, populated):
    res = runner.invoke(cli, ["clean", "-o", str(populated), "--yes"])
    assert res.exit_code == 0
    assert names(populated / "tas") == [".DS_Store"]
    assert (populated / "precip").is_dir() and names(populated / "precip") == []


def test_clean_prompt_can_be_declined(runner, populated):
    res = runner.invoke(cli, ["clean", "-o", str(populated)], input="n\n")
    assert res.exit_code == 0
    assert len(names(populated / "tas")) == 4


def test_clean_incomplete_only_keeps_complete_files(runner, populated):
    res = runner.invoke(cli, ["clean", "-o", str(populated), "--incomplete-only", "--yes"])
    assert res.exit_code == 0
    assert names(populated / "tas") == [".DS_Store", "a.nc"]
    assert names(populated / "precip") == ["d.nc"]


def test_clean_var_limits_scope(runner, populated):
    runner.invoke(cli, ["clean", "-o", str(populated), "--var", "precip", "--yes"])
    assert names(populated / "precip") == []
    assert len(names(populated / "tas")) == 4


def test_clean_remove_dirs_keeps_folder_with_other_files(runner, populated):
    res = runner.invoke(cli, ["clean", "-o", str(populated), "--remove-dirs", "--yes"])
    assert res.exit_code == 0
    assert not (populated / "precip").exists()
    assert names(populated / "tas") == [".DS_Store"]
    assert "Kept" in res.output


def test_clean_force_removes_folders_with_other_files(runner, populated):
    res = runner.invoke(cli, ["clean", "-o", str(populated), "--force", "--yes"])
    assert res.exit_code == 0
    assert not (populated / "tas").exists()
    assert not (populated / "precip").exists()


def test_clean_force_with_incomplete_only_is_rejected(runner, populated):
    res = runner.invoke(cli, ["clean", "-o", str(populated), "--force", "--incomplete-only", "--yes"])
    assert res.exit_code != 0
    assert len(names(populated / "tas")) == 4


def test_clean_never_touches_unknown_folders(runner, tmp_path, make_hdf5_file):
    other = tmp_path / "documents"
    make_hdf5_file(other / "keep.nc")
    res = runner.invoke(cli, ["clean", "-o", str(tmp_path), "--force", "--yes"])
    assert "Nothing to clean" in res.output
    assert (other / "keep.nc").exists()


def test_clean_dry_run_with_remove_dirs_lists_folders_and_deletes_nothing(runner, populated):
    res = runner.invoke(cli, ["clean", "-o", str(populated), "--remove-dirs", "--dry-run"])
    assert res.exit_code == 0
    flat = res.output.replace("\n", "")  # rich wraps long temp paths
    assert f"Would remove folder {populated / 'precip'}" in flat
    assert f"Would remove folder {populated / 'tas'}" not in flat  # .DS_Store keeps it
    assert "4 files and 1 folders would be removed" in res.output
    assert len(names(populated / "tas")) == 4
    assert names(populated / "precip") == ["d.nc"]
