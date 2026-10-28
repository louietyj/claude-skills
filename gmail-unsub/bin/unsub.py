#!/usr/bin/env python3
"""Weekly Gmail unsubscribe sweep: find promotional senders, act on Louie's
decisions with RFC 8058 one-click POSTs, and keep a per-run state snapshot.

Subcommands, in run order:
  state pull   newest snapshot from the store -> work/prev/
  sweep        Promotions + Social threads since a date -> work/sweep.json
  plan         decisions + prev state + sweep -> work/jobs.json, work/judge.json
  act          run work/jobs.json -> work/results.jsonl
  finalize     everything above + work/tiers.json -> work/new/ + work/triage.html
  state push   work/new/ -> the store, as a new dated snapshot

Gmail is read through bash-mcp's mcp_call.py, so message bodies never reach
the model's context. Nothing here sends mail: mailto unsubscribes are never used.
"""

import argparse
import base64
import concurrent.futures
import datetime as dt
import email
import email.header
import email.utils
import glob
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = "/drafts/gmail-unsub"
CATEGORIES = ("promotions", "social")
ACTIONABLE = ("one-click", "https-link")
GRACE_DAYS = 14
STATE_FILES = ("meta.json", "registry.json", "status.json", "decisions.json", "sweep.json",
               "log.jsonl", "page-data.json")


# ---------- pure helpers ----------

def doc_key(sender: str) -> str:
    """The artifact-db document id for a sender address."""
    return re.sub(r"[^A-Za-z0-9_\-.~:@+]", "_", sender.lower())[:180]


def ts_from_id(gmail_id: str) -> dt.datetime:
    """Gmail message and thread ids are hex with the ms timestamp in the high bits."""
    return dt.datetime.fromtimestamp((int(gmail_id, 16) >> 20) / 1000, dt.timezone.utc)


def parse_list_unsubscribe(value: str, post: str) -> dict:
    """RFC 2369 <URI> list + RFC 8058 one-click, which requires HTTPS. URIs can be
    folded across header lines, so whitespace inside one is dropped."""
    uris = [re.sub(r"\s+", "", u) for u in re.findall(r"<([^>]*)>", value or "")]
    https = [u for u in uris if u.lower().startswith("https://")]
    if https and (post or "").strip().lower() == "list-unsubscribe=one-click":
        return {"method": "one-click", "url": https[0]}
    if https:
        return {"method": "https-link", "url": https[0]}
    mailto = any(u.lower().startswith("mailto:") for u in uris)
    return {"method": "mailto" if mailto else "none", "url": None}


def alias_display(addr: str) -> tuple[str, bool]:
    """SimpleLogin reverse-alias addresses -> the real sender, flagged."""
    m = re.match(r"(.+?)_at_(.+?)_[a-z0-9]+@simplelogin\.co$", addr)
    if m:
        return f"{m.group(1)}@{m.group(2).replace('_', '.')}", True
    return addr, False


def day(iso: str) -> dt.date:
    return dt.date.fromisoformat(iso[:10])


def plan(decisions: dict, registry: dict, status: dict, sweep: list) -> tuple[list, list]:
    """Unsubscribes due now, and new senders needing a tier. A sender is retried only
    on a new URL: after a failure, or once if still mailing past the grace period."""
    seen = {s["k"]: s for s in sweep}
    jobs = []
    for k, d in sorted(decisions.items()):
        if d != "kill":
            continue
        rec = dict(registry.get(k, {}))
        if k in seen:
            s = seen[k]
            rec.update(addr=s["addr"], method=s["m"], url=s["url"] or rec.get("url"))
        if rec.get("method") not in ACTIONABLE or not rec.get("url"):
            continue
        st = status.get(k)
        retry = False
        if st:
            if st["status"] == "skipped":
                continue
            if st["status"] == "failed" and rec["url"] == st.get("url"):
                continue
            if st["status"] == "unsubscribed":
                if not ignoring(st, seen.get(k)) or st.get("retries", 0) >= 1 or rec["url"] == st.get("url"):
                    continue
                retry = True
        jobs.append({"k": k, "addr": rec.get("addr", k), "m": rec["method"], "url": rec["url"], "retry": retry})
    judge = [s for s in sweep if s["m"] in ACTIONABLE and not registry.get(s["k"], {}).get("tier")]
    return jobs, judge


