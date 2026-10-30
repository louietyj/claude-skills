---
name: musescore
description: "Find a song's sheet music on musescore.com and download it as the page SVGs (vector, every element tagged Note/Lyrics/TieSegment/...), score.mid and the measure layout, logged in as Louie (Pro+, so official Hal Leonard/Faber scores too); optionally turn the vocal line plus lyrics into ABC. Use whenever a music question about a song (its key, chords, melody, phrasing, cadences, how it ends, what an arrangement does) would be answered better from the actual score, or when he asks to grab, pull or download a score from MuseScore."
---

# MuseScore scores

`msfetch` does the mechanics: search, download, convert. You do the judgement,
which is picking the right score from the search results.

## Setup (once per conversation)

The web-browser skill comes first, since it provides `pinchtab`:

```bash
bash "$(ls -1dt /mnt/skills/*/web-browser ~/.claude/skills/synced/*/web-browser 2>/dev/null | head -1)/setup.sh"
bash "$(ls -1dt /mnt/skills/*/musescore ~/.claude/skills/synced/*/musescore 2>/dev/null | head -1)/setup.sh"
```

The second command puts `msfetch` on PATH and prints the path of `login.sh`.
**Never re-derive the path.**

## Log in

Check this first. Community scores work logged out, but official ones need
his login.

```bash
msfetch whoami        # {"id":0,...} = logged out; "name":"Louie Tan","pro_plus":true = ready
```

If it shows logged out, log in through request-credential, using its usual
`ask`/`wait`/`run` flow. Agreed scope: log in and view scores only. No
purchases, favourites, settings or posts.

```bash
request-credential ask musescore.com "Log in to fetch a score"
request-credential wait <id> && request-credential run <id> -- sh <login.sh path from setup>
```

It should end on `https://musescore.com/dashboard`. Cookies die with the
sandbox, so each new conversation logs in again.

**Never `nav --block-images` in this browser while working with MuseScore.**
The flag also blocks the page's own `fetch()` of `score_N.svg`. That is what
left an earlier session stuck on page 1. `msfetch` navigates without it.

## 1. Search

```bash
msfetch search "viva la vida coldplay" --type piano-voice --sort views
```

Defaults: interactive scores only, meaning MuseScore files with MIDI and
playback. PDF-only uploads (scanned publisher PDFs, no MIDI) are left out. Add
`--any-format` to include them; they show as `PDF-ONLY no MIDI`.

| flag | meaning |
|---|---|
| `--type piano-voice\|pvg\|solo\|any` | MuseScore's instrumentation facet: Piano-Voice, Piano-Voice-Guitar, Solo |
| `--official` / `--community` | publisher scores only, or uploads by users only |
| `--sort relevance\|rating\|views` | relevance by default |
| `--page N` | 20 results per page |

Each result prints its id, title, `[official]`/`[community]`, instrumentation,
part names, page count and duration, key and time signature, rating, views,
uploader and URL.

**Choosing:**
- **The song, not a namesake.** Check the title and the artist in it. Duration
  should be near the recording's length, unless he wants an excerpt.
- **Has MIDI.** Keep the default filter. A PDF-only score gives SVG pages and
  no MIDI, so there's no ABC. That is fine for reading, not for analysis.
- **Fits the question.** For melody or lyrics, a score with a *Voice* part
  (Piano-Voice or PVG). For harmony, PVG has chord symbols. For a piano
  question, Solo.
- **Official beats community for accuracy.** Hal Leonard and Faber are
  transcribed from the recording, in the original key. Community scores are
  often transposed (see the key column: "Someone Like You" in C is a
  transposition), simplified, or partial. The best-rated community score with
  many votes is a fine second choice.
- **Key in the listing ≠ the original key.** Check the key column against what
  the recording is in before analysing.
- Several candidates of similar standing: fetch two and compare.

## 2. Fetch

```bash
msfetch fetch <score-url> <out-dir> [--pdf] [--abc [--voice-track N]]
```

