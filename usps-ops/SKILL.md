---
name: usps-ops
description: Prepares Louie's physical mail — letters on his letterhead, and stamps.com postage turned into a print-ready envelope or postcard that carries his own Intelligent Mail barcode (IMb) for tracking — logs each piece to QuickLetterTracker, and tracks pieces through USPS Informed Visibility day by day. Use when he wants to write or send a letter or postcard by mail, attaches a stamps.com postage PDF, mentions IMb, mail tracking, envelopes, postcards, or "Sent Mail", or asks to opt out of arbitration by mail.
---

# usps-ops

| Piece | Deliverables |
|---|---|
| Letter | letter `.docx` + envelope `.docx` with postage |
| Postcard | one postcard `.docx` (message + postage) |

Every stamped piece also gets: the stamps.com PDF renamed `(Postage).pdf`, and a line in `tracking.dat`.

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
7. **Log** our IMb to `tracking.dat`, and add the piece to the tracking list.
8. **Track** it: the Duet check-in does this daily, using Tracking a piece.

## Setup

```bash
SK="$(ls -1dt /mnt/skills/*/usps-ops ~/.claude/skills/synced/*/usps-ops 2>/dev/null | head -1)"; echo "$SK"
```

Shell variables die between bash calls: re-run that line at the top of any call that uses `$SK`. Work in `/tmp/usps`.

## Dropbox

Read through the Dropbox connector. For binaries, get a `download_link` and `curl -sSfo <file> "<url>"` it **once** — the URL is single-use, so never open or preview it first.

- Templates: `/Custom Office Templates/` — `Letterhead (US).dotx`, `Postcard.dotx`, `Envelope #10 (USPS).dotx`, `Envelope #6 (USPS).dotx`. Always fetch fresh; never reuse a copy from an earlier conversation.
- Finished mail: `/Sent Mail/`
- Tracker: `/QuickLettertracker/tracking.dat`. Never touch `prefs.dat` or `qlprefs` beside it.

The connector cannot upload binaries, but it can move them. Put each `.docx`/`.pdf` into `Sent Mail` in two steps:

1. `cfs upload /outbox/<file> --from <local file> --new` (the durable filesystem; `session-init` sets it up).
2. Connector `move` from `/Apps/louietyj-claude-ai/outbox/<file>` to `/Sent Mail/<file>`. He approves each move.

If `cfs` isn't available or he declines a move, deliver those files as downloads (save them to `/mnt/user-data/outputs/`) and tell him to drop them in `Sent Mail`.

## Naming

`YYYY-MM-DD - <Recipient> - <Subject>` is the stem, e.g. `2026-10-02 - Acme - Arbitration opt-out`. List `/Sent Mail/` and match how earlier pieces to the same kind of recipient were named.

- `<stem>.docx` — letter
- `<stem> (Postage).pdf` — his stamps.com PDF, renamed
- `<stem> (Envelope) - <our IMb>.docx` or `<stem> (Postcard) - <our IMb>.docx`
- `<stem> (Tracking).json` for an envelope, `<stem> (Postcard) (Tracking).json` for a postcard

A file name carries an IMb only if that barcode is tracked: ours names the piece, while stamps.com's is replaced on the piece and stays out of the postage PDF's name.

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

Then put the deliverables, including the renamed postage PDF, in `Sent Mail` under their final names (see Dropbox) and list them for him.

**He does the final polish in Word.** Point out layout problems you can see, such as an address line that wraps or postcard text cut off at the bottom. Fix them only if he asks you to polish. If he does, the fixes are your call: a smaller `font_size` (his postcards have used 9pt text), shorter wording, or a wider text box.

## Logging to tracking.dat

After he has the files:

```bash
python3 "$SK/scripts/tracking_dat.py" tracking.dat <our IMb> "<RECIPIENT SHORT NAME>" [YYYY-MM-DD]
```

The name is the stem's recipient, e.g. `ACME`.

*Automates:* prepending `M/D/YYYY<TAB>zip9<TAB>dpc<TAB>tracking20<TAB>RECIPIENT` (no zero padding, CRLF endings, newest first), and refusing duplicates.

