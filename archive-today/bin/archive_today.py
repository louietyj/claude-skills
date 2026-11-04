#!/usr/bin/env python3
"""archive.today without a browser: newest snapshot, submit, wait, read as text.

The captcha in front of archive.today is solved over plain HTTP: a solver service
returns a reCAPTCHA token, which goes to the page's own /cdn-cgi/l/chk_captcha
endpoint with the fingerprint the page itself sends when its fingerprint script
fails. The server answers with a cf_clearance cookie.
"""

import argparse
import http.client
import http.cookiejar
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HOST = "https://archive.ph"
PUBLIC_HOST = HOST
ONION = "http://archiveiya74codqgiixo33q62qlrqtkgmcitqx5u2oeqnmn5bpcbiyd.onion"
TOR_PROXY = "socks5h://127.0.0.1:9050"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.expanduser("~/.cache/archive-today")
TROUBLE = os.path.join(SKILL_DIR, "TROUBLESHOOTING.md")
# The sandbox's bash calls die at 300 s; leave room to print.
DEFAULT_BUDGET = 240

TRACKING = {
    "fbclid", "gclid", "gclsrc", "dclid", "gbraid", "wbraid", "msclkid", "yclid", "twclid",
    "ttclid", "igshid", "li_fat_id", "mc_cid", "mc_eid", "mkt_tok", "_ga", "_gl", "ref",
    "ref_src", "ref_url", "smid", "smtyp", "cmpid", "mod", "share", "s_cid", "cmp", "ncid",
    "sr_share", "at_medium", "at_campaign", "guccounter", "guce_referrer", "guce_referrer_sig",
    "taid", "ito", "sref", "itm_source", "itm_medium", "itm_campaign", "ocid", "partner",
    # Gift-link tokens: private, and they make the URL miss its snapshots.
    "unlocked_article_code", "giftcopy", "gift",
}
STUB_MARKERS = [
    "subscribe to continue", "subscribe to read", "to continue reading", "already a subscriber",
    "security verification", "just a moment", "access denied", "access blocked",
    "err_proxy", "verify you are human", "unusual activity",
]

EXIT_FAIL, EXIT_NONE, EXIT_PENDING, EXIT_REFUSED, EXIT_BLOCKED = 1, 3, 4, 5, 6


class Broken(Exception):
    """Something archive.today or a solver did that the script doesn't expect."""

    def __init__(self, section, detail):
        super().__init__(detail)
        self.section = section


class Refused(Exception):
    """archive.today declined to capture the URL. A fact about the site, not a bug."""


class Blocked(Exception):
    """This environment's egress proxy refuses archive.today."""


def log(msg):
    print(msg, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- HTTP

os.makedirs(CACHE, exist_ok=True)
_jar = http.cookiejar.MozillaCookieJar(os.path.join(CACHE, "cookies.txt"))
try:
    _jar.load(ignore_discard=True, ignore_expires=True)
except (OSError, http.cookiejar.LoadError):
    pass
_cafile = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
_ctx = ssl.create_default_context(cafile=_cafile) if _cafile and os.path.exists(_cafile) else ssl.create_default_context()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(_jar), urllib.request.HTTPSHandler(context=_ctx), _NoRedirect)
_proxied = False
_timeout = 60


