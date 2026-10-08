# ---
# created: 28 July 2026
# author: Lumo2.0, kaedonkers
# modified: 08 October 2026
# ---
"""
CHESS-MET NetCDF Download Utility
"""

import calendar
import concurrent.futures
import logging
# TODO: save logging to file for diagnostics
import os
from urllib.parse import urlsplit
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Generator, List, Optional, Tuple

import click
import requests
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

console = Console()
logger = logging.getLogger(__name__)

OUTDIR_DEFAULT = Path("data/chessmet")
VARS = ("dtr", "huss", "precip", "psurf", "rlds", "rsds", "sfcWind", "tas")
YEARS = (1961, 2019)

_HDF5_SIGNATURE = b"\x89HDF\r\n\x1a\n"


def is_complete_netcdf(path: Path) -> bool:
    """True if `path` is a NetCDF-4/HDF5 file that is not truncated.

    The HDF5 superblock records the intended end-of-file address, so a short
    file is detected by reading ~64 bytes (no NetCDF library needed).
    """
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            head = f.read(64)
    except OSError:
        return False
    if head[:8] != _HDF5_SIGNATURE:
        return False
    version = head[8]
    if version in (0, 1):
        offset_size = head[13]
        pos = 24 + (4 if version == 1 else 0) + 2 * offset_size  # skip base + free-space addresses
    elif version in (2, 3):
        offset_size = head[9]
        pos = 12 + 2 * offset_size
    else:
        return False
    eof = int.from_bytes(head[pos:pos + offset_size], "little")
    return eof > 0 and size >= eof


class AuthenticationError(RuntimeError):
    """Server rejected the credentials (401/403); retrying will not help."""


def _env_token() -> Optional[str]:
    return (os.getenv("EIDC_TOKEN") or "").strip() or None


TOKEN_PREFIX = "pat_"
TOKENS_URL = "https://catalogue.ceh.ac.uk/sso/tokens"
DEFAULT_BASE_URL = "https://catalogue.ceh.ac.uk/datastore/eidchub/835a50df-e74f-4bfb-b593-804fd61d5eab"
ALLOWED_HOST = "catalogue.ceh.ac.uk"  # the token is only ever sent here
DATASET_URL = "https://doi.org/10.5285/835a50df-e74f-4bfb-b593-804fd61d5eab"


def parse_period(value, end: bool = False) -> Tuple[int, int]:
    """Parse YYYY, YYYYMM or YYYYMMDD into a (year, month).

    Files are monthly, so a day is coerced to the month containing it. A bare year means
    January when `end` is False and December when `end` is True.
    """
    text = str(value).strip()
    if not text.isdigit() or len(text) not in (4, 6, 8):
        raise ValueError(f"'{value}' is not YYYY, YYYYMM or YYYYMMDD")
    year = int(text[:4])
    month = int(text[4:6]) if len(text) >= 6 else (12 if end else 1)
    if not 1 <= month <= 12:
        raise ValueError(f"'{value}' has an invalid month ({month})")
    if len(text) == 8 and not 1 <= int(text[6:]) <= calendar.monthrange(year, month)[1]:
        raise ValueError(f"'{value}' has an invalid day ({text[6:]})")
    return year, month


def month_range(start: Tuple[int, int], end: Tuple[int, int]):
    """Yield (year, month) from start to end inclusive."""
    year, month = start
    while (year, month) <= end:
        yield year, month
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def resolve_months(
    start_year: int,
    end_year: Optional[int],
    start_month: Optional[int],
    end_month: Optional[int],
) -> Tuple[int, int, int, int]:
    """Fill in omitted end year and months the way the CLI does.

    A bare year is the whole year. A start month with no end year and no end month is just that
    month. An end year with no end month runs to December.
    """
    if end_year is None:
        end_year = start_year
        if end_month is None:
            end_month = start_month if start_month is not None else 12
    elif end_month is None:
        end_month = 12
    return start_year, end_year, start_month if start_month is not None else 1, end_month


