"""Manual check that EIDC_TOKEN works as a bearer token (NOT collected by pytest).

Fetches the first chunk of one CHESS-MET file, or the whole file with --save.

    pixi run python tests/manual_token_check.py                 # tas, 2000-01
    pixi run python tests/manual_token_check.py 1999 7 --var huss
    pixi run python tests/manual_token_check.py 2000 1 --save   # saves to data/token_test/
"""
import argparse
import calendar
import sys
from pathlib import Path

import requests

from chessmet.download import DATASET_URL, TOKENS_URL, ChessMetConfig, ChessMetDownloader


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("year", nargs="?", type=int, default=2000)
    p.add_argument("month", nargs="?", type=int, default=1)
    p.add_argument("--var", default="tas")
    p.add_argument("--save", action="store_true", help="download the whole file")
    args = p.parse_args()

    cfg = ChessMetConfig()  # chessmet.download loads .env on import
    if not cfg.token:
        print(f"EIDC_TOKEN is not set (check .env). Create one at {TOKENS_URL}")
        return 2
    if not cfg.token_looks_valid:
        print("WARNING: token does not start with 'pat_'.")

    dl = ChessMetDownloader(cfg)
    url = dl._build_url(args.var, args.year, args.month)
    print(f"GET {url}")

    try:
        with dl._create_session_with_auth().get(url, stream=True, timeout=60) as r:
            print(f"Status: {r.status_code}  Content-Type: {r.headers.get('Content-Type')}")
            if r.status_code == 401:
                print(f"FAIL: token rejected (expired/revoked/invalid). New token: {TOKENS_URL}")
                print("WWW-Authenticate:", r.headers.get("WWW-Authenticate"))
                return 1
            if r.status_code == 403:
                print(f"FAIL: forbidden. Accept the dataset licence first: {DATASET_URL}")
                return 1
            r.raise_for_status()
            if "text/html" in r.headers.get("Content-Type", ""):
                print("FAIL: got an HTML page (login/licence page?), not data.")
                return 1

            first = next(r.iter_content(chunk_size=65536), b"")
            is_netcdf = first[:3] == b"CDF" or first[:4] == b"\x89HDF"
            print(f"First chunk: {len(first)} bytes, NetCDF signature: {is_netcdf}")
            print(f"Content-Length: {r.headers.get('Content-Length', 'unknown')}")

            if args.save:
                out = Path("data/token_test") / args.var / url.rsplit("/", 1)[1]
                out.parent.mkdir(parents=True, exist_ok=True)
                with open(out, "wb") as f:
                    f.write(first)
                    for chunk in r.iter_content(chunk_size=cfg.chunk_size):
                        f.write(chunk)
                print(f"Saved {out} ({out.stat().st_size:,} bytes)")

            print("OK: token authentication works." if is_netcdf
                  else "WARN: 200 response but content doesn't look like NetCDF.")
            return 0 if is_netcdf else 1
    except requests.RequestException as e:
        print(f"FAIL: request error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
