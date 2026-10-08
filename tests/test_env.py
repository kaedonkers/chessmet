# ---
# created: 08 October 2026
# author: kaedonkers, Claude Sonnet 5.5
# modified: 08 October 2026
# ---

"""Importing chessmet must not read .env; the CLI loads it explicitly from the cwd."""
import os
import subprocess
import sys

import pytest
from click.testing import CliRunner

from chessmet.cli import cli
from chessmet.download import ChessMetConfig


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
