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

Start a design from `/Phomymo Templates/48x40.json` in his main Dropbox (not the durable filesystem): his
blank page, like Word's Normal.dotm. It sets the label size for his 50 x 40 mm labels (printed 48 mm wide,
the head's width) and holds one text box inset 3 mm on every side, with sample text. It carries no design
intent; lay the label out as you see fit. The other files in that folder are bare-bones quick starts he
edits by hand in Phomymo, not designs to fill, so open one only if he names it. The Dropbox connector's
`download_link` plus `curl` gets a file into the sandbox byte-exact; the link is single-use and expires,
and `fetch` is the fallback if `curl` can't reach it.

`{{Name}}` placeholders are for designs you write yourself: `make_link.py` fills them from `Name=value`
arguments and errors on an argument that matches none. `[[...]]` expressions are evaluated by Phomymo when
it prints; the format doc lists them and their pitfalls.

## Scripts

One bash call, since shell variables die between calls:

```bash
SKILL_DIR="$(ls -1dt /mnt/skills/*/phomymo-link ~/.claude/skills/synced/*/phomymo-link 2>/dev/null | head -1)"
python3 "$SKILL_DIR/make_link.py" design.json [Name=value ...] > link.txt
node "$SKILL_DIR/preview.mjs" "$(cat link.txt)" preview.png [--tz America/Los_Angeles]
```

- `make_link.py`: design JSON -> link. `\n` in a value is a line break; it warns about unfilled `{{...}}`
  and errors on a `Name=value` with no matching placeholder.
- `preview.mjs`: link -> PNG of exactly the bitmap Print would send, at 3x, with `[[...]]` evaluated in
  `--tz` (default his, Pacific). It loads the link in headless Chromium and asks the page for its print
  bitmap, so it is the only faithful view of the printed result. A few seconds warm; the first run in a
  sandbox installs `puppeteer-core`, plus `chrome-headless-shell` (~100 MB) if there is no Chromium.