@dataclass
class ChessMetConfig:
    """Configuration for CHESS-MET download."""
    base_url: str = field(default_factory=lambda: os.getenv("EIDC_BASE_URL", DEFAULT_BASE_URL))
    valid_vars: Tuple[str, ...] = VARS
    valid_years: Tuple[int, int] = YEARS
    timeout_seconds: int = 120
    max_workers: int = 2
    chunk_size: int = 65536
    rate_limit_delay: float = field(default_factory=lambda: float(os.getenv("RATE_LIMIT_DELAY", "3.0")))
    default_outdir: Path = OUTDIR_DEFAULT
    max_retries: int = 3
    
    # Preferred: personal access token (EIDC_TOKEN), sent as a Bearer token.
    token: Optional[str] = field(default_factory=_env_token, repr=False)
    def __post_init__(self):
        self.base_url = self.base_url.rstrip("/")
        parts = urlsplit(self.base_url)
        if parts.scheme != "https" or parts.hostname != ALLOWED_HOST or parts.username or parts.password:
            raise ValueError(
                f"base_url must be an https URL on {ALLOWED_HOST} (got {self.base_url!r}); "
                "the access token is only sent to that host."
            )

    @property
    def token_looks_valid(self) -> bool:
        """EIDC tokens normally start with 'pat_'; used only to warn, never to block."""
        return bool(self.token) and self.token.startswith(TOKEN_PREFIX)

    @property
    def min_year(self) -> int:
        return self.valid_years[0]

    @property
    def max_year(self) -> int:
        return self.valid_years[1]

    def validate_vars(self, var: str):
        if var not in self.valid_vars:
            raise ValueError(f"Variable '{var}' not in valid list {self.valid_vars}")

    def validate_years(self, start_year: int, end_year: int, start_month: int = 1, end_month: int = 12):
        if (start_year, start_month) > (end_year, end_month):
            raise ValueError(
                f"start ({start_year}-{start_month:02d}) is after end ({end_year}-{end_month:02d})"
            )
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
    """Downloads CHESS-MET NetCDF files from UKCEH EIDC using a personal access token."""
    
    def __init__(self, config: Optional[ChessMetConfig] = None):
        self.config = config or ChessMetConfig()
        self._session: Optional[requests.Session] = None
        self._auth_failed = threading.Event()
    
    @property
    def session(self) -> requests.Session:
        """Lazy-load authenticated session."""
        if self._session is None:
            self._session = self._create_session_with_auth()
        return self._session
    
    def _build_url(self, var: str, year: int, month: int) -> str:
        last_day = calendar.monthrange(year, month)[1]
        filename = f"chess-met_{var}_gb_1km_daily_{year}{month:02d}01-{year}{month:02d}{last_day}.nc"
        return f"{self.config.base_url}/{var}/{filename}"
    
    def _file_exists_locally(self, filepath: Path) -> bool:
        """True if a complete (non-truncated) NetCDF file is already present."""
        return is_complete_netcdf(filepath)

    def _stream_to_file(self, response: requests.Response, filepath: Path) -> int:
        """Write the response to `<file>.part`, verify its length, then rename into place."""
        filepath.parent.mkdir(parents=True, exist_ok=True)
        part = filepath.with_name(filepath.name + ".part")
        try:
            with open(part, "wb") as f:
                for chunk in response.iter_content(chunk_size=self.config.chunk_size):
                    if chunk:
                        f.write(chunk)
            written = part.stat().st_size
            expected = response.headers.get("Content-Length")
            if expected is not None and expected.isdigit() and written != int(expected):
                raise OSError(f"Incomplete download: got {written:,} of {int(expected):,} bytes")
            part.replace(filepath)
            return written
        finally:
            part.unlink(missing_ok=True)
    
    def _prepare_filepath(self, outdir: Path, var: str, year: int, month: int) -> Path:
        subdir = outdir / var
        last_day = calendar.monthrange(year, month)[1]
        fname = f"chess-met_{var}_gb_1km_daily_{year}{month:02d}01-{year}{month:02d}{last_day}.nc"
        subdir.mkdir(parents=True, exist_ok=True)
        return subdir / fname
    
    def _create_session_with_auth(self) -> requests.Session:
        """Create a fresh session authenticated with the bearer token."""
        if not self.config.token:
            raise AuthenticationError(
                "No EIDC token found. Set EIDC_TOKEN in .env "
                f"(create one at {TOKENS_URL})."
            )
        session = requests.Session()
        session.headers.update({
            "User-Agent": "chess-met-downloader/1.0",
            "Accept": "*/*",
        })
        session.headers["Authorization"] = f"Bearer {self.config.token}"
        return session

    def _auth_error(self, status: int) -> AuthenticationError:
        if status == 403:
            return AuthenticationError(
                "403 — access forbidden. The dataset licence may not have been accepted yet: "
                f"open {DATASET_URL}, choose 'Download the data' and accept the licence (one-time)."
            )
        return AuthenticationError(
            f"{status} — token rejected. It may have expired or been revoked; "
            f"create a new personal access token at {TOKENS_URL} and update EIDC_TOKEN."
        )
    
    def download_worker(
        self,
        args: Tuple[int, str, Path],
        retry_attempts: int = 3,
    ) -> Tuple[int, DownloadResult]:
        """Worker function with retry and re-authentication."""
        idx, url, filepath = args
        
        if self._auth_failed.is_set():
            return (idx, DownloadResult(url=url, filepath=filepath, success=False,
                                        error="Skipped: authentication failed earlier"))
        
        for attempt in range(retry_attempts):
            try:
                session = self._create_session_with_auth()
                
                with session.get(url, stream=True, timeout=self.config.timeout_seconds) as response:
                    if response.status_code in (401, 403):
                        raise self._auth_error(response.status_code)

                    response.raise_for_status()

                    content_type = response.headers.get("Content-Type", "")
                    if "text/html" in content_type:
                        raise RuntimeError("Server returned HTML login page")

                    actual_size = self._stream_to_file(response, filepath)

                session.close()
                return (idx, DownloadResult(
                    url=url, filepath=filepath, success=True, size_bytes=actual_size
                ))
            
            except (requests.RequestException, RuntimeError, OSError) as e:
                error_msg = str(e)
                
                if isinstance(e, AuthenticationError):
                    self._auth_failed.set()
                elif attempt < retry_attempts - 1 and isinstance(
                    e, (requests.ConnectionError, requests.Timeout)
                ):
                    logger.warning(f"Attempt {attempt + 1}/{retry_attempts}: {filepath.name}")
                    time.sleep(5 * (attempt + 1))  # Exponential backoff
                    continue
                
                
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
            if self._auth_failed.is_set():
                results.append(DownloadResult(url=url, filepath=filepath, success=False,
                                              error="Skipped: authentication failed earlier"))
                progress.advance(task_id, advance=1)
                if grand_task is not None:
                    progress.advance(grand_task, advance=1)
                continue
            
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
                    if response.status_code in (401, 403):
                        raise self._auth_error(response.status_code)
                    response.raise_for_status()
                    
                    content_type = response.headers.get("Content-Type", "")
                    if "text/html" in content_type:
                        raise RuntimeError("Server returned HTML login page")
                    
                    actual_size = self._stream_to_file(response, filepath)
                    progress.advance(task_id, advance=1)
                    if grand_task is not None:
                        progress.advance(grand_task, advance=1)
                    logger.info(f"OK: {filepath.name} ({actual_size:,} bytes)")
                    results.append(DownloadResult(url=url, filepath=filepath, success=True, size_bytes=actual_size))
            
            except (requests.RequestException, RuntimeError, OSError) as e:
                error_msg = str(e)
                if isinstance(e, AuthenticationError):
                    self._auth_failed.set()
                logger.error(f"FAIL: {filepath.name} — {error_msg}")
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
        start_year: int,
        end_year: Optional[int] = None,
        outdir: Path = OUTDIR_DEFAULT,
        skip_existing: bool = True,
        parallel: bool = False,
        num_workers: int = 1,
        progress: Optional[Progress] = None,
        grand_task: Optional[int] = None,
        start_month: Optional[int] = None,
        end_month: Optional[int] = None,
    ) -> List[DownloadResult]:
        """Download the monthly files for a single variable.

        The range runs from start_year/start_month to end_year/end_month inclusive.
        Omitted values are filled in as by `resolve_months`: year only means the whole year, and
        a start month alone means just that month.
        """
        start_year, end_year, start_month, end_month = resolve_months(start_year, end_year, start_month, end_month)
        
        self.config.validate_vars(var)
        self.config.validate_years(start_year, end_year, start_month, end_month)
        
        urls_and_paths = []
        for year, month in month_range((start_year, start_month), (end_year, end_month)):
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
        vars_: Optional[List[str]],
        start_year: int,
        end_year: Optional[int] = None,
        outdir: Path = OUTDIR_DEFAULT,
        skip_existing: bool = True,
        parallel: bool = True,
        num_workers: int = 2,
        start_month: Optional[int] = None,
        end_month: Optional[int] = None,
    ) -> dict:
        """Download multiple variables with grand progress bar."""
        vars_ = vars_ or list(self.config.valid_vars)
        start_year, end_year, start_month, end_month = resolve_months(start_year, end_year, start_month, end_month)
        results = {}
        
        # Count ALL files that need downloading
        total_tasks = 0
        for var in vars_:
            for year, month in month_range((start_year, start_month), (end_year, end_month)):
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
                    start_month=start_month,
                    end_month=end_month,
                    outdir=outdir,
                    skip_existing=skip_existing,
                    parallel=parallel,
                    num_workers=num_workers,
                    progress=progress,  # ← shared progress instance
                    grand_task=grand_task,  # ← grand task ID
                )
        
        return results
