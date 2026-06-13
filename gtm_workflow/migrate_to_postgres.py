"""One-time migration: copy your local SQLite data (leads, campaigns, contacts,
auto-discovery config) into the cloud Postgres database, so the deployed app
starts with your existing 111 leads instead of empty.

Usage (run locally, after the Postgres DB exists on Render/Neon):

    set TARGET_DATABASE_URL=postgresql://user:pass@host:5432/dbname   (PowerShell: $env:TARGET_DATABASE_URL="...")
    python migrate_to_postgres.py

Get the connection string from Render → your Postgres → "External Database URL".
Re-running is safe-ish only on an empty target; it INSERTs rows (duplicates if run twice).
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
BASEDIR = os.path.dirname(os.path.abspath(__file__))

from sqlalchemy import create_engine
from database import db, Lead, Campaign, CampaignContact, AutoDiscoveryConfig  # noqa: F401


def _normalize(url: str) -> str:
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def main():
    target = (len(sys.argv) > 1 and sys.argv[1]) or os.getenv("TARGET_DATABASE_URL", "")
    target = _normalize(target.strip())
    if not target or not target.startswith("postgresql://"):
        sys.exit("Provide the Postgres URL: python migrate_to_postgres.py <postgresql://...>  "
                 "(or set TARGET_DATABASE_URL).")

    src_url = "sqlite:///" + os.path.join(BASEDIR, "gtm.db").replace("\\", "/")
    print("Source :", src_url)
    print("Target :", target.split("@")[-1])  # don't print credentials

    src = create_engine(src_url)
    dst = create_engine(target)

    # Build the schema on the target from the real models (correct column types).
    db.metadata.create_all(dst)

    # Insert parents before children so foreign keys resolve.
    order = [Lead, Campaign, AutoDiscoveryConfig, CampaignContact]
    total = 0
    for model in order:
        table = model.__table__
        with src.connect() as sc:
            rows = [dict(r) for r in sc.execute(table.select()).mappings()]
        if not rows:
            print(f"  {table.name}: 0 rows")
            continue
        with dst.begin() as dc:
            dc.execute(table.insert(), rows)
        total += len(rows)
        print(f"  {table.name}: {len(rows)} rows copied")

    print(f"\nDone — {total} rows migrated to Postgres.")


if __name__ == "__main__":
    main()
