---
name: usps-ops
description: Prepares Louie's physical mail — letters on his letterhead, and stamps.com postage turned into a print-ready envelope or postcard that carries his own Intelligent Mail barcode (IMb) for tracking — and logs each piece to QuickLetterTracker. Use when he wants to write or send a letter or postcard by mail, attaches a stamps.com postage PDF, mentions IMb, mail tracking, envelopes, postcards, or "Sent Mail", or asks to opt out of arbitration by mail.
---

# usps-ops

| Piece | Deliverables |
|---|---|
| Letter | letter `.docx` + envelope `.docx` with postage |
| Postcard | one postcard `.docx` (message + postage) |

Every stamped piece also gets: the stamps.com PDF renamed `(Postage) - <stamps IMb>.pdf`, and a line in `tracking.dat`.

## Workflow

1. **Message.** Draft it with him. For a letter, build the letter `.docx` from the letterhead.
2. **Postage.** He buys it on stamps.com and attaches the PDF.
3. **Take three things from the PDF:**
   - the USPS-normalized recipient address;
   - the postage indicia image;
   - stamps.com's IMb, which you need only for its routing digits.
4. **Make our IMb.** It keeps the same routing, but uses his MID and his next serial. It **replaces** stamps.com's barcode on the piece. USPS no longer lets his MID track pieces under stamps.com's MID; under his own MID he can.
5. **Build the piece** from the envelope (#6 or #10) or postcard template:
   - indicia → stamp box;
   - our barcode → barcode slot;
   - address → address block;
   - for a postcard, the message as well.
6. **Preview, then deliver** the files under his naming convention. He does the final polish in Word and prints.
7. **Log** our IMb to `tracking.dat`.

## Setup

```bash
SK="$(ls -1dt /mnt/skills/*/usps-ops ~/.claude/skills/synced/*/usps-ops 2>/dev/null | head -1)"; echo "$SK"
```

Shell variables die between bash calls: re-run that line at the top of any call that uses `$SK`. Work in `/tmp/usps`.

## Dropbox

Read through the Dropbox connector. For binaries, get a `download_link` and `curl -sSfo <file> "<url>"` it **once** — the URL is single-use, so never open or preview it first.

- Templates: `/Custom Office Templates/` — `Letterhead (US).dotx`, `Postcard.dotx`, `Envelope #10 (USPS).dotx`, `Envelope #6 (USPS).dotx`. Always fetch fresh; never reuse a copy from an earlier conversation.
- Finished mail: `/Sent Mail/`
- Tracker: `/QuickLetterTracker/tracking.dat`. Never touch `prefs.dat` or `qlprefs` beside it.

The connector cannot upload binaries. Deliver `.docx`/`.pdf` files as downloads (save them to `/mnt/user-data/outputs/`) and tell him to drop them in `Sent Mail`.

## Naming

`YYYY-MM-DD - <Recipient> - <Subject>` is the stem, e.g. `2026-10-02 - Acme - Arbitration opt-out`. List `/Sent Mail/` and match how earlier pieces to the same kind of recipient were named.

- `<stem>.docx` — letter
- `<stem> (Postage) - <stamps.com IMb>.pdf` — his stamps.com PDF, renamed
- `<stem> (Envelope) - <our IMb>.docx` or `<stem> (Postcard) - <our IMb>.docx`

## Writing the message

Draft with him; he approves the wording before you build anything. Letters are dated like `2 Oct 2026` and address the recipient in normal case, as written in their terms (the USPS-normalized form is only for the envelope).

## Reading the stamps.com PDF

**Assume nothing about its layout.** Output varies with the stamps.com product and with how he saved it, and the scripts below are primitives, not a parser. Look at the page first (`pdftoppm -r 100 -png`), then work out where each piece is.

- **Recipient address.** Copy the lines stamps.com printed verbatim, because they are the USPS-normalized form (e.g. `KNG OF PRUSSA PA 19406-2608`). Exception: if he shortened the recipient so stamps.com would accept it (e.g. dropped a `c/o` line), restore his original name/attention lines in UPPERCASE. Keep the street line and city/state/ZIP line exactly as printed.
- **Indicia.** The postage block: amount, `US POSTAGE`, mail class, date, and a 2-D code. It goes on the piece exactly as stamps.com produced it, so extract it losslessly. Never redraw it.
- **stamps.com IMb.** 65 bars, each `F`/`A`/`D`/`T` (full, ascender, descender, tracker), that decode to 31 digits. The last 11 are the routing: ZIP5 + ZIP4 + delivery point. That's all you need from it.

```bash
pdftotext -layout postage.pdf -                          # the text, in reading order
python3 "$SK/scripts/stamps_pdf.py" inspect postage.pdf  # images, rows of thin rects, text lines, with positions
python3 "$SK/scripts/stamps_pdf.py" imb postage.pdf [--row I | --bbox x0,top,x1,bottom]
python3 "$SK/scripts/stamps_pdf.py" image postage.pdf <index> /tmp/usps/indicia   # lossless extract
python3 "$SK/scripts/stamps_pdf.py" crop postage.pdf x0,top,x1,bottom out.png      # render a region
python3 "$SK/scripts/imb.py" decode <65 FADT letters>
```

- `inspect` coordinates are PDF points from the top-left, after the page's rotation and before its cropbox. A viewer or `pdftoppm` applies the cropbox, so its positions can be offset. To check what a region really contains, `crop` it and look.
- `imb` with no options needs exactly one row of 65 vector bars. `--bbox` reads bars from pixels in a tight box around the barcode instead; it works for images, glyphs, anything rendered.
- A barcode set in the IMb font shows up as a 65-letter FADT text line. Decode that text directly.
- `image` writes a JPEG byte-for-byte, and any other format as a lossless PNG. If the indicia isn't a single embedded image, `crop` its region at 600 dpi.

**When none of these fit, write your own code.** pdfplumber, pypdfium2, poppler and PIL are all available. If you can't get a decode you trust, ask him for the IMb from his stamps.com receipt. **Never guess digits.**

**Checks:**
- **ZIP+4.** The decoded routing's ZIP+4 should equal the ZIP+4 in the recipient address: `55117560310` goes with `SAINT PAUL MN 55117-5603`. 73 of his 74 past pieces matched. In the one that didn't, the address said `20850-4302` and the barcode said `20850-4304`. On a mismatch, first rule out a misread by decoding another way. If the barcode really differs, tell him and let him choose.
- **Other fields.** Past stamps.com IMbs all had barcode ID `00`, STID `040`, and a MID from `206238` to `206241`. Mention anything else.
- **Indicia.** Look at the extracted image before using it.

## Our IMb

1. **Serial.** Download `tracking.dat`, list `/Sent Mail/`, and save the names to a file. Then `python3 "$SK/scripts/next_serial.py" tracking.dat listing.txt`. It refuses to guess if it finds nothing — don't override that.
   *Automates:* the highest serial under MID `903429007` found in either source, + 1. Never reuse a serial.
2. **Build.** `python3 "$SK/scripts/imb.py" build <serial> <routing>`.
   *Automates:* `00` + STID `310` + `903429007` + 6-digit serial + routing. STID 310 means Basic First-Class Mail, no address correction, IV tracking. Don't copy stamps.com's `040`: it requests manual address correction, which needs a printed endorsement.
3. **Bars.** `python3 "$SK/scripts/imb.py" encode <our IMb>`.
   *Automates:* the USPS encoding (CRC + codeword tables) in `imb_core.py`. Never produce bars any other way. Always round-trip with `imb.py decode`.

**Which envelope (#6 or #10).** Use what he said. If he didn't say, ask.

## Building the piece

```bash
python3 "$SK/scripts/fill_template.py" template.dotx out.docx spec.json
```

```json
{
  "text": {"ADDRESS": ["ACME", "C/O REGISTERED AGENT, INC.", "1010 DALE ST N", "SAINT PAUL MN 55117-5603"],
           "CONTENT": ["Hello,", "", "Name:\tLouie Tan"]},
  "font_size": {"CONTENT": 9},
  "barcode": "FADT…",
  "stamp": "/tmp/usps/indicia.jpg"
}
```

*Automates:* a new `.docx` from the template, with each `<NAME>`/`<<NAME>>` placeholder filled:
- the indicia image goes in the stamp box, 1.54" wide, as on his past pieces;
- our bars go in the barcode slot, set in the `USPSIMBStandard` font;
- the address lines and the message go in as one paragraph per list item (`""` is a blank line, `\t` a tab).

The letterhead's date and recipient fields are filled as `DATE` and `RECIPIENT`. `font_size` is the only formatting applied; layout is left alone. The script fails if a placeholder is left unfilled, or if the spec names one it can't find, e.g. because he edited the template. In that case, open the template, see what changed, and fill it yourself.

## Verify, then deliver

```bash
bash "$SK/scripts/preview.sh" /tmp/usps/preview out1.docx out2.docx
```

*Automates:* installing the bundled fonts (Figtree, USPSIMBStandard), then running `soffice` → PDF → `pdftoppm`. Without those fonts the barcode renders as letters. LibreOffice only approximates Word, so judge content and placement, not exact line breaks.

Check:
- the stamp is in its box;
- the barcode renders as bars;
- `imb.py decode <the bars you inserted>` equals our IMb.

Then save the deliverables, including the renamed postage PDF, under their final names and list them for him.

**He does the final polish in Word.** Point out layout problems you can see, such as an address line that wraps or postcard text cut off at the bottom. Fix them only if he asks you to polish. If he does, the fixes are your call: a smaller `font_size` (his postcards have used 9pt text), shorter wording, or a wider text box.

## Logging to tracking.dat

After he has the files:

```bash
python3 "$SK/scripts/tracking_dat.py" tracking.dat <our IMb> "<RECIPIENT SHORT NAME>" [YYYY-MM-DD]
```

The name is the stem's recipient, e.g. `ACME`.

*Automates:* prepending `M/D/YYYY<TAB>zip9<TAB>dpc<TAB>tracking20<TAB>RECIPIENT` (no zero padding, CRLF endings, newest first), and refusing duplicates.

Writing it back means replacing the file through the connector:

1. Tell him the exact line being added.
2. `delete` the old file. That moves it to Dropbox's deleted files, so it's recoverable.
3. `create_file` at the same path with the new full content, keeping `\r\n` line endings.
4. Download it again and `cmp` it with your local copy. If they differ, say so and stop.
