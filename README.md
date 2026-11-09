# plaud4

Unofficial Python client for the **Plaud 4.0** web API. Upload audio from a script or a server into your own Plaud account, let Plaud transcribe and summarise it, and keep the login alive without opening a browser every day.

In November 2026 Plaud migrated accounts to "4.0". The old API answers `-1800907 Account migrated to 4.0`, and existing unofficial clients stopped working. This one talks to the 4.0 API the way `web.plaud.ai` does.

> **Not affiliated with Plaud.** This uses undocumented endpoints observed in the Plaud web app. Plaud can change them at any time, and automated access may be against their terms. Your account, your risk. If you can, use Plaud's official developer platform instead.

- No dependencies (Python 3.9+, standard library only). `ffmpeg` is optional, for non-mp3 input.
- Uploads any size: multipart upload with correct S3 part sizes.
- Survives Plaud revoking tokens: re-mints them from your user session, like the web app.
- Credentials stay in `~/.config/plaud4/`, mode `600`, and are never printed.

## Install

```bash
pip install git+https://github.com/fatihdrmaz/plaud4
```

## Login

Plaud keeps your user session in two **HttpOnly** cookies. JavaScript cannot read them, so you copy them once from DevTools.

| Cookie | Lifetime | Purpose |
|---|---|---|
| `pld_ut` | ~24 hours | user token |
| `pld_urt` | ~30 days | renews `pld_ut`; only sent to `/auth/refresh-user-token`, so it is hidden in most views |

1. Open an **incognito/private window** and sign in at <https://web.plaud.ai>.
   A separate session means your normal browser and the script don't invalidate each other.
2. DevTools → **Network**, reload the page, and filter for `workspaces`. Click the `list` request → **Cookies** tab → tick **Show filtered-out request cookies**.
3. Run:

   ```bash
   plaud4 login
   ```

   Paste the **Value** of `pld_ut`, then of `pld_urt`. Input is hidden.
4. Close the incognito window **without logging out**. Logging out ends that session.

```console
$ plaud4 status
workspace          : ws_xxxxxxxx @ api-apse1.plaud.ai
workspace token    : 23.9 h (2026-11-10 10:39)
user token (pld_ut): 23.9 h (2026-11-10 10:44)
user refresh (pld_urt): 30.0 days (2026-12-09 10:36)
```

`pld_urt` is not extended by use. **Repeat the login about once a month.** `plaud4 status` warns 3 days before it expires.

## Use

```bash
plaud4 upload meeting.mp3 --name "Weekly sync"           # prints the new file_id
plaud4 upload call.webm --name "Client call" --wait transcript   # converts via ffmpeg, waits
plaud4 detail f_0123...                                  # processing state (no content)
plaud4 wait f_0123... --need summary
plaud4 workspaces                                        # if you have more than one
plaud4 refresh                                           # keep tokens fresh (cron)
```

Plaud starts transcription automatically after an upload. Very short recordings get a transcript but no summary (`Content too short to summarize`).

### From Python

```python
from plaud4 import Plaud

p = Plaud(log=print)
fid = p.upload("meeting.mp3", name="Weekly sync")
p.wait(fid, need="TRANSCRIPT")      # raises PlaudError on failure / timeout
print(p.status(fid))                # {'busy': False, 'AUDIO': None, 'TRANSCRIPT': 1, 'SUMMARY': 1, ...}
```

Every call refreshes tokens when needed. If Plaud revokes the workspace token (`-419` / `-420`), the client mints a new one from your cookies and retries once.

### Keep it alive

Run `refresh` regularly, so the user session is used at least daily:

```cron
*/30 * * * * plaud4 refresh >/dev/null 2>&1 || echo "plaud4 refresh failed" | mail -s plaud4 you@example.com
```

## Example: record online meetings into Plaud

The author uses this behind a self-hosted meeting bot ([Vexa](https://github.com/Vexa-ai/vexa)). A Telegram message with a Meet/Zoom/Teams link sends the bot into the meeting. When the meeting ends, a cron job downloads the recording and calls `Plaud().upload()`. Once `status()` reports `TRANSCRIPT == 1`, the local copy is deleted. See [examples/meeting-bot.md](examples/meeting-bot.md).

## Troubleshooting

| Error | Meaning |
|---|---|
| `status=-419 workspace token revoked` / `-420 re-exchange required` | Normal; handled automatically. If it persists, the cookies are gone: `plaud4 login`. |
| `HTTP 401` on `/auth/refresh-user-token` | `pld_urt` expired or was revoked (e.g. you logged out in that window): `plaud4 login`. |
| `both pld_ut and pld_urt are required` | `pld_urt` is only visible with **Show filtered-out request cookies** ticked. |
| `HTML response (Cloudflare block or wrong host)` | Request blocked. Report it with the endpoint in an issue. |
| `-1800907 Account migrated to 4.0` | You are using a pre-4.0 client. Not this one. |
| `-302 user region mismatch` | Handled automatically: Plaud names the regional host (e.g. `api-apse1`) and it is remembered in `region.json`. |

How the protocol works: [docs/PROTOCOL.md](docs/PROTOCOL.md).

## License

MIT
