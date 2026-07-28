# ---
# created: 28 July 2026
# author: Lumo2.0, kaedonkers
# modified: 28 July 2026
# ---
"""
CHESS-MET NetCDF Downloader Utility

Downloads CHESS-MET gridded climate data from UKCEH EIDC catalogue.
Requires EIDC authentication via credentials in .env file.

Usage:
    # Install dependencies
    pip install requests python-dotenv click rich
    
    # Ensure .env contains:
    # EIDC_USERNAME=your_username
    # EIDC_PASSWORD=your_password
    
    # Run from command line
    python chess_downloader.py download --var tas --start 2000 --end 2000
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

load_dotenv()
console = Console()
logger = logging.getLogger(__name__)

OUTDIR_DEFAULT = Path("./data/chessmet")
VARS = ("dtr", "huss", "precip", "psurf", "rlds", "rsds", "sfcWind", "tas")
YEARS = (1961, 2019)

@dataclass
class ChessMetConfig:
    """Configuration for CHESS-MET download."""
    base_url: str = os.getenv(
        "EIDC_BASE_URL",
        "https://catalogue.ceh.ac.uk/datastore/eidchub/835a50df-e74f-4bfb-b593-804fd61d5eab"
    )
    valid_vars: Tuple[str, ...] = VARS
    valid_years: Tuple[int, int] = YEARS
    default_start_year: int = 2000
    default_end_year: int = 2000
    timeout_seconds: int = 120
    max_workers: int = 2
    chunk_size: int = 65536
    rate_limit_delay: float = float(os.getenv("RATE_LIMIT_DELAY", "3.0"))
    default_outdir: str = OUTDIR_DEFAULT
    max_retries: int = 3
    
    @property
    def username(self) -> Optional[str]:
        return os.getenv("EIDC_USERNAME")
    
    @property
    def password(self) -> Optional[str]:
        return os.getenv("EIDC_PASSWORD")

@dataclass
class DownloadResult:
    """Result of a single file download."""
    url: str
    filepath: Path
    success: bool
    size_bytes: Optional[int] = None
    error: Optional[str] = None

class ChessMetDownloader:
    """Downloads CHESS-MET NetCDF files from UKCEH EIDC with HTTP Basic Auth."""
    
    def __init__(self, config: Optional[ChessMetConfig] = None):
        self.config = config or ChessMetConfig()
        self._session: Optional[requests.Session] = None
    
    @property
    def session(self) -> requests.Session:
        """Lazy-load authenticated session."""
        if self._session is None:
            if not self.config.username or not self.config.password:
                raise RuntimeError(
                    "EIDC credentials not found. Set in .env:\n"
                    "  EIDC_USERNAME=your_username\n"
                    "  EIDC_PASSWORD=your_password"
                )
            self._session = requests.Session()
            credentials = f"{self.config.username}:{self.config.password}"
            encoded = base64.b64encode(credentials.encode()).decode()
            self._session.headers.update({
                "Authorization": f"Basic {encoded}",
                "User-Agent": "chess-met-downloader/1.0",
                "Accept": "*/*",
            })
        return self._session
    
    def _build_url(self, var: str, year: int, month: int) -> str:
        last_day = calendar.monthrange(year, month)[1]
        filename = f"chess-met_{var}_gb_1km_daily_{year}{month:02d}01-{year}{month:02d}{last_day}.nc"
        return f"{self.config.base_url}/{var}/{filename}"
    
    def _file_exists_locally(self, filepath: Path, min_size: int = 10_000_000) -> bool:
        return filepath.exists() and filepath.stat().st_size >= min_size
    
    def _prepare_filepath(self, outdir: Path, var: str, year: int, month: int) -> Path:
        subdir = outdir / var
        last_day = calendar.monthrange(year, month)[1]
        fname = f"chess-met_{var}_gb_1km_daily_{year}{month:02d}01-{year}{month:02d}{last_day}.nc"
        subdir.mkdir(parents=True, exist_ok=True)
        return subdir / fname
    
    def _create_session_with_auth(self) -> requests.Session:
        """Create a fresh authenticated session."""
        if not self.config.username or not self.config.password:
            raise RuntimeError("EIDC credentials not found")
        session = requests.Session()
        credentials = f"{self.config.username}:{self.config.password}"
        encoded = base64.b64encode(credentials.encode()).decode()
        session.headers.update({
            "Authorization": f"Basic {encoded}",
            "User-Agent": "chess-met-downloader/1.0",
        })
        return session
    
    def download_worker(
        self,
        args: Tuple[int, str, Path],
        retry_attempts: int = 3,
    ) -> Tuple[int, DownloadResult]:
        """Worker function with retry and re-authentication."""
        idx, url, filepath = args
        
        for attempt in range(retry_attempts):
            try:
                session = self._create_session_with_auth()
                
                with session.get(url, stream=True, timeout=self.config.timeout_seconds) as response:
                    if response.status_code == 401:
                        if attempt == 0:
                            raise RuntimeError("401 on first request — check credentials")
                        else:
                            logger.warning(f"Attempt {attempt + 1}: 401 error, re-authenticating...")
                            continue
                    
                    response.raise_for_status()
                    
                    content_type = response.headers.get("Content-Type", "")
                    if "text/html" in content_type:
                        raise RuntimeError("Server returned HTML login page")
                    
                    filepath.parent.mkdir(parents=True, exist_ok=True)
                    
                    with open(filepath, "wb") as f:
                        for chunk in response.iter_content(chunk_size=self.config.chunk_size):
                            if chunk:
                                f.write(chunk)
                    
                    actual_size = filepath.stat().st_size
                    return (idx, DownloadResult(
                        url=url, filepath=filepath, success=True, size_bytes=actual_size
                    ))
            
            except (requests.RequestException, RuntimeError, OSError) as e:
                error_msg = str(e)
                
                if attempt < retry_attempts - 1 and "401" in error_msg:
                    logger.warning(f"Attempt {attempt + 1}/{retry_attempts}: {filepath.name}")
                    time.sleep(5 * (attempt + 1))  # Exponential backoff
                    continue
                
                try:
                    filepath.unlink(missing_ok=True)
                except OSError:
                    pass
                
                return (idx, DownloadResult(url=url, filepath=filepath, success=False, error=error_msg))
        
        return (idx, DownloadResult(url=url, filepath=filepath, success=False, error="Max retries exceeded"))
    
    def download_var_serial(
        self,
        var: str,
        urls_and_paths: List[Tuple[str, Path]],
        outdir: Path,
        grand_progress: Optional[Progress] = None,
        grand_task: Optional[int] = None,
    ) -> List[DownloadResult]:
        """Serial download with progress bar."""
        if not urls_and_paths:
            return []
        
        results: List[DownloadResult] = []
        total_files = len(urls_and_paths)
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=40),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            # TransferSpeedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(f"[cyan]Downloading {var:<7}", total=total_files)
            
            for url, filepath in urls_and_paths:
                if self._file_exists_locally(filepath):
                    progress.advance(task, advance=1)
                    if grand_progress and grand_task is not None:
                        grand_progress.update(grand_task, advance=1)
                    logger.info(f"SKIP: {filepath.name} ({filepath.stat().st_size:,} bytes)")
                    results.append(DownloadResult(url=url, filepath=filepath, success=True, size_bytes=filepath.stat().st_size))
                    time.sleep(self.config.rate_limit_delay)
                    continue
                
                session = self._create_session_with_auth()
                try:
                    with session.get(url, stream=True, timeout=self.config.timeout_seconds) as response:
                        if response.status_code == 401:
                            raise RuntimeError("401 — check EIDC_USERNAME and EIDC_PASSWORD")
                        response.raise_for_status()
                        
                        content_type = response.headers.get("Content-Type", "")
                        if "text/html" in content_type:
                            raise RuntimeError("Server returned HTML login page")
                        
                        filepath.parent.mkdir(parents=True, exist_ok=True)
                        with open(filepath, "wb") as f:
                            for chunk in response.iter_content(chunk_size=self.config.chunk_size):
                                if chunk:
                                    f.write(chunk)
                        
                        actual_size = filepath.stat().st_size
                        progress.advance(task, advance=1)
                        if grand_progress and grand_task is not None:
                            grand_progress.update(grand_task, advance=1)
                        logger.info(f"OK: {filepath.name} ({actual_size:,} bytes)")
                        results.append(DownloadResult(url=url, filepath=filepath, success=True, size_bytes=actual_size))
                
                except (requests.RequestException, RuntimeError, OSError) as e:
                    error_msg = str(e)
                    logger.error(f"FAIL: {filepath.name} — {error_msg}")
                    try:
                        filepath.unlink(missing_ok=True)
                    except OSError:
                        pass
                    results.append(DownloadResult(url=url, filepath=filepath, success=False, error=error_msg))
                
                time.sleep(self.config.rate_limit_delay)
        
        return results
    
    def download_var_parallel(
        self,
        var: str,
        urls_and_paths: List[Tuple[str, Path]],
        outdir: Path,
        num_workers: int,
        grand_progress: Optional[Progress] = None,
        grand_task: Optional[int] = None,
    ) -> List[DownloadResult]:
        """Parallel download with progress bar."""
        if not urls_and_paths:
            return []
        
        tasks = [(i, url, filepath) for i, (url, filepath) in enumerate(urls_and_paths)]
        results_map = {}
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=40),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(f"[cyan]Downloading {var:<7}", total=len(tasks))
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
                future_to_idx = {
                    executor.submit(self.download_worker, task_item): task_item[0]
                    for task_item in tasks
                }
                
                for future in concurrent.futures.as_completed(future_to_idx):
                    idx, result = future.result()
                    results_map[idx] = result
                    progress.advance(task, advance=1)
                    if grand_progress and grand_task is not None:
                        grand_progress.update(grand_task, advance=1)
        
        return [results_map[i] for i in range(len(tasks))]
    
    def download_var(
        self,
        var: str,
        start_year: Optional[int] = None,
        end_year: Optional[int] = None,
        outdir: Path = OUTDIR_DEFAULT,
        skip_existing: bool = True,
        parallel: bool = False,
        num_workers: int = 1,
        grand_progress: Optional[Progress] = None,
        grand_task: Optional[int] = None,
    ) -> List[DownloadResult]:
        """Download all monthly files for a single variable."""
        start_year = start_year or self.config.default_start_year
        end_year = end_year or self.config.default_end_year
        
        # ─── VALIDATIONS ───────────────────────────────────────
        if var not in self.config.valid_vars:
            raise ValueError(f"Variable '{var}' not in valid list {self.config.valid_vars}")
        
        if start_year > end_year:
            raise ValueError(f"start_year ({start_year}) > end_year ({end_year})")
        
        min_year, max_year = self.config.valid_years
        if start_year < min_year:
            raise ValueError(f"start_year ({start_year}) before valid range (min: {min_year})")
        if end_year > max_year:
            raise ValueError(f"end_year ({end_year}) after valid range (max: {max_year})")
        # ────────────────────────────────────────────────────────
        
        urls_and_paths = []
        for year in range(start_year, end_year + 1):
            for month in range(1, 13):
                url = self._build_url(var, year, month)
                filepath = self._prepare_filepath(outdir, var, year, month)
                if not skip_existing or not self._file_exists_locally(filepath):
                    urls_and_paths.append((url, filepath))
        
        if not urls_and_paths:
            logger.info(f"All files for {var} already exist in {outdir}/{var}")
            return []
        
        logger.info(f"Downloading {len(urls_and_paths)} files for {var}...")
        
        if parallel and num_workers > 1:
            return self.download_var_parallel(var, urls_and_paths, outdir, num_workers, grand_progress, grand_task)
        else:
            return self.download_var_serial(var, urls_and_paths, outdir, grand_progress, grand_task)
    
    def download_all_vars(
        self,
        vars_: Optional[List[str]] = None,
        start_year: Optional[int] = None,
        end_year: Optional[int] = None,
        outdir: Path = OUTDIR_DEFAULT,
        parallel: bool = True,
        num_workers: int = 2,
    ) -> dict:
        """Download multiple variables with grand progress bar."""
        vars_ = vars_ or list(self.config.valid_vars)
        results = {}
        
        # Count ALL files that need downloading
        total_tasks = 0
        for var in vars_:
            for year in range(start_year or 1989, (end_year or 2005) + 1):
                for month in range(1, 13):
                    filepath = self._prepare_filepath(outdir, var, year, month)
                    if not self._file_exists_locally(filepath):
                        total_tasks += 1
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]{task.description}"),
            BarColumn(bar_width=40),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            grand_task = progress.add_task("[yellow]Overall Progress   ", total=total_tasks)
            
            for var in vars_:
                results[var] = self.download_var(
                    var=var,
                    start_year=start_year,
                    end_year=end_year,
                    outdir=outdir,
                    parallel=parallel,
                    num_workers=num_workers,
                    grand_progress=progress,
                    grand_task=grand_task,
                )
        
        return results

# ───────────────────────────────────────────────────────────────
# Click CLI
# ───────────────────────────────────────────────────────────────

@click.group()
@click.option("-v", "--verbose", count=True, help="Increase logging verbosity (-v, -vv).")
@click.version_option(version="1.0.0", prog_name="chess-dl")
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
    results = dl.download_all_vars(vars_=selected, start_year=start, end_year=end, outdir=outdir, parallel=(workers > 1), num_workers=workers)
    
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

if __name__ == "__main__":
    cli()