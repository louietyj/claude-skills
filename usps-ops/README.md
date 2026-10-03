# usps-ops

Turns Louie's letters, postcards and stamps.com postage into print-ready Word files that carry his own Intelligent Mail barcode, so USPS Informed Visibility tracks each piece under his mailer ID. USPS stopped letting one mailer ID look up another's pieces, so stamps.com's barcode can no longer be used for tracking.

```
SKILL.md              the workflow, naming, and what each script automates
scripts/
  stamps_pdf.py       inspect a stamps.com PDF; extract the indicia; decode its barcode
  imb.py              build, encode and decode IMbs
  imb_core.py         USPS encoder/decoder, vendored from a BSD-licensed pure-Python implementation
  next_serial.py      next free serial under his mailer ID
  fill_template.py    .dotx + spec.json -> .docx
  preview.sh          .docx -> PNG via LibreOffice
  tracking_dat.py     prepend a line to QuickLetterTracker's tracking.dat
fonts/                Figtree (OFL) for previews; USPSIMBStandard is not committed, see below
tests/test_usps_ops.py
```

The scripts are mechanical primitives. Judgement, such as reading an unfamiliar PDF layout or polishing a postcard that overflows, is left to the agent, which SKILL.md briefs on what each script does so it can do the step itself when a script doesn't fit.

## Tests

```bash
pip install pdfplumber pypdfium2 lxml pillow pytest
pytest tests
```

The encoder, serial and `tracking.dat` tests are self-contained. The PDF and template tests read his Dropbox (`~/Dropbox/Sent Mail`, `~/Dropbox/Custom Office Templates`, or `$USPS_DROPBOX`) and skip when it isn't there.

## Package for claude.ai

Put `USPSIMBStandard.ttf` (free from USPS's [Intelligent Mail barcode font page](https://ribbs.usps.gov/onecodesolution/download.cfm)) in `fonts/`. It has no license text, so it is gitignored rather than redistributed. Then `python package.py` writes `usps-ops.zip`; upload it under Settings → Capabilities → Skills.

The Dropbox connector can't upload binaries, so the skill hands over the files as downloads and he saves them to `Sent Mail` himself.
