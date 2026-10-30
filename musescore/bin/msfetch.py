#!/usr/bin/env python3
"""msfetch: search musescore.com and download a score's page SVGs, MIDI and layout.

usage:
  msfetch search <query> [--type piano-voice|pvg|solo|any] [--official|--community]
                         [--any-format] [--sort relevance|rating|views] [--page N]
  msfetch fetch <score-url> <out-dir> [--pdf] [--abc [--voice-track N]]
  msfetch abc <out-dir> [--voice-track N]
  msfetch whoami

Every request goes through a browser already on musescore.com (`pinchtab` by
default, `--browser pinchtab-windows` for Louie's Chrome). Bytes are fetched by
the page itself and handed back base64; musescore.com refuses the sandbox's own
HTTP client.

Exit codes: 0 ok, 2 bad page / nothing usable, 3 needs login or purchase,
4 MuseScore served a different score than the one asked for.
"""
import argparse
import base64
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
NODE_MODULES = os.path.expanduser("~/.cache/msfetch/node_modules")  # abcjs, installed by setup.sh
# Fallback only: the live suffix is read from the page's own bundle each run.
KNOWN_SUFFIXES = ["111010"]
INSTRUMENTATION = {"piano-voice": 139, "pvg": 140, "solo": 114}
SORT = {"rating": "rating", "views": "view_count"}