Write it back **in place**. Never delete and recreate it: the file gets a new ID, which breaks its public shared link, and the tracker app reads `tracking.dat` through that link. A move keeps the ID, and so does a `cfs` overwrite, so round-trip it through the app folder:

1. Tell him the exact line being added.
2. `get_file_metadata` on `/QuickLettertracker/tracking.dat` and note its `id`.
3. Connector `move` from that `id` to `/Apps/louietyj-claude-ai/tracking.dat`. He approves it.
4. `cfs read /tracking.dat` (the rev rule), then `cfs download` it and `cmp` it with the copy you edited from. If they differ, someone changed it in the meantime, so redo your edit on the current bytes.
5. `cfs upload /tracking.dat --from tracking.dat --rev <rev>`. Use upload, not `edit`, so the `\r\n` line endings survive byte for byte. Download it again and `cmp`.
6. Connector `move` from the same `id` back to `/QuickLettertracker/tracking.dat`. He approves it.
7. Confirm the `id` is unchanged and that `list_shared_links` on the path still shows the same link.

The file is missing from its folder between steps 3 and 6, so do them back to back.

Then add the piece to the tracking list, `/mail-tracking/pieces.md` in `cfs` (format in its header).

## Tracking a piece

USPS Informed Visibility reports a piece's scans by IMb, but only for pieces under his MID, so pieces made before 2026-09-23 with stamps.com's barcode can't be tracked any more. Its answers are unreliable twice over:
- Two lookups a minute apart can return different scans. `fetch` asks 5 times and keeps the answer with the most data.
- Scans sometimes **vanish** for good. So a version is never thrown away once it holds something the newer one lacks.

```bash
python3 "$SK/scripts/iv_track.py" fetch <IMb> > new.json      # best of 5, in the saved file format
python3 "$SK/scripts/iv_track.py" compare <old.json|-> new.json
```

`fetch` needs `pip install requests` if it's missing. It logs in with `USPS_USERNAME`, `USPS_PASSWORD` and `USPS_CLIENT_ID`, read from the environment, from `usps-iv.env` beside this file, or from `cfs` at `/_devfiles/claude-skills/usps-ops/usps-iv.env`. It reaches `services.usps.com` and `iv.usps.com`.

`compare` reports a `verdict`, plus the scans gained and lost, the latest scan and the expected delivery date:

| verdict | meaning | do |
|---|---|---|
| `empty` | no data yet (IV can lag a day or two after mailing) | nothing |
| `first` | no saved snapshot yet | save `new.json` as the piece's `(Tracking).json` |
| `same` | nothing changed | nothing |
| `changed` | new data, nothing lost | overwrite the `(Tracking).json` in place |
| `lost` | the saved snapshot holds a scan or field the new one lacks | keep the old version under a new name, then save the new one |

Moving files works as for `tracking.dat`, and the connector's `move` needs no approval, so an unattended run can do all of it:
- **Save a new file:** `cfs upload /outbox/<name> --from new.json --new`, then `move` it from `/Apps/louietyj-claude-ai/outbox/<name>` to `/Sent Mail/<name>`.
- **Overwrite in place:** `move` the file into `/Apps/louietyj-claude-ai/`, `cfs read` it for the rev, `cfs upload --rev` over it, and `move` it back.
- **Keep the old version:** rename it with `move`, adding ` (as of YYYY-MM-DD)` before `.json`, where the date is its `server_modified` date in PT. Then save `new.json` under the plain name.

To get the saved snapshot to compare against, `download_link` the `(Tracking).json` and `curl` it.

### Reading a snapshot

- **Settled:** no new scans for a couple of days, the last one in a destination phase (`Phase 3`, carrier sortation), and the expected delivery date passed. IV has no delivery scan for letters, so this is as close to "delivered" as it gets.
- **Worth a look:** no scans 3 days after mailing, no destination scan by the expected date, scans heading somewhere other than the recipient's area, a scan that looks like a return, or scans that vanished.

How often to check and when to stop is up to whoever is tracking; the Duet check-in's schedule is in its prompt.