def use_proxy(proxy, onion=False):
    """Send archive.today traffic through proxy (socks5://, socks5h:// or http://),
    and to its onion address if onion. The solver APIs keep the direct route."""
    global _opener, _proxied, _timeout, HOST
    scheme = urllib.parse.urlsplit(proxy).scheme
    if scheme in ("socks5", "socks5h"):
        try:
            import socks
            from sockshandler import SocksiPyHandler
        except ImportError:
            raise SystemExit("--proxy socks5h://... needs PySocks: pip install PySocks (setup.sh installs it)")
        p = urllib.parse.urlsplit(proxy)
        # It handles https itself, with the system CAs: TLS through it ends at the real
        # server, not at the egress proxy that _ctx's bundle exists for.
        routes = [SocksiPyHandler(socks.SOCKS5, p.hostname, p.port or 1080, rdns=scheme == "socks5h")]
    elif scheme == "http":
        if onion:
            raise SystemExit("the onion address needs a SOCKS proxy (socks5h://...); "
                             "an HTTP CONNECT proxy can't carry its plain-http requests")
        routes = [urllib.request.ProxyHandler({"http": proxy, "https": proxy}), urllib.request.HTTPSHandler(context=_ctx)]
    else:
        raise SystemExit(f"unsupported --proxy scheme {scheme!r}: use socks5h://, socks5:// or http://")
    _opener = urllib.request.build_opener(*routes, urllib.request.HTTPCookieProcessor(_jar), _NoRedirect)
    _proxied, _timeout = True, 180  # Tor runs at 30-90 KB/s; a big snapshot takes a minute or two.
    if onion:
        HOST = ONION


def public(url):
    """A snapshot URL as people share it: the clearnet host, not the onion one."""
    return url.replace(ONION, PUBLIC_HOST) if url else url


def blocked_here():
    """True if the egress proxy refuses archive.today: over plain HTTP it says so in a
    header, where over HTTPS it just drops the connection."""
    try:
        _opener.open(urllib.request.Request("http://archive.ph/", headers={"User-Agent": UA}), timeout=20)
    except urllib.error.HTTPError as e:
        return e.code == 403 and e.headers.get("x-block-reason") == "hostname_blocked"
    except Exception:  # noqa: BLE001 -- an unanswered probe proves nothing either way
        return False
    return False


def request(url, data=None, headers=None, tries=8):
    """One request, no redirects followed. archive.today resets some TLS handshakes
    from the sandbox's egress, so connection errors are retried."""
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})})
    for attempt in range(tries):
        try:
            with _opener.open(req, timeout=_timeout) as resp:
                out = resp.status, resp.headers, resp.read()
            break
        except urllib.error.HTTPError as e:
            out = e.code, e.headers, e.read()
            break
        except (urllib.error.URLError, OSError, http.client.HTTPException) as e:  # incl. a body cut short
            last = e
            if attempt == 0 and not _proxied and blocked_here():
                raise Blocked()
            time.sleep(min(2 + 2 * attempt, 10))
    else:
        raise Broken("connection", f"{url}: {tries} connection attempts failed, last: {last}")
    _jar.save(ignore_discard=True, ignore_expires=True)
    return out


def fetch(url, headers=None):
    """GET through the captcha: solve it when it shows up, then retry once."""
    status, hdrs, body = request(url, headers=headers)
    if status == 429 and b"recaptcha" in body:
        clear_captcha(url, body)
        status, hdrs, body = request(url, headers=headers)
        if status == 429 and b"recaptcha" in body:
            raise Broken("captcha-rejected", "solved the captcha, but archive.today still serves it")
    return status, hdrs, body


# ---------------------------------------------------------------- captcha

def clear_captcha(page_url, page):
    text = page.decode("utf-8", "replace")
    sitekey = re.search(r"""['"]sitekey['"]\s*:\s*['"]([\w-]{30,})['"]""", text)
    xden = re.search(r"xden=(\w+)", text)
    missing = [n for n, ok in [
        ("sitekey", sitekey), ("xden", xden), ("/cdn-cgi/l/chk_captcha", "/cdn-cgi/l/chk_captcha" in text),
        ("response= field", "'response='" in text), ("&location= field", "'&location='" in text),
        ("&fp= field", "'&fp='" in text)] if not ok]
    if missing:
        dump = os.path.join(CACHE, "captcha-page.html")
        with open(dump, "wb") as fh:
            fh.write(page)
        raise Broken("captcha-page-changed", f"captcha page lacks {', '.join(missing)}; saved it to {dump}")

    t0 = time.time()
    token = solve_recaptcha(page_url, sitekey.group(1))
    # What the page sends when its ThumbmarkJS fingerprint script fails to load.
    fp = {"error": True, "stage": "resource", "message": "script load failed", "online": True, "timing": None}
    data = urllib.parse.urlencode({"response": token, "location": page_url, "xden": xden.group(1),
                                   "fp": json.dumps(fp)}).encode()
    status, _, body = request(f"{HOST}/cdn-cgi/l/chk_captcha", data, {
        "Content-Type": "application/x-www-form-urlencoded", "Referer": page_url, "Origin": HOST})
    if status != 200:
        raise Broken("captcha-rejected", f"chk_captcha answered {status}: {body[:200]!r}")
    log(f"[captcha cleared in {time.time() - t0:.0f}s]")


