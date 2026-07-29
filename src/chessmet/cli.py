# ---
# created: 28 July 2026
# author: Lumo2.0, kaedonkers
# modified: 28 July 2026
# ---
"""
CHESS-MET NetCDF Downloader CLI

Downloads CHESS-MET gridded climate data from UKCEH EIDC catalogue.
Requires EIDC authentication via credentials in .env file.

Usage:
    # Ensure .env contains:
    # EIDC_USERNAME=your_username
    # EIDC_PASSWORD=your_password
    
    # Run from command line
    chessmet download --var tas --start 2000 --end 2000
"""

import base64
import calendar
import concurrent.futures
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Generator, List, Optional, Tuple

import click
import requests
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TransferSpeedColumn,
)
from requests.exceptions import ConnectionError, HTTPError, RequestException, Timeout

from chessmet import __version__
from chessmet.download import OUTDIR_DEFAULT, VARS, YEARS, ChessMetConfig, ChessMetDownloader, DownloadResult

load_dotenv()
console = Console()
logger = logging.getLogger(__name__)

# ───────────────────────────────────────────────────────────────
# Click CLI
# ───────────────────────────────────────────────────────────────

@click.group()
@click.option("-v", "--verbose", count=True, help="Increase logging verbosity (-v, -vv).")
@click.version_option(version=__version__, prog_name="chessmet")
@click.pass_context
def cli(ctx, verbose):
    """Download CHESS-MET NetCDF files from UKCEH EIDC."""
    ctx.ensure_object(dict)
    level = logging.WARNING if verbose == 0 else (logging.INFO if verbose == 1 else logging.DEBUG)
    logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=level, force=True)
    ctx.obj["log_level"] = level

@cli.command()
@click.option("--var", "vars_", type=click.Choice(VARS), multiple=True)
@click.option("--start", type=int, default=None)
@click.option("--end", type=int, default=None)
@click.option("--outdir", type=click.Path(file_okay=False, path_type=Path), default=OUTDIR_DEFAULT, show_default=True)
@click.option("--no-skip", "--overwrite", "skip_existing", flag_value=False, default=True)
@click.option("--workers", type=int, default=1, show_default=True)
@click.option("--dry-run", is_flag=True)
@click.pass_context
def download(ctx, vars_, start, end, outdir, skip_existing, workers, dry_run):
    """Download monthly NetCDF files for one or more variables."""
    selected = list(vars_) if vars_ else VARS
    
    config = ChessMetConfig(valid_vars=tuple(selected), max_workers=workers)
    if not config.username or not config.password:
        console.print("[bold red]ERROR:[/bold red] EIDC credentials not found in .env file.")
        ctx.exit(1)
    
    console.print(f"[bold cyan]Downloading[/bold cyan] {', '.join(selected)} [dim]for[/dim] {start or 2000}–{end or 2000} [dim]→[/dim] {outdir}")
    
    if dry_run:
        console.print("\n[yellow][DRY RUN][/yellow]")
        dl = ChessMetDownloader(config=config)
        for var in selected:
            for year in range(start or 2000, (end or 2000) + 1):
                for month in range(1, 13):
                    filepath = dl._prepare_filepath(outdir, var, year, month)
                    status = "[dim]SKIP[/dim]" if dl._file_exists_locally(filepath) else "[green]DOWNLOAD[/green]"
                    console.print(f"  {status} {filepath.relative_to(outdir)}")
        return
    
    console.print(f"[dim]Mode:[/dim] {'parallel (' + str(workers) + ' workers)' if workers > 1 else 'serial'}")
    
    dl = ChessMetDownloader(config=config)
    results = dl.download_all_vars(
        vars_=selected, 
        start_year=start, 
        end_year=end, 
        outdir=outdir, 
        parallel=(workers > 1), 
        num_workers=workers,
        skip_existing=skip_existing,
    )
    
    total_success = sum(sum(r.success for r in res) for res in results.values())
    total_failed = sum(len(res) - sum(r.success for r in res) for res in results.values())
    
    console.print(f"\n[bold green]{'=' * 50}[/bold green]")
    console.print(f"[bold]Done:[/bold] {total_success} succeeded, {total_failed} failed")
    
    if total_failed > 0:
        console.print("\n[bold red]Failed downloads:[/bold red]")
        for var, res_list in results.items():
            for r in res_list:
                if not r.success:
                    console.print(f"  [red]✗[/red] {var}/{r.filepath.name}")
                    console.print(f"    {r.error}")

@cli.command()
@click.option("--var", "vars_", type=click.Choice(VARS), multiple=True)
@click.option("--start", type=int, default=None)
@click.option("--end", type=int, default=None)
@click.option("--outdir", type=click.Path(file_okay=False, path_type=Path), default=OUTDIR_DEFAULT, show_default=True)
@click.pass_context
def status(ctx, vars_, start, end, outdir):
    """Check which files exist locally vs. missing."""
    selected = list(vars_) if vars_ else VARS
    config = ChessMetConfig(valid_vars=tuple(selected))
    dl = ChessMetDownloader(config=config)
    
    missing, present, incomplete = 0, 0, 0
    for var in selected:
        for year in range(start or 2000, (end or 2000) + 1):
            for month in range(1, 13):
                fp = dl._prepare_filepath(outdir, var, year, month)
                if fp.exists():
                    if fp.stat().st_size > 10_000_000:
                        present += 1
                    else:
                        incomplete += 1
                else:
                    missing += 1
    
    console.print(f"\n[bold green]{present}[/bold green] present ✓ | [yellow]{incomplete}[/yellow] incomplete ~ | [bold red]{missing}[/bold red] missing ✗")

@cli.command()
@click.option("--var", "vars_", type=click.Choice(VARS), multiple=True)
@click.option("--outdir", type=click.Path(file_okay=False, path_type=Path), default=OUTDIR_DEFAULT, show_default=True)
@click.option("--yes", is_flag=True, default=False)
@click.pass_context
def clean(ctx, vars_, outdir, yes):
    """Remove downloaded NetCDF files."""
    selected = list(vars_) if vars_ else VARS
    if not yes:
        if not click.confirm(f"Delete all NetCDF files for {', '.join(selected)} in {outdir}?"):
            ctx.exit(0)
    
    removed = 0
    for var in selected:
        var_dir = outdir / var
        if var_dir.exists():
            for fp in list(var_dir.glob("*.nc")):
                fp.unlink()
                removed += 1
            try:
                var_dir.rmdir()
            except OSError:
                pass
    
    console.print(f"[bold green]Removed[/bold green] {removed} files")
