---
name: youtube-download
description: "Download a YouTube video (or its audio) into the sandbox, then pull frames at given timestamps or the thumbnail. Use whenever a task needs the video itself, not just its words: screengrabs or thumbnails of the relevant moments, checking what is on screen, a clip. Transcripts are not this skill: use mcp-supadata for those."
---

# YouTube download

YouTube walls the sandbox's datacenter IP after about one request, so plain
yt-dlp here fails. Three rungs, in order; move down only when the one above
failed:

1. **dlsrv, scripted.** The free backend behind yt1s.com.co. The browser only
   gets a short-lived download link; curl fetches the file. Fast (10-30 MB/s),
   costs nothing, and never touches Louie's home IP.
2. **yt1s.com.co, clicked through like a person.** Same backend, through its
   maintained front door. For when rung 1 breaks because the API changed.
3. **yt-dlp through Louie's home IP.** Slow (his ~30 Mbps upload), finicky,
   and too much of it gets his home IP walled by YouTube for a while. The only
   rung that can cut a clip without downloading the whole video.

A thumbnail needs none of them: `ytdl thumb <video> -o thumb.jpg`.

## Setup (once per conversation)

```bash
bash "$(ls -1dt /mnt/skills/*/web-browser ~/.claude/skills/synced/*/web-browser 2>/dev/null | head -1)/setup.sh"
bash "$(ls -1dt /mnt/skills/*/youtube-download ~/.claude/skills/synced/*/youtube-download 2>/dev/null | head -1)/setup.sh"
```

The second puts `ytdl` on PATH. `<video>` is any YouTube URL or the bare
11-character id. If pinchtab's syntax has changed since, follow its own
instructions over the batches below.

## Rung 1: dlsrv, scripted

```bash
export JS="$(ytdl js <video> --quality 720)"
pinchtab execute-batch <<EOF
nav $(ytdl page <video>)
eval --await-promise "\$JS" > /tmp/link.json
EOF
ytdl fetch /tmp/link.json -o <dir-or-file>
```

Run all of it in one bash call, straight through: the link expires about 80 s
after the eval. `fetch` prints the size, speed and what ffprobe sees.

- `--quality`: 2160 1440 1080 720 480 360 240 144 for mp4. **720 is the
  default and plenty** for frames, checking what is on screen, or a clip.
  Go higher only when the pixels are the product: a poster, a print, a crop
  you will blow up. Up to 1080 is H.264; 1440 and 2160 come as AV1 (YouTube
  has no H.264 above 1080), which ffmpeg and browsers read but some older
  players don't. Asking for more than the video has quietly gives its best,
  in AV1. The site's own page hides 1440 and up behind a desktop-app upsell;
  the API serves them.
- `--format mp3|m4a|opus` for audio only. Whatever the name, the file has so
  far been AAC at about 128 kbps; `fetch` saves it as `.m4a` when it names the
  file itself.

**Probe**, only when the top quality matters: `ytdl js <video> --probe` in
place of the link script prints `{"best":"2160p av1","at_1080":"1080p h264"}`.
It asks for 2160 and 1080 and reads the filenames the backend would serve;
the backend looks the video up but downloads nothing until a link is fetched.
Each probe is still two download requests against the rate limit below.

**Rate limit.** The front end answered 429 "Too many requests. Please slow
down." to the seventh download request in about a minute (three probes,
then a download; 2026-11-23). Space calls out; on `rate_limited`, wait a
minute.

`fetch` exits 2 when the eval returned an error instead of a link, and prints
it:

| error | meaning |
|---|---|
| `wrong_page` | the tab is not on the embed page; nav again |
| `no_session` | the page never solved its captcha. Look at the screenshot. Retry once, then rung 2 |
| `rate_limited` | too many requests; wait a minute, then once more |
| `touch_failed`, `download_refused`, `progress_failed`, `no_url` | the API changed or refused this video. Go to rung 2; if rung 2 works, fix the script (below) |
| `upstream_refused`, `conversion_failed` | their backend could not get the video from YouTube ("Request failed with status code 403/422"). Rung 2 fails identically; go to rung 3 |

Exit 3 is a failed download, usually an expired link: eval again and fetch at
once.

## Rung 2: yt1s.com.co, by hand

The site is plastered with ad hooks: **every Download click opens an ad tab
and makes it current.** Close it and go back to the yt1s tab each time.

1. `nav https://yt1s.com.co/en230/`, then `fill first:css:input "<url>"`
   with the full YouTube URL (`fill` takes a selector, not the box's
   placeholder text). It converts on its own (no click); within ~8 s the page
   moves to `/download/` and shows the dlsrv panel.
2. Click **Video** (Audio is the default tab), then the row's **Download**.
   An invisible cover makes ref and `text:` clicks fail as occluded: click by
   coordinates off the screenshot. The 1440p and 2160p rows show **Install**
   (a desktop-app upsell) instead: use rung 1 for those.
3. Close the ad tab (`tab`, `tab close <id>`, `tab <yt1s id>`). A dialog
   shows **Download Now**: click it (coordinates again). Another ad tab
   opens; close it. A **Download Failed** dialog instead means the backend
   could not get the video, as `upstream_refused` does in rung 1.
