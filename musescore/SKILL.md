---
name: musescore
description: "Find a song's sheet music on musescore.com and download it as the page SVGs (vector, every element tagged Note/Lyrics/TieSegment/...), score.mid and the measure layout, logged in as Louie (Pro+, so official Hal Leonard/Faber scores too); optionally turn the vocal line plus lyrics into ABC. Use whenever a music question about a song (its key, chords, melody, phrasing, cadences, how it ends, what an arrangement does) would be answered better from the actual score, or when he asks to grab, pull or download a score from MuseScore."
---

# MuseScore scores

You drive the browser yourself; this skill gives you two page scripts and a
CLI for everything after them:

- `js/search.js` and `js/fetch-score.js` hold the MuseScore knowledge. Each is
  one async expression that you evaluate in the page, awaited. It returns a
  single JSON string, which you save to a file.
- `msfetch` turns those files into results and score files, and never touches
  the browser. That way pinchtab's syntax can change without breaking
  anything here. Write the browser steps in whatever syntax pinchtab's own
  instructions give you today.

## Setup (once per conversation)

```bash
bash "$(ls -1dt /mnt/skills/*/web-browser ~/.claude/skills/synced/*/web-browser 2>/dev/null | head -1)/setup.sh"
bash "$(ls -1dt /mnt/skills/*/musescore ~/.claude/skills/synced/*/musescore 2>/dev/null | head -1)/setup.sh"
```

The second command puts `msfetch` on PATH. `msfetch js search` and
`msfetch js fetch-score` print the scripts' paths. **Never re-derive the path.**

## The browser rules

- **Never navigate with `--block-images`** (or any image or resource
  blocking) on musescore.com. It also blocks the page's own `fetch()` of
  `score_N.svg`. A tab opened that way serves page 1 and nothing else, which
  is what broke the 2026-10-29 session.
- **Look at the screenshot after every navigation.** musescore.com sometimes
  answers with a Cloudflare "Verify you are human" page. The scripts then
  report "not a score page" or "not a search page". Run pinchtab's captcha
  `solve` step, wait until the real page shows, then evaluate again.
- **Get the script into the eval without retyping it.** As of 2026-10-30 a
  batch line expands `$VARS`, so this works: export `JS="$(cat "$(msfetch js
  fetch-score)")"`, then the step `eval --await-promise "$JS" > raw.json`.
  The output is several MB; it must go to a file, never into your context.

## Log in

Community scores work logged out; official ones need his login. Both scripts
report who is logged in: `results` prints it in its header, `unpack` as
`logged_in_as`. If it is null and you need an official score, log in through
request-credential the way the web-browser skill describes. Agreed scope: log
in and view scores only. No purchases, favourites, settings or posts.

- The form is at `https://musescore.com/user/login`.
- Fields: `#username` and `#password`.
- Submit: `button[name=op]`. It stays disabled until React sees keystrokes, so
  type into the fields rather than setting their values.
- Success lands on `https://musescore.com/dashboard`. Check for that inside
  the same `run`.

Cookies die with the sandbox, so each new conversation logs in again.

## 1. Search

```bash
msfetch search-url "viva la vida coldplay" --type piano-voice --sort views
```

That prints a results URL. Navigate to it, evaluate `search.js` and save the
output to `search.json`, then run:

```bash
msfetch results search.json
```

| flag | meaning |
|---|---|
| `--type piano-voice\|pvg\|solo\|any` | instrumentation facet (ids 139, 140, 114) |
| `--official` / `--community` | publisher scores only, or uploads by users only |
| `--any-format` | include PDF-only scores (no MIDI); without it they are filtered out |
| `--sort relevance\|rating\|views` | |
| `--page N` | 20 per page |

**Choosing:**
- **The song, not a namesake.** Check the title, the artist, and a duration
  near the recording's.
- **Has MIDI.** Keep the default filter. PDF-only scores give SVG pages but no
  MIDI, so there's no ABC.
- **Fits the question.** For melody or lyrics, a score with a Voice part
  (Piano-Voice or PVG). For harmony, PVG, which has chord symbols. For a piano
  question, Solo.
- **Official beats community for accuracy.** Hal Leonard and Faber are
  transcribed from the recording, in its key. Community scores are often
  transposed, simplified or partial. Compare the key column with the
  recording's key. A Chris Mann cover in G is not Coldplay's A♭.
- When several candidates are close, fetch two and compare.

## 2. Fetch

Navigate to the score URL, evaluate `fetch-score.js` (awaited) with its output
saved to `raw.json`, then:

```bash
msfetch unpack raw.json <out-dir> [--pdf]
```

This writes `pages/score_N.svg` (PNG for pre-2015 uploads), `score.mid`,
`space.jsonp` and `meta.json`, plus `score.pdf` with `--pdf`. It prints a
one-line summary. The eval takes 5–20 s.

| exit | meaning | what to do |
|---|---|---|
| 0 | every page (plus MIDI unless PDF-only) | go on |
| 2 | not a score page, or pages missing | look at the screenshot (Cloudflare?), read stderr, evaluate again |
| 3 | jmuse refused every token | official score: log in. If you are logged in, see "When it breaks" |
| 4 | **a file came from a different score than page 1** | nothing was saved. Evaluate once more; if it repeats, use his Chrome (`pinchtab-windows`, after a grant, in your own tab) |

