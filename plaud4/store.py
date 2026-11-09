"""On-disk state: session, cookies, config. Every file is written atomically with mode 600."""
from __future__ import annotations

import json
import os
import secrets
import time
from typing import Any, Dict

HOME = os.environ.get("PLAUD4_HOME") or os.path.expanduser("~/.config/plaud4")


def path(name: str) -> str:
    return os.path.join(HOME, name)


def read(name: str, default: Any = None) -> Any:
    try:
        with open(path(name)) as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def write(name: str, data: Any) -> None:
    os.makedirs(HOME, mode=0o700, exist_ok=True)
    tmp = path(name) + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path(name))


def session() -> Dict[str, Any]:
    """workspace_id, domain, member_id, target_node_id, token, expires_at,
    refresh_token, refresh_expires_at (ms epoch)."""
    return read("session.json", {}) or {}


def save_session(s: Dict[str, Any]) -> None:
    write("session.json", s)


def cookies() -> Dict[str, str]:
    return (read("cookies.json", {}) or {}).get("cookies", {})


def save_cookies(c: Dict[str, str]) -> None:
    write("cookies.json", {"cookies": c, "updated_at": int(time.time())})


def _local_timezone() -> str:
    tz = os.environ.get("TZ", "")
    if "/" in tz:
        return tz.lstrip(":")
    try:
        with open("/etc/timezone") as f:
            return f.read().strip() or "UTC"
    except OSError:
        pass
    try:
        target = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in target:
            return target.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    return "UTC"


def config() -> Dict[str, Any]:
    """device_id (random, stable per install) and IANA timezone (edit config.json to change)."""
    c = read("config.json", None)
    if not c:
        c = {"device_id": secrets.token_hex(8), "timezone": _local_timezone()}
        write("config.json", c)
    return c
