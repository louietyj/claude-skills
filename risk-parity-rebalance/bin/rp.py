#!/usr/bin/env python3
"""rp -- the arithmetic behind risk-parity-rebalance.

Each subcommand does a piece of work that has gone wrong when done by hand in
context:

  closeouts   when each futures leg drops out of the portfolio, from IBKR's
              published close-out table (never derived from FND rules)
  vols        60 complete months of adjusted closes via `lmcps`, straight into
              the calculation -- no numbers re-typed
  plan        current vs target pillars, and the effect of a proposed set of
              target quantities
  hours       the liquid window per market, in PT

stdlib only: the claude.ai sandbox cannot pip install.
"""

import argparse
import csv
import datetime as dt
import io
import json
import math
import os
import shlex
import subprocess
import sys
import time
import urllib.request

RP_HOME = os.environ.get("RP_HOME", "/tmp/rp")
ACCOUNT = os.path.join(RP_HOME, "account.json")
VOLS = os.path.join(RP_HOME, "vols.json")

CLOSEOUT_URL = "https://www.interactivebrokers.com/webrest/futures/physicaldelivery"

PILLARS = {
    "EQ": ("Equities", ("ES", "MES", "VT")),
    "BN": ("Bonds", ("ZN", "ZB", "TLT", "IEF")),
    "GLD": ("Gold", ("GC", "MGC", "GLD")),
    "CM": ("Commodities", ("CL", "MCL", "PDBC")),
}
PILLAR_OF = {sym: key for key, (_, syms) in PILLARS.items() for sym in syms}
ETFS = {"VT", "TLT", "IEF", "GLD", "PDBC"}

# Vol proxy per pillar. USO tracks front-month WTI, the same underlying as CL.
PROXIES = {"EQ": "VT", "BN": "TLT", "GLD": "GLD", "CM": "USO"}

# Typical historical ranges. Deliberately tight: a breach is information.
GUARDRAILS = {"EQ": (10, 22), "BN": (8, 18), "GLD": (10, 22), "CM": (20, 40)}

LOOKBACK_MONTHS = 60

# Products known to be financially settled. A futures leg missing from the
# close-out table is only treated as cash-settled if its root is listed here;
# anything else missing is a lookup failure, not evidence of cash settlement.
EQUITY_INDEX = {"ES", "MES", "NQ", "MNQ", "RTY", "M2K", "YM", "MYM"}  # stop 09:30 ET on LTD
CASH_SETTLED = EQUITY_INDEX | {"MCL"}

# Hours behind the exchange's local time for US Pacific. US zones share DST
# transitions, so a fixed offset is exact.
PT_OFFSET = {"CBOT": 2, "CME": 2, "NYMEX": 3, "COMEX": 3}
TZ_LABEL = {"CBOT": "CT", "CME": "CT", "NYMEX": "ET", "COMEX": "ET"}

MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()

ALL_MONTHS = frozenset(range(1, 13))

# Which delivery months actually carry the open interest. A product lists months
# outside its cycle ("serial" months) that almost nobody trades: GC Nov'26 had
# 4.3K open interest against Dec'26's 325K, and Feb'27 -- further out -- had 41K.
# Liquidity follows the cycle, not the distance. A root that is absent here has
# no claim recorded, which `closeouts` reports rather than guessing.
LIQUID_MONTHS = {
    "GC": frozenset({2, 4, 6, 8, 10, 12}),
    "MGC": frozenset({2, 4, 6, 8, 10, 12}),
    "ZN": frozenset({3, 6, 9, 12}),
    "ZB": frozenset({3, 6, 9, 12}),
    "ZF": frozenset({3, 6, 9, 12}),
    "ZT": frozenset({3, 6, 9, 12}),
    "UB": frozenset({3, 6, 9, 12}),
    "ES": frozenset({3, 6, 9, 12}),
    "MES": frozenset({3, 6, 9, 12}),
    "CL": ALL_MONTHS,   # WTI lists and trades every month
    "MCL": ALL_MONTHS,
}

