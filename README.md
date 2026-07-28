# chessmet
Python package + CLI for downloading CHESS-MET NetCDF data from the UKCEH EIDC catalogue using authenticated URL requests.

https://catalogue.ceh.ac.uk/documents/835a50df-e74f-4bfb-b593-804fd61d5eab


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
pixi run chessmet --help
```

### 3) Install with pip

```bash
pip install -e .
chessmet --help
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
```

You can also run the module directly:

```bash
python -m cli --help
```

