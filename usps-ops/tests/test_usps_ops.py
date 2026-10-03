"""Tests for the usps-ops scripts.

The stamps.com PDF and template tests use Louie's real files, from the Dropbox
folders below (override with USPS_DROPBOX), and are skipped when they're absent.
"""
import datetime
import glob
import os
import re
import sys
import zipfile

import pdfplumber
import pytest
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import fill_template  # noqa: E402
import imb  # noqa: E402
import next_serial  # noqa: E402
import stamps_pdf  # noqa: E402
import tracking_dat  # noqa: E402

DROPBOX = os.environ.get("USPS_DROPBOX", os.path.expanduser("~/Dropbox"))
SENT = os.path.join(DROPBOX, "Sent Mail")
TEMPLATES = os.path.join(DROPBOX, "Custom Office Templates")

# Bars from finished pieces that were built by hand, one for each of our mailer ID's first serials.
POSTCARD_IMB = "0031090342900700000755117560310"
POSTCARD_BARS = "FADTATTAAFADDTFDFDTATFDFDAFAATTDTDATTFTFADADTAAADFADDFDDAADTATFTF"
ENVELOPE_IMB = "0031090342900700000619406260888"
ENVELOPE_BARS = "ATTFATFDAAFDDDAAFTDTFATDDTTDAADTDDDATDFDADAADFFAFTDAFAFDATDFTDFDD"

# stamps.com IMbs whose PDFs are in Sent Mail, found by the IMb in the filename.
STAMPS_IMBS = [
    "0004020624153877262955117560310",
    "0004020624153883179194025140099",
    # Acrobat Distiller: page rotated 90° with a cropbox, and a palette (Flate) indicia.
    "0004020623953621295191510156565",
]
DISTILLER_IMB = STAMPS_IMBS[-1]


def need(path):
    if not os.path.exists(path):
        pytest.skip(f"{path} not available")
    return path


def sent_pdf(stamps_imb):
    matches = glob.glob(os.path.join(SENT, f"*{stamps_imb}*.pdf"))
    if not matches:
        pytest.skip(f"no PDF for {stamps_imb} in {SENT}")
    return matches[0]


@pytest.mark.parametrize("imb_digits,bars", [(POSTCARD_IMB, POSTCARD_BARS), (ENVELOPE_IMB, ENVELOPE_BARS)])
def test_encode_matches_finished_pieces(imb_digits, bars):
    assert imb.encode(imb_digits) == bars
    assert imb.decode(bars) == imb_digits


def test_build_uses_our_mid_and_stid():
    assert imb.build(7, "55117560310") == POSTCARD_IMB


def test_decode_keeps_leading_zero_in_routing():
    puerto_rico = imb.build(8, "00901123401")
    assert imb.decode(imb.encode(puerto_rico)) == puerto_rico


def test_split_six_digit_mid():
    f = imb.split("0004020624153877262955117560310")
    assert (f["stid"], f["mid"], f["serial"], f["zip9"], f["dpc"]) == ("040", "206241", "538772629", "551175603", "10")


def test_next_serial_ignores_other_mids():
    text = "\n".join([
        "2026-10-02 - Acme - Refund (Envelope) - 0004020624153877262955117560310.pdf",
        "2026-10-02 - Acme - Refund (Postcard) - " + POSTCARD_IMB + ".docx",
        "9/23/2026\t194062608\t88\t00310903429007000006\tACME",
    ])
    assert next_serial.serials(text) == [7, 6]


def test_tracking_dat_prepends_crlf_line(tmp_path):
    path = tmp_path / "tracking.dat"
    path.write_bytes(b"9/9/2026\t940251400\t99\t00040206241538831791\tOLDCO\r\n")
    line = tracking_dat.make_line(POSTCARD_IMB, "Acme", datetime.date(2026, 10, 2))
    tracking_dat.prepend(str(path), line)
    assert path.read_bytes() == (
        b"10/2/2026\t551175603\t10\t00310903429007000007\tACME\r\n"
        b"9/9/2026\t940251400\t99\t00040206241538831791\tOLDCO\r\n"
    )
    with pytest.raises(SystemExit):
        tracking_dat.prepend(str(path), line)


@pytest.mark.parametrize("expected", STAMPS_IMBS)
def test_stamps_pdf_decodes_imb_from_vectors_and_pixels(expected):
    path = sent_pdf(expected)
    with pdfplumber.open(path) as pdf:
        rows = [r for r in stamps_pdf.bar_rows(pdf.pages[0]) if len(r["rects"]) == stamps_pdf.IMB_BARS]
    assert len(rows) == 1
    row = rows[0]
    assert stamps_pdf.imb_from_row(row) == expected
    x0 = min(r["x0"] for r in row["rects"])
    x1 = max(r["x1"] for r in row["rects"])
    padded = (x0 - 3, row["top"] - 3, x1 + 3, row["bottom"] + 3)
    assert stamps_pdf.imb_from_pixels(path, 0, padded) == expected


