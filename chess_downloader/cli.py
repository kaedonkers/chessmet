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
from requests.auth import HTTPBasicAuth
from requests.exceptions import ConnectionError, HTTPError, RequestException, Timeout

load_dotenv()

logger = logging.getLogger(__name__)


@dataclass
class ChessMetConfig:
    """Configuration for CHESS-MET download."""

    base_url: str = os.getenv(
        "EIDC_BASE_URL",
        "https://catalogue.ceh.ac.uk/datastore/eidchub/835a50df-e74f-4bfb-b593-804fd61d5eab",
    )
    valid_vars: Tuple[str, ...] = ("precip", "tas", "rsds")
    default_start_year: int = 1989
    default_end_year: int = 2005
    timeout_seconds: int = 60
    max_workers: int = 4
    chunk_size: int = 65536
    rate_limit_delay: float = 0.5

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
    """Downloads CHESS-MET NetCDF files from UKCEH EIDC."""

    def __init__(self, config: Optional[ChessMetConfig] = None):
        self.config = config or ChessMetConfig()
        self._session: Optional[requests.Session] = None

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()

            if not self.config.username or not self.config.password:
                raise RuntimeError(
                    "EIDC credentials not found. Set in .env file:\n"
                    "  EIDC_USERNAME=your_username\n"
                    "  EIDC_PASSWORD=your_password"
                )

            self._session.auth = HTTPBasicAuth(self.config.username, self.config.password)
            self._session.headers.update(
                {
                    "User-Agent": "chess-met-downloader/1.0",
                    "Accept": "*/*",
                }
            )

        return self._session

    def _build_url(self, var: str, year: int, month: int) -> str:
        last_day = calendar.monthrange(year, month)[1]
        filename = (
            f"chess-met_{var}_gb_1km_daily_"
            f"{year}{month:02d}01-{year}{month:02d}{last_day}.nc"
        )
        return f"{self.config.base_url}/{var}/{filename}"

    def _file_exists_locally(self, filepath: Path) -> bool:
        return filepath.exists() and filepath.stat().st_size > 10_000_000

    def _download_file(self, url: str, filepath: Path) -> DownloadResult:
        if self._file_exists_locally(filepath):
            size = filepath.stat().st_size
            logger.info("SKIP: %s (%s bytes)", filepath.name, f"{size:,}")
            return DownloadResult(url=url, filepath=filepath, success=True, size_bytes=size)

        try:
            with self.session.get(url, stream=True, timeout=self.config.timeout_seconds) as response:
                response.raise_for_status()

                content_type = response.headers.get("Content-Type", "")
                if "text/html" in content_type:
                    error_path = filepath.with_suffix(".html")
                    with open(error_path, "wb") as file_handle:
                        file_handle.write(response.content[:10000])
                    raise RuntimeError(
                        f"Server returned HTML page (saved to {error_path.name}). "
                        "Check credentials or URL."
                    )

                expected_size = int(response.headers.get("Content-Length", 0))

                filepath.parent.mkdir(parents=True, exist_ok=True)
                with open(filepath, "wb") as file_handle:
                    for chunk in response.iter_content(chunk_size=self.config.chunk_size):
                        if chunk:
                            file_handle.write(chunk)

                actual_size = filepath.stat().st_size

                if expected_size > 0 and abs(actual_size - expected_size) > 1000:
                    raise RuntimeError(
                        f"Size mismatch: expected {expected_size:,}, got {actual_size:,}"
                    )

            logger.info("OK: %s (%s bytes)", filepath.name, f"{actual_size:,}")
            return DownloadResult(
                url=url,
                filepath=filepath,
                success=True,
                size_bytes=actual_size,
            )

        except (
            HTTPError,
            RequestException,
            ConnectionError,
            Timeout,
            OSError,
            RuntimeError,
        ) as error:
            error_msg = str(error)
            logger.error("FAIL: %s — %s", filepath.name, error_msg)

            try:
                filepath.unlink(missing_ok=True)
            except OSError:
                pass

            return DownloadResult(url=url, filepath=filepath, success=False, error=error_msg)

    def _month_range(
        self, start_year: int, end_year: int
    ) -> Generator[Tuple[int, int], None, None]:
        for year in range(start_year, end_year + 1):
            for month in range(1, 13):
                yield year, month

    def _prepare_filepath(self, outdir: Path, var: str, year: int, month: int) -> Path:
        subdir = outdir / var
        last_day = calendar.monthrange(year, month)[1]
        fname = f"chess-met_{var}_gb_1km_daily_{year}{month:02d}01-{year}{month:02d}{last_day}.nc"
        subdir.mkdir(parents=True, exist_ok=True)
        return subdir / fname

    def download_var(
        self,
        var: str,
        start_year: Optional[int] = None,
        end_year: Optional[int] = None,
        outdir: Path = Path("./chess_met_data"),
        skip_existing: bool = True,
        parallel: bool = False,
    ) -> List[DownloadResult]:
        start_year = start_year or self.config.default_start_year
        end_year = end_year or self.config.default_end_year

        if var not in self.config.valid_vars:
            logger.warning("Variable '%s' not in validated list %s", var, self.config.valid_vars)
        if start_year < 1961 or end_year > 2015:
            logger.warning(
                "Year range %s-%s outside typical CHESS-MET coverage (1961-2015)",
                start_year,
                end_year,
            )
        if start_year > end_year:
            raise ValueError(f"start_year ({start_year}) > end_year ({end_year})")

        tasks = []
        for year, month in self._month_range(start_year, end_year):
            url = self._build_url(var, year, month)
            filepath = self._prepare_filepath(outdir, var, year, month)
            if not skip_existing or not self._file_exists_locally(filepath):
                tasks.append((url, filepath))

        if not tasks:
            logger.info("All files for %s already exist in %s/%s", var, outdir, var)
            return []

        results: List[DownloadResult] = []

        if parallel:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=self.config.max_workers
            ) as executor:
                futures = {
                    executor.submit(self._download_file_with_ratelimit, url, fp): (url, fp)
                    for url, fp in tasks
                }
                for future in concurrent.futures.as_completed(futures):
                    results.append(future.result())
        else:
            for url, filepath in tasks:
                results.append(self._download_file_with_ratelimit(url, filepath))
                time.sleep(self.config.rate_limit_delay)

        successes = sum(result.success for result in results)
        failures = len(results) - successes
        logger.info("Completed %s: %s succeeded, %s failed", var, successes, failures)

        return results

    def _download_file_with_ratelimit(self, url: str, filepath: Path) -> DownloadResult:
        result = self._download_file(url, filepath)
        time.sleep(self.config.rate_limit_delay)
        return result

    def download_all_vars(
        self,
        vars_: Optional[List[str]] = None,
        start_year: Optional[int] = None,
        end_year: Optional[int] = None,
        outdir: Path = Path("./chess_met_data"),
        parallel: bool = True,
    ) -> dict:
        vars_ = vars_ or list(self.config.valid_vars)
        results = {}
        for var in vars_:
            logger.info("Processing variable: %s", var)
            results[var] = self.download_var(
                var=var,
                start_year=start_year,
                end_year=end_year,
                outdir=outdir,
                parallel=parallel,
            )
        return results


