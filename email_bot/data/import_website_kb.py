"""
Import official website resource entries into the knowledge base.

Usage:
    # With the FastAPI server running on localhost:8000:
    python data/import_website_kb.py

    # Or specify a custom server URL:
    python data/import_website_kb.py --base-url http://localhost:8000
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

DATA_FILE = Path(__file__).parent / "website_kb_entries.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Import website KB entries")
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="FastAPI server base URL (default: http://localhost:8000)",
    )
    args = parser.parse_args()

    entries = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    print(f"Loaded {len(entries)} KB entries from {DATA_FILE.name}")

    success = 0
    failed = 0

    with httpx.Client(base_url=args.base_url, timeout=30) as client:
        for entry in entries:
            try:
                resp = client.post("/admin/kb", json=entry)
                resp.raise_for_status()
                print(f"  OK: {entry['id']} — {entry['title'][:50]}")
                success += 1
            except httpx.HTTPStatusError as exc:
                print(f"  FAIL: {entry['id']} — HTTP {exc.response.status_code}: {exc.response.text[:200]}")
                failed += 1
            except Exception as exc:
                print(f"  FAIL: {entry['id']} — {exc}")
                failed += 1

    print(f"\nDone: {success} imported, {failed} failed (total {len(entries)})")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
