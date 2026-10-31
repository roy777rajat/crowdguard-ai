# Database tables

Exported from a fresh install: 16 tables. Row counts below are for that
moment; tables marked *fills while running* start empty and grow as the simulator runs.

| Table | Columns | Rows | What it holds |
|---|---:|---:|---|
| `audit` | 7 | 1 | Who did what: sign-ins and every create, update or delete |
| `cameras` | 11 | 18 | Cameras per zone; 'simulated' unless you register a video (starter data) |
| `forecasts` | 6 | 0 | 5/10-minute predictions per pandal. Filled while running |
| `incident_events` | 8 | 0 | History of each incident: who changed it, when, from and to status |
| `incidents` | 14 | 0 | Alerts raised by the rules (warning/critical), with status and owner |
| `llm_calls` | 20 | 0 | Every AI model call: tokens, latency, estimated cost (no prompts stored) |
| `locations` | 3 | 4 | Areas of the city that group pandals (starter data) |
| `measurements` | 12 | 0 | Occupancy, entry and exit rates over time. Empty until the simulator or a camera runs |
| `pandals` | 11 | 6 | Each pandal: capacity and the warn/critical thresholds (starter data) |
| `runs` | 14 | 0 | One row per processing run, with status and timing |
| `sessions` | 3 | 1 | Active demo sign-in tokens |
| `settings` | 2 | 1 | App flags such as demo_seeded and the model budget |
| `stages` | 10 | 0 | Steps inside each run (so you can see where time went or what failed) |
| `users` | 8 | 4 | Demo accounts and roles (hashes not exported) |
| `vision_counts` | 12 | 0 | People counts from video, with detector name and whether a real model was used |
| `zones` | 6 | 18 | Areas inside a pandal (entry gate, queue...) with exit capacity (starter data) |