class Browser:
    def __init__(self, cmd):
        self.cmd = cmd
        self.tmp = tempfile.mkdtemp(prefix="msfetch-")

    def batch(self, lines, timeout=180):
        r = subprocess.run([self.cmd, "execute-batch"], input="\n".join(lines) + "\n",
                           capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            raise RuntimeError(f"{self.cmd} batch failed:\n{r.stdout}\n{r.stderr}")
        return r.stdout

    def evals(self, exprs, timeout=180):
        """Run JS expressions (awaited) in one batch; return each raw result string."""
        paths, lines = [], []
        for i, e in enumerate(exprs):
            p = os.path.join(self.tmp, f"ev{i}.txt")
            if os.path.exists(p):
                os.remove(p)
            paths.append(p)
            # A batch step is one line.
            lines.append(f"eval --await-promise {shquote(' '.join(e.splitlines()))} > {p}")
        self.batch(lines, timeout)
        return [open(p).read() for p in paths]

    def eval(self, expr, timeout=180):
        return self.evals([expr], timeout)[0]

    def nav(self, url):
        # Never --block-images: it blocks the page's own fetch() of score_N.svg/png too.
        self.batch([f"nav {shquote(url)}"])


def shquote(s):
    return "'" + s.replace("'", "'\\''") + "'"


def token(score_id, kind, index, suffix):
    return hashlib.md5(f"{score_id}{kind}{index}{suffix}".encode()).hexdigest()[:4]


def scoredata_hash(url):
    m = re.search(r"scoredata/g/([0-9a-f]{40})/", url or "")
    return m.group(1) if m else None


# --------------------------------------------------------------------- search

SEARCH_JS = r"""(async()=>{
const d=window.UGAPP&&UGAPP.store.page.data;
if(!d||!d.scores) return JSON.stringify({error:'not a search page'});
const clean=t=>(t||'').replace(/\[\/?b\]/g,'');
return JSON.stringify({total:d.pagination&&d.pagination.totalCount, scores:d.scores.map(s=>({
 id:s.id, title:clean(s.title), url:s.href, official:!!s.is_official, pdf_only:!!s.is_pdf,
 by:s.user&&s.user.name, instrumentation:(s.instrumentations||[]).map(x=>x.name).join(' / '),
 parts:(s.parts_names||[]).join(' / '), pages:s.pages_count, duration:s.duration,
 key:s.key_signature, time:s.time_signature, rating:s.rating&&s.rating.rating,
 votes:s.rating&&s.rating.count, views:s.hits, purchased:!!s.is_purchased}))});
})()"""


def cmd_search(b, o):
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
    from urllib.parse import urlencode
    url = "https://musescore.com/sheetmusic?" + urlencode(q)
    b.nav(url)
    res = json.loads(b.eval(SEARCH_JS))
    if "error" in res:
        print(f"ERROR: {res['error']} ({url})", file=sys.stderr)
        return 2
    print(f"# {url}\n# {res['total']} results; showing {len(res['scores'])}")
    for s in res["scores"]:
        flags = ",".join(f for f, v in (("official", s["official"]), ("PDF-ONLY no MIDI", s["pdf_only"]),
                                         ("purchased", s["purchased"])) if v) or "community"
        print(f"{s['id']}  {s['title']}  [{flags}]\n"
              f"    {s['instrumentation']} | parts: {s['parts']} | {s['pages']}pp {s['duration']} | "
              f"key {s['key']} {s['time'] or ''} | ★{s['rating']} ({s['votes']}) {s['views']} views | by {s['by']}\n"
              f"    {s['url']}")
    return 0


# ---------------------------------------------------------------------- fetch

META_JS = r"""(async()=>{
const st=window.UGAPP&&UGAPP.store, s=st&&st.page&&st.page.data&&st.page.data.score;
if(!s) return JSON.stringify({error:'not a score page'});
const re0=/\/static\/musescore\/scoredata\/g\/[0-9a-f]{40}\/score_0\.(svg|png)/;
const cands=[...document.querySelectorAll('link[as=image]')].map(l=>l.href)
  .concat([...document.images].map(i=>i.src))
  .concat(performance.getEntriesByType('resource').map(e=>e.name));
const page0=(cands.find(u=>re0.test(u)&&/score_0\.svg/.test(u))||cands.find(u=>re0.test(u))||'').split('@')[0];
const space=performance.getEntriesByType('resource').map(e=>e.name).find(n=>/scoredata\/g\/[0-9a-f]{40}\/space\.jsonp/.test(n))||null;
const bundles=[...document.head.innerHTML.matchAll(/href=["'](https:\/\/musescore\.com\/static\/public\/build\/[^"']+\.js)["']/g)].map(m=>m[1]);
const suffixes=[];
for(const u of bundles){try{const t=await (await fetch(u)).text();
  for(const m of t.matchAll(/\(e\+t\+n\+"([^"]+)"\)\.substr\(0,4\)|"([^"]+)"\)\.substr\(0,4\)/g)){const x=m[1]||m[2]; if(!suffixes.includes(x)) suffixes.push(x);}}catch(e){}}
const u=st.user||{};
return JSON.stringify({id:s.id, title:s.title, pages:s.pages_count, pdf_only:!!s.is_pdf, official:!!s.is_official,
  purchased:!!s.is_purchased, parts:s.parts_names, instrumentation:(s.instrumentations||[]).map(x=>x.name),
  keysig:s.keysig, measures:s.measures, duration:s.duration, page_url:location.href,
  user:{id:u.id||0, name:u.username||null, pro:!!u.isPro, pro_plus:!!u.isProPlus},
  page0, space, suffixes});
})()"""

JMUSE_JS = r"""(async()=>{
const reqs=%s, out={};
for(const [k,url,auth] of reqs){for(let a=0;a<4;a++){try{const r=await fetch(url,{headers:{Authorization:auth}});
  let j=null; try{j=await r.json();}catch(e){}
  out[k]={status:r.status, url:j&&j.info&&j.info.url||null, error:j&&j.error&&j.error.message||null};
}catch(e){out[k]={status:0,error:String(e)};}
if(out[k].status&&out[k].status!==429&&out[k].status<500) break; await new Promise(r=>setTimeout(r,800*(a+1)));}}
return JSON.stringify(out);
})()"""

BYTES_JS = r"""(async()=>{
let r, err;
for(let a=0;a<4;a++){try{r=await fetch(%s,{cache:'no-store'}); if(r.ok) break; err='ERR '+r.status;}catch(e){err='ERR '+e;}
  await new Promise(z=>setTimeout(z,800*(a+1)));}
if(!r||!r.ok) return err;
const b=new Uint8Array(await r.arrayBuffer()); let s='';
for(let i=0;i<b.length;i+=8192) s+=String.fromCharCode(...b.subarray(i,i+8192));
return btoa(s);
})()"""


def jmuse(b, score_id, wanted, suffixes):
    """wanted: list of (key, type, index). Tries each auth suffix until one is accepted."""
    last = None
    for suf in suffixes:
        reqs = [[k, f"/api/jmuse?id={score_id}&type={t}&index={i}", token(score_id, t, i, suf)]
                for k, t, i in wanted]
        res = json.loads(b.eval(JMUSE_JS % json.dumps(reqs), timeout=240))
        last = res
        if any(v["status"] == 200 for v in res.values()):
            return suf, res
        if any(v["status"] not in (401, 403) for v in res.values()):
            break  # a 404 means "no such file / no access", not a bad token
    return None, last


def cmd_fetch(b, o):
    if not o.skip_nav:
        b.nav(o.url)
    meta = json.loads(b.eval(META_JS))
    if "error" in meta:
        print(f"ERROR: {meta['error']} -- is {o.url} a musescore.com score page?", file=sys.stderr)
        return 2
    sid, npages = meta["id"], meta["pages"]
    want_id = re.search(r"/scores/(\d+)", o.url)
    if want_id and int(want_id.group(1)) != sid:
        print(f"ERROR: page reports score {sid}, URL asked for {want_id.group(1)}", file=sys.stderr)
        return 4
    h0 = scoredata_hash(meta["page0"])
    if not h0:
        print("ERROR: no score_0 image found on the page (old PNG-only upload, or the viewer did not load)",
              file=sys.stderr)
        return 2
    suffixes = meta["suffixes"] + [s for s in KNOWN_SUFFIXES if s not in meta["suffixes"]]
    wanted = [(f"img{i}", "img", i) for i in range(1, npages)]
    if not meta["pdf_only"]:
        wanted.append(("midi", "midi", 0))
    suf, res = jmuse(b, sid, wanted, suffixes) if wanted else ("", {})
    if wanted and suf is None:
        statuses = sorted({v["status"] for v in res.values()})
        print(f"ERROR: MuseScore refused every file (HTTP {statuses}). "
              + ("Official score: log in with an account that has access."
                 if meta["official"] else "Try logging in."), file=sys.stderr)
        meta["jmuse"] = res
        json.dump(meta, open(os.path.join(o.out, "meta.json"), "w"), indent=1)
        return 3

    urls = {"score_0" + os.path.splitext(meta["page0"].split("?")[0])[1]: meta["page0"]}
    problems = []
    for k, t, i in wanted:
        v = res[k]
        if v["status"] != 200 or not v["url"]:
            problems.append(f"{k}: HTTP {v['status']} {v['error'] or ''}".strip())
            continue
        h = scoredata_hash(v["url"])
        if h != h0:
            problems.append(f"{k}: served from {h}, page 1 is {h0}")
            continue
        ext = os.path.splitext(v["url"].split("?")[0])[1]
        urls[("score.mid" if t == "midi" else f"score_{i}{ext}")] = v["url"]
    if meta["space"] and scoredata_hash(meta["space"]) == h0:
        urls["space.jsonp"] = meta["space"]
    elif not meta["pdf_only"]:
        urls["space.jsonp"] = f"https://musescore.com/static/musescore/scoredata/g/{h0}/space.jsonp"

    wrong = [p for p in problems if "served from" in p]
    if wrong:
        print("ERROR: MuseScore served files from a different score than page 1 -- a decoy or the wrong "
              "score. Nothing was saved.\n  " + "\n  ".join(wrong), file=sys.stderr)
        return 4

    os.makedirs(os.path.join(o.out, "pages"), exist_ok=True)
    names = list(urls)
    blobs = b.evals([BYTES_JS % json.dumps(urls[n]) for n in names], timeout=600)
    saved = []
    for n, blob in zip(names, blobs):
        if blob.startswith("ERR"):
            problems.append(f"{n}: {blob}")
            continue
        dest = os.path.join(o.out, "pages", n) if n.startswith("score_") else os.path.join(o.out, n)
        with open(dest, "wb") as fh:
            fh.write(base64.b64decode(blob))
        saved.append(n)

    pages = sorted((n for n in saved if n.startswith("score_")), key=lambda n: int(re.search(r"\d+", n).group()))
    meta.update(hash=h0, files=saved, problems=problems, auth_suffix=suf)
    meta.pop("suffixes", None)
    json.dump(meta, open(os.path.join(o.out, "meta.json"), "w"), indent=1)

    fmt = "svg" if pages and pages[0].endswith(".svg") else "png"
    summary = {"id": sid, "title": meta["title"], "pages": f"{len(pages)}/{npages}", "format": fmt,
               "midi": "score.mid" in saved, "space": "space.jsonp" in saved,
               "official": meta["official"], "pdf_only": meta["pdf_only"],
               "logged_in_as": meta["user"]["name"], "out": os.path.abspath(o.out)}
    print(json.dumps(summary))
    for p in problems:
        print("WARNING: " + p, file=sys.stderr)
    if len(pages) < npages:
        print(f"ERROR: only {len(pages)} of {npages} pages saved", file=sys.stderr)
        if any("Failed to fetch" in p for p in problems):
            print("  'Failed to fetch' on every try usually means the tab blocks images (a `nav --block-images` "
                  "earlier in it). msfetch navigates without it; rerun.", file=sys.stderr)
        return 3 if any("HTTP 40" in p for p in problems) else 2

    if o.pdf:
        render_pdf(o.out, pages)
    if o.abc:
        if "score.mid" not in saved or fmt != "svg":
            print("ERROR: --abc needs SVG pages and score.mid", file=sys.stderr)
            return 2
        return cmd_abc(None, o)
    return 0


def render_pdf(out, pages):
    import cairosvg
    import pymupdf
    doc = pymupdf.open()
    for n in pages:
        p = os.path.join(out, "pages", n)
        if n.endswith(".svg"):
            doc.insert_pdf(pymupdf.open("pdf", cairosvg.svg2pdf(url=p)))
        else:
            doc.insert_pdf(pymupdf.open("pdf", pymupdf.open(p).convert_to_pdf()))
    doc.save(os.path.join(out, "score.pdf"))


# ------------------------------------------------------------------------ abc

def cmd_abc(_b, o):
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


def cmd_whoami(b, _o):
    b.nav("https://musescore.com/")
    print(b.eval("(async()=>{const u=(window.UGAPP&&UGAPP.store.user)||{};"
                 "return JSON.stringify({id:u.id||0,name:u.username||null,pro:!!u.isPro,pro_plus:!!u.isProPlus})})()"))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--browser", default="pinchtab")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--type", choices=["piano-voice", "pvg", "solo", "any"], default="any")
    s.add_argument("--official", action="store_true")
    s.add_argument("--community", action="store_true")
    s.add_argument("--any-format", action="store_true", help="include PDF-only scores (no MIDI)")
    s.add_argument("--sort", choices=["relevance", "rating", "views"], default="relevance")
    s.add_argument("--page", type=int, default=1)
    f = sub.add_parser("fetch")
    f.add_argument("url")
    f.add_argument("out")
    f.add_argument("--pdf", action="store_true")
    f.add_argument("--abc", action="store_true")
    f.add_argument("--voice-track", type=int)
    f.add_argument("--skip-nav", action="store_true", help="use the page the tab is already on")
    a = sub.add_parser("abc")
    a.add_argument("out")
    a.add_argument("--voice-track", type=int)
    sub.add_parser("whoami")
    o = ap.parse_args()
    if getattr(o, "out", None):
        os.makedirs(o.out, exist_ok=True)
    b = Browser(o.browser)
    return {"search": cmd_search, "fetch": cmd_fetch, "abc": cmd_abc, "whoami": cmd_whoami}[o.cmd](b, o)


if __name__ == "__main__":
    sys.exit(main())