4. The file downloads in the yt1s tab, not the current one, so `wait
   --download` on the ad tab times out. Run `downloads`, take the handle, then
   `wait --download <handle> -o <file>`.

Tested 2026-11-22: 720p, 44 MB.

## Rung 3: yt-dlp through the home IP

```bash
pip install -q -U --pre "yt-dlp[default]"
bash "$(ls -1dt /mnt/skills/*/home-proxy ~/.claude/skills/synced/*/home-proxy 2>/dev/null | head -1)/setup.sh"
env -u HTTPS_PROXY -u https_proxy yt-dlp --js-runtimes node --no-progress \
  --proxy socks5h://127.0.0.1:1080 \
  -f 'bv*[height<=720]+ba/b[height<=720]' \
  --download-sections '*0:42-0:52' \
  -o '/tmp/clip.%(ext)s' '<url>'
```

Each piece is load-bearing (all tested 2026-11-22, 3 of 3 videos):

- **`--pre`, every conversation.** The nightly. YouTube breaks yt-dlp every
  few weeks; a four-week-old release was already failing.
- **`--js-runtimes node`.** yt-dlp needs a JavaScript runtime to solve
  YouTube's player challenges and only enables deno by default, which the
  sandbox lacks. Without it formats go missing or extraction fails.
- **The home proxy, with `HTTPS_PROXY` unset.** Otherwise the request leaves
  from the sandbox's egress proxy and gets "Sign in to confirm you're not a
  bot".
- **`--download-sections '*START-END'`** fetches only that stretch of the
  video. Use it whenever you need moments rather than the whole thing: it is
  lighter on his IP and his bandwidth. Add `--force-keyframes-at-cuts` only
  if the cut must be frame-exact; it re-encodes.
- Drop `--download-sections` for the whole video; `-F` lists the formats.

Be gentle with his IP: one video at a time, and stop at the first "Sign in to
confirm you're not a bot" through the proxy rather than retrying: his IP is
walled for now.

"This video is unavailable" from every rung does not mean the video is gone.
On 2026-11-23 dQw4w9WgXc0 played in Louie's Chrome at home, incognito too,
while curl from his PC (IPv4 and IPv6), yt-dlp and CloakBrowser through his
home IP all got "unavailable" and dlsrv got 403/422: some videos are served
only to a genuine Chrome. Tell Louie that rather than calling it missing; no
rung here gets them.

## Frames

```bash
ytdl frames <file> 0:42 83 1:02:03.5 -o <dir>
```

One JPEG per timestamp, named by its time.

## How dlsrv works, and what to check when rung 1 breaks

Read off the live site on 2026-11-22. It is undocumented and ad-funded, so
expect drift. When rung 1 fails and rung 2 works, the front end changed: find
which step below moved, fix `js/dlsrv.js`, tell Louie what changed, and commit
the fix to claude-skills (`youtube-download/`).

1. **Front door.** yt1s.com.co (one of many white-label sites) embeds
   `https://embed.dlsrv.online/v1/full?videoId=<id>`, a Next.js app behind
   Cloudflare. If the iframe moves, `ytdl page` must follow it.
2. **Session.** On load the page solves a Cap.js proof-of-work challenge
   (`POST https://check.dlsrv.online/<key>/challenge`, then `/api/verify` with
   the `capToken`) and stores a JWT in `sessionStorage.__session`. Its claims
   are `ip`, `uaf` (a user-agent fingerprint) and a 300 s expiry. The
   challenge endpoint answers `{"error":"Blocked"}` to curl and to stock
   headless Chromium, even from a residential IP; CloakBrowser passes. The JWT
   only works from the browser that earned it (curl with the same User-Agent
   gets 403), which is why the API calls run in the page.
3. **Download.** `POST /api/session/touch {videoId}` first, or the next call
   answers 403 "Session not ready". Then `POST /api/download/mp4` (or
   `/api/download/mp3` for mp3/m4a/opus) with `{videoId, format, quality}` and
   `Authorization: Bearer <jwt>`. One session serves several videos, each
   with its own touch.
4. **Result.** `{"status":"tunnel","url":"https://yt1s-worker-N.dlsrv.online/tunnel?id=…&exp=…&sig=…&sec=…&iv=…","filename":…}`:
   cobalt's tunnel format, so the backend looks like a self-hosted cobalt
   fleet (cobalt's default tunnel lifespan is 90 s). The URL needs no auth
   and expires in about 80 s. In cobalt, the API call only resolves YouTube's
   stream URLs and caches them (`createStream` in `api/src/stream/manage.js`);
   nothing streams from YouTube until `GET /tunnel`, which is what makes a
   probe cheap. The filename ends `(<height>p, <codec>)`.
5. **v3.** The bundle also has a job API: `POST /api/v3/download` returns
   `{job}`, polled at `GET /api/v3/progress?job=` until `{done, url}`. The
   script follows a `job` if the v1 endpoints start answering with one. The
   page picks v1 or v3 from an `apiVersion` prop; grep the bundle for
   `/api/v3` to see which it uses now.
6. **Where to look.** The page's chunks are under `/_next/static/chunks/`;
   grep them for `/api/`, `apiEndpoint` (the Cap key) and `requestDownload`.
