"""Mechanical tools for pulling postage out of a stamps.com PDF.

Nothing here assumes a layout: `inspect` reports what is on the page, and you
decide which image is the indicia and where the barcode is. For the address,
read `pdftotext -layout postage.pdf -` yourself.

    stamps_pdf.py inspect <pdf>
        Per page: size, embedded images (index, bbox, pixels, filters), rows of
        thin vector rectangles (candidate barcodes), and text lines.
    stamps_pdf.py imb <pdf> [--page N] [--row I | --bbox x0,top,x1,bottom]
        Decode an IMb. --row decodes vector row I from `inspect`; --bbox renders
        that region and reads the bars from pixels, which also works when the
        barcode is an image or font glyphs.
    stamps_pdf.py image <pdf> <index> <out_base> [--page N]
        Write embedded image <index> losslessly: <out_base>.jpg with the original
        bytes for JPEG data, otherwise <out_base>.png of the decoded pixels.
    stamps_pdf.py crop <pdf> x0,top,x1,bottom <out.png> [--page N] [--dpi 600]
        Render a region of the page to PNG.

Coordinates are PDF points with the origin at the top-left, as `inspect` prints.
They ignore the page's cropbox, so they can differ from what a viewer or
pdftoppm shows; `crop` uses the same frame, so check a region with it.
"""
import argparse
import json
import os
import sys

import pdfplumber
import pypdfium2 as pdfium

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import imb  # noqa: E402

IMB_BARS = 65


def bar_rows(page):
    """Group thin, short filled rectangles into rows whose vertical extents overlap."""
    bars = [r for r in page.rects if r["width"] < 4 and r["height"] < 30]
    rows = []
    for r in sorted(bars, key=lambda r: r["top"]):
        for row in rows:
            if r["top"] <= row["bottom"] and r["bottom"] >= row["top"]:
                row["rects"].append(r)
                row["top"] = min(row["top"], r["top"])
                row["bottom"] = max(row["bottom"], r["bottom"])
                break
        else:
            rows.append({"top": r["top"], "bottom": r["bottom"], "rects": [r]})
    return rows


def classify(bars, top, bottom):
    """bars: [(x, bar_top, bar_bottom)] -> FADT string."""
    height = bottom - top
    code = ""
    for _, bar_top, bar_bottom in sorted(bars):
        ascends = bar_top < top + height * 0.2
        descends = bar_bottom > bottom - height * 0.2
        code += "TADF"[descends << 1 | ascends]
    return code


def decode_bars(code):
    if len(code) != IMB_BARS:
        raise SystemExit(f"found {len(code)} bars, an IMb has {IMB_BARS}")
    return imb.decode(code)


def imb_from_row(row):
    bars = [(r["x0"], r["top"], r["bottom"]) for r in row["rects"]]
    return decode_bars(classify(bars, row["top"], row["bottom"]))


def pdfium_page(pdf_path, page_index):
    page = pdfium.PdfDocument(pdf_path)[page_index]
    page.set_cropbox(*page.get_mediabox())
    return page


def render(pdf_path, page_index, bbox, dpi=600):
    """Render bbox, in pdfplumber's frame (rotation applied, cropbox ignored), to a PIL image."""
    page = pdfium_page(pdf_path, page_index)
    width, height = page.get_size()
    x0, top, x1, bottom = bbox
    crop = (x0, height - bottom, width - x1, top)
    return page.render(scale=dpi / 72, crop=crop, fill_color=(255, 255, 255, 255)).to_pil()


def imb_from_pixels(pdf_path, page_index, bbox, dpi=600):
    image = render(pdf_path, page_index, bbox, dpi).convert("L")
    width, height = image.size
    px = image.load()
    dark_ys = [[y for y in range(height) if px[x, y] < 128] for x in range(width)]
    bars, start = [], None
    for x in range(width + 1):
        dark = x < width and dark_ys[x]
        if dark and start is None:
            start = x
        elif not dark and start is not None:
            ys = [y for col in dark_ys[start:x] for y in col]
            bars.append((start, min(ys), max(ys)))
            start = None
    if not bars:
        raise SystemExit("no dark pixels in that region")
    return decode_bars(classify(bars, min(b[1] for b in bars), max(b[2] for b in bars)))


