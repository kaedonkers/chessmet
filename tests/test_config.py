# ---
# created: 28 July 2026
# author: Lumo2.0, kaedonkers, Claude Sonnet 5.5
# modified: 08 October 2026
# ---
"""Test ChessMetConfig defaults and validation."""
import pytest

from chessmet.download import OUTDIR_DEFAULT, VARS, YEARS, ChessMetConfig


def test_config_defaults():
    config = ChessMetConfig()
    assert config.valid_vars == VARS
    assert config.valid_years == YEARS
    assert (config.min_year, config.max_year) == YEARS
    assert config.default_outdir == OUTDIR_DEFAULT
    assert config.token is None  # conftest removes EIDC_TOKEN


@pytest.mark.parametrize("var", VARS)
def test_validate_vars_accepts_known(var):
    ChessMetConfig().validate_vars(var)


@pytest.mark.parametrize("var", ["invalid_var", "", "TAS"])
def test_validate_vars_rejects_unknown(var):
    with pytest.raises(ValueError, match="not in valid list"):
        ChessMetConfig().validate_vars(var)


@pytest.mark.parametrize("start,end", [(1961, 1961), (2019, 2019), (1961, 2019), (2000, 2000)])
def test_validate_years_accepts_range_and_boundaries(start, end):
    ChessMetConfig(valid_years=(1961, 2019)).validate_years(start, end)


@pytest.mark.parametrize("start,end,match", [
    (1950, 1960, "before valid range"),
    (1960, 1970, "before valid range"),
    (2020, 2025, "after valid range"),
    (2010, 2020, "after valid range"),
    (2010, 2005, r"start_year \(2010\) > end_year \(2005\)"),
])
def test_validate_years_rejects(start, end, match):
    with pytest.raises(ValueError, match=match):
        ChessMetConfig(valid_years=(1961, 2019)).validate_years(start, end)


def test_download_var_validates_before_any_network_use(downloader):
    with pytest.raises(ValueError, match="not in valid list"):
        downloader.download_var("invalid_var", start_year=1991, end_year=1992)
    with pytest.raises(ValueError, match="before valid range"):
        downloader.download_var("tas", start_year=1950, end_year=1960)
