#!/usr/bin/env python3
"""msfetch: the browser-free half of the musescore skill.

The browser half is two evals the agent runs itself (see SKILL.md): js/search.js
on a results page and js/fetch-score.js on a score page, each saving its JSON
output to a file. This turns those files into something usable.

usage:
  msfetch search-url <query> [--type piano-voice|pvg|solo|any] [--official|--community]
                             [--any-format] [--sort relevance|rating|views] [--page N]
  msfetch results <search.json>
  msfetch unpack <raw.json> <out-dir> [--pdf]
  msfetch abc <out-dir> [--voice-track N]
  msfetch render <out-dir> <page|last>... [--scale S]   # -> <out-dir>/page_N.png to look at
  msfetch js search|fetch-score          # path of a page script

Exit codes: 0 ok, 2 bad input / incomplete, 3 needs login or access,
4 MuseScore served a different score than the one asked for.
"""
import argparse
import base64
import glob
import json
import os
import re
import subprocess
import sys
from urllib.parse import urlencode

HERE = os.path.dirname(os.path.abspath(__file__))
NODE_MODULES = os.path.expanduser("~/.cache/msfetch/node_modules")  # abcjs, installed by setup.sh
INSTRUMENTATION = {"piano-voice": 139, "pvg": 140, "solo": 114}
SORT = {"rating": "rating", "views": "view_count"}


def load_eval_output(path):
    """The eval's JSON, tolerating whatever wrapping the browser tool puts around it."""
    raw = open(path, encoding="utf-8").read().strip()
    i = raw.find("{")
    if i < 0:
        raise SystemExit(f"ERROR: no JSON in {path}: {raw[:300]!r}")
    v = json.loads(raw[i:raw.rindex("}") + 1]) if raw[0] != '"' else json.loads(raw)
    while isinstance(v, str):  # a tool that JSON-quotes the returned string
        v = json.loads(v)
    return v


def cmd_search_url(o):
    q = {"text": o.query}
    if not o.any_format:
        q["score_format"] = "interactive"
    if o.type != "any":
        q["instrumentation"] = INSTRUMENTATION[o.type]
    if o.official:
        q["type"] = "official"
    if o.community:
        q["type"] = "non-official"
    if o.sort in SORT:
        q["sort"] = SORT[o.sort]
    if o.page > 1:
        q["page"] = o.page
    print("https://musescore.com/sheetmusic?" + urlencode(q))
    return 0


def cmd_results(o):
    res = load_eval_output(o.file)
    if not res.get("ok"):
        print(f"ERROR: {res.get('error')} ({res.get('url')})", file=sys.stderr)
        return 2
    print(f"# {res['url']}\n# {res['total']} results; showing {len(res['scores'])}; logged in as {res['user']}")
    for s in res["scores"]:
        flags = ",".join(f for f, v in (("official", s["official"]), ("PDF-ONLY no MIDI", s["pdf_only"]),
                                         ("purchased", s["purchased"])) if v) or "community"
        print(f"{s['id']}  {s['title']}  [{flags}]\n"
              f"    {s['instrumentation']} | parts: {s['parts']} | {s['pages']}pp {s['duration']} | "
              f"key {s['key']} {s['time'] or ''} | ★{s['rating']} ({s['votes']}) {s['views']} views | by {s['by']}\n"
              f"    {s['url']}")
    return 0