# Market, liquid window in ET, whether it trades on Globex outside that window,
# and what the measurement showed. The windows come from 30-minute volume bars
# on Fri 2026-10-02 rather than from a spec page.
SESSIONS = (
    ("Equity index — ES, MES", (9, 30), (16, 0), True,
     "RTH 85K–192K per 30min, overnight 2K–7K"),
    ("Treasuries — ZN, ZB", (8, 20), (15, 0), True,
     "volume peaks 08:30 ET, with a close spike ~15:00 ET"),
    ("Gold — GC, MGC", (8, 20), (13, 30), True,
     "settles 13:30 ET; 8K–11K per 30min inside, ~1–2K after"),
    ("Crude — CL, MCL", (9, 0), (14, 30), True,
     "settles 14:30 ET, with a volume spike into it"),
    ("US ETFs — VT, GLD, TLT, PDBC", (9, 30), (16, 0), False,
     "NYSE/Nasdaq regular session; no extended hours"),
)


def die(msg: str, code: int = 1):
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def today_arg(value):
    return dt.date.fromisoformat(value) if value else dt.date.today()


# --- calendar ----------------------------------------------------------------

def _nth_weekday(year, month, weekday, n):
    d = dt.date(year, month, 1)
    d += dt.timedelta(days=(weekday - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def _last_weekday(year, month, weekday):
    d = dt.date(year, month + 1, 1) - dt.timedelta(days=1) if month < 12 else dt.date(year, 12, 31)
    return d - dt.timedelta(days=(d.weekday() - weekday) % 7)


def _easter(year):
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return dt.date(year, month, day + 1)


def _observed(d):
    if d.weekday() == 5:
        return d - dt.timedelta(days=1)
    if d.weekday() == 6:
        return d + dt.timedelta(days=1)
    return d


def us_holidays(year):
    """US exchange holidays. CME trades an abbreviated session on some of these
    rather than closing, so treating them as closed only ever moves a deadline
    earlier -- the safe direction."""
    return {
        _observed(dt.date(year, 1, 1)),
        _nth_weekday(year, 1, 0, 3),
        _nth_weekday(year, 2, 0, 3),
        _easter(year) - dt.timedelta(days=2),
        _last_weekday(year, 5, 0),
        _observed(dt.date(year, 6, 19)),
        _observed(dt.date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),
        _nth_weekday(year, 11, 3, 4),
        _observed(dt.date(year, 12, 25)),
    }


def is_business_day(d):
    return d.weekday() < 5 and d not in us_holidays(d.year)


def prev_business_day(d):
    d -= dt.timedelta(days=1)
    while not is_business_day(d):
        d -= dt.timedelta(days=1)
    return d


def business_days_between(start, end):
    """Business days in (start, end]; negative when end is before start."""
    if end < start:
        return -business_days_between(end, start)
    n, d = 0, start
    while d < end:
        d += dt.timedelta(days=1)
        n += is_business_day(d)
    return n


def fmt_day(d, today=None):
    """'Fri 10/16', with /26 appended when the year is not the current one."""
    out = f"{d:%a} {d.month}/{d.day}"
    return out if today and d.year == today.year else out + f"/{d:%y}"


def is_dst(d):
    """US DST: second Sunday in March to first Sunday in November. ET and PT
    switch together, so their 3-hour gap is constant and needs no tz database --
    which the sandbox may not carry."""
    start = _nth_weekday(d.year, 3, 6, 2)
    end = _nth_weekday(d.year, 11, 6, 1)
    return start <= d < end


def now_et(at=None):
    """Current exchange time (ET). `at` is a naive ISO datetime read as PT,
    since that is the clock Louie reads when placing an order."""
    if at:
        pt = dt.datetime.fromisoformat(at)
        return pt + dt.timedelta(hours=3)
    utc = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    return utc - dt.timedelta(hours=4 if is_dst(utc.date()) else 5)


def et_to_pt(when):
    return when - dt.timedelta(hours=3)


def hm(t):
    return f"{t[0]:02d}:{t[1]:02d}"


def hm_pt(t):
    return f"{(t[0] - 3) % 24:02d}:{t[1]:02d}"


# --- hours -------------------------------------------------------------------

def liquid_window(day, opens, closes):
    return (dt.datetime.combine(day, dt.time(*opens)),
            dt.datetime.combine(day, dt.time(*closes)))


def next_liquid_open(when, opens, closes):
    """The next moment the liquid window is open, at or after `when` (ET)."""
    day = when.date()
    for _ in range(10):
        if is_business_day(day):
            start, end = liquid_window(day, opens, closes)
            if when < end:
                return max(when, start)
        day += dt.timedelta(days=1)
        when = dt.datetime.combine(day, dt.time(0, 0))
    return None


def globex_open(when):
    """CME electronic session: Sun 18:00 ET through Fri 17:00 ET, with a daily
    17:00-18:00 ET break. Holidays are not modelled here, so a holiday reads as
    'thin' rather than closed -- it is only ever used to discourage trading."""
    wd, t = when.weekday(), when.time()
    if wd == 5:                                   # Saturday
        return False
    if wd == 6:                                   # Sunday: opens 18:00
        return t >= dt.time(18, 0)
    if wd == 4 and t >= dt.time(17, 0):           # Friday: shut at 17:00
        return False
    return not (dt.time(17, 0) <= t < dt.time(18, 0))


def cmd_hours(args):
    et = now_et(args.at)
    print(f"Now: {et_to_pt(et):%a %Y-%m-%d %H:%M} PT ({et:%H:%M} ET)"
          f"{'' if is_business_day(et.date()) else '  -- exchange holiday or weekend'}\n")
    print("| Market | Liquid window (PT) | Liquid window (ET) | Right now | Measured |")
    print("|---|---|---|---|---|")
    for label, opens, closes, globex, note in SESSIONS:
        start, end = liquid_window(et.date(), opens, closes)
        if is_business_day(et.date()) and start <= et < end:
            state = f"**liquid**, closes {et_to_pt(end):%H:%M} PT"
        else:
            nxt = next_liquid_open(et, opens, closes)
            opens_at = f"opens {et_to_pt(nxt):%a %H:%M} PT" if nxt else "unknown"
            state = f"thin (Globex) — {opens_at}" if globex and globex_open(et) \
                else f"closed — {opens_at}"
        print(f"| {label} | {hm_pt(opens)}–{hm_pt(closes)} | {hm(opens)}–{hm(closes)} | "
              f"{state} | {note} |")
    print("\nPlace rolls and large orders inside the liquid window. Outside it the "
          "book thins out,\nand a roll pays the wider spread twice, once per leg. "
          "Half-day sessions (the day after\nThanksgiving, Christmas Eve) close "
          "early and are not modelled here.")
    return 0


# --- account -----------------------------------------------------------------

def _unwrap(value, key):
    return value[key] if isinstance(value, dict) and key in value else value


def load_account():
    """account.json holds the IBKR connector's results pasted verbatim:
    {"positions": <get_account_positions>, "balances": <get_account_balances>}"""
    path = ACCOUNT
    if not os.path.exists(path):
        die(f"{path} not found. Write the verbatim get_account_positions and "
            f"get_account_balances results there first (see SKILL.md).")
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    positions = _unwrap(raw.get("positions"), "positions") or []
    balances = _unwrap(raw.get("balances"), "balances") or []
    base = next((b for b in balances if b.get("currency") == "BASE"), None) or \
        next((b for b in balances if b.get("currency") == "USD"), None)
    return positions, base


def root_of(position):
    return position["contract_description"].split()[0]


# --- closeouts ---------------------------------------------------------------

def load_table(path):
    if path:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    req = urllib.request.Request(CLOSEOUT_URL, headers={"User-Agent": "curl/8.5.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def parse_stamp(s):
    """'20261127 1315' -> datetime, exchange-local."""
    day, hhmm = s.split()
    return dt.datetime.strptime(day + hhmm.zfill(4), "%Y%m%d%H%M")


def month_label(yyyymm):
    return f"{MONTHS[int(yyyymm[4:]) - 1]}'{yyyymm[2:4]}"


def ladder_for(table, row, today):
    """Later contracts in the same product: each with its flat-by date, the
    business days to it, and whether its month is on the liquid cycle."""
    key = (row["exchange"], row["tradingClass"])
    cycle = LIQUID_MONTHS.get(row["tradingClass"])
    out = []
    for r in sorted((r for r in table if (r["exchange"], r["tradingClass"]) == key
                     and r["contractMonth"] > row["contractMonth"]),
                    key=lambda r: r["contractMonth"]):
        fb = prev_business_day(parse_stamp(r["longFutCallsShortPutsLiq"]).date())
        liquid = None if cycle is None else int(r["contractMonth"][4:]) in cycle
        out.append((r, fb, business_days_between(today, fb), liquid))
    return out


def suggest_roll(ladder, horizon):
    """The nearest later month that is both liquid and outside the horizon."""
    return next(((r, fb, left) for r, fb, left, liquid in ladder
                 if liquid is not False and left > horizon), None)


def cmd_closeouts(args):
    today = today_arg(args.today)
    table = load_table(args.table)
    by_conid = {str(r["conid"]): r for r in table}

    snap = max(r["date"][:10] for r in table)
    age = (today - dt.date.fromisoformat(snap)).days
    print(f"Close-out table snapshot {snap} ({len(table)} contracts).")
    if age > args.max_age:
        print(f"WARNING: snapshot is {age} days old -- the table may not have "
              f"been refreshed. Treat its dates as unverified.")

    cash = {}
    for spec in args.cash:
        try:
            conid, rest = spec.split("=")
            root, ltd = rest.split(":")
            cash[conid] = (root.upper(), dt.datetime.strptime(ltd, "%Y%m%d").date())
        except ValueError:
            die(f"--cash wants CONID=ROOT:YYYYMMDD, got {spec!r}")

    legs = []
    if not args.no_account:
        positions, _ = load_account()
        legs += [(str(p["contract_id"]), p["contract_description"], p["position"], False)
                 for p in positions if p.get("asset_class") == "FUT"]
    legs += [(str(c), None, 1, True) for c in args.candidate]
    if not legs:
        print("No futures positions and no candidates.")
        return 0

    rows, ladders, blocked, notes = [], {}, [], []
    for conid, desc, qty, candidate in legs:
        row = by_conid.get(conid)
        roll_into = "—"
        if row:
            label = desc or f"{row['tradingClass']} {month_label(row['contractMonth'])} @{row['exchange']}"
            liq = parse_stamp(row["longFutCallsShortPutsLiq" if qty > 0 else "shortFutCallsLongPutsLiq"])
            # Everything is shown in Louie's own clock. An exchange whose offset
            # is not known keeps its local time, labelled, rather than being
            # silently presented as PT.
            if row["exchange"] in PT_OFFSET:
                pt = liq - dt.timedelta(hours=PT_OFFSET[row["exchange"]])
                drop = f"{fmt_day(pt.date(), today)} {pt:%H:%M}"
            else:
                drop = (f"{fmt_day(liq.date(), today)} {liq:%H:%M} "
                        f"{TZ_LABEL.get(row['exchange'], 'exch-local')}")
            why = "IBKR may liquidate"
            flat_by = prev_business_day(liq.date())
            if not candidate:
                ladder = ladders.setdefault((row["exchange"], row["tradingClass"]),
                                            (row, ladder_for(table, row, today)))[1]
                pick = suggest_roll(ladder, args.horizon)
                roll_into = month_label(pick[0]["contractMonth"]) if pick \
                    else "none liquid and far enough out"
        elif conid in cash:
            root, ltd = cash[conid]
            label = desc or f"{root} (conid {conid})"
            if root not in CASH_SETTLED:
                blocked.append(f"{label}: {root} is not a known cash-settled product, "
                               f"yet it is absent from the close-out table. Do not guess.")
                continue
            # Equity-index futures stop trading at 09:30 ET on the LTD, which is
            # 06:30 PT. Other cash-settled products get the date only.
            drop = fmt_day(ltd, today) + (" 06:30" if root in EQUITY_INDEX else "")
            why = "cash-settles"
            flat_by = prev_business_day(ltd)
            # Cash-settled products are absent from the table, so their ladder
            # is not here either: it comes from search_futures.
            roll_into = "next quarterly" if root in EQUITY_INDEX else "see search_futures"
        else:
            label = desc or f"conid {conid}"
            root = desc.split()[0] if desc else None
            if root in CASH_SETTLED:
                blocked.append(f"{label}: cash-settled, so not in the close-out table. "
                               f"Re-run with --cash {conid}={root}:<last_trading_date "
                               f"from search_futures>.")
            else:
                blocked.append(f"{label}: not in the close-out table and not a known "
                               f"cash-settled product. Do not guess its deadline.")
            continue

        # The urgency rides on the leg as a marker rather than in its own
        # column: there are only four states and three of them need no words.
        left = business_days_between(today, flat_by)
        if candidate:
            serial = row is not None and (cyc := LIQUID_MONTHS.get(row["tradingClass"])) \
                is not None and int(row["contractMonth"][4:]) not in cyc
            if left <= args.horizon:
                mark, note = "⛔", "too close to open"
            elif serial:
                mark, note = "⛔", "serial month -- thin"
            else:
                mark, note = "", "ok to open"
            label += f" [candidate: {note}]"
        elif left < 0:
            mark = "🚨"
            notes.append(f"{label}: PAST its deadline -- may already be liquidated or settled.")
        elif left == 0:
            mark = "🚨"
            notes.append(f"{label}: TODAY is the last session to be flat.")
        elif left <= args.horizon:
            mark = "⚠️"
        else:
            mark = ""
        rows.append((f"{mark} {label}".strip(), qty, why, drop,
                     fmt_day(flat_by, today), roll_into))

    if rows:
        print()
        print("| Leg | Qty | Drops out because | Drop-out moment (PT) | Flat by | Roll into |")
        print("|---|---|---|---|---|---|")
        for r in rows:
            print("| " + " | ".join(str(c) for c in r) + " |")
        if any(r[0].startswith(("⚠️", "🚨")) for r in rows):
            print(f"\n⚠️ = roll within {args.horizon} business days · 🚨 = act now")
        for n in notes:
            print(f"  {n}")

    # The ladder behind each suggestion, so the pick can be overridden with the
    # alternatives and their own deadlines in view.
    for row, later in ladders.values():
        if not later:
            continue
        print(f"\n{row['tradingClass']} @{row['exchange']} -- later contracts:")
        for r, fb, left, liquid in later[:args.ladder]:
            tag = "cycle unrecorded" if liquid is None else \
                ("liquid cycle" if liquid else "SERIAL month -- thin, avoid")
            mark = ", inside horizon" if left <= args.horizon else ""
            print(f"  {month_label(r['contractMonth'])}  conid {r['conid']}  "
                  f"flat by {fmt_day(fb, today)}  ({left} biz days{mark})  {tag}")

        pick = suggest_roll(later, args.horizon)
        if pick:
            r, fb, left = pick
            print(f"  → suggested roll: {month_label(r['contractMonth'])} "
                  f"(conid {r['conid']}), flat by {fmt_day(fb, today)}")
            if LIQUID_MONTHS.get(row["tradingClass"]) is None:
                print(f"     {row['tradingClass']}'s liquid cycle is unrecorded -- confirm "
                      f"with future_open_interest before using this.")
        else:
            print("  → no later contract is both liquid and outside the horizon. "
                  "Work it out by hand.")

    if blocked:
        print("\nBLOCKED -- resolve before proposing anything:")
        for b in blocked:
            print(f"  - {b}")
        return 2
    return 0


# --- vols --------------------------------------------------------------------

def fetch_csv(symbol, from_dir):
    if from_dir:
        with open(os.path.join(from_dir, f"{symbol}.csv"), encoding="utf-8") as fh:
            return fh.read()
    cmd = shlex.split(os.environ.get("RP_LMCPS", "lmcps")) + [
        "call", "mcp-alphavantage", "TIME_SERIES_MONTHLY_ADJUSTED",
        json.dumps({"symbol": symbol})]
    # The free tier also allows only one request per second, and says so as a
    # successful result. That limit clears by waiting; the daily one does not.
    for attempt in range(3):
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=180)
        except FileNotFoundError:
            die("`lmcps` is not on PATH. Run local-mcps setup (session-init does it) and retry.")
        if proc.returncode != 0:
            die(f"lmcps failed for {symbol}:\n{proc.stderr.strip()}")
        if "1 request per second" not in proc.stdout:
            return proc.stdout
        time.sleep(2 * (attempt + 1))
    return proc.stdout


def monthly_closes(text, symbol, today):
    """Adjusted month-end closes, oldest first, current (partial) month dropped."""
    if not text.lstrip().startswith("timestamp"):
        snippet = text.strip()[:300]
        die(f"Alpha Vantage returned no series for {symbol}:\n  {snippet}\n"
            f"If that is a quota or rate limit: `lmcps rotate mcp-alphavantage`, then retry.")
    rows = list(csv.DictReader(io.StringIO(text)))
    this_month = today.strftime("%Y-%m")
    closes = sorted((r["timestamp"][:7], float(r["adjusted close"]))
                    for r in rows if r["timestamp"][:7] < this_month)
    first_of_month = today.replace(day=1)
    expected = (first_of_month - dt.timedelta(days=1)).strftime("%Y-%m")
    if not closes or closes[-1][0] != expected:
        die(f"{symbol}: latest complete month is {closes[-1][0] if closes else 'none'}, "
            f"expected {expected}. The series is stale.")
    need = LOOKBACK_MONTHS + 1
    if len(closes) < need:
        die(f"{symbol}: only {len(closes)} complete months, need {need}.")
    return closes[-need:]


def annual_vol(closes):
    prices = [p for _, p in closes]
    rets = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices))]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(12) * 100


