"""Low-level HTTP for the Plaud 4.0 web API (stdlib only).

Plaud sits behind Cloudflare, which rejects Python's default User-Agent, and it
reports most errors as HTTP 200 with a negative ``status`` in the JSON body.
Both quirks are handled here.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
import uuid
from typing import Dict, Optional, Tuple

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"
)

BROWSER_HEADERS = {
    "accept": "*/*",
    "user-agent": USER_AGENT,
    "origin": "https://web.plaud.ai",
    "referer": "https://web.plaud.ai/",
    "app-platform": "web",
    "app-language": "en",
    "edit-from": "web",
    "edit_from": "web4",
}

# Known body ``status`` codes
STATUS_TOKEN_REVOKED = -419      # workspace token revoked
STATUS_REEXCHANGE = -420         # workspace session gone; mint a new one from the user session
STATUS_REGION_MISMATCH = -302    # wrong regional API host
STATUS_MIGRATED = -1800907       # old (pre-4.0) API called for a migrated account
STATUS_TOO_SHORT = -1800002      # recording too short to summarize


class PlaudError(RuntimeError):
    def __init__(self, message: str, status: Optional[int] = None, http_status: Optional[int] = None,
                 payload: Optional[dict] = None):
        super().__init__(message)
        self.status = status
        self.http_status = http_status
        self.payload = payload or {}

    @property
    def region_api(self) -> Optional[str]:
        """On -302 (user region mismatch) Plaud says which host to use."""
        if self.status != STATUS_REGION_MISMATCH:
            return None
        return ((self.payload.get("data") or {}).get("domains") or {}).get("api")

    @property
    def auth_expired(self) -> bool:
        return self.status in (STATUS_TOKEN_REVOKED, STATUS_REEXCHANGE) or self.http_status in (401, 403)


def request(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    body=None,
    cookies: Optional[Dict[str, str]] = None,
    timeout: int = 120,
    check_status: bool = True,
) -> Tuple[dict, Dict[str, str]]:
    """Send a request. Returns (json_body, cookies_set_by_server)."""
    data = None
    h = dict(BROWSER_HEADERS)
    h["x-request-id"] = str(uuid.uuid4())
    if body is not None:
        data = json.dumps(body).encode()
        h["content-type"] = "application/json"
    elif method in ("POST", "PUT"):
        data = b""
    h.update(headers or {})
    if cookies:
        h["cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in h.items():
        req.add_header(k, v)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        raw = e.read()[:300]
        status = None
        try:
            status = json.loads(raw).get("status")
        except Exception:
            pass
        text = raw.decode(errors="replace")
        if "<html" in text.lower():
            text = "HTML response (Cloudflare block or wrong host)"
        raise PlaudError(f"HTTP {e.code} on {url}: {text[:200]}", status=status, http_status=e.code)
    set_cookies: Dict[str, str] = {}
    for sc in resp.headers.get_all("Set-Cookie") or []:
        m = re.match(r"\s*([^=;\s]+)=([^;]*)", sc)
        if m and m.group(2):
            set_cookies[m.group(1)] = m.group(2)
    raw = resp.read()
    try:
        payload = json.loads(raw) if raw else {}
    except ValueError:
        raise PlaudError(f"non-JSON response from {url}: {raw[:120]!r}")
    if check_status and payload.get("status") not in (0, None):
        raise PlaudError(
            f"{url.split('.ai', 1)[-1]}: status={payload.get('status')} msg={payload.get('msg')}",
            status=payload.get("status"),
            payload=payload,
        )
    return payload, set_cookies


def put_raw(url: str, chunk: bytes, timeout: int = 600) -> str:
    """PUT a part to a presigned S3 URL, return its ETag."""
    req = urllib.request.Request(url, data=chunk, method="PUT")
    req.add_header("content-type", "")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        etag = (r.headers.get("ETag") or "").strip('"')
    if not etag:
        raise PlaudError("S3 did not return an ETag for an upload part")
    return etag
