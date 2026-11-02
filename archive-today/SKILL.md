---
name: archive-today
description: "Reads a hard-paywalled or vanished article through archive.today (archive.ph), with no browser: takes the newest snapshot, or submits the page and waits for the capture, then prints the article as clean text. Solves archive.today's captcha itself with a bundled CapSolver key. Use for WSJ, NYT, FT, Bloomberg, The Atlantic, The Economist and other hard paywalls that mcp-jina and web_fetch return as a teaser or nothing; for a page that changed or died since someone archived it; or when Louie asks for an archive.ph/archive.today copy. Not for private pages: a submitted URL becomes a public snapshot."
---

# archive-today

Setup, once per conversation:

```bash
bash "$(ls -1dt /mnt/skills/*/archive-today ~/.claude/skills/synced/*/archive-today 2>/dev/null | head -1)/setup.sh"
```

Then:

```bash
archive-today read <url>          # article text on stdout; progress and warnings on stderr
archive-today read <url> > a.txt  # long articles run to thousands of words: file it, then read what you need
```

`read` takes the newest snapshot. If there is none, it submits the page and waits;
a capture usually takes 1 to 5 minutes. It stops after `--budget` seconds (default
240, under the 300 s bash limit), exits 4 and prints the exact `archive-today wait ...`
command to resume. Run that; don't submit again.

| Command | Does |
|---|---|
| `read <url>` | newest snapshot, else submit + wait; prints text. Also takes a snapshot URL |
| `newest <url>` | prints the newest snapshot URL; exit 3 if none |
| `submit <url>` | starts a capture; prints the snapshot URL, `wip <id>`, or `queued` |
| `wait <id>` / `wait --url <url>` | resumes waiting, then prints text |

`--fresh` (on `read` and `submit`) captures again even if a snapshot exists.
`--url-only` prints the snapshot URL instead of the text; `--html FILE` also saves the
page. Exit codes: 0 done, 1 broken (see below), 3 no snapshot, 4 still capturing.

## The URL decides whether there's a match

archive.today keys snapshots on the exact URL, so one stray parameter means no match
and a needless fresh capture. `read` strips the common trackers itself (`utm_*`,
`fbclid`, `gclid`, `mod`, `smid`, `cmpid`, `ref` and the like) and drops the
`#fragment`, logging what it changed.

Strip the rest yourself before calling it:
- share and gift tokens, session ids, and campaign-looking parameters it doesn't know;
- AMP and mobile variants: use the canonical article URL.

Keep parameters that pick the content (`?id=`, `?p=`, `?v=`, `?article=`). If a URL with a
query misses, try `newest` once without the query before submitting.

## Fresh captures

When the newest snapshot is a bad capture or out of date, `read --fresh <url>` captures
the page again. That is archive.today's own "save again" (`anyway=1`). A cache-busting
parameter such as `?ts=` doesn't work: archive.today normalizes it away and hands back
the existing snapshot. Fresh captures took 2 to 5 minutes in testing, so expect an
exit 4 and one `wait`.

## Bad captures

Some snapshots hold the paywall stub, a bot-check page or a proxy error rather than
the article. `read` warns on stderr when the text is short or mentions "subscribe",
"security verification" and similar. Then:
1. try `--fresh`, as above;
2. if that is a stub too, archive.today's crawler is being turned away. Move on to a
   wire-syndicated copy (AOL, Yahoo, MSN: search the headline) or ghostarchive.

## When it fails

Exit 1 prints `archive-today FAILED (<section>)` and names a section of
`TROUBLESHOOTING.md`, next to this file. Read that section before retrying or
working around it; it says what changed and how to repair the script.

## Cost and privacy

Each captcha solve costs about $0.001. The clearance lasts only minutes, so expect a
solve per session; repeated calls within it reuse the cookie. Anything you submit
becomes a public, permanent snapshot: never submit a gift link, a URL with a token,
or a page only Louie's login can see.