def inverse_vol(vols):
    inv = {k: 1 / v for k, v in vols.items()}
    total = sum(inv.values())
    return {k: v / total for k, v in inv.items()}


def cmd_vols(args):
    today = today_arg(args.today)
    raw, window = {}, None
    for n, (key, sym) in enumerate(PROXIES.items()):
        if n and not args.from_dir:
            time.sleep(1.2)
        closes = monthly_closes(fetch_csv(sym, args.from_dir), sym, today)
        raw[key] = annual_vol(closes)
        window = (closes[0][0], closes[-1][0])
    capped = {k: max(GUARDRAILS[k][0], min(GUARDRAILS[k][1], v)) for k, v in raw.items()}
    w_raw, w_cap = inverse_vol(raw), inverse_vol(capped)

    os.makedirs(RP_HOME, exist_ok=True)
    with open(VOLS, "w", encoding="utf-8") as fh:
        json.dump({"window": window, "raw": raw, "capped": capped,
                   "weights_raw": w_raw, "weights_capped": w_cap}, fh, indent=1)

    print(f"Vol inputs ({LOOKBACK_MONTHS} complete months to {window[1]}, fetched live)\n")
    print("| Pillar | Proxy | Raw vol | Floor | Cap | Used | Capped? | Weight |")
    print("|---|---|---|---|---|---|---|---|")
    for key, (name, _) in PILLARS.items():
        lo, hi = GUARDRAILS[key]
        print(f"| {name} | {PROXIES[key]} | {raw[key]:.2f}% | {lo}% | {hi}% | {capped[key]:.2f}% | "
              f"{'yes' if capped[key] != raw[key] else 'no'} | {w_cap[key]:.1%} |")
    if capped != raw:
        print("\nA guardrail bound this run. Weights on raw vols would be: "
              + ", ".join(f"{PILLARS[k][0]} {w_raw[k]:.1%}" for k in PILLARS))
    else:
        print("\nNothing bound this run -- raw and used vols are identical.")
    print(f"Saved to {VOLS}. `plan` uses the Used column unless given --raw.")
    return 0


