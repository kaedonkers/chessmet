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

import calendar
import logging
import shutil
import sys
from pathlib import Path
from typing import List, Optional

import click
from dotenv import find_dotenv, load_dotenv
from rich.logging import RichHandler

from chessmet import __version__

# `console` is shared with download.py: the progress bar and the log handler must use the same Console,
# otherwise log lines are drawn over the bar instead of above it.
from chessmet.download import (
    OUTDIR_DEFAULT,
    TOKEN_PREFIX,
    TOKENS_URL,
    VARS,
    ChessMetConfig,
    ChessMetDownloader,
    console,
    is_complete_netcdf,
    month_range,
    parse_period,
    resolve_months,
)

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


# Console verbosity by number of -v flags. Failures are always listed in the end-of-run
# summary, so by default nothing is logged to the console (it would break the progress bar).
_CONSOLE_LEVELS = {0: logging.CRITICAL, 1: logging.WARNING, 2: logging.INFO}


def _configure_logging(verbose: int, log_file: Optional[Path]) -> None:
    """Console logging goes through rich (above the progress bar); --log-file records INFO+."""
    pkg_logger = logging.getLogger("chessmet")
    for handler in list(pkg_logger.handlers):  # the CLI can run more than once per process
        pkg_logger.removeHandler(handler)
        handler.close()

    console_handler = RichHandler(console=console, show_path=False, rich_tracebacks=False)
    console_handler.setLevel(_CONSOLE_LEVELS.get(verbose, logging.DEBUG))
    pkg_logger.addHandler(console_handler)

    if log_file is not None:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG if verbose >= 3 else logging.INFO)
        file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        pkg_logger.addHandler(file_handler)

    pkg_logger.setLevel(logging.DEBUG)  # handlers do the filtering


PERIOD_HELP = "YYYY, YYYYMM or YYYYMMDD. Files are monthly, so a day is rounded to its month."


def _period_option(ctx, param, value):
    """Click callback: keep the raw text, but reject malformed values with a usage error."""
    if value is not None:
        try:
            parse_period(value)
        except ValueError as e:
            raise click.BadParameter(str(e))
    return value


VARS_HELP = f"Variable(s): {', '.join(VARS)}. Repeat the option or comma-separate (--vars tas,precip). Default: all."


def _vars_option(ctx, param, value):
    """Click callback: flatten repeated/comma-separated values and validate them."""
    out = []
    for item in value:
        for name in item.split(","):
            name = name.strip()
            if not name:
                continue
            if name not in VARS:
                raise click.BadParameter(f"{name!r} is not one of: {', '.join(VARS)}")
            if name not in out:
                out.append(name)
    return tuple(out)


def _resolve_period(config, start, end, verb):
    """Turn --start/--end text into clamped ((year, month), (year, month)) bounds.

    Omitted values are filled in by `resolve_months`, shared with the library. With no --start,
    the whole available range is used.
    """
    first, last = (config.min_year, 1), (config.max_year, 12)
    if start is None:
        first_ym = first
        last_ym = parse_period(end, end=True) if end is not None else last
    else:
        year, month = parse_period(start)
        bare_year = len(str(start).strip()) == 4
        end_year, end_month = parse_period(end, end=True) if end is not None else (None, None)
        sy, ey, sm, em = resolve_months(year, end_year, None if bare_year else month, end_month)
        first_ym, last_ym = (sy, sm), (ey, em)
    if first_ym > last_ym:
        raise click.UsageError(f"--start ({start}) is after --end ({end}).")
    if first_ym < first:
        console.print(f"[bold orange1]WARNING:[/bold orange1] {start} is before {first[0]}-{first[1]:02d}: {verb} start={first[0]}-{first[1]:02d}")
        first_ym = first
    if last_ym > last:
        console.print(f"[bold orange1]WARNING:[/bold orange1] {end or start} is after {last[0]}-{last[1]:02d}: {verb} end={last[0]}-{last[1]:02d}")
        last_ym = last
    return first_ym, last_ym


def _fmt_period(first_ym, last_ym):
    last_day = calendar.monthrange(*last_ym)[1]
    return f"{first_ym[0]}-{first_ym[1]:02d}-01 – {last_ym[0]}-{last_ym[1]:02d}-{last_day:02d}"


@click.group()
@click.option("-v", "--verbose", count=True,
              help="Show warnings and errors as they happen (-v), progress (-vv), debug (-vvv).")
@click.option("--log-file", type=click.Path(dir_okay=False, path_type=Path), default=None,
              help="Also append INFO-level logs (with timestamps) to this file. Off by default.")
@click.version_option(version=__version__, prog_name="chessmet")
@click.pass_context
def cli(ctx, verbose, log_file):
    """Download CHESS-MET NetCDF files from UKCEH EIDC."""
    ctx.ensure_object(dict)
    # Explicit and cwd-only: never searches the package's own parent directories
    load_dotenv(find_dotenv(usecwd=True))
    _configure_logging(verbose, log_file)

