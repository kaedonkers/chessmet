# chessmet
Python package + CLI for downloading CHESS-MET NetCDF data from the UKCEH EIDC catalogue using authenticated URL requests.

https://catalogue.ceh.ac.uk/documents/835a50df-e74f-4bfb-b593-804fd61d5eab

## Data

CHESS-MET is a 1 km daily meteorology dataset for Great Britain, stored as one NetCDF file per variable per month.
Check the dataset page above for the current variables, years and licence; the values below may go out of date.

- **Variables:** `dtr`, `huss`, `precip`, `psurf`, `rlds`, `rsds`, `sfcWind`, `tas`
- **Years:** 1961–2019
- **Defaults:** all variables if `--var` is omitted; 2000–2000 if `--start`/`--end` are omitted
- **Size:** roughly 90–100 MB per file, so one variable for the full period is tens of GB. Check your disk space first.

Files are saved as `<outdir>/<var>/chess-met_<var>_gb_1km_daily_<YYYYMMDD>-<YYYYMMDD>.nc`
(default `outdir` is `data/chessmet`). Re-running skips files that are already complete, re-downloads truncated ones,
and `--overwrite` (alias `--no-skip`) forces everything to be downloaded again.
Files are written as `.part` first and renamed once verified, so an interrupted download (e.g. Ctrl+C) never leaves a
truncated `.nc` that looks complete. Remove leftovers with `chessmet clean --incomplete-only`.

## Setup

### 1) Authentication

Create a personal access token in your EIDC account and put it in a local `.env` file in the directory you run `chessmet` from (the CLI reads `.env` from the current directory, or any parent of it):

```env
EIDC_TOKEN=your_personal_access_token
# Optional:
# EIDC_BASE_URL (must be https on catalogue.ceh.ac.uk; the token is never sent elsewhere)
# EIDC_BASE_URL=https://catalogue.ceh.ac.uk/datastore/eidchub/835a50df-e74f-4bfb-b593-804fd61d5eab
```

`.env` is ignored by git. The token is sent as `Authorization: Bearer <token>`.
Tokens expire (up to 364 days). A `401` means the token was rejected: create a new one at
https://catalogue.ceh.ac.uk/sso/tokens and update `EIDC_TOKEN`.
A `403` usually means you have not yet accepted the dataset licence: open
https://doi.org/10.5285/835a50df-e74f-4bfb-b593-804fd61d5eab, choose "Download the data" and accept it (one-time).
EIDC tokens normally start with `pat_`; the CLI warns if yours does not.

If `EIDC_TOKEN` is not set and you run in an interactive terminal, the CLI prompts for the token
(hidden input). It is held in memory only and never stored. Username/password authentication is
no longer supported by the EIDC for programmatic downloads.

### 2) Install CLI
Install with **one** of these (each puts `chessmet` on your PATH in its own isolated environment).
We recommend [`uv`](https://docs.astral.sh/uv/) or [`pixi`](https://pixi.prefix.dev/latest/); `pipx` also works.

```bash
uv tool install git+https://github.com/kaedonkers/chessmet
pixi global install --git https://github.com/kaedonkers/chessmet
pipx install git+https://github.com/kaedonkers/chessmet
```

Remember to run `chessmet` from the folder containing your `.env` (or export `EIDC_TOKEN`).

To update, reinstall to pick up new commits:

```bash
uv tool install --reinstall git+https://github.com/kaedonkers/chessmet
pixi global install --force-reinstall --git https://github.com/kaedonkers/chessmet
pipx install --force git+https://github.com/kaedonkers/chessmet
```

## CLI usage

```bash
# Download one variable/year range
chessmet download --var tas --start 1989 --end 2005

# Download multiple variables in parallel
chessmet download --var tas --var precip --workers 4

# Show planned downloads without downloading
chessmet download --var rsds --start 2000 --end 2001 --dry-run

# Check local file status
chessmet status --var tas --start 1989 --end 1990

# Clean downloaded files
chessmet clean --var tas --yes
chessmet clean --var tas --dry-run   # list what would be removed, delete nothing
chessmet clean --incomplete-only --dry-run   # only truncated files and leftover .part files
chessmet clean --var tas --remove-dirs --yes   # also remove the emptied tas/ folder
chessmet clean --var tas --force --yes   # remove tas/ even if it holds other files
```

### Messages and log file

Failures are always listed in a summary at the end of a `download`. To see them as they happen
(these options go **before** the subcommand, not after it):

```bash
chessmet -v download ...    # warnings and errors (retries, failed files), above the progress bar
chessmet -vv download ...   # also per-file progress
chessmet --log-file run.log download ...   # also append timestamped INFO logs to run.log
```

No log file is written unless you pass `--log-file`. Logs never contain the token.

## Python usage

The package is also a library. Install it however you prefer:

```bash
pip install git+https://github.com/kaedonkers/chessmet
uv add git+https://github.com/kaedonkers/chessmet
pixi add --pypi "chessmet @ git+https://github.com/kaedonkers/chessmet"
```

The library never reads `.env` itself; it only looks at real environment variables, or at a token you pass in.
Only the CLI loads `.env` (from the current directory or a parent).

```python
from pathlib import Path
from chessmet.download import ChessMetConfig, ChessMetDownloader

# a) Pass the token explicitly (e.g. from your own secrets manager)
config = ChessMetConfig(token="pat_your_personal_access_token")

# b) Or load .env yourself, then let the config read EIDC_TOKEN from the environment
# from dotenv import load_dotenv
# load_dotenv()              # searches upward from the script's folder; or load_dotenv(".env")
# config = ChessMetConfig()

downloader = ChessMetDownloader(config)
results = downloader.download_all_vars(
    vars_=["tas"], start_year=2000, end_year=2000,
    outdir=Path("data/chessmet"), parallel=False,
)
for var, files in results.items():
    for r in files:
        print(var, r.filepath.name, "ok" if r.success else r.error)
```

A rejected token raises no exception from `download_all_vars`; the failure is reported in each result's `error`
(see [Authentication](#1-authentication)).

## Licence and citation

The data are licensed by UKCEH under the [CHESS-met licence](https://eidc.ac.uk/licences/chessmet/plain)
(accept it once on the dataset page to download). You must cite the dataset in any publication that uses it:

> Robinson, E. L., Blyth, E. M., Clark, D. B., Comyn-Platt, E., Rudd, A. C., & Wiggins, M. (2023). *Climate hydrology and
> ecology research support system meteorology dataset for Great Britain (1961-2019) [CHESS-met]*. NERC EDS Environmental
> Information Data Centre. https://doi.org/10.5285/835a50df-e74f-4bfb-b593-804fd61d5eab

Acknowledgement text required on publications and presentations:

> "Climate hydrology and ecology research support system meteorology dataset for Great Britain (1961-2019) [CHESS-met]
> data licensed from UK Centre for Ecology & Hydrology. © Database Right/Copyright UK Centre for Ecology & Hydrology.
> All rights reserved. Contains material based on Met Éireann data © Met Éireann, Met Office and OS data © Crown copyright
> and database right 2019 and University of East Anglia Climatic Research Unit © CRU"

This package is not affiliated with UKCEH.