def ignoring(st: dict, swept: dict | None) -> bool:
    """Mail dated after the grace period means the unsubscribe didn't take."""
    return bool(swept) and day(swept["last"]) > day(st["actedAt"]) + dt.timedelta(days=GRACE_DAYS)


def finalize(registry: dict, status: dict, decisions: dict, sweep: list, tiers: dict,
             results: list) -> tuple[dict, dict, list]:
    reg = {k: dict(v) for k, v in registry.items()}
    for s in sweep:
        r = reg.setdefault(s["k"], {"first_seen": s["first"]})
        r.update(addr=s["addr"], name=s["name"], cat=s["cat"], method=s["m"],
                 alias=s["alias"], subj=s["subj"])
        if s.get("url"):
            r["url"] = s["url"]
        r["first_seen"] = min(r.get("first_seen", s["first"]), s["first"])
        r["last_seen"] = max(r.get("last_seen", s["last"]), s["last"])
    for k, t in tiers.items():
        reg.setdefault(k, {}).update(tier=t["tier"], why=t.get("why", ""))

    st = {k: dict(v) for k, v in status.items()}
    for r in results:
        prev = st.get(r["k"], {})
        st[r["k"]] = {
            "status": {"accepted": "unsubscribed"}.get(r["outcome"], r["outcome"]),
            "actedAt": r["ts"], "url": r["url"],
            "retries": prev.get("retries", 0) + (1 if r.get("retry") else 0),
            **({"reason": r["reason"]} if r.get("reason") else {}),
        }

    page = []
    for s in sweep:
        rec = reg.get(s["k"], {})
        if s["m"] not in ACTIONABLE or rec.get("tier") == "exclude":
            continue
        cur = st.get(s["k"])
        if cur and cur["status"] == "unsubscribed":
            if not ignoring(cur, s):
                continue
            group = "ignoring"
        elif decisions.get(s["k"]) == "keep":
            group = "prevkept"
        else:
            group = rec.get("tier") or "maybe"
        page.append({**{k: s[k] for k in ("k", "name", "addr", "cat", "n", "first", "last", "subj", "m", "alias")},
                     "sug": group, "why": rec.get("why", ""), "st": cur})
    return reg, st, page


# ---------- I/O ----------

def mcp_call_path() -> str:
    for pat in ("/mnt/skills/*/bash-mcp/mcp_call.py",
                os.path.expanduser("~/.claude/skills/synced/*/bash-mcp/mcp_call.py"),
                os.path.expanduser("~/.claude/skills/bash-mcp/mcp_call.py")):
        hits = sorted(glob.glob(pat), key=os.path.getmtime, reverse=True)
        if hits:
            return hits[0]
    sys.exit("bash-mcp's mcp_call.py not found: install the bash-mcp skill.")


def gmail(tool: str, args: dict, retries: int = 1) -> dict:
    for attempt in range(retries + 1):
        p = subprocess.run([sys.executable, "-I", mcp_call_path(), "Gmail", tool, json.dumps(args)],
                           capture_output=True, text=True, timeout=180)
        if p.returncode == 0 and p.stdout.strip():
            return json.loads(p.stdout)
        if p.returncode == 3:
            sys.exit(f"Gmail {tool} needs approval in this session; it must be always-allow.")
    raise RuntimeError(f"Gmail {tool} failed: {p.stderr.strip()[:300]}")


def search_all(query_base: str, after: dt.date) -> list:
    """Every thread matching the query since `after`. The connector has returned
    no nextPageToken, so this pages by moving `before:` back over the dates seen."""
    threads, before = {}, None
    for _ in range(200):
        q = f"{query_base} after:{after:%Y/%m/%d}" + (f" before:{before:%Y/%m/%d}" if before else "")
        res = gmail("search_threads", {"query": q, "pageSize": 50})
        batch = res.get("threads", [])
        for t in batch:
            threads[t["id"]] = t
        if len(batch) < 50:
            break
        oldest = min(ts_from_id(m["id"]) for t in batch for m in t["messages"]).date()
        nxt = oldest + dt.timedelta(days=1)
        before = nxt if before is None or nxt < before else before - dt.timedelta(days=1)
        if before <= after:
            break
    return list(threads.values())