@cli.command(no_args_is_help=True)
@click.option("--var", "--vars", "vars_", multiple=True, callback=_vars_option, metavar="VAR[,VAR...]", help=VARS_HELP)
@click.option("-s", "--start", default=None, callback=_period_option, help=f"First month (required). {PERIOD_HELP}")
@click.option("-e", "--end", default=None, callback=_period_option,
              help="Last month. Defaults to the end of the --start period (a year gives December).")
@click.option("-o", "--outdir", type=click.Path(file_okay=False, path_type=Path), default=OUTDIR_DEFAULT, show_default=True)
@click.option("-w", "--workers", type=int, default=1, show_default=True)
@click.option("--no-skip", "--overwrite", "skip_existing", flag_value=False, default=True)
@click.option("--dry-run", is_flag=True)
@click.pass_context
def download(ctx, vars_, start, end, outdir, skip_existing, workers, dry_run):
    """Download monthly NetCDF files for one or more variables."""
    selected = list(vars_) if vars_ else VARS
    if start is None:
        raise click.UsageError("--start is required (e.g. --start 2000).")

    config = ChessMetConfig(valid_vars=tuple(selected), max_workers=workers)
    if not dry_run:
        _ensure_credentials(ctx, config)
    # if some vars invalid: console.print(warning)
    # if *no* vars valid: console.print(error) and exit
    (sy, sm), (ey, em) = _resolve_period(config, start, end, "Using")

    console.print("[bold green]Downloading CHESS-MET files[/bold green]")
    console.print(f"[dim]Period:[/dim] {_fmt_period((sy, sm), (ey, em))}")
    console.print(f"[dim]Vars:  [/dim] [bold magenta]{'[/bold magenta]  [bold magenta]'.join(selected)}[/bold magenta]")
    console.print(f"[dim]Outdir:[/dim] {outdir.resolve()}")
    
    if dry_run:
        console.print("\n[yellow][DRY RUN][/yellow]")
        dl = ChessMetDownloader(config=config)
        for var in selected:
            for year, month in month_range((sy, sm), (ey, em)):
                filepath = dl._prepare_filepath(outdir, var, year, month)
                status = "[dim]SKIP[/dim]" if dl._file_exists_locally(filepath) else "[green]DOWNLOAD[/green]"
                console.print(f"  {status} {filepath.relative_to(outdir)}")
        return
    
    console.print(f"[dim]Mode:  [/dim] {'parallel (' + str(workers) + ' workers)' if workers > 1 else 'serial'}")
    
    dl = ChessMetDownloader(config=config)
    results = dl.download_all_vars(
        vars_=selected, 
        start_year=sy,
        end_year=ey,
        start_month=sm,
        end_month=em,
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
@click.option("--var", "--vars", "vars_", multiple=True, callback=_vars_option, metavar="VAR[,VAR...]", help=VARS_HELP)
@click.option("-s", "--start", default=None, callback=_period_option,
              help=f"First month. {PERIOD_HELP} Defaults to the first available month.")
@click.option("-e", "--end", default=None, callback=_period_option,
              help="Last month. Defaults to the end of the --start period, or the last available month.")
@click.option("-o", "--outdir", type=click.Path(file_okay=False, path_type=Path), default=OUTDIR_DEFAULT, show_default=True)
@click.pass_context
def status(ctx, vars_, start, end, outdir):
    """Check which files exist locally vs. missing."""
    selected = list(vars_) if vars_ else VARS
    config = ChessMetConfig(valid_vars=tuple(selected))
    (sy, sm), (ey, em) = _resolve_period(config, start, end, "Checking status with")

    dl = ChessMetDownloader(config=config)
    
    missing, present, incomplete = 0, 0, 0
    for var in selected:
        for year, month in month_range((sy, sm), (ey, em)):
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
    
    console.print("Status of downloaded files")
    console.print(f"Period: {_fmt_period((sy, sm), (ey, em))}")
    console.print(f"Vars : [bold magenta]{'[/bold magenta]  [bold magenta]'.join(selected)}[/bold magenta]")
    console.print(f"[bold green]{present}[/bold green] present ✓ | [yellow]{incomplete}[/yellow] incomplete ~ | [bold red]{missing}[/bold red] missing ✗")

@cli.command()
@click.option("--var", "--vars", "vars_", multiple=True, callback=_vars_option, metavar="VAR[,VAR...]", help=VARS_HELP)
@click.option("-o", "--outdir", type=click.Path(file_okay=False, path_type=Path), default=OUTDIR_DEFAULT, show_default=True)
@click.option("-y", "--yes", is_flag=True, default=False)
@click.option("-i", "--incomplete-only", is_flag=True, help="Only remove incomplete files (truncated .nc and leftover .part files).")
@click.option("-f", "--force", is_flag=True, help="Remove variable subfolders even if they contain other files (implies --remove-dirs).")
@click.option("--remove-dirs", is_flag=True, help="Also remove the variable subfolders from OUTDIR once empty.")
@click.option("--dry-run", is_flag=True, help="List files that would be removed without deleting them.")
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
