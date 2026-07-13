"""Create a consistent SQLite account backup using SQLite's online backup API."""

from __future__ import annotations

import argparse
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB = Path(os.getenv("APP_DB_PATH", BASE_DIR / "data" / "geoai_tko.sqlite3"))
DEFAULT_BACKUP_DIR = BASE_DIR / "backups" / "accounts"


def backup_database(source: Path, destination_dir: Path, keep: int = 14) -> Path:
    if not source.exists():
        raise FileNotFoundError(f"Account database does not exist: {source}")
    destination_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = destination_dir / f"geoai_tko_accounts_{stamp}.sqlite3"
    with closing(sqlite3.connect(source)) as source_connection, closing(sqlite3.connect(destination)) as backup_connection:
        source_connection.backup(backup_connection)
        result = backup_connection.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise RuntimeError("Backup integrity check failed")
        backup_connection.commit()

    backups = sorted(destination_dir.glob("geoai_tko_accounts_*.sqlite3"), reverse=True)
    for expired in backups[max(1, keep):]:
        expired.unlink()
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_DB)
    parser.add_argument("--destination", type=Path, default=DEFAULT_BACKUP_DIR)
    parser.add_argument("--keep", type=int, default=14)
    args = parser.parse_args()
    backup = backup_database(args.source, args.destination, args.keep)
    print(backup)


if __name__ == "__main__":
    main()