def fetch_headers(msg_id: str) -> dict:
    raw = gmail("get_message", {"messageId": msg_id, "messageFormat": "RAW"})["raw"]
    m = email.message_from_bytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))

    def h(name):
        v = m.get(name)
        if v is None:
            return ""
        try:
            return str(email.header.make_header(email.header.decode_header(v)))
        except Exception:
            return str(v)
    return {"from": h("From"), "lu": h("List-Unsubscribe"), "lup": h("List-Unsubscribe-Post")}


def cmd_sweep(a):
    after = dt.date.fromisoformat(a.after)
    rows = []
    for cat in CATEGORIES:
        for t in search_all(f"category:{cat}", after):
            msgs = t.get("messages") or []
            if not msgs:
                continue
            newest = max(msgs, key=lambda m: int(m["id"], 16))
            rows.append({"cat": cat, "sender": msgs[0]["sender"].lower(), "id": newest["id"],
                         "subject": newest.get("subject") or msgs[0].get("subject", "")})
    by = {}
    for r in rows:
        by.setdefault(r["sender"], []).append(r)
    os.makedirs(a.work, exist_ok=True)
    groups = []
    for sender, rs in by.items():
        rs.sort(key=lambda r: int(r["id"], 16), reverse=True)
        groups.append((sender, rs))

    def one(item):
        sender, rs = item
        try:
            hd = fetch_headers(rs[0]["id"])
        except Exception as e:
            hd = {"from": sender, "lu": "", "lup": "", "error": str(e)[:200]}
        name, addr = email.utils.parseaddr(hd["from"].replace("\n", " "))
        shown, alias = alias_display((addr or sender).lower())
        lu = parse_list_unsubscribe(hd["lu"], hd["lup"])
        return {"k": doc_key(sender), "sender": sender, "addr": shown, "alias": alias, "name": name or shown,
                "cat": rs[0]["cat"], "n": len(rs),
                "last": ts_from_id(rs[0]["id"]).date().isoformat(),
                "first": ts_from_id(rs[-1]["id"]).date().isoformat(),
                "newest_id": rs[0]["id"], "subj": [r["subject"][:90] for r in rs[:3]],
                "m": lu["method"], "url": lu["url"], **({"error": hd["error"]} if "error" in hd else {})}

    with concurrent.futures.ThreadPoolExecutor(4) as ex:
        out = sorted(ex.map(one, groups), key=lambda s: -s["n"])
    json.dump({"after": a.after, "until": dt.date.today().isoformat(), "threads": len(rows), "senders": out},
              open(os.path.join(a.work, "sweep.json"), "w"), indent=1)
    methods = {}
    for s in out:
        methods[s["m"]] = methods.get(s["m"], 0) + 1
    errs = sum(1 for s in out if "error" in s)
    print(f"sweep: {len(rows)} threads, {len(out)} senders since {a.after}; methods {methods}"
          + (f"; {errs} header fetches FAILED (method shows none)" if errs else ""))


