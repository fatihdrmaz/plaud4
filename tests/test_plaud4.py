import base64
import json
import os
import tempfile
import time
import unittest

os.environ["PLAUD4_HOME"] = tempfile.mkdtemp()

from plaud4 import auth, client, store  # noqa: E402
from plaud4.cli import _cookies_from_curl  # noqa: E402
from plaud4.http import PlaudError  # noqa: E402


def jwt(exp):
    body = base64.urlsafe_b64encode(json.dumps({"exp": int(exp)}).encode()).decode().rstrip("=")
    return f"h.{body}.s"


TOKEN_DATA = {"workspace_token": "WT", "expires_in": 86400, "refresh_token": "RT", "refresh_expires_in": 2592000}


class FakeAPI:
    """Stands in for plaud4.http.request; records calls."""

    def __init__(self):
        self.calls = []
        self.ws_refresh_status = 0
        self.revoke_next_file_call = False

    def __call__(self, method, url, headers=None, body=None, cookies=None, **kw):
        path = url.split(".ai", 1)[1]
        self.calls.append((method, path, dict(cookies or {})))
        if path == auth.PATH_USER_REFRESH:
            if not (cookies or {}).get("pld_urt"):
                raise PlaudError("HTTP 401", http_status=401)
            return {"status": 0}, {"pld_ut": jwt(time.time() + 86400)}
        if path.startswith(auth.PATH_WS_REFRESH):
            if self.ws_refresh_status:
                raise PlaudError("revoked", status=self.ws_refresh_status)
            return {"status": 0, "data": TOKEN_DATA}, {}
        if path.startswith(auth.PATH_MINT):
            if auth.jwt_exp(cookies.get("pld_ut")) < time.time():
                raise PlaudError("HTTP 401", http_status=401)
            return {"status": 0, "data": TOKEN_DATA}, {}
        if path.startswith(auth.PATH_WORKSPACES):
            if url.startswith("https://api.plaud.ai"):
                raise PlaudError("region", status=-302, payload={"status": -302, "data": {
                    "domains": {"api": "https://api-x.plaud.ai"}}})
            return {"status": 0, "data": {"workspaces": [
                {"workspace_id": "ws_A", "name": "Personal", "api_domain": "https://api-x.plaud.ai",
                 "member_id": "mem_B", "region": "aws:x"}]}}, {}
        if path.startswith(client.FILE_API):
            if self.revoke_next_file_call:
                self.revoke_next_file_call = False
                raise PlaudError("revoked", status=-419)
            p = path[len(client.FILE_API):]
            if p == "/upload/create":
                return {"status": 0, "data": {"upload_id": "U", "part_urls": ["u1", "u2"], "total_chunks": 2}}, {}
            if p.startswith("/files/detail/"):
                return {"status": 0, "data": {"task": {"in_progress_tasks": []}, "objects": [
                    {"object_type": "TRANSCRIPT", "task_status": 1}]}}, {}
            return {"status": 0, "data": {}}, {}
        raise AssertionError("unexpected " + url)


class Base(unittest.TestCase):
    def setUp(self):
        for f in os.listdir(store.HOME):
            os.remove(os.path.join(store.HOME, f))
        self.api = FakeAPI()
        auth.request = self.api
        client.request = self.api
        self.parts = []
        client.put_raw = lambda url, chunk, **kw: (self.parts.append(len(chunk)) or "etag-" + url)


class TestHelpers(unittest.TestCase):
    def test_part_size(self):
        self.assertEqual(client.part_size(10_146_740, 2), 5 * 1024 * 1024)
        self.assertEqual(client.part_size(800_000, 1), 800_000)
        with self.assertRaises(PlaudError):
            client.part_size(6_000_000, 3)

    def test_jwt_exp(self):
        self.assertEqual(auth.jwt_exp(jwt(1234)), 1234)
        self.assertIsNone(auth.jwt_exp("garbage"))

    def test_curl(self):
        t = "curl 'https://api.plaud.ai/x' \\\n -H 'accept: */*' \\\n -b 'pld_ut=AAA; pld_urt=BBB; _ga=1'"
        self.assertEqual(_cookies_from_curl(t)["pld_urt"], "BBB")

    def test_file_id(self):
        self.assertRegex(client.new_file_id(), r"^f_[0-9a-f]{32}$")


class TestAuth(Base):
    def test_login_picks_workspace_and_mints(self):
        s = auth.import_cookies({"pld_ut": jwt(time.time() + 86400), "pld_urt": jwt(time.time() + 9e5)})
        self.assertEqual(s["workspace_id"], "ws_A")
        self.assertEqual(s["target_node_id"], "n_system_mem_B_1")
        self.assertEqual(store.session()["token"], "WT")
        self.assertEqual(s["domain"], "https://api-x.plaud.ai")
        self.assertEqual(auth.user_api(), "https://api-x.plaud.ai")  # learned from -302

    def test_revoked_workspace_falls_back_to_mint_with_user_refresh(self):
        store.save_cookies({"pld_ut": jwt(time.time() - 10), "pld_urt": jwt(time.time() + 9e5)})
        store.save_session({"workspace_id": "ws_A", "domain": "api-x.plaud.ai", "refresh_token": "old"})
        self.api.ws_refresh_status = -420
        s = auth.refresh_workspace()
        self.assertEqual(s["token"], "WT")
        paths = [c[1] for c in self.api.calls]
        self.assertIn(auth.PATH_USER_REFRESH, paths)
        self.assertGreater(auth.jwt_exp(store.cookies()["pld_ut"]), time.time())

    def test_no_urt_is_a_clear_error(self):
        store.save_cookies({"pld_ut": jwt(time.time() - 10)})
        with self.assertRaises(PlaudError):
            auth.ensure_user()


class TestClient(Base):
    def setUp(self):
        super().setUp()
        store.save_cookies({"pld_ut": jwt(time.time() + 86400), "pld_urt": jwt(time.time() + 9e5)})
        store.save_session({"workspace_id": "ws_A", "domain": "api-x.plaud.ai", "target_node_id": "n_system_mem_B_1",
                            "token": "WT", "expires_at": (time.time() + 86400) * 1000, "refresh_token": "RT"})

    def test_upload_two_parts(self):
        f = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        f.write(os.urandom(10_146_740))
        f.close()
        fid = client.Plaud().upload(f.name, "Meeting")
        self.assertRegex(fid, r"^f_")
        self.assertEqual(self.parts, [5 * 1024 * 1024, 10_146_740 - 5 * 1024 * 1024])
        paths = [c[1].split("/v4", 1)[-1] for c in self.api.calls]
        self.assertEqual(paths, ["/upload/create", "/upload/merge-part", "/upload/confirm-audio-web"])

    def test_revoked_token_is_reminted_and_retried(self):
        self.api.revoke_next_file_call = True
        st = client.Plaud().status("f_x")
        self.assertEqual(st["TRANSCRIPT"], 1)
        self.assertTrue(any(c[1].startswith(auth.PATH_MINT) for c in self.api.calls))

    def test_files_are_private(self):
        mode = os.stat(store.path("cookies.json")).st_mode & 0o777
        self.assertEqual(mode, 0o600)


if __name__ == "__main__":
    unittest.main()
