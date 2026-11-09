"""Plaud 4.0 file API: upload audio, read file details and processing state."""
from __future__ import annotations

import math
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from typing import Callable, Dict, Optional

from . import auth, store
from .http import PlaudError, put_raw, request

FILE_API = "/file-app/v4"
MIN_PART = 5 * 1024 * 1024  # S3 multipart: every part except the last must be >= 5 MiB
SCENE_WEB_UPLOAD = 101


def new_file_id() -> str:
    """Client-side file id, UUIDv7-like: 12 hex of epoch-ms + 20 random hex."""
    return "f_" + f"{int(time.time() * 1000):012x}{uuid.uuid4().hex[12:]}"


def part_size(size: int, parts: int) -> int:
    if parts <= 1:
        return size
    p = max(math.ceil(size / parts), MIN_PART)
    if (parts - 1) * p >= size:
        raise PlaudError(f"cannot split {size} bytes into {parts} parts of >= 5 MiB")
    return p


class Plaud:
    """Small client for one Plaud 4.0 workspace.

    Credentials come from ``~/.config/plaud4`` (``plaud4 login``). Tokens are
    refreshed, and re-minted when Plaud revokes them, automatically.
    """

    def __init__(self, log: Callable[[str], None] = lambda m: None):
        self.log = log
        self.cfg = store.config()

    # -------------------------------------------------------------- transport
    def _headers(self, s: dict) -> Dict[str, str]:
        return {
            "authorization": "Bearer " + s["token"],
            "x-device-id": self.cfg["device_id"],
            "x-scope-id": s["workspace_id"],
            "x-scope-type": "workspace",
            "x-timezone-iana": self.cfg.get("timezone", "UTC"),
        }

    def api(self, method: str, path: str, body=None) -> dict:
        s = auth.ensure()
        url = auth._host(s.get("domain")) + FILE_API + path
        try:
            return request(method, url, headers=self._headers(s), body=body)[0]
        except PlaudError as e:
            if not e.auth_expired:
                raise
            self.log("workspace token rejected, minting a new one")
            s = auth.mint(s)
            return request(method, url, headers=self._headers(s), body=body)[0]

    # -------------------------------------------------------------- upload
    def upload(self, path: str, name: Optional[str] = None, start_ms: Optional[int] = None) -> str:
        """Upload an audio file; Plaud starts transcription on its own.
        Non-mp3 input is converted with ffmpeg. Returns the new file_id."""
        name = name or os.path.splitext(os.path.basename(path))[0]
        start_ms = int(start_ms if start_ms is not None else os.path.getmtime(path) * 1000)
        if not path.lower().endswith(".mp3"):
            with tempfile.TemporaryDirectory() as d:
                mp3 = os.path.join(d, "audio.mp3")
                self._to_mp3(path, mp3)
                return self._upload_mp3(mp3, name, start_ms)
        return self._upload_mp3(path, name, start_ms)

    @staticmethod
    def _to_mp3(src: str, dst: str) -> None:
        if not shutil.which("ffmpeg"):
            raise PlaudError("only .mp3 is uploaded directly; install ffmpeg to convert other formats")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src,
                        "-codec:a", "libmp3lame", "-q:a", "4", dst], check=True)

    def _upload_mp3(self, path: str, name: str, start_ms: int) -> str:
        s = auth.ensure()
        size = os.path.getsize(path)
        self.log(f"create ({size} bytes)")
        c = self.api("POST", "/upload/create", {"file_name": name, "file_size": size,
                                                "file_type": "audio/mpeg", "file_extension": "mp3"})["data"]
        urls = c["part_urls"]
        n = c.get("total_chunks", len(urls))
        chunk = part_size(size, n)
        parts = []
        with open(path, "rb") as f:
            for i, url in enumerate(urls, 1):
                parts.append({"etag": put_raw(url, f.read(chunk)), "part_number": i})
                self.log(f"part {i}/{n} uploaded")
        self.api("POST", "/upload/merge-part", {"upload_id": c["upload_id"], "parts": parts})
        fid = new_file_id()
        self.api("POST", "/upload/confirm-audio-web", {
            "file_id": fid, "upload_id": c["upload_id"], "file_name": name, "start_time": start_ms,
            "scene_source": SCENE_WEB_UPLOAD, "target_node_id": s["target_node_id"],
        })
        self.log(f"confirmed {fid}")
        return fid

    # -------------------------------------------------------------- state
    def detail(self, file_id: str) -> dict:
        return self.api("GET", f"/files/detail/{file_id}")["data"]

    def status(self, file_id: str) -> Dict[str, Optional[int]]:
        """{'busy': bool, 'AUDIO': None, 'TRANSCRIPT': 1, 'SUMMARY': 1, ...}
        task_status 1 = done; None = not started; anything else = failed."""
        d = self.detail(file_id)
        out: Dict[str, Optional[int]] = {"busy": bool((d.get("task") or {}).get("in_progress_tasks"))}
        for o in d.get("objects") or []:
            out[o.get("object_type")] = o.get("task_status")
        return out

    def wait(self, file_id: str, timeout: int = 7200, every: int = 30,
             need: str = "TRANSCRIPT") -> Dict[str, Optional[int]]:
        """Block until `need` (TRANSCRIPT or SUMMARY) is done. Raises on failure/timeout."""
        end = time.time() + timeout
        while True:
            st = self.status(file_id)
            v = st.get(need)
            if v == 1:
                return st
            if v not in (None, 1):
                raise PlaudError(f"{need} failed with task_status={v}")
            if time.time() > end:
                raise PlaudError(f"timed out waiting for {need}")
            time.sleep(every)