# --- plan --------------------------------------------------------------------

def k(x, sign=False):
    lead = "−" if x < 0 else ("+" if sign else "")
    return f"{lead}${abs(x) / 1000:,.0f}K"


def arrow(old, new, fmt):
    return fmt(old) if abs(old - new) < 1e-9 else f"{fmt(old)} → {fmt(new)}"


def cmd_plan(args):
    positions, base = load_account()
    if not base:
        die("account.json has no BASE/USD balance row.")
    nlv, cash = base["net_liquidation_value"], base["cash_balance"]
    if not os.path.exists(VOLS):
        die(f"{VOLS} not found -- run `vols` first.")
    with open(VOLS, encoding="utf-8") as fh:
        weights = json.load(fh)["weights_raw" if args.raw else "weights_capped"]

    held = {}
    unmapped = []
    for p in positions:
        sym = root_of(p)
        if sym not in PILLAR_OF:
            if sym not in args.ignore:
                unmapped.append(p["contract_description"])
            continue
        h = held.setdefault(sym, {"qty": 0, "mv": 0.0, "cost": 0.0, "fut": p["asset_class"] == "FUT"})
        h["qty"] += p["position"]
        h["mv"] += p["market_value"]
        h["cost"] += p["average_price"] * p["position"]
    if unmapped:
        die("Positions with no pillar: " + "; ".join(unmapped) +
            "\nAsk the user where they belong, or pass --ignore SYM to leave them out.", 2)

    inst = {}
    for sym, h in held.items():
        unit = h["mv"] / h["qty"] if h["qty"] else 0.0
        inst[sym] = dict(h, unit=unit, new=h["qty"])
    for spec in args.targets:
        try:
            sym, rest = spec.split("=")
            qty_s, _, unit_s = rest.partition("@")
            sym, qty = sym.upper(), float(qty_s)
        except ValueError:
            die(f"target wants SYM=QTY or SYM=QTY@UNIT_NOTIONAL, got {spec!r}")
        if sym not in PILLAR_OF:
            die(f"{sym} is not in any pillar.")
        if sym not in inst:
            if not unit_s:
                die(f"{sym} is not held, so its unit notional is unknown: pass {sym}=QTY@NOTIONAL "
                    f"(futures: price x multiplier).")
            inst[sym] = {"qty": 0, "mv": 0.0, "cost": 0.0, "unit": float(unit_s),
                         "fut": sym not in ETFS, "new": qty}
        else:
            if unit_s:
                inst[sym]["unit"] = float(unit_s)
            inst[sym]["new"] = qty
        if inst[sym]["fut"] and qty != int(qty):
            die(f"{sym}: futures come in whole contracts.")

    for i in inst.values():
        i["new_mv"] = i["new"] * i["unit"]
    gross_old = sum(i["mv"] for i in inst.values())
    gross_new = sum(i["new_mv"] for i in inst.values())
    target_gross = nlv * args.leverage

    print(f"NLV {k(nlv)} · cash {k(cash)} · gross notional {k(gross_old)} · "
          f"leverage {gross_old / nlv:.2f}x · target {args.leverage:.2f}x = {k(target_gross)}\n")
    print("| Instrument | Weight | Target | Notional | Units |")
    print("|---|---|---|---|---|")
    pct = lambda x: f"{x:.1%}"
    units = lambda x: f"{x:,.0f}"
    for key, (name, syms) in PILLARS.items():
        members = [s for s in syms if s in inst]
        old = sum(inst[s]["mv"] for s in members)
        new = sum(inst[s]["new_mv"] for s in members)
        tgt = weights[key]
        print(f"| **{name}** | {arrow(old / gross_old, new / gross_new, pct)} | "
              f"{tgt:.1%} · {k(tgt * target_gross)} | {arrow(old, new, k)} | |")
        for s in members:
            i = inst[s]
            print(f"| – {s} | {arrow(i['mv'] / gross_old, i['new_mv'] / gross_new, pct)} | | "
                  f"{arrow(i['mv'], i['new_mv'], k)} | {arrow(i['qty'], i['new'], units)} |")

    # Futures settle variation margin daily; buying or selling them moves no
    # cash beyond margin. Only ETF trades change the cash line.
    net_cash = sum((i["qty"] - i["new"]) * i["unit"] for i in inst.values() if not i["fut"])
    cash_after = cash + net_cash
    lev_after = gross_new / nlv
    print(f"\nNet cash from ETF trades: {k(net_cash, sign=True)} · "
          f"cash after: {k(cash_after)} · leverage after: {lev_after:.2f}x")

    tax = []
    for s, i in sorted(inst.items()):
        if i["fut"] or not i["qty"]:
            continue
        avg = i["cost"] / i["qty"]
        unreal = i["mv"] - i["cost"]
        sold = i["qty"] - i["new"]
        if sold > 0:
            realized = sold * (i["unit"] - avg)
            tax.append(f"{s}: sell {sold:,.0f} realizes ≈ {k(realized, sign=True)} (average cost ${avg:,.2f}; specific-lot selection changes this)")
        elif sold < 0 and unreal < 0:
            tax.append(f"{s}: unrealized {k(unreal)} is a harvest candidate, but this plan buys "
                       f"{-sold:,.0f} more -- a loss sale within 30 days of a purchase is a wash sale")
        elif unreal < 0:
            tax.append(f"{s}: unrealized {k(unreal)} -- harvest candidate")
    if tax:
        print("\nTax (approximate, not advice):")
        for t in tax:
            print(f"  - {t}")
        print("  - Futures (Section 1256) are marked to market at year end regardless, so changing "
              "contract counts is tax-neutral.")

    flags = []
    if lev_after > 3.0:
        flags.append(f"URGENT: leverage {lev_after:.2f}x > 3.0x -- reduce before adding.")
    if lev_after < 2.0:
        flags.append(f"Underinvested: leverage {lev_after:.2f}x < 2.0x.")
    if cash_after < 50_000:
        flags.append(f"Margin headroom: cash after trades {k(cash_after)} < $50K.")
    for s, i in inst.items():
        if not i["fut"] and abs(i["new"] - i["qty"]) > 5000:
            flags.append(f"{s}: {abs(i['new'] - i['qty']):,.0f} shares -- use a limit order or tranches.")
    if flags:
        print("\nFlags:")
        for f in flags:
            print(f"  - {f}")
    return 0