@click.group()
@click.option("-v", "--verbose", count=True, help="Increase logging verbosity (-v, -vv).")
@click.version_option(version="1.0.0", prog_name="chess-dl")
@click.pass_context
def cli(ctx, verbose):
    """Download CHESS-MET NetCDF files from UKCEH EIDC."""

    ctx.ensure_object(dict)
    level = logging.WARNING
    if verbose == 1:
        level = logging.INFO
    elif verbose >= 2:
        level = logging.DEBUG

    logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=level)
    ctx.obj["log_level"] = level


@cli.command()
@click.option(
    "--var",
    "vars_",
    type=click.Choice(["precip", "tas", "rsds"]),
    multiple=True,
    help="Variable(s) to download. Repeat for multiple (--var tas --var precip).",
)
@click.option("--start", type=int, default=None, help="Start year (inclusive).")
@click.option("--end", type=int, default=None, help="End year (inclusive).")
@click.option(
    "--outdir",
    type=click.Path(file_okay=False, path_type=Path),
    default="./chess_met_data",
    show_default=True,
)
@click.option(
    "--no-skip",
    "--overwrite",
    "skip_existing",
    flag_value=False,
    default=True,
    help="Force re-download existing files.",
)
@click.option("--workers", type=int, default=None, help="Max concurrent download workers (default: 1 for serial).")
@click.option("--dry-run", is_flag=True, help="Show what would be downloaded without fetching.")
@click.pass_context
def download(ctx, vars_, start, end, outdir, skip_existing, workers, dry_run):
    """Download monthly NetCDF files for one or more variables."""

    selected = list(vars_) if vars_ else ["precip", "tas", "rsds"]
    workers = workers or 1

    config = ChessMetConfig(valid_vars=tuple(selected), max_workers=workers)
    if not config.username or not config.password:
        click.echo("ERROR: EIDC credentials not found in .env file.", err=True)
        click.echo("Set EIDC_USERNAME and EIDC_PASSWORD in .env:", err=True)
        click.echo("  EIDC_USERNAME=your_username", err=True)
        click.echo("  EIDC_PASSWORD=your_password", err=True)
        ctx.exit(1)

    dl = ChessMetDownloader(config=config)
    click.echo(f"Downloading {', '.join(selected)} for {start or 1989}–{end or 2005} → {outdir}")

    if dry_run:
        click.echo("\n[DRY RUN] Files that would be downloaded:")
        for var in selected:
            for year in range(start or 1989, (end or 2005) + 1):
                for month in range(1, 13):
                    filepath = dl._prepare_filepath(outdir, var, year, month)
                    status = "SKIP" if dl._file_exists_locally(filepath) else "DL"
                    click.echo(f"  [{status}] {filepath.relative_to(outdir)}")
        return

    if workers > 1:
        click.echo(f"Parallel mode: {workers} workers")
    else:
        click.echo("Serial mode: one file at a time")

    results = dl.download_all_vars(
        vars_=selected,
        start_year=start,
        end_year=end,
        outdir=outdir,
        parallel=(workers > 1),
    )

    total_success = sum(sum(result.success for result in res) for res in results.values())
    total_failed = sum(len(res) - sum(result.success for result in res) for res in results.values())

    click.echo(f"\n{'=' * 50}")
    click.echo(f"Done: {total_success} succeeded, {total_failed} failed")

    if total_failed > 0:
        click.echo("\nFailed downloads:", err=True)
        for var, res_list in results.items():
            for result in res_list:
                if not result.success:
                    click.echo(f"  ✗ {var}/{result.filepath.name}", err=True)
                    click.echo(f"    {result.error}", err=True)