Writes `<out-dir>/pages/score_N.svg` (PNG for pre-2015 uploads), `score.mid`,
`space.jsonp` (measure positions per page plus playback timing),
`meta.json`, and, with `--pdf`, `score.pdf` built from the SVGs. It prints a
one-line JSON summary: pages got/expected, format, midi, who it was logged in
as. That takes 5–20 s.

How it works: page 1 is the static URL the page preloads. Pages 2+ and the
MIDI come from `/api/jmuse`, signed with the token the site's own bundle
computes (`md5(id+type+index+suffix)[:4]`, with the suffix read live from the
bundle). No scrolling, no viewer state. Every file must come from the same
`scoredata/g/<hash>/` as page 1.

| exit | meaning | what to do |
|---|---|---|
| 0 | every page saved (plus MIDI unless PDF-only) | go on |
| 2 | not a score page, or pages missing | read stderr; rerun once |
| 3 | MuseScore refused (401/403/404) | official score: check `whoami`, log in |
| 4 | **a file came from a different score than page 1** | nothing was saved. Do not fall back to scrolling the viewer. Rerun once; if it repeats, use `--browser pinchtab-windows` (his Chrome, after a grant) |

Exit 4 exists because a 2026-10-29 session got Bach's Goldberg Variations back
for Viva La Vida's pages 2–7 and nearly analysed them. That decoy has not
reproduced since (2026-10-30, 7 scores). Even so, **view one rendered page
other than the first before you rely on the content**:

```bash
python3 -c "import cairosvg; cairosvg.svg2png(url='<out>/pages/score_3.svg', write_to='/tmp/p4.png', scale=0.8, background_color='white')"
```

### His Chrome instead

`msfetch --browser pinchtab-windows fetch ...` runs the same thing in his
logged-in Chrome. Get a grant first, open your own tab
(`nav <url> --new-tab`), then pass `--skip-nav` so msfetch uses that tab rather
than navigating the one he's looking at. Use it only when the sandbox browser
fails twice.

## 3. ABC (optional)

```bash
msfetch abc <out-dir> [--voice-track N]      # or fetch ... --abc
```

The command aligns MIDI notes to the SVG noteheads, OCRs the lyric glyphs and
writes `out.abc` (melody plus aligned lyrics) and `report.json`. It needs SVG
pages, `score.mid` and `space.jsonp`. It prints the MIDI streams; if the ABC
picked the piano rather than the voice, rerun with the right `--voice-track`.

Three artifacts, each authoritative for something different:
- **SVG pages**: how it is *written*. Ties, beaming, enharmonic spelling, chord
  symbols, repeats, dynamics. Look at them when anything seems off.
- **score.mid**: what *sounds*. Exact pitches and onsets, with repeats unrolled.
  It loses spelling, ties and rests-vs-staccato.
- **out.abc**: a derived view for analysis. Its rhythm notation is
  reconstructed, so the SVG and MIDI win over it.

`report.json` lists flagged measures, meaning heads that don't match notes or
unplaced syllables. Look at those on the page before quoting them.

How good it is (tested 2026-10-30; 10–70 s per score):
- **Hal Leonard**: near-clean. Every note matched, and the lyrics round-trip
  through abcjs.
- **Faber**: every note matched. Verse labels ("1.", "2.") leak into the
  lyrics.
- **Community**: notes mostly right. Lyric OCR is noisy, and MIDI exported
  with expressive timing yields odd durations (`B,/8-B,/60`). Use the SVG and
  MIDI there, not the ABC.

Known limits: two voices on one staff (the top note wins); only one stream
goes to ABC.

## Using what you got

These are licensed arrangements on his Pro+ account, for his own analysis.
Analyse them and quote what's needed (a phrase, a cadence, a chord). Don't
paste a whole arrangement into a shared or published artifact. Working files
live in the scratchpad. Save one to the cfs store only if he asks.
