# Recording online meetings into Plaud

A Plaud device only captures a meeting it can hear. If you join from your phone, or wear headphones, the meeting never reaches Plaud. The idea here is to send a bot into the meeting and push its recording into the same Plaud account, so every meeting ends up transcribed and summarised in one place.

```
Telegram "join <link>"  ──►  meeting bot (Vexa, self-hosted)  ──►  recording (webm)
                                                                      │  cron, every 10 min
                                                                      ▼
                                   ffmpeg → mp3 → plaud4 upload → wait for TRANSCRIPT == 1
                                                                      │
                                                                      ▼
                                                   delete local copy (and the bot's copy)
```

## Pieces

- **[Vexa](https://github.com/Vexa-ai/vexa)**, Apache-2.0, self-hosted. It sends a bot to Google Meet, Zoom or Teams. Use it with transcription off (`"transcribe_enabled": false`, `"recording_enabled": true`): Plaud does the transcription. Recordings come from `GET /recordings`, then `GET /recordings/{id}/master?type=audio`; download its `raw_url`.
- **plaud4**: `Plaud().upload(path, name, start_ms)`, then `Plaud().status(file_id)`.
- A small worker that keeps state per recording (`new → downloaded → uploaded → deleted`), so it can resume after a crash and never deletes anything before Plaud confirms the transcript.

## Lessons

- **Google Meet rejects anonymous bots at random.** Older Vexa builds knocked once and then sat on a black "waiting" screen while the host saw nothing. Newer builds re-knock up to 10 times. If it still happens, alert yourself and record that meeting another way.
- Set `automatic_leave.max_time_left_alone` so the bot leaves on its own after the meeting ends.
- Bind every Vexa port to `127.0.0.1`. Its web terminal has no login.
- Alert on every failure: upload failed twice, transcript missing after 2 h, Plaud session gone. With an unofficial API, the dangerous failure is the silent one.
- Expect a monthly re-login for the Plaud cookies (`plaud4 status` shows the date).