def cmd_unpack(o):
    res = load_eval_output(o.file)
    meta = res.get("meta") or {}
    if not res.get("ok"):
        print(f"ERROR: {res.get('error')}", file=sys.stderr)
        for w in res.get("wrong", []):
            print("  " + w, file=sys.stderr)
        if meta:
            print(f"  score {meta.get('id')} '{meta.get('title')}', logged in as {meta.get('user', {}).get('name')}",
                  file=sys.stderr)
        return 4 if res.get("wrong") else 3 if res.get("needs_login") else 2
    os.makedirs(os.path.join(o.out, "pages"), exist_ok=True)
    for name, b64 in res["files"].items():
        dest = os.path.join(o.out, "pages", name) if name.startswith("score_") else os.path.join(o.out, name)
        with open(dest, "wb") as fh:
            fh.write(base64.b64decode(b64))
    pages = sorted((n for n in res["files"] if n.startswith("score_")), key=lambda n: int(re.search(r"\d+", n).group()))
    meta.update(files=sorted(res["files"]), problems=res["problems"])
    json.dump(meta, open(os.path.join(o.out, "meta.json"), "w"), indent=1)

    print(json.dumps({"id": meta["id"], "title": meta["title"], "pages": f"{len(pages)}/{meta['pages']}",
                      "format": "svg" if pages and pages[0].endswith(".svg") else "png",
                      "midi": "score.mid" in res["files"], "space": "space.jsonp" in res["files"],
                      "official": meta["official"], "pdf_only": meta["pdf_only"],
                      "logged_in_as": meta["user"]["name"], "out": os.path.abspath(o.out)}))
    for p in res["problems"]:
        print("WARNING: " + p, file=sys.stderr)
    if len(pages) < meta["pages"]:
        print(f"ERROR: only {len(pages)} of {meta['pages']} pages", file=sys.stderr)
        if any("Failed to fetch" in p for p in res["problems"]):
            print("  'Failed to fetch' on every retry usually means the tab blocks images "
                  "(navigated with --block-images). Navigate again without it.", file=sys.stderr)
        return 2
    if o.pdf:
        render_pdf(o.out, pages)
    return 0


def svg_for_cairo(path):
    # MuseScore emits negative dash lengths (e.g. on hairpins), which cairo rejects outright.
    src = open(path, encoding="utf-8").read()
    return re.sub(r' stroke-dasharray="[^"]*-[^"]*"', "", src).encode()


def render_pdf(out, pages):
    import cairosvg
    import pymupdf
    doc = pymupdf.open()
    for n in pages:
        p = os.path.join(out, "pages", n)
        if n.endswith(".svg"):
            doc.insert_pdf(pymupdf.open("pdf", cairosvg.svg2pdf(bytestring=svg_for_cairo(p))))
        else:
            doc.insert_pdf(pymupdf.open("pdf", pymupdf.open(p).convert_to_pdf()))
    doc.save(os.path.join(out, "score.pdf"))


def cmd_render(o):
    import cairosvg
    files = {int(re.search(r"\d+", n).group()) + 1: n for n in os.listdir(os.path.join(o.out, "pages"))
             if n.startswith("score_")}
    for want in o.pages:
        num = max(files) if want == "last" else int(want)
        if num not in files:
            print(f"ERROR: no page {num}; have 1-{max(files)}", file=sys.stderr)
            return 2
        src, dest = os.path.join(o.out, "pages", files[num]), os.path.join(o.out, f"page_{num}.png")
        if src.endswith(".svg"):
            cairosvg.svg2png(bytestring=svg_for_cairo(src), write_to=dest, scale=o.scale, background_color="white")
        else:
            import shutil
            shutil.copy(src, dest)
        print(dest)
    return 0


# ------------------------------------------------------------------------ abc

