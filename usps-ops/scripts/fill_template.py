"""Fill one of Louie's Word templates (.dotx) and save it as a .docx.

    fill_template.py <template.dotx> <out.docx> <spec.json>

spec.json keys (all optional, but every placeholder in the template must be filled):
    "text":    {"NAME": ["line", "", "line"], ...}
               Fills <NAME> / <<NAME>>. Each list item becomes its own paragraph,
               cloned from the placeholder's paragraph; "" is a blank paragraph.
    "barcode": "FADT..."   65-char IMb bar string for <BARCODE>, set in USPSIMBStandard.
    "stamp":   "indicia.jpg"   image for <STAMP>, 1.54" wide, aspect ratio kept.
    "font_size": {"NAME": 9}   point size for a text placeholder's runs.

The script makes no layout decisions: text that overflows its box stays that way.

The letterhead's content controls are unwrapped into plain paragraphs first: the
"Publish Date" control becomes <<DATE>> and the "Address" control <<RECIPIENT>>.

Text-box placeholders exist twice (DrawingML and its VML fallback); both get filled.
"""
import copy
import json
import os
import re
import shutil
import sys
import tempfile
import zipfile

from lxml import etree
from PIL import Image

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
IMAGE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
W = "{%s}" % W_NS
NS = {"w": W_NS, "wp": WP_NS}

PLACEHOLDER_RE = re.compile(r"<<?([A-Z]+)>>?")
BARCODE_FONT = "USPSIMBStandard"
# Matches the stamp size in Louie's finished pieces; the template's stamp box is 1414780 EMU wide.
STAMP_WIDTH_EMU = 1405890
STAMP_MEDIA = "media/usps_indicia"

INLINE_PIC = """\
<w:drawing xmlns:w="{w}" xmlns:wp="{wp}" xmlns:r="{r}"
    xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
    xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">
  <wp:inline distT="0" distB="0" distL="0" distR="0">
    <wp:extent cx="{cx}" cy="{cy}"/>
    <wp:effectExtent l="0" t="0" r="0" b="0"/>
    <wp:docPr id="{doc_pr_id}" name="Postage {doc_pr_id}"/>
    <wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect="1"/></wp:cNvGraphicFramePr>
    <a:graphic>
      <a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">
        <pic:pic>
          <pic:nvPicPr><pic:cNvPr id="0" name="{name}"/><pic:cNvPicPr><a:picLocks noChangeAspect="1"/></pic:cNvPicPr></pic:nvPicPr>
          <pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>
          <pic:spPr>
            <a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>
            <a:prstGeom prst="rect"><a:avLst/></a:prstGeom>
          </pic:spPr>
        </pic:pic>
      </a:graphicData>
    </a:graphic>
  </wp:inline>
</w:drawing>"""


def unwrap_content_controls(root):
    """Replace each content control with its content, turning known ones into placeholders."""
    for sdt in list(root.iter(W + "sdt")):
        alias = sdt.find("w:sdtPr/w:alias", NS)
        content = sdt.find("w:sdtContent", NS)
        texts = content.findall(".//w:t", NS)
        current = "".join(t.text or "" for t in texts)
        if alias is not None and alias.get(W + "val") == "Publish Date":
            placeholder = "<<DATE>>"
        elif current == "Address":
            placeholder = "<<RECIPIENT>>"
        else:
            placeholder = None
        if placeholder and texts:
            texts[0].text = placeholder
            for t in texts[1:]:
                t.getparent().remove(t)
        for style in content.iterfind(".//w:rStyle", NS):
            if style.get(W + "val") == "PlaceholderText":
                style.getparent().remove(style)
        parent = sdt.getparent()
        index = parent.index(sdt)
        for child in reversed(list(content)):
            parent.insert(index, child)
        parent.remove(sdt)


def placeholder_runs(root):
    """Yield (name, run, t) for every w:t whose text is exactly one placeholder."""
    for t in list(root.iter(W + "t")):
        m = PLACEHOLDER_RE.fullmatch((t.text or "").strip())
        if m:
            yield m.group(1), t.getparent(), t


def set_text(t, text):
    t.text = text
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")


def set_run_text(run, line):
    """Replace the run's text with line, turning tabs into <w:tab/>."""
    for old in run.findall("w:t", NS) + run.findall("w:tab", NS):
        run.remove(old)
    for i, segment in enumerate(line.split("\t")):
        if i:
            etree.SubElement(run, W + "tab")
        if segment:
            set_text(etree.SubElement(run, W + "t"), segment)


def fill_lines(run, lines):
    """Put lines[0] in place of the placeholder; clone the paragraph for the rest."""
    para = run.getparent()
    if para.tag != W + "p":
        raise ValueError("placeholder run is not directly inside a paragraph")
    template_run = copy.deepcopy(run)
    set_run_text(run, lines[0])
    anchor = para
    for line in lines[1:]:
        new_para = etree.Element(W + "p")
        ppr = para.find("w:pPr", NS)
        if ppr is not None:
            new_para.append(copy.deepcopy(ppr))
        if line:
            new_run = copy.deepcopy(template_run)
            set_run_text(new_run, line)
            new_para.append(new_run)
        anchor.addnext(new_para)
        anchor = new_para


def run_properties(run):
    rpr = run.find("w:rPr", NS)
    if rpr is None:
        rpr = etree.Element(W + "rPr")
        run.insert(0, rpr)
    return rpr


