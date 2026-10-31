#!/usr/bin/env python3
"""Export the database layout and starter content to docs/database/ so people can read it without running the app.

    python scripts/export_db_docs.py [path/to/crowdguard.db]

Writes: schema.sql (every table), csv/<table>.csv for reference tables, and TABLES.md (row counts per table).
Password hashes and salts are never exported. Simulated data only.
"""
from __future__ import annotations

import csv
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "database"
SEED_TABLES = ["locations", "pandals", "zones", "cameras", "users", "settings"]
SKIP_COLUMNS = {"users": {"password_hash", "salt"}}
PURPOSE = {
    "locations": "Areas of the city that group pandals (starter data)",
    "pandals": "Each pandal: capacity and the warn/critical thresholds (starter data)",
    "zones": "Areas inside a pandal (entry gate, queue...) with exit capacity (starter data)",
    "cameras": "Cameras per zone; 'simulated' unless you register a video (starter data)",
    "users": "Demo accounts and roles (hashes not exported)",
    "settings": "App flags such as demo_seeded and the model budget",
    "measurements": "Occupancy, entry and exit rates over time. Empty until the simulator or a camera runs",
    "forecasts": "5/10-minute predictions per pandal. Filled while running",
    "runs": "One row per processing run, with status and timing",
    "stages": "Steps inside each run (so you can see where time went or what failed)",
    "incidents": "Alerts raised by the rules (warning/critical), with status and owner",
    "incident_events": "History of each incident: who changed it, when, from and to status",
    "llm_calls": "Every AI model call: tokens, latency, estimated cost (no prompts stored)",
    "audit": "Who did what: sign-ins and every create, update or delete",
    "sessions": "Active demo sign-in tokens",
    "vision_counts": "People counts from video, with detector name and whether a real model was used",
}


def main() -> int:
    db = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "crowdguard.db"
    if not db.exists():
        print(f"Database not found: {db} (start the app once first)")
        return 1
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    (OUT / "csv").mkdir(parents=True, exist_ok=True)
    ddl = [r[0] + ";" for r in con.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY type DESC, name")]
    (OUT / "schema.sql").write_text("-- CrowdGuard AI database layout (SQLite), exported from a running install.\n\n"
                                    + "\n\n".join(ddl) + "\n", encoding="utf-8")
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    rows = []
    for t in tables:
        n = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        cols = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")')]
        rows.append((t, len(cols), n))
        if t in SEED_TABLES:
            keep = [c for c in cols if c not in SKIP_COLUMNS.get(t, set())]
            with (OUT / "csv" / f"{t}.csv").open("w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(keep)
                w.writerows(con.execute(f'SELECT {",".join(chr(34)+c+chr(34) for c in keep)} FROM "{t}" ORDER BY 1'))
    md = ["# Database tables\n", f"Exported from a fresh install: {len(tables)} tables. Row counts below are for that",
          "moment; tables marked *fills while running* start empty and grow as the simulator runs.\n",
          "| Table | Columns | Rows | What it holds |", "|---|---:|---:|---|"]
    for t, c, n in rows:
        md.append(f"| `{t}` | {c} | {n} | {PURPOSE.get(t, '')} |")
    (OUT / "TABLES.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"Wrote {OUT} ({len(tables)} tables)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