def cmd_abc(o):
    sys.path.insert(0, HERE)
    import align
    out = o.out
    meta = json.load(open(os.path.join(out, "meta.json")))
    title = re.sub(r"\s*\((Piano|Vocal|Voice)[^)]*\)\s*$", "", meta.get("title") or "Score")
    work = os.path.join(out, "_work")
    os.makedirs(work, exist_ok=True)
    for s in glob.glob(os.path.join(out, "pages", "score_*.svg")):
        d = os.path.join(work, os.path.basename(s))
        if not os.path.exists(d):
            os.symlink(os.path.abspath(s), d)
    for name in ("score.mid", "space.jsonp"):
        d = os.path.join(work, name)
        if not os.path.exists(d):
            os.symlink(os.path.abspath(os.path.join(out, name)), d)
    r = align.run(work)
    a = align.align(r)
    ev, rep, orphan = align.lyrics_pass(r, a)
    streams = sorted(k for k in ev if k != "_hx")
    ti = o.voice_track if o.voice_track is not None else (streams[0] if streams else 0)
    abc = align.to_abc(r, a, ev, title=title, ti=ti)
    open(os.path.join(out, "out.abc"), "w").write(abc)

    rt = {}
    try:
        jr = json.loads(subprocess.run(["node", os.path.join(HERE, "abccheck.js"), os.path.join(out, "out.abc")],
                                       capture_output=True, text=True,
                                       env={**os.environ, "NODE_PATH": NODE_MODULES}).stdout)
        got = [x.rstrip("-") for x in jr["syl"] if x and x != "_"]
        E = ev.get(ti, {})
        want = [E[t][p["pass"]]["syl"]["text"] for p in r["passes"] for t in sorted(E)
                if p["t0"] <= t < p["t1"] and p["pass"] in E[t]]
        rt = {"abcjs_warnings": jr["warnings"], "lyrics_roundtrip_ok": got == want}
    except Exception as e:
        rt = {"abcjs_error": str(e)}
    tot = lambda k: int(sum(x[k] for x in rep))
    flagged = [{"measure": x["m"] + 1, "pass": x["pass"], "staff": x["staff"], "heads": x["heads"],
                "midi_notes": x["notes"], "matched": x["matched"], "syllables": x["syl"],
                "assigned": x["syl_assigned"], "far": int(x["far"]),
                "page": next(m["page"] + 1 for m in a["meas"] if m["id"] == x["m"])}
               for x in rep if x["matched"] < min(x["heads"], x["notes"]) or x["syl_assigned"] < x["syl"] or x["far"]]
    report = {
        "streams": [{"index": i, "name": t["name"].strip("\x00"), "channel": t["ch"], "notes": len(t["notes"])}
                    for i, t in enumerate(r["tracks"])],
        "abc_stream": ti, "staff_map": {str(k): v for k, v in a["staff_map"].items()}, "ocr": r["stats"],
        "vocal_measures": len(rep), "noteheads": tot("heads"), "midi_notes": tot("notes"), "matched": tot("matched"),
        "syllable_boxes": sum(len(g["sylls"]) for g in r["pg"].values()),
        "syllables_assigned": tot("syl_assigned"), "orphans": [(x["page"] + 1, x.get("text")) for x in orphan],
        "flagged_measures": flagged, **rt,
    }
    json.dump(report, open(os.path.join(out, "report.json"), "w"), indent=1, default=str)
    print(json.dumps({k: report[k] for k in ("abc_stream", "noteheads", "midi_notes", "matched",
                                             "syllable_boxes", "syllables_assigned")}
                     | {"flagged": len(flagged), "orphans": len(report["orphans"]),
                        "lyrics_roundtrip_ok": report.get("lyrics_roundtrip_ok"),
                        "streams": [f"{s['index']}:{s['name']}" for s in report["streams"]]}))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search-url")
    s.add_argument("query")
    s.add_argument("--type", choices=["piano-voice", "pvg", "solo", "any"], default="any")
    s.add_argument("--official", action="store_true")
    s.add_argument("--community", action="store_true")
    s.add_argument("--any-format", action="store_true", help="include PDF-only scores (no MIDI)")
    s.add_argument("--sort", choices=["relevance", "rating", "views"], default="relevance")
    s.add_argument("--page", type=int, default=1)
    r = sub.add_parser("results")
    r.add_argument("file")
    u = sub.add_parser("unpack")
    u.add_argument("file")
    u.add_argument("out")
    u.add_argument("--pdf", action="store_true")
    a = sub.add_parser("abc")
    a.add_argument("out")
    a.add_argument("--voice-track", type=int)
    v = sub.add_parser("render")
    v.add_argument("out")
    v.add_argument("pages", nargs="+", help="1-based page numbers, or 'last'")
    v.add_argument("--scale", type=float, default=0.4)
    j = sub.add_parser("js")
    j.add_argument("name", choices=["search", "fetch-score"])
    o = ap.parse_args()
    if o.cmd == "js":
        print(os.path.join(os.path.dirname(HERE), "js", o.name + ".js"))
        return 0
    return {"search-url": cmd_search_url, "results": cmd_results, "unpack": cmd_unpack, "abc": cmd_abc,
            "render": cmd_render}[o.cmd](o)


if __name__ == "__main__":
    sys.exit(main())
