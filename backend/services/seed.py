"""Demo seed data. Everything here is fictional and non-sensitive.

Structure: Location -> Pandal (association) -> 3 Zones -> 1 camera per zone.
Databases created by the first release have 3 flat demo pandals without a location; they are
deactivated (kept, not deleted) the first time the structured seed runs.
"""
from __future__ import annotations

import time
from typing import Dict, List

from backend.repositories.store import Repos
from backend.security.auth import DemoAuth
from backend.services.structure import create_pandal, ensure_locations

DEMO_PASSWORDS = {"admin": "demo-admin", "operator": "demo-operator",
                  "manager": "demo-manager", "auditor": "demo-auditor"}

DEMO_ZONES = ["Entry gate", "Main pandal", "Exit gate"]
# Fictional association names, so no real organisation is implied.
DEMO_PANDALS: Dict[str, List[str]] = {
    "Kolkata South": ["Dakshin Sarbojanin Association", "Lake Palli Association"],
    "Kolkata North": ["Uttar Sarbojanin Association", "Shyam Palli Association"],
    "Kolkata East": ["Purba Sarbojanin Association"],
    "Kolkata West": ["Paschim Sarbojanin Association"],
}


def seed_demo(repos: Repos, auth: DemoAuth) -> None:
    locations = {loc["name"]: loc for loc in ensure_locations(repos)}
    # Demo pandals are created once. After that the admin owns the registry: deleting every pandal must
    # not bring the demo ones back on the next start.
    first_time = repos.get_setting("demo_seeded") is None
    if first_time and not any(p.get("location_id") for p in repos.pandals.list()):
        for legacy in repos.pandals.list({"active": 1}):
            repos.pandals.update(legacy["id"], active=0, updated_at=time.time())
        for loc_name, names in DEMO_PANDALS.items():
            for name in names:
                create_pandal(repos, locations[loc_name]["id"], name, DEMO_ZONES)
    repos.set_setting("demo_seeded", True)
    # Databases seeded earlier carry a "Demo " prefix on names shown in the UI; drop it when that is safe.
    taken = {p["name"] for p in repos.pandals.list()}
    for p in repos.pandals.list():
        if p["name"].startswith("Demo ") and p["name"][5:] not in taken:
            repos.pandals.update(p["id"], name=p["name"][5:], updated_at=time.time())
            taken.add(p["name"][5:])
    for u in repos.users.list():
        if (u.get("display_name") or "").startswith("Demo "):
            repos.users.update(u["id"], display_name=u["display_name"][5:])
    structured = [p["id"] for p in repos.pandals.list({"active": 1}) if p.get("location_id")]
    if not repos.users.list():
        auth.create_user("admin", DEMO_PASSWORDS["admin"], "admin", "Administrator")
        auth.create_user("operator", DEMO_PASSWORDS["operator"], "operator", "Control-room Operator")
        auth.create_user("manager", DEMO_PASSWORDS["manager"], "manager", "Pandal Manager", structured[:1])
        auth.create_user("auditor", DEMO_PASSWORDS["auditor"], "auditor", "Auditor")
    else:
        # A manager still pointing at a deactivated flat demo pandal gets the first structured one instead.
        active = set(structured)
        for u in repos.users.list({"role": "manager"}):
            ids = u.get("pandal_ids") or []
            if ids and not (set(ids) & active) and structured:
                repos.users.update(u["id"], pandal_ids=structured[:1])