def load(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def load_decisions(d):
    """ArtifactData list with out_dir writes <dir>/decisions/<doc_id>.json."""
    out = {}
    for f in glob.glob(os.path.join(d, "decisions", "*.json")):
        v = json.load(open(f))
        v = v.get("data", v)
        if v.get("d") in ("kill", "keep"):
            out[os.path.basename(f)[:-5]] = v["d"]
    return out


def cmd_plan(a):
    prev = os.path.join(a.work, "prev")
    decisions = load_decisions(os.path.join(a.work, "dec"))
    registry, status = load(f"{prev}/registry.json", {}), load(f"{prev}/status.json", {})
    sweep = load(os.path.join(a.work, "sweep.json"), {"senders": []})["senders"]
    jobs, judge = plan(decisions, registry, status, sweep)
    json.dump(jobs, open(os.path.join(a.work, "jobs.json"), "w"), indent=1)
    json.dump([s["k"] for s in judge], open(os.path.join(a.work, "judge.json"), "w"))
    kills = sum(1 for d in decisions.values() if d == "kill")
    print(f"plan: {len(decisions)} decisions ({kills} unsubscribe); {len(jobs)} jobs "
          f"({sum(j['retry'] for j in jobs)} retries); {len(judge)} new senders to judge")
    for s in judge:
        print(f"  {s['k']}\t{s['n']}\t{s['addr']}{' (alias)' if s['alias'] else ''}\t| {' / '.join(x[:60] for x in s['subj'])}")


def unsubscribe(job: dict) -> dict:
    host = urllib.parse.urlparse(job["url"]).hostname
    r = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "k": job["k"], "addr": job["addr"],
         "method": job["m"], "host": host, "url": job["url"], "retry": job.get("retry", False)}
    ua = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) list-unsubscribe"}
    try:
        if job["m"] == "one-click":
            req = urllib.request.Request(job["url"], data=b"List-Unsubscribe=One-Click", method="POST",
                                         headers={**ua, "Content-Type": "application/x-www-form-urlencoded"})
        else:
            req = urllib.request.Request(job["url"], headers=ua)
        with urllib.request.urlopen(req, timeout=30) as resp:
            r["status"] = resp.status
            body = resp.read(200000).decode("utf-8", "replace")
        if job["m"] == "one-click":
            r["outcome"] = "accepted" if 200 <= r["status"] < 300 else "failed"
        else:
            r.update(outcome="review", page=page_text(body))
    except urllib.error.HTTPError as e:
        r.update(status=e.code, outcome="failed", reason=f"HTTP {e.code}")
    except Exception as e:
        r.update(outcome="failed", reason=f"{type(e).__name__}: {str(e)[:120]}")
    return r


def page_text(html: str) -> str:
    """Visible text of a web-link page, for Claude to judge whether it confirmed."""
    html = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", html)
    controls = len(re.findall(r"(?i)<form\b|<button\b|type=[\"']?submit", html))
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()
    return (f"[{controls} form/button elements] " if controls else "") + (text[:1500] or "[no visible text]")


def cmd_act(a):
    jobs = load(os.path.join(a.work, "jobs.json"), [])
    with concurrent.futures.ThreadPoolExecutor(4) as ex:
        results = list(ex.map(unsubscribe, jobs))
    with open(os.path.join(a.work, "results.jsonl"), "w") as fh:
        for r in results:
            fh.write(json.dumps(r) + "\n")
    tally = {}
    for r in results:
        tally[r["outcome"]] = tally.get(r["outcome"], 0) + 1
    print(f"act: {len(results)} attempted; {tally}")
    for r in results:
        if r["outcome"] in ("failed", "skipped"):
            print(f"  {r['outcome']}\t{r['addr']}\t{r.get('reason', '')}")
    for r in results:
        if r["outcome"] == "review":
            print(f"  REVIEW\t{r['k']}\t{r['addr']}\n    page: {r['page']}")