def _read_key(name, env):
    if os.environ.get(env):
        return os.environ[env].strip()
    path = os.path.join(SKILL_DIR, name)
    return open(path).read().strip() if os.path.exists(path) else ""


def _task_api(base, key, task, timeout=180):
    def call(ep, payload):
        req = urllib.request.Request(f"{base}/{ep}", json.dumps(payload).encode(), {"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=40, context=_ctx).read())

    created = call("createTask", {"clientKey": key, "task": task})
    if created.get("errorId"):
        raise RuntimeError(f"{created.get('errorCode')}: {created.get('errorDescription')}")
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(3)
        r = call("getTaskResult", {"clientKey": key, "taskId": created["taskId"]})
        if r.get("errorId"):
            raise RuntimeError(f"{r.get('errorCode')}: {r.get('errorDescription')}")
        if r.get("status") == "ready":
            return r["solution"]["gRecaptchaResponse"]
    raise RuntimeError(f"no token after {timeout}s")


def solve_recaptcha(page_url, sitekey):
    solvers = [
        ("capsolver", "https://api.capsolver.com", _read_key("capsolver.key", "CAPSOLVER_API_KEY"),
         "ReCaptchaV2TaskProxyLess"),
        ("2captcha", "https://api.2captcha.com", _read_key("2captcha.key", "TWOCAPTCHA_API_KEY"),
         "RecaptchaV2TaskProxyless"),
    ]
    errors = []
    for name, base, key, task_type in solvers:
        if not key:
            errors.append(f"{name}: no key")
            continue
        try:
            return _task_api(base, key, {"type": task_type, "websiteURL": page_url, "websiteKey": sitekey})
        except Exception as e:  # noqa: BLE001 -- any solver failure moves on to the next one
            errors.append(f"{name}: {e}")
            log(f"[{name} failed: {e}]")
    raise Broken("solver", "; ".join(errors))


# ---------------------------------------------------------------- archive.today

def normalize(url, keep_query=False):
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme not in ("http", "https"):
        raise SystemExit(f"not an http(s) URL: {url}")
    query = parts.query
    if not keep_query:
        pairs = urllib.parse.parse_qsl(query, keep_blank_values=True)
        query = urllib.parse.urlencode(
            [(k, v) for k, v in pairs if not (k.lower() in TRACKING or k.lower().startswith("utm_"))])
    clean = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))
    if clean != url.strip():
        log(f"[normalized to {clean}]")
    return clean


def is_snapshot(url):
    return bool(re.match(r"https?://(archive\.(ph|today|is|li|vn|md|fo)|[a-z2-7]{56}\.onion)/"
                         r"(\d{14}/|(?!newest$|oldest$|submit$|search$)[A-Za-z0-9]{4,6}$)", url))


def via_host(snapshot):
    """A snapshot URL rewritten onto the host requests go to (the onion one under --tor)."""
    return re.sub(r"^https?://[^/]+", HOST, snapshot)


def newest(url):
    """The newest snapshot's URL, or None."""
    status, hdrs, body = fetch(f"{HOST}/newest/{url}")
    if status in (301, 302) and hdrs.get("Location"):
        return hdrs["Location"]
    if status == 404:
        return None
    raise Broken("unexpected-response", f"/newest/ answered {status}: {body[:200]!r}")


