"""Test ChessMetConfig validation."""
import pytest
from chessmet.cli import ChessMetConfig, ChessMetDownloader, OUTDIR_DEFAULT, download

def test_config_defaults():
    """Verify config defaults are reasonable."""
    config = ChessMetConfig(
        valid_vars=("precip", "tas"),
        valid_years=(1961, 2019),
    )
    
    assert config.valid_vars == ("precip", "tas")
    assert config.valid_years == (1961, 2019)
    assert config.default_outdir == OUTDIR_DEFAULT

def test_invalid_variable_validation():
    """Test that invalid variables raise ValueError."""
    config = ChessMetConfig(
        valid_vars=("precip", "tas"),
        valid_years=(1961, 2019),
    )
    downloader = ChessMetDownloader(config=config)
    
    with pytest.raises(ValueError, match="not in valid list"):
        downloader.download_var("invalid_var", start_year=1991, end_year=1992)

def test_year_range_validation():
    """Test that year ranges outside valid_years raise ValueError."""
    config = ChessMetConfig(
        valid_vars=("tas",),
        valid_years=(1961, 2019),
    )
    downloader = ChessMetDownloader(config=config)
    
    # Too early
    with pytest.raises(ValueError, match="before valid range"):
        downloader.download_var("tas", start_year=1950, end_year=1960)
    
    # Too late
    with pytest.raises(ValueError, match="after valid range"):
        downloader.download_var("tas", start_year=2020, end_year=2025)
    
    # Reversed
    with pytest.raises(ValueError, match=r"start_year \(\d+\) > end_year \(\d+\)"):
        downloader.download_var("tas", start_year=2010, end_year=2005)

@pytest.mark.skip(reason="This downloads data and is not suitable for unit tests.")
def test_valid_subrange():
    """Test that valid subranges within valid_years are accepted."""
    config = ChessMetConfig(
        valid_vars=("tas",),
        valid_years=(1961, 2019),
    )
    downloader = ChessMetDownloader(config=config)
    
    # This should NOT raise
    downloader.download_var("tas", start_year=2000, end_year=2000, dry_run=True)  # Use dry_run to avoid actual download