# phomymo-link

Lets Claude design a label, from one of Louie's templates or from scratch, and hand him a link that opens
it at [phomymo.louietyj.me](https://phomymo.louietyj.me), his fork of
[Phomymo](https://github.com/transcriptionstream/phomymo), ready to print on his M110.

```
SKILL.md          when to use it, where templates live, how to write label text
design-format.md  the design JSON, for editing templates or composing labels from scratch
make_link.py      design JSON (+ optional Field=value pairs) -> link (stdlib only)
preview.mjs       link -> PNG of the exact bitmap Print would send (headless Chromium)
```

`design-format.md` was worked out from the fork's source (`elements.js` for fields and defaults,
`canvas.js` for what values do, `index.html` for the option lists); Phomymo has no format docs.

## Preview

`preview.mjs` opens the link in headless Chromium via `puppeteer-core` and calls
`window.phomymoPrintPreview()`, a hook in the fork that builds the bitmap through the same
`buildPrintRaster()` the Print button uses. Nothing about rendering is reimplemented, so the preview can't
drift from what prints. An earlier prototype rendered in Node with `@napi-rs/canvas` and Phomymo's
`canvas.js`; it worked, but about half of it re-created browser behaviour (image loading, SVG, fonts) and
the app's print setup, and one of those gaps silently dropped images.

The bitmap still depends on the browser's text rasteriser: Chrome on Windows (DirectWrite) and on
Android or Linux (FreeType) differ by edge pixels, so there is no single ground truth, and Linux Chromium
is closest to his phone. It uses the first plain Chromium it finds (`/usr/bin/chromium`, Playwright's or
puppeteer's caches), else installs `chrome-headless-shell` into `~/.cache/phomymo-preview`. CloakBrowser
is never used: its fingerprint patches add noise to canvas reads. Measured in the `dev/sandbox.sh`
container: 2.3s warm, 8s first run, 14s when it has to download Chromium.

## The link format

`#design=v1.<base64url(deflate-raw(minified design JSON))>`. The fork decodes it with the browser's
`DecompressionStream('deflate-raw')` and hands it to the same code as Import from File, which replaces the
design on screen and saves nothing. The design rides in the fragment, which browsers never send to the
server. A text-only label comes to about 600 characters; `v1` leaves room to change the encoding.

## Templates

Not in this repo, since some carry personal details. They sit in Louie's Dropbox under
`/Phomymo Templates/`, as JSON exported from Phomymo, and the skill pulls them through the Dropbox
connector. `{{Field}}` placeholders are filled by
`make_link.py`; `[[...]]` expressions (dates) are left for Phomymo to fill at print time.

## Package for claude.ai

`python package.py` writes `phomymo-link.zip`; upload it under Settings → Capabilities → Skills. Needs
the Dropbox connector for the templates.