Exit 4 exists because a 2026-10-29 session got Bach's Goldberg Variations back
for Viva La Vida's pages 2–7. **Render and view one page other than the first
before you rely on the content:**

```bash
python3 -c "import cairosvg; cairosvg.svg2png(url='<out>/pages/score_3.svg', write_to='/tmp/p4.png', scale=0.8, background_color='white')"
```

## 3. ABC (optional)

```bash
msfetch abc <out-dir> [--voice-track N]
```

The command aligns MIDI notes to the SVG noteheads, OCRs the lyric glyphs and
writes `out.abc` (melody plus aligned lyrics) and `report.json`. It needs SVG
pages, `score.mid` and `space.jsonp`. It prints the MIDI streams; if the ABC
picked the piano rather than the voice, rerun with the right `--voice-track`.

The three outputs are authoritative for different things:
- **SVG pages**: how it is written. Ties, beaming, spelling, chord symbols,
  repeats.
- **score.mid**: what sounds. Exact pitches and onsets, repeats unrolled.
- **out.abc**: a derived view for analysis. Its rhythm notation is
  reconstructed, so the SVG and MIDI win over it.

How good it is (tested 2026-10-30; 10–70 s per score):
- **Hal Leonard**: near-clean. Every note matched, and the lyrics round-trip
  through abcjs.
- **Faber**: every note matched. Verse labels ("1.", "2.") leak into the
  lyrics.
- **Community**: notes mostly right. Lyric OCR is noisy, and MIDI exported
  with expressive timing gives odd durations (`B,/8-B,/60`). Use the SVG and
  MIDI there, not the ABC.

`report.json` lists flagged measures. Look at those on the page before quoting
them. Known limits: two voices on one staff (the top note wins); only one
stream goes to ABC.

## Using what you got

These are licensed arrangements on his Pro+ account, for his own analysis.
Analyse them and quote what's needed (a phrase, a cadence, a chord). Don't
paste a whole arrangement into a shared or published artifact. Work in the
scratchpad. Save to the cfs store only if he asks.

## How MuseScore serves a score, and what to check when it breaks

MuseScore has no public API; its REST API was shut down years ago. Everything
below was read off the live site on 2026-10-30, so expect it to drift. When a
script fails, find which step below changed and fix the script, then tell
Louie what changed and commit the fix to the claude-skills repo
(`musescore/`). The LibreScore userscript
(github.com/LibreScore/dl-librescore, `src/file.ts` and `src/scoreinfo.ts`)
does the same job and is usually updated quickly when MuseScore changes. Read
it through a GitHub tool, never a raw fetch.

1. **Page state.** Every page embeds its data in `window.UGAPP.store`:
   - `page.data.score` on a score page: `id`, `title`, `pages_count`,
     `is_pdf`, `is_official`, `is_purchased`, `parts_names`,
     `instrumentations`, `keysig`.
   - `page.data.scores` (plus `pagination`, `filters`) on a results page.
   - `user`: `id` 0 when logged out, `username`, `isPro`, `isProPlus`.

   If this moves, look for another embedded JSON store (`<div class="js-store"
   data-content>`, `__NEXT_DATA__`) in the HTML.
2. **Page 1** is a static file, which the page preloads:
   `<link rel="preload" as="image" href="https://musescore.com/static/musescore/scoredata/g/<40-hex hash>/score_0.svg?no-cache=…">`.
   **Do not use `og:image`**: it is a differently rendered thumbnail under
   another hash. Old uploads only have `score_0.png`. Strip any `@…` size
   suffix from the URL.
3. **Pages 2+ and the MIDI** come from
   `GET /api/jmuse?id=<id>&type=img|midi&index=<n>`, with header
   `Authorization: md5(id + type + index + SUFFIX).slice(0, 4)`. The response is
   `{info: {url}}`, a signed S3 URL that lasts 5 minutes, under the same hash as
   page 1.
   - SUFFIX was `"111010"`. The script reads it live from the site's bundles
     (`/static/public/build/…/*.js`) by matching `"<suffix>").substr(0,4)`; the
     surrounding code is `(e+t+n+"111010").substr(0,4)`.
   - If every request gets 401/403 while logged in, grep the bundles for
     `jmuse` to see what the client sends now.
   - The real client also sends `X-MU-FRONTEND-VER`, `X-Mu-Unified-Id` and
     sometimes `x-mu-cp`, which looks like a challenge token. None were
     needed on 2026-10-30. If jmuse starts returning an error whose message
     triggers a re-challenge in the bundle, those headers are the next thing
     to add.
   - 404 for an official score means no access: not logged in, or not
     entitled.
4. **Layout**: `space.jsonp` sits beside the pages, at
   `/static/musescore/scoredata/g/<hash>/space.jsonp`. It holds measure boxes
   in 1200-dpi units plus playback timing. Older SVGs are 72 dpi (width about
   600), newer ones 360 dpi; `align.py` normalises both.
5. **Same-score check**: every file's `scoredata/g/<hash>` must equal page
   1's. A mismatch means a decoy or the wrong score. Never "fix" it by
   relaxing the check.
6. **Bytes** are fetched inside the page, because musescore.com returns 403 to
   the sandbox's own HTTP client.
7. MuseScore listens for a `devtoolschange` event. dl-librescore suppresses
   it, which suggests the site may detect devtools or automation. If decoys
   start appearing consistently, suspect this first.