@cli.command()
@click.option(
    "--var",
    "vars_",
    type=click.Choice(["precip", "tas", "rsds"]),
    multiple=True,
    help="Variable(s) to check.",
)
@click.option("--start", type=int, default=None, help="Start year.")
@click.option("--end", type=int, default=None, help="End year.")
@click.option(
    "--outdir",
    type=click.Path(file_okay=False, path_type=Path),
    default="./chess_met_data",
    show_default=True,
)
@click.pass_context
def status(ctx, vars_, start, end, outdir):
    """Check which files exist locally vs. missing."""

    del ctx
    selected = list(vars_) if vars_ else ["precip", "tas", "rsds"]
    config = ChessMetConfig(valid_vars=tuple(selected))
    dl = ChessMetDownloader(config=config)

    missing = 0
    present = 0
    incomplete = 0

    for var in selected:
        for year in range(start or 1989, (end or 2005) + 1):
            for month in range(1, 13):
                file_path = dl._prepare_filepath(outdir, var, year, month)
                if file_path.exists():
                    size = file_path.stat().st_size
                    if size > 10_000_000:
                        present += 1
                    else:
                        incomplete += 1
                else:
                    missing += 1

    click.echo(f"\n{present} files present ✓")
    click.echo(f"{incomplete} files incomplete (~)")
    click.echo(f"{missing} files missing ✗")


@cli.command()
@click.option(
    "--var",
    "vars_",
    type=click.Choice(["precip", "tas", "rsds"]),
    multiple=True,
    help="Variable(s) to clean.",
)
@click.option(
    "--outdir",
    type=click.Path(file_okay=False, path_type=Path),
    default="./chess_met_data",
    show_default=True,
)
@click.option("--yes", is_flag=True, default=False, help="Skip confirmation.")
@click.pass_context
def clean(ctx, vars_, outdir, yes):
    """Remove downloaded NetCDF files for given variables."""

    selected = list(vars_) if vars_ else ["precip", "tas", "rsds"]

    if not yes:
        if not click.confirm(f"Delete all NetCDF files for {', '.join(selected)} in {outdir}?"):
            ctx.exit(0)

    removed = 0
    for var in selected:
        var_dir = outdir / var
        if not var_dir.exists():
            continue
        for file_path in list(var_dir.glob("*.nc")):
            file_path.unlink()
            removed += 1
        try:
            var_dir.rmdir()
        except OSError:
            pass

    click.echo(f"Removed {removed} files")
