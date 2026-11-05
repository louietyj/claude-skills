#!/usr/bin/env python3
"""Fill the Food Label (Menu) template once per label, shrinking the font until the text fits.

usage: labels.py TEMPLATE.json OUT_DIR "Label one text" ["Label two text" ...]

For each label it tries font sizes 28, 26, 24, 22, 20 and keeps the largest whose preview stays inside
the text box. Prints one line per label: `FITS <size> <text>` or `OVERFLOW 20 <text>` (still too long at
the minimum: shorten the text and rerun). Writes OUT_DIR/labels.html, the page to publish, and
OUT_DIR/tile.png, the previews tiled for checking. Links never reach stdout, so they are never retyped.
"""
import copy, glob, json, os, subprocess, sys, tempfile
from PIL import Image

SIZES = [28, 26, 24, 22, 20]  # 20 is the floor: ~19 px bold is the smallest he finds legible on the M110
PHOMYMO = sorted(glob.glob("/mnt/skills/*/phomymo-link") + glob.glob(os.path.expanduser("~/.claude/skills/synced/*/phomymo-link")),
                 key=os.path.getmtime, reverse=True)[0]

def overflows(png, box, pill_top):
    # The preview is the 384 px-wide print bitmap scaled up. Any ink outside the text box (above it, in
    # the gap before the date pill, or in the side margins) means the text spilled.
    im = Image.open(png).convert("L"); s = im.width / 384
    x0, y0, x1, y1 = box
    def ink(xa, ya, xb, yb):
        return any(im.getpixel((x, y)) < 128 for y in range(int(ya*s), int(yb*s)) for x in range(int(xa*s), int(xb*s)))
    return ink(0, 0, 384, y0) or ink(0, y1, 384, pill_top) or ink(0, y0, x0, y1) or ink(x1, y0, 384, y1)

def render(design, text, png, link_txt):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(design, f)
    link = subprocess.run(["python3", f"{PHOMYMO}/make_link.py", f.name, f"Food={text}"],
                          check=True, capture_output=True, text=True).stdout.strip()
    open(link_txt, "w").write(link)
    subprocess.run(["node", f"{PHOMYMO}/preview.mjs", link, png], check=True, capture_output=True)

template, out, texts = json.load(open(sys.argv[1])), sys.argv[2], sys.argv[3:]
os.makedirs(out, exist_ok=True)
food = next(e for e in template["elements"] if e.get("text") == "{{Food}}")
pill_top = min(e["y"] for e in template["elements"] if e.get("type") == "shape")
box = (food["x"], food["y"], food["x"] + food["width"], food["y"] + food["height"])

pngs, page_args = [], []
for i, text in enumerate(texts):
    png, link_txt = os.path.join(out, f"{i+1}.png"), os.path.join(out, f"{i+1}.txt")
    for size in SIZES:
        design = copy.deepcopy(template)
        next(e for e in design["elements"] if e.get("text") == "{{Food}}")["fontSize"] = size
        render(design, text, png, link_txt)
        bad = overflows(png, box, pill_top)
        if not bad:
            break
    print(f"{'OVERFLOW' if bad else 'FITS'} {size} {text}")
    pngs.append(png)
    page_args += ["--label", text, link_txt, png]

page = subprocess.run(["python3", f"{PHOMYMO}/make_page.py", "--title", "Cafe labels", *page_args],
                      check=True, capture_output=True, text=True).stdout
open(os.path.join(out, "labels.html"), "w").write(page)

ims = [Image.open(p) for p in pngs]; w, h = ims[0].size; cols = min(2, len(ims)); rows = -(-len(ims) // cols)
tile = Image.new("RGB", (cols*w + (cols-1)*20, rows*h + (rows-1)*20), "white")
for k, im in enumerate(ims):
    tile.paste(im, ((k % cols)*(w+20), (k // cols)*(h+20)))
tile.save(os.path.join(out, "tile.png"))
