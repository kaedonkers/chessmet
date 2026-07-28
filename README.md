# eidc_data

Python package + Click CLI for downloading CHESS-MET NetCDF data from the UKCEH EIDC catalogue using authenticated URL requests.

## Setup

### 1) Create credentials file

Create a local `.env` file in the repository root:

```env
EIDC_USERNAME=your_username
EIDC_PASSWORD=your_password
# Optional:
# EIDC_BASE_URL=https://catalogue.ceh.ac.uk/datastore/eidchub/835a50df-e74f-4bfb-b593-804fd61d5eab
```

`.env` is ignored by git.

### 2) Install with pixi (recommended)

```bash
pixi install
pixi run chess-dl --help
```

### 3) Install with pip

```bash
pip install -e .
chess-dl --help
```

## CLI usage

```bash
# Download one variable/year range
chess-dl download --var tas --start 1989 --end 2005

# Download multiple variables in parallel
chess-dl download --var tas --var precip --workers 4

# Show planned downloads without downloading
chess-dl download --var rsds --start 2000 --end 2001 --dry-run

# Check local file status
chess-dl status --var tas --start 1989 --end 1990

# Clean downloaded files
chess-dl clean --var tas --yes
```

You can also run the module directly:

```bash
python -m chess_downloader --help
```