def submit(url, fresh=False):
    """Returns ("done", snapshot) or ("wip", id); raises Refused.

    Without fresh, a URL archived recently comes back as its existing snapshot;
    archive.today matches it after normalizing the URL itself, so a cache-busting
    parameter doesn't get round that. fresh is the "save again" button's anyway=1.
    """
    if HOST == ONION:
        # Same captcha as the clearnet, but its clearance never takes over the onion
        # (2026-11-04: cf_clearance set, next submit challenged again, both solvers).
        raise Refused(f"can't submit {url} over Tor: archive.today's onion address gates submissions "
                      f"behind a captcha this script can't clear, so only existing snapshots are readable")
    status, hdrs, body = fetch(f"{HOST}/submit/?{'anyway=1&' if fresh else ''}url={urllib.parse.quote(url, safe='')}",
                               headers={"Referer": HOST + "/"})
    target = hdrs.get("Location") or ""
    refresh = re.search(r"url=(\S+)", hdrs.get("Refresh") or "")
    if refresh:
        target = refresh.group(1)
    m = re.search(r"/wip/(\w+)", target)
    if m:
        return "wip", m.group(1)
    # Already archived: the "save it again?" prompt, under the existing snapshot's id.
    m = re.match(r"https?://archive\.\w+/(\w+)/again\b", target)
    if m:
        return "done", f"{HOST}/{m.group(1)}"
    if target and is_snapshot(target):
        return "done", target
    if status == 200 and b"Loading" in body:
        # A static page with no wip id, no refresh and no script. Every wsj.com URL got
        # it on 2026-11-02, with or without anyway=1, from this script and from real
        # browsers on datacenter and residential IPs; no capture ever appeared.
        raise Refused(f"archive.today accepted no capture of {url} (its id-less 'Loading' page)")
    raise Broken("unexpected-response", f"/submit/ answered {status}, target {target!r}: {body[:200]!r}")


def wait(wip_id, deadline):
    """Poll /wip/<id> until the capture lands. Returns its snapshot URL, or None at the deadline."""
    while time.time() < deadline:
        status, hdrs, _ = fetch(f"{HOST}/wip/{wip_id}")
        loc = hdrs.get("Location") or ""
        if status in (301, 302) and "/wip/" not in loc:
            return loc
        time.sleep(min(10, max(0, deadline - time.time())))
    return None


def read_snapshot(snapshot, html_out=None):
    import trafilatura

    status, _, body = fetch(snapshot)
    if status != 200:
        raise Broken("unexpected-response", f"snapshot {snapshot} answered {status}")
    html = body.decode("utf-8", "replace")
    if html_out:
        with open(html_out, "w", encoding="utf-8") as fh:
            fh.write(html)
    text = trafilatura.extract(html, favor_recall=True, include_comments=False) or ""
    meta = trafilatura.extract_metadata(html)
    when = re.search(r'itemprop="pubdate" datetime="([^"]+)"', html)
    print(f"# {meta.title if meta and meta.title else '(untitled)'}")
    print(f"snapshot: {public(snapshot)}" + (f"  (archived {when.group(1)})" if when else ""))
    print()
    print(text)
    words = len(text.split())
    low = text.lower()
    stub = [m for m in STUB_MARKERS if m in low]
    if words < 300 or (stub and words < 1500):
        log(f"[warning: {words} words{', mentions ' + ', '.join(repr(s) for s in stub) if stub else ''}: "
            f"this snapshot may have captured a paywall or error page. See {TROUBLE}, 'Bad captures'.]")
    else:
        log(f"[{words} words]")


# ---------------------------------------------------------------- CLI

def cmd_newest(a):
    snap = newest(normalize(a.url, a.keep_query))
    if not snap:
        log("no snapshot")
        return EXIT_NONE
    print(public(snap))
    return 0


def cmd_submit(a):
    kind, val = submit(normalize(a.url, a.keep_query), a.fresh)
    print(public(val) if kind == "done" else f"wip {val}")
    return 0


