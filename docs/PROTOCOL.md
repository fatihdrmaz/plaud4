# Plaud 4.0 web API: what this client relies on

These are observations from the `web.plaud.ai` app (November 2026), not documentation. Everything here may change without notice.

## Hosts

| Host | Used for |
|---|---|
| `https://api.plaud.ai` | user-session endpoints (refresh user token, list workspaces). For accounts outside the default region some of them answer `-302 user region mismatch` with `data.domains.api`, the regional host to use instead (e.g. `https://api-apse1.plaud.ai`). |
| workspace `domain`, e.g. `https://api-apse1.plaud.ai` | workspace token endpoints, file API (`/file-app/v4/...`) |

Every request needs browser-like headers: Cloudflare blocks Python's default User-Agent with an HTML 403. The web app sends `origin`/`referer: https://web.plaud.ai`, `app-platform: web`, `edit_from: web4`, a fresh `x-request-id` (UUID), and, on file calls, `x-device-id`, `x-timezone-iana`, `x-scope-type: workspace`, `x-scope-id: <workspace_id>`.

**Errors usually arrive as HTTP 200** with a negative `status` in the JSON body. Check `status == 0`.

## Two-layer auth

```
pld_urt (cookie, ~30 d, path-restricted, HttpOnly)
   │  POST api.plaud.ai/auth/refresh-user-token          (cookies only, empty body)
   ▼
pld_ut (cookie, ~24 h, HttpOnly)  ── Set-Cookie on the response
   │  POST {domain}/user-app/auth/workspace/token/{ws}   (cookies only)   "mint"
   ▼
workspace_token (~24 h) + refresh_token (~30 d)
   │  POST {domain}/user-app/auth/workspace/refresh/{ws}
   │       Authorization: Bearer <refresh_token>, X-Scope-Type/Id
   ▼
new workspace_token (+ rotated refresh_token)
```

Token responses carry `workspace_token`, `expires_in`, `refresh_token`, `refresh_expires_in`, `wt_expires_at`, `version_tag`.

Plaud **revokes workspace tokens** from time to time: `-419 workspace token revoked`, and on refresh `-420 token invalid or session expired, re-exchange required`. It looks like a newer mint for the same workspace (for example by your browser) invalidates older ones. The web app recovers by minting again from the cookies, and this client does the same. Holding only the workspace token and its refresh token is therefore not enough for unattended use.

`pld_urt` keeps its original expiry when used. Its `Path` restricts it to the refresh endpoint, so it doesn't show in the cookie list of normal requests. In Chrome DevTools: Network → any `api.plaud.ai` request → Cookies → *Show filtered-out request cookies*.

Logging out (`POST /auth/access-token-logout`) ends that session's cookies.

## Workspaces

`GET https://api.plaud.ai/team-app/workspaces/list?include_details=true` (cookies) → `data.workspaces[]` with `workspace_id`, `name`, `api_domain`, `member_id`, `role`, `region`, `workspace_type`, ….
The default "my recordings" folder is `n_system_<member_id>_1`.

## Upload (4 steps)

All under `{domain}/file-app/v4` with the workspace token.

1. `POST /upload/create` with `{file_name, file_size, file_type: "audio/mpeg", file_extension: "mp3"}` → `{upload_id, part_urls[], total_chunks}`.
2. `PUT` each part to its presigned S3 URL (`content-type` empty) and keep the `ETag`.
   This is an S3 multipart upload, so **every part except the last must be ≥ 5 MiB**. Splitting a 9.7 MB file into two equal halves (4.8 MB each) makes step 3 fail with `status=-500 internal error`.
3. `POST /upload/merge-part` with `{upload_id, parts: [{etag, part_number}]}`.
4. `POST /upload/confirm-audio-web` with `{file_id, upload_id, file_name, start_time (epoch ms), scene_source: 101, target_node_id}`.
   `file_id` is generated **client-side**: `"f_"` + 12 hex digits of epoch-ms + 20 random hex digits (UUIDv7-like).

Confirming starts transcription automatically.

## Processing state

`GET /files/detail/{file_id}` → `data.task.in_progress_tasks` (list) and `data.objects[]`, each with `object_type` and `task_status` (`1` = done, `null` = not started):

`AUDIO`, `TRANSCRIPT`, `POLISHED_TRANSCRIPT`, `SUMMARY`, `OUTLINE`

A ~20 minute recording had all of them done within about a minute of upload. Very short recordings produce no summary (`-1800002 Content too short to summarize`).

## Status codes seen

| status | meaning |
|---|---|
| `0` | ok |
| `-419` | workspace token revoked |
| `-420` | workspace session gone, re-exchange (mint) required |
| `-302` | user region mismatch (wrong regional host) |
| `-500` | internal error (seen on a bad multipart merge) |
| `-1800002` | content too short to summarize |
| `-1800907` | account migrated to 4.0 (old API) |