def test_stamps_pdf_writes_jpeg_unchanged(tmp_path):
    path = sent_pdf(STAMPS_IMBS[0])
    out = stamps_pdf.write_image(path, 0, 0, str(tmp_path / "indicia"))
    with pdfplumber.open(path) as pdf:
        assert open(out, "rb").read() == pdf.pages[0].images[0]["stream"].get_rawdata()


def test_stamps_pdf_decodes_palette_image_to_png(tmp_path):
    out = stamps_pdf.write_image(sent_pdf(DISTILLER_IMB), 0, 0, str(tmp_path / "indicia"))
    with Image.open(out) as im:
        assert out.endswith(".png") and im.size == (290, 201)
        darkest, lightest = im.convert("L").getextrema()
        assert lightest - darkest > 200  # real content, not a blank or all-black render


def fill(tmp_path, template, spec):
    out = tmp_path / "out.docx"
    filled = fill_template.fill(need(os.path.join(TEMPLATES, template)), str(out), spec)
    with zipfile.ZipFile(out) as z:
        parts = {n: z.read(n).decode("utf-8", "replace") for n in z.namelist()}
    return filled, parts


def stamp_image(tmp_path):
    path = tmp_path / "indicia.jpg"
    Image.new("RGB", (290, 201), "white").save(path)
    return str(path)


def assert_no_placeholders(parts):
    for name, xml in parts.items():
        if name.startswith("word/") and "glossary" not in name and name.endswith(".xml"):
            assert not re.search(r"&lt;&lt;?[A-Z]+&gt;&gt;?", xml), name


def test_fill_postcard(tmp_path):
    spec = {
        "text": {
            "CONTENT": ["Hello,", "", "Name:\tLouie Tan"],
            "ADDRESS": ["ACME", "C/O REGISTERED AGENT, INC.", "1010 DALE ST N", "SAINT PAUL MN 55117-5603"],
        },
        "font_size": {"CONTENT": 9},
        "barcode": POSTCARD_BARS,
        "stamp": stamp_image(tmp_path),
    }
    filled, parts = fill(tmp_path, "Postcard.dotx", spec)
    assert filled == ["ADDRESS", "BARCODE", "CONTENT", "STAMP"]
    assert_no_placeholders(parts)
    assert "document.main+xml" in parts["[Content_Types].xml"]
    assert 'Extension="jpg"' in parts["[Content_Types].xml"]
    assert "media/usps_indicia.jpg" in parts["word/_rels/header1.xml.rels"]
    assert parts["word/header1.xml"].count("<a:blip ") == 2  # DrawingML and VML-fallback copies
    assert parts["word/document.xml"].count(POSTCARD_BARS) == 2
    assert 'w:ascii="USPSIMBStandard"' in parts["word/document.xml"]
    assert "<w:tab/>" in parts["word/document.xml"]
    assert parts["word/document.xml"].count('<w:sz w:val="18"/>') >= 2  # CONTENT at 9pt, in both copies
    assert 'cx="1941840"' in parts["word/document.xml"]  # address box left as the template has it


@pytest.mark.parametrize("template", ["Envelope #10 (USPS).dotx", "Envelope #6 (USPS).dotx"])
def test_fill_envelopes(template, tmp_path):
    spec = {
        "text": {"ADDRESS": ["ACME", "488 DREW CT", "KNG OF PRUSSA PA 19406-2608"]},
        "barcode": ENVELOPE_BARS,
        "stamp": stamp_image(tmp_path),
    }
    filled, parts = fill(tmp_path, template, spec)
    assert filled == ["ADDRESS", "BARCODE", "STAMP"]
    assert_no_placeholders(parts)
    assert "KNG OF PRUSSA PA 19406-2608" in parts["word/document.xml"]


def test_fill_letterhead(tmp_path):
    spec = {"text": {"DATE": ["2 Oct 2026"], "RECIPIENT": ["Acme", "Saint Paul, MN 55117"], "CONTENT": ["Hello,"]}}
    filled, parts = fill(tmp_path, "Letterhead (US).dotx", spec)
    assert filled == ["CONTENT", "DATE", "RECIPIENT"]
    doc = parts["word/document.xml"]
    assert_no_placeholders(parts)
    assert "<w:sdt>" not in doc and "PlaceholderText" not in doc and "[Publish Date]" not in doc
    assert "2 Oct 2026" in doc


def test_fill_rejects_missing_placeholder(tmp_path):
    with pytest.raises(SystemExit, match="unfilled placeholders"):
        fill(tmp_path, "Envelope #10 (USPS).dotx", {"text": {"ADDRESS": ["X"]}})
