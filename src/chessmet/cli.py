# ---
# created: 28 July 2026
# author: Lumo2.0, kaedonkers
# modified: 08 October 2026
# ---
"""
CHESS-MET NetCDF Downloader CLI

Downloads CHESS-MET gridded climate data from UKCEH EIDC catalogue.
Requires an EIDC personal access token (EIDC_TOKEN in .env). If it is not set
and the terminal is interactive, you are prompted for it (hidden, never stored).

Usage:
    # Ensure .env contains:
    # EIDC_TOKEN=your_personal_access_token
    
    # Run from command line
    chessmet download --var tas --start 2000 --end 2000
"""

import base64
import calendar
import concurrent.futures
import logging
import os
import shutil
import sys
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
from chessmet.download import (
    OUTDIR_DEFAULT, VARS, YEARS, START_DEFAULT, END_DEFAULT,
    ChessMetConfig, ChessMetDownloader, DownloadResult, TOKEN_PREFIX, TOKENS_URL, is_complete_netcdf
    )

load_dotenv()
console = Console()
logger = logging.getLogger(__name__)

# ───────────────────────────────────────────────────────────────
# Click CLI
# ───────────────────────────────────────────────────────────────

def _ensure_credentials(ctx, config: ChessMetConfig) -> None:
    """Use EIDC_TOKEN if set; otherwise prompt for it (kept in memory only)."""
    if not config.token:
        if not sys.stdin.isatty():
            console.print(
                "[bold red]ERROR:[/bold red] No EIDC_TOKEN set (e.g. in .env) and no terminal "
                f"available to prompt for one. Create a token at {TOKENS_URL}"
            )
            ctx.exit(1)
        console.print(f"[yellow]No EIDC_TOKEN found.[/yellow] Paste your personal access token "
                      f"(hidden, not stored). Create one at {TOKENS_URL}")
        config.token = click.prompt("Token", hide_input=True).strip()
        if not config.token:
            console.print("[bold red]ERROR:[/bold red] Empty token.")
            ctx.exit(1)
    if not config.token_looks_valid:
        console.print(f"[bold orange1]WARNING:[/bold orange1] Token does not start with "
                      f"'{TOKEN_PREFIX}'; EIDC tokens normally do. Downloads may fail with 401.")


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
    start = start or START_DEFAULT
    end = end or END_DEFAULT
    
    config = ChessMetConfig(valid_vars=tuple(selected), max_workers=workers)
    if not dry_run:
        _ensure_credentials(ctx, config)
    # if some vars invalid: console.print(warning)
    # if *no* vars valid: console.print(error) and exit
    if start < config.min_year:
        console.print(f"[bold orange1]WARNING:[/bold orange1] {start} < {config.min_year}: Using start={config.min_year}")
        start = config.min_year
    if end > config.max_year:
        console.print(f"[bold orange1]WARNING:[/bold orange1] {end} > {config.max_year}: Using   end={config.max_year}")
        end = config.max_year

    console.print(f"[bold green]Downloading CHESS-MET files[/bold green]")
    console.print(f"[dim]Years: [/dim] {start}–{end}")
    console.print(f"[dim]Vars:  [/dim] [bold magenta]{'[/bold magenta]  [bold magenta]'.join(selected)}[/bold magenta]")
    console.print(f"[dim]Outdir:[/dim] {outdir.resolve()}")
    
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
    
    console.print(f"[dim]Mode:  [/dim] {'parallel (' + str(workers) + ' workers)' if workers > 1 else 'serial'}")
    
    dl = ChessMetDownloader(config=config)
    results = dl.download_all_vars(
        vars_=selected, 
        start_year=start, 
        end_year=end, 
        outdir=outdir, 
        skip_existing=skip_existing,
        parallel=(workers > 1), 
        num_workers=workers,
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
    start = start or YEARS[0]
    end = end or YEARS[-1]

    config = ChessMetConfig(valid_vars=tuple(selected))
    if start < config.min_year:
        console.print(f"[bold orange1]WARNING:[/bold orange1] {start} < {config.min_year}: Checking status with start={config.min_year}")
        start = config.min_year
    if end > config.max_year:
        console.print(f"[bold orange1]WARNING:[/bold orange1] {end} > {config.max_year}: Checking status with   end={config.max_year}")
        end = config.max_year

    dl = ChessMetDownloader(config=config)
    
    missing, present, incomplete = 0, 0, 0
    for var in selected:
        for year in range(start or YEARS[0], (end or YEARS[-1]) + 1):
            for month in range(1, 13):
                fp = dl._prepare_filepath(outdir, var, year, month)
                if fp.exists():
                    if is_complete_netcdf(fp):
                        present += 1
                    else:
                        incomplete += 1
                elif fp.with_name(fp.name + ".part").exists():
                    incomplete += 1
                else:
                    missing += 1
    
    console.print(f"Status of downloaded files")
    console.print(f"Years: {start}–{end}")
    console.print(f"Vars : [bold magenta]{'[/bold magenta]  [bold magenta]'.join(selected)}[/bold magenta]")
    console.print(f"[bold green]{present}[/bold green] present ✓ | [yellow]{incomplete}[/yellow] incomplete ~ | [bold red]{missing}[/bold red] missing ✗")

@cli.command()
@click.option("--var", "vars_", type=click.Choice(VARS), multiple=True)
@click.option("--outdir", type=click.Path(file_okay=False, path_type=Path), default=OUTDIR_DEFAULT, show_default=True)
@click.option("--yes", is_flag=True, default=False)
@click.option("--dry-run", is_flag=True, help="List files that would be removed without deleting them.")
@click.option("--incomplete-only", is_flag=True, help="Only remove incomplete files (truncated .nc and leftover .part files).")
@click.option("--remove-dirs", is_flag=True, help="Also remove the variable subfolders from OUTDIR once empty.")
@click.option("--force", is_flag=True, help="Remove variable subfolders even if they contain other files (implies --remove-dirs).")
@click.pass_context
def clean(ctx, vars_, outdir, yes, dry_run, incomplete_only, remove_dirs, force):
    """Remove downloaded NetCDF files.

    Without --var, only known variable folders in OUTDIR are cleaned.
    """
    if force and incomplete_only:
        raise click.UsageError("--force cannot be combined with --incomplete-only")
    remove_dirs = remove_dirs or force
    selected = list(vars_) if vars_ else list(VARS)

    targets: List[Path] = []
    var_dirs: List[Path] = []
    for var in selected:
        var_dir = outdir / var
        if not var_dir.is_dir():
            continue
        var_dirs.append(var_dir)
        for fp in sorted(var_dir.glob("*.nc")) + sorted(var_dir.glob("*.nc.part")):
            if incomplete_only and fp.suffix == ".nc" and is_complete_netcdf(fp):
                continue
            targets.append(fp)

    # Without --force, a folder is removable only if nothing but the targeted files would remain
    removable = [
        d for d in var_dirs
        if remove_dirs and (force or set(d.iterdir()) <= {fp for fp in targets if fp.parent == d})
    ]
    kept = [d for d in var_dirs if remove_dirs and d not in removable]

    if not targets and not removable:
        console.print("Nothing to clean")
        return

    if dry_run:
        for fp in targets:
            console.print(f"Would remove {fp}")
        for d in removable:
            console.print(f"Would remove folder {d}" + (" (and all contents)" if force else ""))
        console.print(f"[bold yellow]Dry run:[/bold yellow] {len(targets)} files and {len(removable)} folders would be removed")
        return

    if not yes:
        kind = "incomplete" if incomplete_only else "all"
        names = ", ".join(sorted({fp.parent.name for fp in targets} | {d.name for d in removable}))
        extra = f" and {len(removable)} folders" if removable else ""
        if not click.confirm(f"Delete {len(targets)} {kind} files{extra} for {names} in {outdir}?"):
            ctx.exit(0)

    for fp in targets:
        fp.unlink()
    for d in removable:
        if force:
            shutil.rmtree(d)
        else:
            d.rmdir()

    msg = f"[bold green]Removed[/bold green] {len(targets)} files"
    if remove_dirs:
        msg += f" and {len(removable)} folders"
    console.print(msg)
    for d in kept:
        console.print(f"[yellow]Kept {d}[/yellow] (still contains files)")
