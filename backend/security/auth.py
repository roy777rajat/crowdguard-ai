"""DEMO authentication and role-based access control.

!! DEMO ONLY. Local password+opaque-token sessions stored in SQLite. NOT production security:
no email verification, no password recovery, no MFA, no rate limiting, no token rotation.
Amazon Cognito is the planned replacement via the TokenVerifier interface below.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Any, Dict, List, Optional, Protocol

from backend.repositories.store import Repos

ROLES = ("admin", "operator", "manager", "auditor")

PERMISSIONS: Dict[str, tuple] = {
    "read": ROLES,
    "registry:write": ("admin",),
    "sim:control": ("admin", "operator"),
    "incident:write": ("admin", "operator", "manager"),
    "ops:read": ("admin", "operator", "auditor"),      # LLM, system metrics, settings (cross-pandal)
    "settings:write": ("admin",),
    "audit:read": ("admin", "auditor"),
}

SESSION_TTL_S = 8 * 3600


class TokenVerifier(Protocol):
    def verify(self, token: str) -> Optional[Dict[str, Any]]: ...


class CognitoVerifier:
    """Placeholder. Real implementation must validate JWT signature, issuer, audience and expiry
    against the Cognito user pool JWKS, and map cognito:groups to ROLES."""

    def verify(self, token: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError("Cognito token validation is not implemented in the demo")


def hash_password(password: str, salt: Optional[str] = None) -> tuple:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 120_000).hex()
    return digest, salt


def has_permission(user: Dict[str, Any], perm: str) -> bool:
    return user["role"] in PERMISSIONS.get(perm, ())


def can_access_pandal(user: Dict[str, Any], pandal_id: int) -> bool:
    if user["role"] != "manager":
        return True
    return pandal_id in (user.get("pandal_ids") or [])


def public_user(user: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": user["id"], "username": user["username"], "role": user["role"],
            "display_name": user.get("display_name"), "pandal_ids": user.get("pandal_ids") or []}


class DemoAuth:
    def __init__(self, repos: Repos):
        self.repos = repos

    def create_user(self, username: str, password: str, role: str, display_name: str = "",
                    pandal_ids: Optional[List[int]] = None) -> Dict[str, Any]:
        if role not in ROLES:
            raise ValueError("invalid role")
        digest, salt = hash_password(password)
        return self.repos.users.create(username=username, password_hash=digest, salt=salt, role=role,
                                       pandal_ids=pandal_ids or [], display_name=display_name or username,
                                       created_at=time.time())

    def login(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        rows = self.repos.users.list({"username": username})
        if not rows:
            hash_password(password)  # equalise timing a little
            return None
        user = rows[0]
        digest, _ = hash_password(password, user["salt"])
        if not hmac.compare_digest(digest, user["password_hash"]):
            return None
        token = secrets.token_urlsafe(32)
        self.repos.sessions.create(token=token, user_id=user["id"], expires_at=time.time() + SESSION_TTL_S)
        return {"token": token, "user": public_user(user)}

    def user_for_token(self, token: str) -> Optional[Dict[str, Any]]:
        sess = self.repos.sessions.get(token)
        if not sess or sess["expires_at"] < time.time():
            return None
        return self.repos.users.get(sess["user_id"])

    def logout(self, token: str) -> None:
        self.repos.db.execute("DELETE FROM sessions WHERE token=?", [token])