def set_font_size(run, size_pt):
    rpr = run_properties(run)
    for tag in ("sz", "szCs"):
        el = rpr.find("w:" + tag, NS)
        if el is None:
            el = etree.SubElement(rpr, W + tag)
        el.set(W + "val", str(round(size_pt * 2)))


def set_barcode(run, t, bars):
    if not re.fullmatch(r"[FADT]{65}", bars):
        raise ValueError(f"barcode must be 65 FADT characters, got {bars!r}")
    set_text(t, bars)
    rpr = run_properties(run)
    fonts = rpr.find("w:rFonts", NS)
    if fonts is not None:
        rpr.remove(fonts)
    fonts = etree.Element(W + "rFonts")
    for attr in ("ascii", "hAnsi", "cs"):
        fonts.set(W + attr, BARCODE_FONT)
    style = rpr.find("w:rStyle", NS)
    rpr.insert(0 if style is None else 1, fonts)


def set_stamp(run, t, rid, size_emu, name, doc_pr_id):
    cx, cy = size_emu
    drawing = etree.fromstring(INLINE_PIC.format(
        w=W_NS, wp=WP_NS, r=R_NS, cx=cx, cy=cy, rid=rid, name=name, doc_pr_id=doc_pr_id))
    run.replace(t, drawing)


def rels_path(part):
    folder, name = part.rsplit("/", 1)
    return f"{folder}/_rels/{name}.rels"


def add_image_rel(files, part, target):
    path = rels_path(part)
    if path in files:
        root = etree.fromstring(files[path])
    else:
        root = etree.Element("{%s}Relationships" % PKG_REL_NS, nsmap={None: PKG_REL_NS})
    existing = {r.get("Id") for r in root}
    for r in root:
        if r.get("Type") == IMAGE_REL and r.get("Target") == target:
            return r.get("Id")
    rid = next(f"rIdUsps{i}" for i in range(1, 100) if f"rIdUsps{i}" not in existing)
    rel = etree.SubElement(root, "{%s}Relationship" % PKG_REL_NS)
    rel.set("Id", rid)
    rel.set("Type", IMAGE_REL)
    rel.set("Target", target)
    files[path] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    return rid


def fix_content_types(files, image_ext):
    root = etree.fromstring(files["[Content_Types].xml"])
    for override in root.iter("{%s}Override" % CT_NS):
        if override.get("PartName") == "/word/document.xml":
            override.set("ContentType", override.get("ContentType").replace(".template.main+xml", ".document.main+xml"))
    if image_ext:
        exts = {d.get("Extension").lower() for d in root.iter("{%s}Default" % CT_NS)}
        if image_ext not in exts:
            default = etree.Element("{%s}Default" % CT_NS)
            default.set("Extension", image_ext)
            default.set("ContentType", "image/png" if image_ext == "png" else "image/jpeg")
            root.insert(0, default)
    files["[Content_Types].xml"] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def fill(template, out, spec):
    with zipfile.ZipFile(template) as z:
        order = z.namelist()
        files = {n: z.read(n) for n in order}

    text = spec.get("text", {})
    stamp = spec.get("stamp")
    image_ext = os.path.splitext(stamp)[1].lstrip(".").lower() if stamp else None
    if stamp:
        with Image.open(stamp) as im:
            px_w, px_h = im.size
        stamp_size = (STAMP_WIDTH_EMU, round(STAMP_WIDTH_EMU * px_h / px_w))
        media = f"{STAMP_MEDIA}.{image_ext}"
        with open(stamp, "rb") as f:
            files["word/" + media] = f.read()
        order.append("word/" + media)

    parts = [n for n in order if re.fullmatch(r"word/(document|header\d+|footer\d+)\.xml", n)]
    font_sizes = spec.get("font_size", {})
    doc_pr_id = 9000
    filled, missing = set(), set()
    for part in parts:
        root = etree.fromstring(files[part])
        if part == "word/document.xml":
            unwrap_content_controls(root)
        changed = False
        for name, run, t in placeholder_runs(root):
            changed = True
            if name == "BARCODE" and "barcode" in spec:
                set_barcode(run, t, spec["barcode"])
            elif name == "STAMP" and stamp:
                rid = add_image_rel(files, part, media)
                if rels_path(part) not in order:
                    order.append(rels_path(part))
                doc_pr_id += 1
                set_stamp(run, t, rid, stamp_size, os.path.basename(stamp), doc_pr_id)
            elif name in text:
                lines = text[name] if isinstance(text[name], list) else [text[name]]
                if name in font_sizes:
                    set_font_size(run, font_sizes[name])
                fill_lines(run, lines)
            else:
                missing.add(name)
                continue
            filled.add(name)
        if changed or part == "word/document.xml":
            files[part] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)

    if missing:
        raise SystemExit(f"unfilled placeholders: {sorted(missing)} (filled: {sorted(filled)})")
    unused = (set(text) | ({"BARCODE"} if "barcode" in spec else set()) | ({"STAMP"} if stamp else set())) - filled
    if unused:
        raise SystemExit(
            f"spec has values for placeholders not found in the template: {sorted(unused)}. "
            "Look at the template: they may be renamed or split across runs.")

    fix_content_types(files, image_ext)
    fd, tmp = tempfile.mkstemp(suffix=".docx", dir=os.path.dirname(os.path.abspath(out)))
    os.close(fd)
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for name in order:
            z.writestr(name, files[name])
    shutil.move(tmp, out)
    return sorted(filled)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    with open(sys.argv[3], encoding="utf-8") as f:
        spec = json.load(f)
    print("filled:", ", ".join(fill(sys.argv[1], sys.argv[2], spec)))
