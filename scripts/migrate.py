"""
migrate.py
==========
Apply any pending pod.db migrations and report what ran.

Equivalent to letting any pipeline script call db.run_migrations() at
startup, but standalone — useful right after `git pull` or before a manual
test run.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src import db


def main() -> None:
    db_path = os.environ.get("POD_DB_PATH", "pod.db")
    conn = db.connect(db_path)
    newly = db.run_migrations(conn)
    if newly:
        print(f"Applied {len(newly)} migration(s) to {db_path}:")
        for f in newly:
            print(f"  + {f}")
    else:
        print(f"No pending migrations for {db_path} (schema is up to date)")
    conn.close()


if __name__ == "__main__":
    main()
