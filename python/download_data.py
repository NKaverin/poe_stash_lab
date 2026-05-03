"""Download poe.watch market snapshots into data/raw/.

Also writes data/raw/manifest.json so the Python and the C++ uploaders
agree on which files map to which league. Edit LEAGUES below to add/remove.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

API_BASE = "https://api.poe.watch/compact"
USER_AGENT = (
    "kibana-elasticsearch-project "
    "(https://github.com/NKaverin/poe_stash_lab; junior portfolio project)"
)
DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

# Each row = one league we download. Edit freely.
LEAGUES = [
    {"api": "Mirage",          "id": "mirage",      "hc": False, "display": "Mirage",            "file": "market_snapshot_Mirage.json"},
    {"api": "Hardcore Mirage", "id": "mirage-hc",   "hc": True,  "display": "Mirage Hardcore",   "file": "market_snapshot_Mirage_HC.json"},
    {"api": "Standard",        "id": "standard",    "hc": False, "display": "Standard",          "file": "market_snapshot_Standard.json"},
    {"api": "Hardcore",        "id": "standard-hc", "hc": True,  "display": "Standard Hardcore", "file": "market_snapshot_Standard_HC.json"},
]


def fetch(api_league: str, dest: Path) -> int:
    """Download one league. Returns bytes written. Raises on HTTP/network error."""
    resp = requests.get(
        API_BASE,
        params={"league": api_league, "all": "true"},
        headers={"User-Agent": USER_AGENT},
        timeout=60,
    )
    resp.raise_for_status()
    if not resp.content:
        raise RuntimeError(f"Empty response from {resp.url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(resp.content)
    return len(resp.content)


def main() -> int:
    print(f"--- Downloading {len(LEAGUES)} snapshot(s) into {DATA_DIR} ---")
    failed = 0
    datasets = []

    for league in LEAGUES:
        dest = DATA_DIR / league["file"]

        # Skip-if-exists: re-running is cheap. Delete a file to refresh it.
        if dest.exists():
            size_mb = dest.stat().st_size / 1024 / 1024
            print(f"[SKIP]  {league['file']:40s} already exists ({size_mb:.1f} MB)")
        else:
            try:
                n = fetch(league["api"], dest)
                print(f"[OK]    {league['file']:40s} {n / 1024 / 1024:.1f} MB")
            except Exception as e:
                print(f"[ERROR] {league['file']:40s} {e}", file=sys.stderr)
                failed += 1
                continue

        datasets.append({
            "league_id": league["id"],
            "league":    league["display"],
            "hardcore":  league["hc"],
            "file":      f"data/raw/{league['file']}",
        })

    # Single source of truth for both Python and C++ uploaders
    manifest_path = DATA_DIR / "manifest.json"
    manifest_path.write_text(json.dumps({
        "snapshot_at": datetime.now(tz=timezone.utc)
                               .isoformat(timespec="seconds")
                               .replace("+00:00", "Z"),
        "source": "poe.watch",
        "datasets": datasets,
    }, indent=2))
    print(f"--- Wrote {manifest_path} ({len(datasets)} datasets) ---")
    print(f"--- Done ({failed} failure(s)) ---")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())