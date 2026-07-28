# ---
# created: 28 July 2026
# author: Lumo2.0, kaedonkers
# modified: 28 July 2026
# ---
"""Minimal test: can we authenticate and download one CHESS-MET file?"""
import os
from pathlib import Path

import requests
from dotenv import load_dotenv
from requests.auth import HTTPBasicAuth

load_dotenv()

USERNAME = os.getenv("EIDC_USERNAME")
PASSWORD = os.getenv("EIDC_PASSWORD")

URL = (
    "https://catalogue.ceh.ac.uk/datastore/eidchub/835a50df-e74f-4bfb-b593-804fd61d5eab/"
    "tas/chess-met_tas_gb_1km_daily_19910101-19910131.nc"
)
DEST = Path("test_download.nc")

print(f"Username: {USERNAME}")
print(f"URL: {URL}")
print()

# ─── Test 1: No auth (should get login page) ──────────────────────
print("Test 1: No authentication")
resp = requests.get(URL, timeout=30)
print(f"  Status: {resp.status_code}")
print(f"  Content-Type: {resp.headers.get('Content-Type')}")
print(f"  Size: {len(resp.content):,} bytes")
print()

# ─── Test 2: With Basic Auth (proactive) ──────────────────────────
print("Test 2: HTTP Basic Auth (proactive)")
resp = requests.get(
    URL,
    auth=HTTPBasicAuth(USERNAME, PASSWORD),
    stream=True,
    timeout=60,
    allow_redirects=True,
)
print(f"  Status: {resp.status_code}")
print(f"  Content-Type: {resp.headers.get('Content-Type')}")
print(f"  Content-Length: {resp.headers.get('Content-Length', 'Unknown')}")

if "text/html" in resp.headers.get("Content-Type", ""):
    print("  ❌ Got HTML login page — auth failed")
elif resp.status_code == 200:
    print("  ✓ Got binary response — streaming to disk...")
    DEST.unlink(missing_ok=True)
    with open(DEST, "wb") as f:
        for chunk in resp.iter_content(chunk_size=65536):
            f.write(chunk)
    size = DEST.stat().st_size
    print(f"  ✓ Saved {DEST.name} ({size:,} bytes)")
    if size < 1_000_000:
        print("  ⚠️  File is suspiciously small")
    else:
        print("  ✅ File size looks correct")
else:
    print(f"  ❌ Unexpected status: {resp.status_code}")
    print(f"  Headers: {dict(resp.headers)}")

# ─── Test 3: Session with manual Authorization header ─────────────
print("\nTest 3: Session with manual Authorization header")
import base64
credentials = f"{USERNAME}:{PASSWORD}"
encoded = base64.b64encode(credentials.encode()).decode()
session = requests.Session()
session.headers.update({"Authorization": f"Basic {encoded}"})
resp = session.get(URL, stream=True, timeout=60, allow_redirects=True)
print(f"  Status: {resp.status_code}")
print(f"  Content-Type: {resp.headers.get('Content-Type')}")
print(f"  Content-Length: {resp.headers.get('Content-Length', 'Unknown')}")

if "text/html" in resp.headers.get("Content-Type", ""):
    print("  ❌ Got HTML login page — auth failed")
elif resp.status_code == 200:
    print("  ✓ Got binary response — streaming to disk...")
    DEST.unlink(missing_ok=True)
    with open(DEST, "wb") as f:
        for chunk in resp.iter_content(chunk_size=65536):
            f.write(chunk)
    size = DEST.stat().st_size
    print(f"  ✓ Saved {DEST.name} ({size:,} bytes)")
    if size < 1_000_000:
        print("  ⚠️  File is suspiciously small")
    else:
        print("  ✅ File size looks correct")
else:
    print(f"  ❌ Unexpected status: {resp.status_code}")
    print(f"  Headers: {dict(resp.headers)}")