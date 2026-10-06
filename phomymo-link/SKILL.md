---
name: phomymo-link
description: "Designs labels for Louie's Phomemo M110 printer and hands him a one-tap phomymo.louietyj.me link that opens the finished label ready to print: start from one of his templates or compose the layout yourself (text, shapes, barcodes, QR codes), and check a pixel-exact preview before sending. Use whenever he wants a label printed or a print link (food label, folder label, address label), including when the label text comes from something you looked up, like today's cafe menu."
---

# phomymo-link

A link `https://phomymo.louietyj.me/#design=v1.<data>` carries a whole label design. Opening it loads the
design into Phomymo, his fork of the browser label designer. He taps Print. Nothing is saved on his side.

## The design

A label is a JSON design: label size plus a list of text, shape, barcode, QR and image elements. Before
editing or composing one, read the format doc, which is deployed with the code it describes:

```bash
curl -fsSL https://phomymo.louietyj.me/docs/design-format.md
```

Phomymo has no official format docs; that page was reverse-engineered from the fork's source and says
which files to read when it isn't enough. If a field behaves differently from the doc, or you need one it
doesn't cover, read the code it points to (same host, e.g. `curl https://phomymo.louietyj.me/canvas.js`)
rather than guessing, and trust the preview over both. You can:

- **Start from one of his templates** when one fits. They live in his main Dropbox, not the durable
  filesystem, in `/Phomymo Templates/`, one Phomymo JSON export each. Use the Dropbox connector:
  `list_folder` to see what's there, then `download_link` for the one you need and `curl` it into the
  sandbox, which keeps the JSON byte-exact. Edit it freely in your copy: change the text, move or resize
  elements, add or remove them. Never write your changes back to his Dropbox.
- **Compose one from scratch** when nothing fits.

`{{Name}}` placeholders, if a template has them, are filled by `make_link.py`'s `Name=value` arguments, a
shortcut rather than a requirement. `[[dt|YYYY-MM-DD]]` and other `[[...]]` expressions are filled by
Phomymo at print time. Leave them.

## Make the link

With the design at `/tmp/label.json` (downloaded and edited, or written from scratch), one bash call, since
shell variables die between calls:

```bash
SKILL_DIR="$(ls -1dt /mnt/skills/*/phomymo-link ~/.claude/skills/synced/*/phomymo-link 2>/dev/null | head -1)"
python3 "$SKILL_DIR/make_link.py" /tmp/label.json "Content=Jasmine rice, broccoli, braised tofu, bulgogi" > /tmp/link.txt
node "$SKILL_DIR/preview.mjs" "$(cat /tmp/link.txt)" /tmp/preview.png && cat /tmp/link.txt
```

A Dropbox `download_link` is single-use and short-lived; get a fresh one per run. If `curl` can't reach
it, fall back to the connector's `fetch` and write the JSON yourself; templates are under 2 KB.

`\n` in a value is a line break. A `warning: {{X}} not filled` on stderr means you missed a field.

## Look at the preview before sending

`preview.mjs` opens the link in headless Chromium and saves exactly the bitmap Print would send, 3x size,
with `[[dt|...]]` dates filled in Louie's timezone. Look at `/tmp/preview.png` every time: it is how you
catch text running off the label, awkward line breaks or a field you missed. Fix the text and rerun until
it looks right. A few seconds once warm; the first run in a sandbox also installs `puppeteer-core` and,
if no Chromium is present, `chrome-headless-shell` (~100 MB).

It uses a plain Chromium only, never headless-browser's CloakBrowser, whose fingerprint patches perturb
canvas pixels. If it fails, say so and still give him the link; the preview is a check, not a gate.

## Writing the text

Labels are small (48 x 40 mm, about four lines of 18 characters at the usual size), and text that
doesn't fit runs off the label. Write what he'd say, not what the source says: "braised tofu", not
"Korean-Style Braised Tofu with Gochujang Glaze". If it still won't fit, cut items or ask which matter.

## Give him the link

Reply with a markdown link whose text is the label content, e.g.
`[Print: Jasmine rice, broccoli, braised tofu, bulgogi](https://phomymo.louietyj.me/#design=v1.…)`.
Show him the preview image with it. The link contains everything in the template, his phone number
included on some, so it goes to him, not anywhere public. Avoid images in templates: they make the link
tens of KB long.
