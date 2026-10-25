"""Repository layer. Services depend on this interface, not on SQL.

A DynamoDB/S3 implementation can later provide the same methods.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from backend.db import Database


class Repository:
    table = ""
    pk = "id"

    def __init__(self, db: Database):
        self.db = db

    def create(self, **fields: Any) -> Dict[str, Any]:
        new_id = self.db.insert(self.table, fields)
        return self.get(fields[self.pk] if self.pk in fields else new_id)  # type: ignore[return-value]

    def get(self, key: Any) -> Optional[Dict[str, Any]]:
        return self.db.one(f"SELECT * FROM {self.table} WHERE {self.pk}=?", [key])

    def list(self, where: Optional[Dict[str, Any]] = None, order: Optional[str] = None,
             desc: bool = False, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        clauses, params = [], []
        for k, v in (where or {}).items():
            if v is None:
                continue
            clauses.append(f"{k}=?")
            params.append(v)
        sql = f"SELECT * FROM {self.table}"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += f" ORDER BY {order or self.pk} {'DESC' if desc else 'ASC'}"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return self.db.query(sql, params)

    def update(self, key: Any, **fields: Any) -> Optional[Dict[str, Any]]:
        self.db.update(self.table, self.pk, key, fields)
        return self.get(key)


class Pandals(Repository):
    table = "pandals"


class Zones(Repository):
    table = "zones"


class Cameras(Repository):
    table = "cameras"


class Measurements(Repository):
    table = "measurements"

    def recent(self, pandal_id: int, limit: int = 5) -> List[Dict[str, Any]]:
        return self.db.query("SELECT * FROM measurements WHERE pandal_id=? ORDER BY id DESC LIMIT ?",
                             [pandal_id, limit])


class Forecasts(Repository):
    table = "forecasts"


class Runs(Repository):
    table = "runs"
    pk = "run_id"


class Stages(Repository):
    table = "stages"


class Incidents(Repository):
    table = "incidents"

    def open_with_key(self, key: str) -> Optional[Dict[str, Any]]:
        return self.db.one("SELECT * FROM incidents WHERE dedupe_key=? AND status!='resolved' ORDER BY id DESC",
                           [key])


class IncidentEvents(Repository):
    table = "incident_events"


class LlmCalls(Repository):
    table = "llm_calls"


class Audit(Repository):
    table = "audit"

    def log(self, actor: str, action: str, entity: str, entity_id: Any = None,
            detail: Optional[Dict[str, Any]] = None) -> None:
        self.db.insert("audit", {"ts": time.time(), "actor": actor, "action": action, "entity": entity,
                                 "entity_id": None if entity_id is None else str(entity_id),
                                 "detail": detail or {}})


class Users(Repository):
    table = "users"


class Sessions(Repository):
    table = "sessions"
    pk = "token"


class VisionCounts(Repository):
    table = "vision_counts"

    def recent_for_cameras(self, camera_ids: List[int], since: float, limit_per_camera: int = 120) -> List[Dict[str, Any]]:
        """Newest-first rows per camera since a timestamp, capped per camera."""
        out: List[Dict[str, Any]] = []
        for cid in camera_ids:
            out.extend(self.db.query("SELECT * FROM vision_counts WHERE camera_id=? AND ts>? ORDER BY id DESC LIMIT ?",
                                     [cid, since, limit_per_camera]))
        return out


class Locations(Repository):
    table = "locations"


class Repos:
    def __init__(self, db: Database):
        self.db = db
        self.pandals = Pandals(db)
        self.zones = Zones(db)
        self.cameras = Cameras(db)
        self.measurements = Measurements(db)
        self.forecasts = Forecasts(db)
        self.runs = Runs(db)
        self.stages = Stages(db)
        self.incidents = Incidents(db)
        self.events = IncidentEvents(db)
        self.llm_calls = LlmCalls(db)
        self.audit = Audit(db)
        self.users = Users(db)
        self.sessions = Sessions(db)
        self.vision = VisionCounts(db)
        self.locations = Locations(db)

    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.db.one("SELECT value FROM settings WHERE key=?", [key])
        if row is None:
            return default
        return row["value"]  # already JSON-decoded by the Database layer

    def set_setting(self, key: str, value: Any) -> None:
        import json
        self.db.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", [key, json.dumps(value)])
