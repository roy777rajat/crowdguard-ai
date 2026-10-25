"""FastAPI dependencies: authentication, role checks and pandal scoping.

Every protected route uses these server-side checks. Hiding things in the UI is not enforcement.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from fastapi import Depends, Header, HTTPException, Request

from backend.security.auth import can_access_pandal, has_permission
from backend.services.context import AppContext


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def bearer_token(authorization: Optional[str] = Header(default=None)) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})
    return authorization[7:].strip()


def current_user(token: str = Depends(bearer_token), ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    user = ctx.auth.user_for_token(token)
    if user is None:
        raise HTTPException(401, "Invalid or expired session", headers={"WWW-Authenticate": "Bearer"})
    return user


def require(perm: str) -> Callable[..., Dict[str, Any]]:
    def dep(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
        if not has_permission(user, perm):
            raise HTTPException(403, f"Role '{user['role']}' is not allowed to do this")
        return user
    return dep


def load_pandal(ctx: AppContext, user: Dict[str, Any], pandal_id: int) -> Dict[str, Any]:
    p = ctx.repos.pandals.get(pandal_id)
    if p is None:
        raise HTTPException(404, "Pandal not found")
    if not can_access_pandal(user, pandal_id):
        raise HTTPException(403, "You are not authorized for this pandal")
    return p
