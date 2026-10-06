---
name: phomymo-link
description: "Designs labels for Louie's Phomemo M110 printer and gives him a phomymo.louietyj.me link that opens the finished label ready to print, from one of his templates or composed from scratch, with a pixel-exact preview. Use whenever he wants a label printed or a print link (food, folder, address labels), including when the content comes from something you looked up, like a cafe menu."
---

# phomymo-link

`https://phomymo.louietyj.me/#design=v1.<data>` carries a whole label design, so anything in the design
(his phone number, on some templates) is in the link. Opening it loads the design into Phomymo, his fork
of a browser label designer; he taps Print there. Nothing is stored server-side.

## Designs

A design is JSON: label size plus text, shape, barcode, QR and image elements. The format is documented,
reverse-engineered, at `https://phomymo.louietyj.me/docs/design-format.md`. Where it's silent or wrong, the
source files it names are on the same host (e.g. `/canvas.js`).

His templates are in his main Dropbox (not the durable filesystem), in `/Phomymo Templates/`. The Dropbox
connector's `download_link` plus `curl` gets one into the sandbox byte-exact; the link is single-use and
expires, and `fetch` is the fallback if `curl` can't reach it.

`{{Name}}` in a design is a placeholder `make_link.py` fills from `Name=value` arguments. `[[...]]`
expressions such as `[[dt|YYYY-MM-DD]]` are evaluated by Phomymo when it prints.

## Scripts

One bash call, since shell variables die between calls:

```bash
SKILL_DIR="$(ls -1dt /mnt/skills/*/phomymo-link ~/.claude/skills/synced/*/phomymo-link 2>/dev/null | head -1)"
python3 "$SKILL_DIR/make_link.py" design.json [Name=value ...] > link.txt
node "$SKILL_DIR/preview.mjs" "$(cat link.txt)" preview.png [--tz America/Los_Angeles]
```

- `make_link.py`: design JSON -> link. `\n` in a value is a line break; it warns about unfilled `{{...}}`.
- `preview.mjs`: link -> PNG of exactly the bitmap Print would send, at 3x, with `[[...]]` evaluated in
  `--tz` (default his, Pacific). It loads the link in headless Chromium and asks the page for its print
  bitmap, so it is the only faithful view of the printed result. A few seconds warm; the first run in a
  sandbox installs `puppeteer-core`, plus `chrome-headless-shell` (~100 MB) if there is no Chromium.