# --- main --------------------------------------------------------------------

def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="rp.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("closeouts", help="when each futures leg drops out")
    c.add_argument("--cash", action="append", default=[], metavar="CONID=ROOT:YYYYMMDD",
                   help="a cash-settled contract and its last_trading_date from search_futures")
    c.add_argument("--candidate", action="append", default=[], metavar="CONID",
                   help="a contract you are considering opening")
    c.add_argument("--horizon", type=int, default=15, help="business days (default 15)")
    c.add_argument("--ladder", type=int, default=3, help="later contracts to list per leg")
    c.add_argument("--max-age", type=int, default=4, help="days before the table counts as stale")
    c.add_argument("--table", help="read the table from a file instead of fetching it")
    c.add_argument("--no-account", action="store_true", help="candidates only")
    c.add_argument("--today")
    c.set_defaults(func=cmd_closeouts)

    h = sub.add_parser("hours", help="liquid trading windows, and whether one is open now")
    h.add_argument("--at", metavar="ISO", help="a PT datetime to evaluate instead of now")
    h.set_defaults(func=cmd_hours)

    v = sub.add_parser("vols", help="pillar vols and inverse-vol weights")
    v.add_argument("--from-dir", help="read <SYMBOL>.csv from here instead of calling lmcps")
    v.add_argument("--today")
    v.set_defaults(func=cmd_vols)

    p = sub.add_parser("plan", help="current vs target, and the effect of proposed quantities")
    p.add_argument("targets", nargs="*", metavar="SYM=QTY[@UNIT_NOTIONAL]")
    p.add_argument("--leverage", type=float, default=2.5)
    p.add_argument("--raw", action="store_true", help="use raw vols instead of capped")
    p.add_argument("--ignore", action="append", default=[], metavar="SYM")
    p.set_defaults(func=cmd_plan)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
