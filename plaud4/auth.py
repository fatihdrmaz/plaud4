"""Authentication for Plaud 4.0.

Plaud 4.0 uses two layers, the same way web.plaud.ai does:

* A **user session**, held in two HttpOnly cookies on ``.plaud.ai``:
  ``pld_ut`` (user token, ~24 h) and ``pld_urt`` (user refresh token, ~30 days,
  path-restricted so it is only sent to the refresh endpoint).
* A **workspace token** (~24 h) plus a workspace refresh token, minted from the
  user session for one workspace. All file APIs use the workspace token.

Plaud revokes workspace tokens from time to time (``-419`` / ``-420``); the web
app silently mints a new one from the user session. This module does the same.
"""
from __future__ import annotations

import base64
import json
import time
from typing import Dict, List, Optional

from . import store
from .http import PlaudError, request

GLOBAL_API = "https://api.plaud.ai"

PATH_USER_REFRESH = "/auth/refresh-user-token"
PATH_WORKSPACES = "/team-app/workspaces/list"
PATH_MINT = "/user-app/auth/workspace/token/"
PATH_WS_REFRESH = "/user-app/auth/workspace/refresh/"

EARLY = 3 * 3600  # refresh anything that expires within 3 hours


def _host(domain: Optional[str]) -> str:
    d = (domain or GLOBAL_API).rstrip("/")
    return d if d.startswith("http") else "https://" + d


def jwt_exp(token: Optional[str]) -> Optional[int]:
    """`exp` claim (epoch seconds) of a JWT, without verifying it."""
    try:
        part = token.split(".")[1]
        return int(json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))["exp"])
    except Exception:
        return None


# ------------------------------------------------------------------ user session
def user_api() -> str:
    """Host for user-session calls. api.plaud.ai answers -302 with the account's
    regional host (e.g. https://api-apse1.plaud.ai); it is remembered here."""
    return (store.read("region.json", {}) or {}).get("api") or GLOBAL_API


def user_request(method: str, path: str, cookies: Dict[str, str]):
    try:
        return request(method, user_api() + path, cookies=cookies)
    except PlaudError as e:
        if not e.region_api or e.region_api.rstrip("/") == user_api():
            raise
        store.write("region.json", {"api": e.region_api.rstrip("/")})
        return request(method, user_api() + path, cookies=cookies)


def refresh_user(cookies: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Use pld_urt to obtain a fresh pld_ut. Saves and returns the cookie jar."""
    c = dict(cookies if cookies is not None else store.cookies())
    if not c.get("pld_urt"):
        raise PlaudError("no pld_urt cookie stored - run `plaud4 login`")
    _, new = user_request("POST", PATH_USER_REFRESH, c)
    c.update(new)
    store.save_cookies(c)
    return c


def ensure_user(cookies: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Return cookies with a pld_ut that is valid for at least EARLY seconds."""
    c = dict(cookies if cookies is not None else store.cookies())
    exp = jwt_exp(c.get("pld_ut"))
    if exp is None or exp - time.time() < EARLY:
        c = refresh_user(c)
    return c


def list_workspaces(cookies: Optional[Dict[str, str]] = None) -> List[dict]:
    c = ensure_user(cookies)
    d, new = user_request("GET", PATH_WORKSPACES + "?include_details=true", c)
    if new:
        c.update(new)
        store.save_cookies(c)
    return (d.get("data") or {}).get("workspaces") or []


def _ws_field(ws: dict, *names):
    for n in names:
        if ws.get(n):
            return ws[n]
    return None


def workspace_info(ws: dict) -> dict:
    """Normalise one entry of the workspace list."""
    member = _ws_field(ws, "member_id", "memberId")
    return {
        "workspace_id": _ws_field(ws, "workspace_id", "workspaceId", "id"),
        "name": _ws_field(ws, "name", "workspace_name"),
        "domain": _ws_field(ws, "domain", "api_domain"),
        "member_id": member,
        # Default "My recordings" folder of the member, e.g. n_system_mem_xxx_1
        "target_node_id": f"n_system_{member}_1" if member else None,
    }


# ------------------------------------------------------------------ workspace token
def _apply(s: dict, data: dict) -> dict:
    tok = data.get("workspace_token") or data.get("access_token")
    if not tok:
        raise PlaudError("token response without a token; fields: " + ", ".join(sorted(data)))
    now = int(time.time() * 1000)
    s["token"] = tok
    s["expires_at"] = now + int(data.get("expires_in") or 0) * 1000
    if data.get("refresh_token"):
        s["refresh_token"] = data["refresh_token"]
    if data.get("refresh_expires_in") is not None:
        s["refresh_expires_at"] = now + int(data["refresh_expires_in"]) * 1000
    store.save_session(s)
    return s


def mint(s: Optional[dict] = None) -> dict:
    """Mint a new workspace token from the user session (what the web app does
    after a -419/-420)."""
    s = dict(s if s is not None else store.session())
    ws = s.get("workspace_id")
    if not ws:
        raise PlaudError("no workspace selected - run `plaud4 login`")
    c = ensure_user()
    last = None
    for host in dict.fromkeys((_host(s.get("domain")), user_api())):
        try:
            d, new = request("POST", host + PATH_MINT + ws, cookies=c)
        except PlaudError as e:
            last = e
            continue
        if new:
            c.update(new)
            store.save_cookies(c)
        return _apply(s, d.get("data") or {})
    raise last


def refresh_workspace(s: Optional[dict] = None) -> dict:
    """Rotate the workspace token with its refresh token; fall back to mint()."""
    s = dict(s if s is not None else store.session())
    if s.get("refresh_token"):
        try:
            d, _ = request(
                "POST",
                _host(s.get("domain")) + PATH_WS_REFRESH + s["workspace_id"],
                headers={
                    "Authorization": "Bearer " + s["refresh_token"],
                    "X-Scope-Type": "workspace",
                    "X-Scope-Id": s["workspace_id"],
                },
            )
            return _apply(s, d.get("data") or {})
        except PlaudError as e:
            if not e.auth_expired:
                raise
    return mint(s)


def ensure(s: Optional[dict] = None) -> dict:
    """Session whose workspace token is valid for at least EARLY seconds.
    Also keeps the user session warm so pld_urt is used regularly."""
    s = dict(s if s is not None else store.session())
    if store.cookies().get("pld_urt"):
        try:
            ensure_user()
        except PlaudError:
            pass  # surfaced later if a mint is actually needed
    if not s.get("token") or s.get("expires_at", 0) / 1000 - time.time() < EARLY:
        s = refresh_workspace(s)
    return s


def import_cookies(c: Dict[str, str], workspace: Optional[str] = None) -> dict:
    """Store browser cookies, pick a workspace and mint its first token."""
    if "pld_urt" not in c or "pld_ut" not in c:
        raise PlaudError("both pld_ut and pld_urt are required (see README: Login)")
    store.save_cookies(c)
    wss = [workspace_info(w) for w in list_workspaces(c)]
    if not wss:
        raise PlaudError("no workspaces returned for this account")
    chosen = next((w for w in wss if w["workspace_id"] == workspace), None) if workspace else wss[0]
    if not chosen:
        raise PlaudError(f"workspace {workspace} not found; available: "
                         + ", ".join(w["workspace_id"] for w in wss))
    s = {k: v for k, v in chosen.items() if v}
    s = mint(s)
    return s
