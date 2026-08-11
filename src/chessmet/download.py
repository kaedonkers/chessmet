# ---
# created: 28 July 2026
# author: Lumo2.0, kaedonkers
# modified: 05 August 2026
# ---
"""
CHESS-MET NetCDF Download Utility
"""

import base64
import calendar
import concurrent.futures
import logging
# TODO: save logging to file for diagnostics
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

OUTDIR_DEFAULT = Path("data/chessmet")
VARS = ("dtr", "huss", "precip", "psurf", "rlds", "rsds", "sfcWind", "tas")
YEARS = (1961, 2019)
START_DEFAULT = 2000
END_DEFAULT = 2000

@dataclass
class ChessMetConfig:
    """Configuration for CHESS-MET download."""
    base_url: str = os.getenv(
        "EIDC_BASE_URL",
        "https://catalogue.ceh.ac.uk/datastore/eidchub/835a50df-e74f-4bfb-b593-804fd61d5eab"
    )
    valid_vars: Tuple[str, ...] = VARS
    valid_years: Tuple[int, int] = YEARS
    default_start_year: int = START_DEFAULT
    default_end_year: int = END_DEFAULT
    timeout_seconds: int = 120
    max_workers: int = 2
    chunk_size: int = 65536
    rate_limit_delay: float = float(os.getenv("RATE_LIMIT_DELAY", "3.0"))
    default_outdir: Path = OUTDIR_DEFAULT
    max_retries: int = 3
    
    @property
    def username(self) -> Optional[str]:
        return os.getenv("EIDC_USERNAME")
    
    @property
    def password(self) -> Optional[str]:
        return os.getenv("EIDC_PASSWORD")

    @property
    def min_year(self) -> int:
        return self.valid_years[0]

    @property
    def max_year(self) -> int:
        return self.valid_years[1]

    def validate_vars(self, var: str):
        if var not in self.valid_vars:
            raise ValueError(f"Variable '{var}' not in valid list {self.valid_vars}")

    def validate_years(self, start_year: int, end_year: int):
        if start_year > end_year:
            raise ValueError(f"start_year ({start_year}) > end_year ({end_year})")
        if start_year < self.min_year:
            raise ValueError(f"start_year ({start_year}) before valid range (min: {self.min_year})")
        if end_year > self.max_year:
            raise ValueError(f"end_year ({end_year}) after valid range (max: {self.max_year})")

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
                session.close()
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
        skip_existing: bool = True,
        progress: Optional[Progress] = None,
        grand_task: Optional[int] = None,
    ) -> List[DownloadResult]:
        """Serial download using shared progress bar."""
        if not urls_and_paths:
            return []
        
        results: List[DownloadResult] = []
        total_files = len(urls_and_paths)
        
        if progress is None:
            raise ValueError("progress must be provided")
        
        task_id = progress.add_task(f"[cyan]Downloading {var:<7}", total=total_files)
        
        for url, filepath in urls_and_paths:
            if skip_existing and self._file_exists_locally(filepath):
                progress.advance(task_id, advance=1)
                if grand_task is not None:
                    progress.advance(grand_task, advance=1)
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
                    progress.advance(task_id, advance=1)
                    if grand_task is not None:
                        progress.advance(grand_task, advance=1)
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
        
        # Note: no progress.remove_task(task_id) — keeps completed bar visible
        
        return results

    def download_var_parallel(
        self,
        var: str,
        urls_and_paths: List[Tuple[str, Path]],
        outdir: Path,
        num_workers: int,
        progress: Optional[Progress] = None,
        grand_task: Optional[int] = None,
    ) -> List[DownloadResult]:
        """Parallel download using shared progress bar."""
        if not urls_and_paths:
            return []
        
        tasks = [(i, url, filepath) for i, (url, filepath) in enumerate(urls_and_paths)]
        results_map = {}
        
        if progress is None:
            raise ValueError("progress must be provided")
        
        task_id = progress.add_task(f"[cyan]Downloading {var:<7}", total=len(tasks))
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
            future_to_idx = {
                executor.submit(self.download_worker, task_item): task_item[0]
                for task_item in tasks
            }
            
            for future in concurrent.futures.as_completed(future_to_idx):
                idx, result = future.result()
                results_map[idx] = result
                progress.advance(task_id, advance=1)
                if grand_task is not None:
                    progress.advance(grand_task, advance=1)
        
        # Note: no progress.remove_task(task_id) — keeps completed bar visible
        
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
        progress: Optional[Progress] = None,
        grand_task: Optional[int] = None,
    ) -> List[DownloadResult]:
        """Download all monthly files for a single variable."""
        start_year = start_year or self.config.default_start_year
        end_year = end_year or self.config.default_end_year
        
        self.config.validate_vars(var)
        self.config.validate_years(start_year, end_year)
        
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
            return self.download_var_parallel(var, urls_and_paths, outdir, num_workers, progress, grand_task)
        else:
            return self.download_var_serial(var, urls_and_paths, outdir, skip_existing, progress, grand_task)
        
    def download_all_vars(
        self,
        vars_: Optional[List[str]] = None,
        start_year: Optional[int] = None,
        end_year: Optional[int] = None,
        outdir: Path = OUTDIR_DEFAULT,
        skip_existing: bool = True,
        parallel: bool = True,
        num_workers: int = 2,
    ) -> dict:
        """Download multiple variables with grand progress bar."""
        vars_ = vars_ or list(self.config.valid_vars)
        start_year = start_year or self.config.default_start_year
        end_year = end_year or self.config.default_end_year
        results = {}
        
        # Count ALL files that need downloading
        total_tasks = 0
        for var in vars_:
            for year in range(start_year, (end_year + 1)):
                for month in range(1, 13):
                    filepath = self._prepare_filepath(outdir, var, year, month)
                    if (not skip_existing) or (not self._file_exists_locally(filepath)):
                        total_tasks += 1
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]{task.description}"),
            BarColumn(bar_width=40),
            TaskProgressColumn(),
            TextColumn("[dim][{task.completed}/{task.total}][/dim]"), 
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            # ADD GRAND TASK FIRST → always rendered at TOP of stack
            grand_task = progress.add_task("[yellow]Overall Progress   ", total=total_tasks)
            
            for var in vars_:
                results[var] = self.download_var(
                    var=var,
                    start_year=start_year,
                    end_year=end_year,
                    outdir=outdir,
                    skip_existing=skip_existing,
                    parallel=parallel,
                    num_workers=num_workers,
                    progress=progress,  # ← shared progress instance
                    grand_task=grand_task,  # ← grand task ID
                )
        
        return results