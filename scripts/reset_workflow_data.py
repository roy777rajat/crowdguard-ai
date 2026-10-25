"""Delete past workflow data so new camera feeds start with a clean history.

Deletes:  runs, stages, measurements, forecasts, incidents, incident events, camera counts (vision_counts),
          wall reset markers; camera status fields are cleared.
Keeps:    pandals/zones/cameras, users, audit log, model-call log (Muse-Spark cost history), settings.
          The number of paid SAM calls already made is remembered so the spend estimate does not reset.
A timestamped backup of the whole database is written first. Stop the backend before running this.

Usage (from the project root, inside .venv):
    python scripts/reset_workflow_data.py            # shows what would be deleted, changes nothing
    python scripts/reset_workflow_data.py --yes      # backs up, then deletes
    python scripts/reset_workflow_data.py --yes --db path/to/other.db
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

TABLES = ["stages", "runs", "forecasts", "measurements", "incident_events", "incidents", "vision_counts"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="data/crowdguard.db")
    ap.add_argument("--yes", action="store_true", help="actually delete (otherwise only report)")
    args = ap.parse_args()
    db = Path(args.db)
    if not db.is_file():
        print(f"Database not found: {db}")
        return 1
    con = sqlite3.connect(str(db))
    counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}
    print("Rows that are workflow data:")
    for t, n in counts.items():
        print(f"  {t:16} {n}")
    if not args.yes:
        print("\nNothing changed. Run again with --yes to back up and delete.")
        return 0
    backup_dir = db.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{db.stem}-{time.strftime('%Y%m%d-%H%M%S')}.db"
    dst = sqlite3.connect(str(backup))
    con.backup(dst)
    dst.close()
    print(f"\nBackup written: {backup}")
    # Keep paid-call accounting: the model bill does not reset just because history is cleared.
    paid = con.execute("SELECT COUNT(*) FROM vision_counts WHERE is_real_model=1").fetchone()[0]
    row = con.execute("SELECT value FROM settings WHERE key='vision_prior_paid_calls'").fetchone()
    prior = int(json.loads(row[0])) if row and row[0] else 0
    with con:
        con.execute("INSERT INTO settings(key,value) VALUES('vision_prior_paid_calls',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(prior + paid),))
        for t in TABLES:
            con.execute(f"DELETE FROM {t}")
        con.execute("DELETE FROM settings WHERE key LIKE 'vision_reset:%'")
        con.execute("UPDATE cameras SET status='registered', last_seen=NULL, last_error=NULL")
    for t in TABLES:
        left = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t:16} now {left}")
    con.close()
    print("Done. Start the backend; new feeds will create fresh workflow runs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
