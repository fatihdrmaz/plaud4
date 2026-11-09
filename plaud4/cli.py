"""plaud4 command line."""
from __future__ import annotations

import argparse
import getpass
import json
import re
import sys
import time

from . import __version__, auth, store
from .client import Plaud
from .http import PlaudError


def _fmt(epoch_s) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(epoch_s)) if epoch_s else "-"


def _left(epoch_s) -> str:
    if not epoch_s:
        return "-"
    h = (epoch_s - time.time()) / 3600
    return f"{h:.1f} h" if abs(h) < 48 else f"{h / 24:.1f} days"


def _cookies_from_curl(text: str) -> dict:
    m = (re.search(r"(?:-b|--cookie)\s+\$?'([^']+)'", text)
         or re.search(r"-H\s+\$?'[Cc]ookie:\s*([^']+)'", text))
    if not m:
        return {}
    out = {}
    for part in m.group(1).split(";"):
        if "=" in part:
            k, v = part.strip().split("=", 1)
            out[k] = v
    return out


def cmd_login(a) -> None:
    c = {}
    if a.curl:
        print("Paste the 'Copy as cURL' text, then Enter and Ctrl-D:", file=sys.stderr)
        c = _cookies_from_curl(sys.stdin.read())
    print("Enter cookie values from DevTools (input is hidden). See README > Login.", file=sys.stderr)
    for name in ("pld_ut", "pld_urt"):
        if name not in c:
            v = getpass.getpass(f"{name}: ").strip()
            if v:
                c[name] = v
    s = auth.import_cookies({k: v for k, v in c.items() if k.startswith("pld_")}, a.workspace)
    print(f"logged in: workspace {s['workspace_id']} ({s.get('name') or '-'}), "
          f"token valid until {_fmt(s['expires_at'] / 1000)}")


def cmd_workspaces(a) -> None:
    for w in auth.list_workspaces():
        i = auth.workspace_info(w)
        print(f"{i['workspace_id']}  {i['name'] or '-'}  domain={i['domain'] or '-'}  member={i['member_id'] or '-'}")


def cmd_status(a) -> None:
    s, c = store.session(), store.cookies()
    print("workspace          :", s.get("workspace_id", "- (run plaud4 login)"), "@", s.get("domain", "-"))
    print("workspace token    :", _left(s.get("expires_at", 0) / 1000), f"({_fmt(s.get('expires_at', 0) / 1000)})")
    for name, label in (("pld_ut", "user token (pld_ut)"), ("pld_urt", "user refresh (pld_urt)")):
        e = auth.jwt_exp(c.get(name))
        print(f"{label:<19}:", "missing" if name not in c else f"{_left(e)} ({_fmt(e)})")
    e = auth.jwt_exp(c.get("pld_urt"))
    if e and e - time.time() < 3 * 86400:
        print("\nWARNING: pld_urt expires soon - run `plaud4 login` again before it does.")


def cmd_refresh(a) -> None:
    s = auth.refresh_workspace() if a.force else auth.ensure()
    print("workspace token valid until", _fmt(s["expires_at"] / 1000))


def cmd_upload(a) -> None:
    p = Plaud(log=(lambda m: print(m, file=sys.stderr)) if a.verbose else (lambda m: None))
    fid = p.upload(a.file, a.name, a.start_ms)
    print(fid)
    if a.wait:
        st = p.wait(fid, need=a.wait.upper())
        print(json.dumps(st))


def cmd_detail(a) -> None:
    d = Plaud().detail(a.file_id)
    if a.raw:
        print(json.dumps(d, ensure_ascii=False, indent=1))
        return
    print("name   :", d.get("file_name") or d.get("name"))
    print("busy   :", bool((d.get("task") or {}).get("in_progress_tasks")))
    for o in d.get("objects") or []:
        print(f"  {o.get('object_type'):<20} task_status={o.get('task_status')}  lang={o.get('language')}")


def cmd_wait(a) -> None:
    print(json.dumps(Plaud().wait(a.file_id, timeout=a.timeout, need=a.need.upper())))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="plaud4", description="Unofficial Plaud 4.0 client")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("login", help="store pld_ut + pld_urt cookies and pick a workspace")
    p.add_argument("--curl", action="store_true", help="read cookies from a pasted 'Copy as cURL'")
    p.add_argument("--workspace", help="workspace id (default: first one)")
    p.set_defaults(fn=cmd_login)

    sub.add_parser("workspaces", help="list workspaces of the account").set_defaults(fn=cmd_workspaces)
    sub.add_parser("status", help="show token / cookie lifetimes").set_defaults(fn=cmd_status)

    p = sub.add_parser("refresh", help="refresh tokens now (use from cron)")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_refresh)

    p = sub.add_parser("upload", help="upload an audio file (Plaud transcribes it)")
    p.add_argument("file")
    p.add_argument("--name")
    p.add_argument("--start-ms", type=int, help="recording start, epoch ms (default: file mtime)")
    p.add_argument("--wait", choices=["transcript", "summary"], help="block until processed")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(fn=cmd_upload)

    p = sub.add_parser("detail", help="processing state of a file")
    p.add_argument("file_id")
    p.add_argument("--raw", action="store_true", help="print the full JSON (contains your content)")
    p.set_defaults(fn=cmd_detail)

    p = sub.add_parser("wait", help="wait until a file is transcribed")
    p.add_argument("file_id")
    p.add_argument("--need", default="transcript", choices=["transcript", "summary"])
    p.add_argument("--timeout", type=int, default=7200)
    p.set_defaults(fn=cmd_wait)

    a = ap.parse_args(argv)
    try:
        a.fn(a)
    except PlaudError as e:
        print("error:", e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
