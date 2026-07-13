"""Create a consistent SQLite account backup using SQLite's online backup API."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB = Path(os.getenv("APP_DB_PATH", BASE_DIR / "data" / "geoai_tko.sqlite3"))
DEFAULT_BACKUP_DIR = BASE_DIR / "backups" / "accounts"
BACKUP_MANIFEST = "latest.json"


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

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "filename": destination.name,
        "size_bytes": destination.stat().st_size,
        "integrity": "ok",
    }
    manifest_path = destination_dir / BACKUP_MANIFEST
    temporary_manifest = destination_dir / f".{BACKUP_MANIFEST}.tmp"
    temporary_manifest.write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    temporary_manifest.replace(manifest_path)

    backups = sorted(destination_dir.glob("geoai_tko_accounts_*.sqlite3"), reverse=True)
    for expired in backups[max(1, keep):]:
        expired.unlink()
    return destination


def backup_status(destination_dir: Path, max_age_hours: float = 36) -> dict:
    manifest_path = destination_dir / BACKUP_MANIFEST
    manifest = None
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = None

    if manifest:
        backup_path = destination_dir / str(manifest.get("filename", ""))
        try:
            created_at = datetime.fromisoformat(str(manifest["created_at"]))
        except (KeyError, TypeError, ValueError):
            created_at = None
    else:
        backups = sorted(
            destination_dir.glob("geoai_tko_accounts_*.sqlite3"), reverse=True
        ) if destination_dir.exists() else []
        backup_path = backups[0] if backups else None
        created_at = (
            datetime.fromtimestamp(backup_path.stat().st_mtime, timezone.utc)
            if backup_path else None
        )

    if created_at and created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    exists = bool(backup_path and backup_path.exists())
    age_hours = (
        (datetime.now(timezone.utc) - created_at).total_seconds() / 3600
        if exists and created_at else None
    )
    return {
        "exists": exists,
        "fresh": bool(exists and age_hours is not None and age_hours <= max_age_hours),
        "created_at": created_at.isoformat(timespec="seconds") if created_at else None,
        "age_hours": round(age_hours, 2) if age_hours is not None else None,
        "size_bytes": backup_path.stat().st_size if exists else None,
    }


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
