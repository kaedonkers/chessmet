# chessmet
Python package + CLI for downloading CHESS-MET NetCDF data from the UKCEH EIDC catalogue using authenticated URL requests.

https://catalogue.ceh.ac.uk/documents/835a50df-e74f-4bfb-b593-804fd61d5eab


## Setup

### 1) Authentication

Create a personal access token in your EIDC account and put it in a local `.env` file in the repository root:

```env
EIDC_TOKEN=your_personal_access_token
# Optional:
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

### 2) Install with pixi (recommended)

```bash
pixi install
pixi run chessmet --help
```

### 3) Install with pip

```bash
pip install -e .
chessmet --help
```

## Python usage

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
(see the `401`/`403` notes above).

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

You can also run the module directly:

```bash
python -m chessmet --help
```
