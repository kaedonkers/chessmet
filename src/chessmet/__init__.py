# ---
# created: 28 July 2026
# author: Lumo2.0, kaedonkers
# modified: 08 October 2026
# ---
"""chessmet: Python package + CLI for downloading CHESS-MET NetCDF data."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("chessmet")
except PackageNotFoundError:  # running from a source tree that isn't installed
    __version__ = "unknown"