def _finish(snap, wip_id, a):
    if snap:
        if a.url_only:
            print(public(snap))
        else:
            read_snapshot(via_host(snap), a.html)
        return 0
    log(f"still capturing after {a.budget}s. Resume with: archive-today wait {wip_id}")
    return EXIT_PENDING


def cmd_read(a):
    deadline = time.time() + a.budget
    if is_snapshot(a.url):
        return _finish(a.url, None, a)
    url = normalize(a.url, a.keep_query)
    snap = None if a.fresh else newest(url)
    wip_id = None
    if not snap:
        log("[submitting a fresh capture]" if a.fresh else "[no snapshot yet: submitting]")
        try:
            kind, val = submit(url, a.fresh)
        except Refused:
            snap = newest(url) if a.fresh else None
            if not snap:
                raise
            log("[archive.today refused a fresh capture; reading the newest snapshot instead]")
            return _finish(snap, None, a)
        if kind == "done":
            snap = val
        else:
            wip_id = val
            log(f"[capturing as wip {val}; captures usually take 1-5 min]")
            snap = wait(wip_id, deadline)
    return _finish(snap, wip_id, a)


def cmd_wait(a):
    return _finish(wait(a.id, time.time() + a.budget), a.id, a)


def main():
    p = argparse.ArgumentParser(prog="archive-today", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    def route(sp):
        sp.add_argument("--tor", action="store_true",
                        help=f"read over archive.today's onion address through the tor-proxy skill ({TOR_PROXY})")
        sp.add_argument("--proxy", help="send archive.today traffic through this proxy: socks5h://, socks5:// or http://")

    def common(sp, fresh=False):
        sp.add_argument("url")
        sp.add_argument("--keep-query", action="store_true", help="don't strip tracking parameters")
        if fresh:
            sp.add_argument("--fresh", action="store_true", help="capture again even if a snapshot exists")
        route(sp)

    def reading(sp):
        sp.add_argument("--budget", type=int, default=DEFAULT_BUDGET, help="seconds to wait for a capture")
        sp.add_argument("--html", help="also save the snapshot's HTML here")
        sp.add_argument("--url-only", action="store_true", help="print the snapshot URL, not its text")

    sp = sub.add_parser("read", help="article text: newest snapshot, else submit and wait")
    common(sp, fresh=True)
    reading(sp)
    sp.set_defaults(fn=cmd_read)
    sp = sub.add_parser("newest", help="newest snapshot URL; exit 3 if none")
    common(sp)
    sp.set_defaults(fn=cmd_newest)
    sp = sub.add_parser("submit", help="start a capture; prints the snapshot URL or a wip id")
    common(sp, fresh=True)
    sp.set_defaults(fn=cmd_submit)
    sp = sub.add_parser("wait", help="resume waiting on a capture, then read it")
    sp.add_argument("id", help="wip id from submit/read")
    reading(sp)
    route(sp)
    sp.set_defaults(fn=cmd_wait)

    a = p.parse_args()
    if a.tor or a.proxy:
        use_proxy(a.proxy or TOR_PROXY, onion=a.tor)
    try:
        return a.fn(a)
    except Refused as e:
        if HOST == ONION:
            log(f"archive-today: {e}.")
        else:
            log(f"archive-today: {e}. It refuses submissions for some sites (wsj.com as of 2026-11-02), "
                f"from anyone; retrying won't help. Move on to a wire-syndicated copy or ghostarchive.")
        return EXIT_REFUSED
    except Blocked:
        log("archive-today: this environment's egress proxy blocks archive.today (x-block-reason: "
            "hostname_blocked). Start the tor-proxy skill, then rerun with --tor: that reads existing "
            "snapshots over archive.today's onion address, though it can't make new ones.")
        return EXIT_BLOCKED
    except Broken as e:
        log(f"archive-today FAILED ({e.section}): {e}")
        log(f"Read the '{e.section}' section of {TROUBLE} before retrying or working around it.")
        return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