def filters(stream):
    return [getattr(f, "name", str(f)) for f, _ in stream.get_filters()]


def image_info(page):
    out = []
    for i, img in enumerate(page.images):
        out.append({
            "index": i,
            "bbox": [round(v, 2) for v in (img["x0"], img["top"], img["x1"], img["bottom"])],
            "pixels": list(img["srcsize"]),
            "filters": filters(img["stream"]),
        })
    return out


def inspect(pdf):
    pages = []
    for n, page in enumerate(pdf.pages):
        pages.append({
            "page": n,
            "size_pt": [round(page.width, 2), round(page.height, 2)],
            "size_in": [round(page.width / 72, 3), round(page.height / 72, 3)],
            "images": image_info(page),
            "bar_rows": [
                {"row": i, "bars": len(r["rects"]),
                 "bbox": [round(v, 2) for v in (min(x["x0"] for x in r["rects"]), r["top"],
                                                max(x["x1"] for x in r["rects"]), r["bottom"])]}
                for i, r in enumerate(bar_rows(page))
            ],
            "text_lines": [
                {"text": l["text"], "bbox": [round(v, 2) for v in (l["x0"], l["top"], l["x1"], l["bottom"])],
                 "size": round(l["chars"][0]["size"], 1)}
                for l in page.extract_text_lines()
            ],
        })
    return pages


def write_image(pdf_path, page_index, index, out_base):
    """pypdfium2 does the extraction: it writes JPEGs as-is and decodes other formats and palettes."""
    with pdfplumber.open(pdf_path) as pdf:
        expected = list(pdf.pages[page_index].images[index]["srcsize"])
    objects = list(pdfium_page(pdf_path, page_index).get_objects(filter=(pdfium.raw.FPDF_PAGEOBJ_IMAGE,)))
    image = objects[index]
    if list(image.get_px_size()) != expected:
        raise SystemExit(f"image {index}: pdfplumber and pypdfium2 disagree on its size; extract it with `crop`")
    extensions = (".jpg", ".png", ".tif", ".jp2")
    for ext in extensions:
        if os.path.exists(out_base + ext):
            os.remove(out_base + ext)
    image.extract(out_base)
    return next(out_base + ext for ext in extensions if os.path.exists(out_base + ext))


def parse_bbox(text):
    values = [float(v) for v in text.split(",")]
    if len(values) != 4:
        raise SystemExit("bbox must be x0,top,x1,bottom")
    return tuple(values)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("inspect")
    p.add_argument("pdf")
    p = sub.add_parser("imb")
    p.add_argument("pdf")
    p.add_argument("--page", type=int, default=0)
    group = p.add_mutually_exclusive_group()
    group.add_argument("--row", type=int)
    group.add_argument("--bbox", type=parse_bbox)
    p = sub.add_parser("image")
    p.add_argument("pdf")
    p.add_argument("index", type=int)
    p.add_argument("out_base")
    p.add_argument("--page", type=int, default=0)
    p = sub.add_parser("crop")
    p.add_argument("pdf")
    p.add_argument("bbox", type=parse_bbox)
    p.add_argument("out")
    p.add_argument("--page", type=int, default=0)
    p.add_argument("--dpi", type=int, default=600)
    args = parser.parse_args()

    if args.cmd == "inspect":
        with pdfplumber.open(args.pdf) as pdf:
            print(json.dumps(inspect(pdf), indent=1))
    elif args.cmd == "imb" and args.bbox:
        print(imb_from_pixels(args.pdf, args.page, args.bbox))
    elif args.cmd == "imb":
        with pdfplumber.open(args.pdf) as pdf:
            rows = bar_rows(pdf.pages[args.page])
        if args.row is None:
            candidates = [i for i, r in enumerate(rows) if len(r["rects"]) == IMB_BARS]
            if len(candidates) != 1:
                raise SystemExit(f"{len(candidates)} vector rows have {IMB_BARS} bars; pass --row or --bbox")
            args.row = candidates[0]
        print(imb_from_row(rows[args.row]))
    elif args.cmd == "image":
        print(write_image(args.pdf, args.page, args.index, args.out_base))
    elif args.cmd == "crop":
        render(args.pdf, args.page, args.bbox, args.dpi).save(args.out)
        print(args.out)


if __name__ == "__main__":
    main()
