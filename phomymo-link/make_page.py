#!/usr/bin/env python3
"""Usage: make_page.py [--title TITLE] --label NAME LINK.txt [PREVIEW.png] [--label ...] > labels.html

Builds the page that hands Louie his labels: per label, its print preview (if given) and an "Open in
Phomymo" button carrying its link. Publish it with the Artifact tool. Links go from file to file, so they
never have to be re-emitted.
"""
import argparse
import base64
import html
import textwrap

PAGE = textwrap.dedent(
    """\
    <!doctype html>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>__TITLE__</title>
    <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Montserrat:wght@700&family=Inter:wght@400;600&display=swap">
    <style>
    :root{--bg:#eef0ee;--paper:#fff;--ink:#121412;--muted:#5d625d;--btn:#121412;--btnfg:#fff;
    --display:"Montserrat",system-ui,sans-serif;--body:"Inter",system-ui,sans-serif}
    @media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#161816;--paper:#f6f6f2;--ink:#ecefeb;--muted:#a0a59f;--btn:#f6f6f2;--btnfg:#121412;color-scheme:dark}}
    :root[data-theme="dark"]{--bg:#161816;--paper:#f6f6f2;--ink:#ecefeb;--muted:#a0a59f;--btn:#f6f6f2;--btnfg:#121412;color-scheme:dark}
    body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--body)}
    main{max-width:460px;margin:0 auto;padding:28px 16px 40px;display:grid;gap:36px}
    section{display:grid;gap:14px}
    h1,h2{font-family:var(--display);letter-spacing:.04em;text-transform:uppercase;margin:0;text-wrap:balance}
    h1{font-size:1.15rem}
    h2{font-size:.95rem}
    .label{background:var(--paper);border-radius:6px;padding:14px;box-shadow:0 1px 3px rgba(0,0,0,.18)}
    .label img{display:block;width:100%;height:auto;image-rendering:pixelated}
    a.print{display:block;text-align:center;background:var(--btn);color:var(--btnfg);font-family:var(--display);font-weight:700;font-size:1.05rem;padding:16px;border-radius:999px;text-decoration:none}
    a.print:focus-visible,button:focus-visible{outline:3px solid var(--muted);outline-offset:3px}
    button{justify-self:center;background:none;border:0;color:var(--muted);font:600 .85rem var(--body);text-decoration:underline;cursor:pointer}
    </style>
    <main>
    <h1>__TITLE__</h1>
    __SECTIONS__
    </main>
    <script>
    document.querySelectorAll("button.copy").forEach((b) => b.addEventListener("click", () => {
      navigator.clipboard.writeText(b.previousElementSibling.href).then(
        () => { b.textContent = "Copied"; },
        () => { b.textContent = "Copy failed: long-press the button above"; });
    }));
    </script>
    """
)

parser = argparse.ArgumentParser()
parser.add_argument("--title", help="page title; defaults to the label's name when there is one label")
parser.add_argument("--label", nargs="+", action="append", required=True, metavar="NAME LINK [PREVIEW]")
args = parser.parse_args()
if any(len(label) not in (2, 3) for label in args.label):
    parser.error("each --label takes NAME LINK.txt [PREVIEW.png]")
if args.title is None and len(args.label) > 1:
    parser.error("--title is required with more than one --label")

sections = []
for name, link_path, *preview in args.label:
    link = open(link_path, encoding="utf-8").read().strip()
    if not link.startswith("https://phomymo.louietyj.me/#design="):
        raise SystemExit(f"error: {link_path} doesn't hold a Phomymo design link")
    parts = ["<section>"]
    if len(args.label) > 1:
        parts.append(f"<h2>{html.escape(name)}</h2>")
    if preview:
        img = base64.b64encode(open(preview[0], "rb").read()).decode()
        alt = html.escape(f"Print preview of {name}", quote=True)
        parts.append(f'<div class="label"><img alt="{alt}" src="data:image/png;base64,{img}"></div>')
    parts.append(f'<a class="print" href="{html.escape(link, quote=True)}" target="_blank" rel="noopener">Open in Phomymo</a>')
    parts.append('<button class="copy" type="button">Copy link</button>')
    parts.append("</section>")
    sections.append("\n".join(parts))

title = html.escape(args.title or args.label[0][0])
print(PAGE.replace("__TITLE__", title).replace("__SECTIONS__", "\n".join(sections)), end="")
