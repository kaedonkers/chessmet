# ---
# created: 28 July 2026
# author: Lumo2.0, kaedonkers, Claude Sonnet 5.5
# modified: 08 October 2026
# ---
"""Test ChessMetConfig: defaults, validation, and where settings come from (environment, .env)."""
import os
import subprocess
import sys

import pytest
from click.testing import CliRunner

from chessmet.cli import cli
from chessmet.download import OUTDIR_DEFAULT, VARS, YEARS, ChessMetConfig, month_range, parse_period, resolve_months


@pytest.mark.parametrize("text, end, expected", [
    ("2000", False, (2000, 1)),
    ("2000", True, (2000, 12)),
    ("200003", False, (2000, 3)),
    ("200003", True, (2000, 3)),
    ("20000315", False, (2000, 3)),
    ("20000315", True, (2000, 3)),
    (2000, True, (2000, 12)),
])
def test_parse_period(text, end, expected):
    assert parse_period(text, end=end) == expected


@pytest.mark.parametrize("text", ["20", "20000", "2000-03", "abc", "200013", "200000", "20000230", "20000300"])
def test_parse_period_rejects(text):
    with pytest.raises(ValueError):
        parse_period(text)


@pytest.mark.parametrize("args, expected", [
    ((2005, None, None, None), (2005, 2005, 1, 12)),
    ((2005, 2006, None, None), (2005, 2006, 1, 12)),
    ((2005, None, 3, None), (2005, 2005, 3, 3)),
    ((2005, None, 3, 7), (2005, 2005, 3, 7)),
    ((2005, 2006, 3, None), (2005, 2006, 3, 12)),
    ((2005, 2006, 3, 7), (2005, 2006, 3, 7)),
])
def test_resolve_months_matches_cli_rules(args, expected):
    assert resolve_months(*args) == expected


def test_month_range_crosses_year_boundary():
    assert list(month_range((2000, 11), (2001, 2))) == [(2000, 11), (2000, 12), (2001, 1), (2001, 2)]


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
    (2010, 2005, "is after end"),
])
def test_validate_years_rejects(start, end, match):
    with pytest.raises(ValueError, match=match):
        ChessMetConfig(valid_years=(1961, 2019)).validate_years(start, end)


def test_download_var_validates_before_any_network_use(downloader):
    with pytest.raises(ValueError, match="not in valid list"):
        downloader.download_var("invalid_var", start_year=1991, end_year=1992)
    with pytest.raises(ValueError, match="before valid range"):
        downloader.download_var("tas", start_year=1950, end_year=1960)


# ── environment and .env ───────────────────────────────────────

def test_import_does_not_load_dotenv(tmp_path):
    (tmp_path / ".env").write_text("EIDC_TOKEN=pat_from_dotenv\n")
    code = "import os, chessmet.download; print(os.getenv('EIDC_TOKEN'))"
    out = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True, env={"PATH": ""} | {"PYTHONPATH": ":".join(sys.path)})
    assert out.stdout.strip() == "None"


def test_config_reads_environment_at_creation(monkeypatch):
    monkeypatch.setenv("EIDC_BASE_URL", "https://catalogue.ceh.ac.uk/other/path/")
    monkeypatch.setenv("RATE_LIMIT_DELAY", "0.5")
    cfg = ChessMetConfig()
    assert cfg.base_url == "https://catalogue.ceh.ac.uk/other/path" and cfg.rate_limit_delay == 0.5


@pytest.mark.parametrize("url", [
    "http://catalogue.ceh.ac.uk/datastore",            # not https
    "https://example.test/datastore",                  # wrong host
    "https://catalogue.ceh.ac.uk.evil.test/x",         # look-alike suffix
    "https://catalogue.ceh.ac.uk@evil.test/x",         # userinfo trick
    "https://user:pw@catalogue.ceh.ac.uk/x",           # credentials in URL
    "",
])
def test_base_url_is_restricted_to_eidc(url):
    with pytest.raises(ValueError, match="catalogue.ceh.ac.uk"):
        ChessMetConfig(base_url=url)


def test_cli_loads_dotenv_from_cwd(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("EIDC_TOKEN=pat_from_dotenv\n")
    monkeypatch.chdir(tmp_path)
    res = CliRunner().invoke(cli, ["download", "--var", "tas", "-s", "2000", "-e", "2000", "-o", str(tmp_path / "o"), "--dry-run"])
    try:
        assert res.exit_code == 0
        assert os.environ.get("EIDC_TOKEN") == "pat_from_dotenv"
    finally:
        os.environ.pop("EIDC_TOKEN", None)  # load_dotenv writes os.environ directly