def cmd_resolve(a):
    path = os.path.join(a.work, "results.jsonl")
    rows = [json.loads(l) for l in open(path)]
    hit = [r for r in rows if r["k"] == a.key and r["outcome"] == "review"]
    if not hit:
        sys.exit(f"no result awaiting review for {a.key}")
    for r in hit:
        r["outcome"] = a.outcome
        if a.reason:
            r["reason"] = a.reason
    with open(path, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    print(f"resolve: {a.key} -> {a.outcome}")


def cmd_finalize(a):
    w, prev = a.work, os.path.join(a.work, "prev")
    registry, status = load(f"{prev}/registry.json", {}), load(f"{prev}/status.json", {})
    decisions = load_decisions(os.path.join(w, "dec"))
    sweep_doc = load(os.path.join(w, "sweep.json"), {"senders": [], "threads": 0})
    tiers = load(os.path.join(w, "tiers.json"), {})
    missing = [k for k in load(os.path.join(w, "judge.json"), []) if k not in tiers]
    if missing:
        sys.exit(f"tiers.json is missing {len(missing)} judged senders, e.g. {missing[:3]}")
    results = [json.loads(l) for l in open(os.path.join(w, "results.jsonl"))] if os.path.exists(
        os.path.join(w, "results.jsonl")) else []
    pending = [r["k"] for r in results if r["outcome"] == "review"]
    if pending:
        sys.exit(f"{len(pending)} web-link pages still need `unsub resolve`: {pending}")
    reg, st, page = finalize(registry, status, decisions, sweep_doc["senders"], tiers, results)

    new = os.path.join(w, "new")
    os.makedirs(new, exist_ok=True)
    meta = {"run": a.run, "after": sweep_doc.get("after"), "until": sweep_doc.get("until"),
            "threads": sweep_doc.get("threads", 0), "artifact": a.artifact}
    files = {"meta.json": meta, "registry.json": reg, "status.json": st, "decisions.json": decisions,
             "sweep.json": sweep_doc, "page-data.json": page}
    for name, obj in files.items():
        json.dump(obj, open(os.path.join(new, name), "w"), indent=1)
    with open(os.path.join(new, "log.jsonl"), "w") as fh:
        for r in results:
            fh.write(json.dumps(r) + "\n")

    data = {"id": a.run, "range": f"{sweep_doc.get('after')} – {sweep_doc.get('until')}",
            "threads": sweep_doc.get("threads", 0), "senders": page}
    tpl = open(os.path.join(HERE, "..", "page", "template.html")).read()
    blob = json.dumps(data, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    open(os.path.join(w, "triage.html"), "w").write(tpl.replace("__DATA__", blob))
    groups = {}
    for p in page:
        groups[p["sug"]] = groups.get(p["sug"], 0) + 1
    print(f"finalize: page lists {len(page)} senders {groups}; state in {new}; page at {w}/triage.html")


def cfs(*args, stdin=None) -> str:
    p = subprocess.run(["cfs", *args], capture_output=True, text=True, input=stdin)
    if p.returncode != 0:
        raise SystemExit(f"cfs {' '.join(args)} failed: {p.stderr.strip() or p.stdout.strip()}")
    return p.stdout


def cmd_state(a):
    if a.op == "pull":
        listing = cfs("list", f"{STORE}/state", "--depth", "1")
        runs = sorted(re.findall(r"/state/(\d{4}-\d{2}-\d{2})/", listing))
        if not runs:
            sys.exit("no state snapshots found")
        dest = os.path.join(a.work, "prev")
        os.makedirs(dest, exist_ok=True)
        for name in STATE_FILES:
            cfs("download", f"{STORE}/state/{runs[-1]}/{name}", "--to", os.path.join(dest, name))
        meta = load(os.path.join(dest, "meta.json"), {})
        print(f"state pull: {runs[-1]} -> {dest}; last run {meta.get('run')}, artifact {meta.get('artifact')}")
    else:
        src = os.path.join(a.work, "new")
        run = load(os.path.join(src, "meta.json"), {})["run"]
        for name in STATE_FILES:
            cfs("upload", f"{STORE}/state/{run}/{name}", "--from", os.path.join(src, name), "--new")
        print(f"state push: {STORE}/state/{run}/ ({len(STATE_FILES)} files)")


def main():
    ap = argparse.ArgumentParser(prog="unsub", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work", default=os.path.abspath("unsub-work"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("state")
    s.add_argument("op", choices=["pull", "push"])
    s = sub.add_parser("sweep")
    s.add_argument("--after", required=True, help="YYYY-MM-DD, inclusive")
    sub.add_parser("plan")
    sub.add_parser("act")
    s = sub.add_parser("resolve")
    s.add_argument("key")
    s.add_argument("outcome", choices=["accepted", "skipped"])
    s.add_argument("reason", nargs="?", default="")
    s = sub.add_parser("finalize")
    s.add_argument("--run", default=dt.date.today().isoformat())
    s.add_argument("--artifact", required=True)
    a = ap.parse_args()
    {"state": cmd_state, "sweep": cmd_sweep, "plan": cmd_plan, "act": cmd_act, "resolve": cmd_resolve,
     "finalize": cmd_finalize}[a.cmd](a)


if __name__ == "__main__":
    main()
