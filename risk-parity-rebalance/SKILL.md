---
name: risk-parity-rebalance
description: "Rebalance Louie's leveraged four-pillar risk-parity portfolio at IBKR: futures roll deadlines, inverse-vol pillar targets, and a proposed set of trades, staged as IBKR order instructions once approved. Run only on /risk-parity-rebalance or an explicit request for a rebalance."
---

# Risk parity rebalance

A rebalance is a **plan against today's state**, like `terraform plan`: read the
account as it is now, compute what it should be, show the difference. It is
idempotent. The proposal depends only on today's positions and today's vols.
Neither the rebalance log in `/memory/portfolio.md` nor whether a past proposal
was executed goes into it. If Louie has a lasting reason to deviate, you discuss
it with Louie after the plan is shown.

Every step's arithmetic runs in `rp`, never by hand in context. Once per
conversation:

```bash
bash "$(ls -1dt /mnt/skills/*/risk-parity-rebalance ~/.claude/skills/synced/*/risk-parity-rebalance 2>/dev/null | head -1)/setup.sh"
```

That lookup is deliberate: claude.ai chat puts skills under `/mnt/skills/user/`
or `/mnt/skills/plugins/`, Cowork and cloud Claude Code under
`~/.claude/skills/synced/<bucket>/`, and hardcoding any one breaks the others.

It puts `rp` on PATH, and every later command is just:

```bash
rp <closeouts|vols|plan|hours> ...
```

**Never re-derive the path or set a shell variable for it** — shell state dies
with the bash call that set it. Just call `rp`. Its own state lives in
`/tmp/rp/`, which does survive across turns within a conversation.

## Pillars

| Pillar | Futures | ETF fill | Vol proxy |
|---|---|---|---|
| Equities | ES, MES | VT | VT |
| Bonds | ZN, ZB | TLT, IEF | TLT |
| Gold | GC, MGC | GLD | GLD |
| Commodities | CL, MCL | PDBC | USO |

Target: inverse-vol weights across the four pillars, times NLV × 2.5 gross
notional.

## 1. Read the account

Call the IBKR connector's `get_account_positions` and `get_account_balances`.
Write both results **verbatim** to `/tmp/rp/account.json`, without summarising
or rounding:

```bash
mkdir -p /tmp/rp && cat > /tmp/rp/account.json <<'EOF'
{"positions": <get_account_positions result>, "balances": <get_account_balances result>}
EOF
```

A futures position's `market_value` is already notional (price × multiplier).
**Never use** `get_account_summary.leverage` or `get_pa_allocation`. Both value
futures at margin/NAV impact, not notional, so they say nothing about risk
parity.

## 2. Close-outs: always first

```bash
rp closeouts
```

This is the reason to run the skill even when nothing needs rebalancing. It
reads IBKR's published close-out table
(`/webrest/futures/physicaldelivery`, matched by conid) and reports, for each
futures leg, when the leg drops out of the portfolio and the last close to be
flat by.

- **Never derive a deadline from FND or expiry rules.** A derived rule was wrong
  several times. The long cutoff is First Position Day for GC, ZN and ZB, and
  the last trade day for CL. IBKR's window before that cutoff also differs by
  product. The table already encodes both.
- **Cash-settled legs (ES, MES, MCL) are not in the table.** They still vanish
  at expiry. Get the contract's `last_trading_date` from `search_contracts`,
  then `search_futures`, and re-run with `--cash CONID=ES:YYYYMMDD`.
- **Exit code 2 means stop.** A leg that is neither in the table nor a known
  cash-settled product has an unknown deadline. Tell Louie. Do not guess.
- ⚠️ and 🚨 legs go into the plan. The `Roll into` column names the suggested
  month; the ladder printed underneath gives each candidate's conid, deadline
  and whether its month is on the liquid cycle or `SERIAL`. Take the conid for
  staging from there. Check any contract you would newly open with
  `--candidate CONID`.

**Roll into a liquid month, not simply the next one.** A product lists months
outside its cycle that almost nobody trades. Gold's cycle is Feb, Apr, Jun, Aug,
Oct, Dec, so Oct rolls to **Dec**, skipping Nov: measured on 2026-10-04, GC
Nov'26 held 4.3K open interest against Dec'26's 325K, and Feb'27 — further out —
held 41K. Liquidity follows the cycle, not the calendar distance. Treasuries and
the equity indices are quarterly (Mar, Jun, Sep, Dec); WTI lists every month.
The script's suggestion encodes this, and `future_open_interest` from
`get_price_snapshot` confirms it in one call if a month looks doubtful or the
script reports the cycle as unrecorded.

## 3. Vols

```bash
rp vols
```

The script uses 60 complete months of adjusted closes for the four proxies,
pulled through `lmcps call mcp-alphavantage`. `session-init` sets up `lmcps`.
The data goes straight into the calculation without passing through context. It
prints raw vols, guardrail-capped vols, and weights for each. **Capped is the
default** unless Louie says otherwise. Show the table.

Always fetch fresh vols; never use hardcoded or remembered estimates. The
script's formula is the method, so don't deviate from it or recompute by hand.
It uses inverse-vol weights, and USO with no multiplier, since USO tracks
front-month WTI, the same underlying as CL.

The guardrails are deliberately tight. A breach is information, not an error:

| Pillar | Band | Typical range |
|---|---|---|
| Equities | 10–22% | Global equity rarely leaves this band outside a crisis |
| Bonds | 8–18% | Long bonds ran ~10% before 2022 and ~17% in the 2022 shock; above 18% is unusual |
| Gold | 10–22% | Usually 12–18%, very rarely outside the band |
| Commodities | 20–40% | Crude's normal regime is 25–35%; above 40% signals an extreme event |

- If the quota is spent: `lmcps rotate mcp-alphavantage`, then run it again.
- If `lmcps` is down, say so. IBKR `get_price_history` (monthly, with
  `include_corporate_actions` for dividends) agrees to within 0.2pp. The cost is
  that you would re-type about 250 numbers by hand, so ask Louie before using
  it.

## 4. Plan

```bash
rp plan                   # today vs target
rp plan VT=3000 ZN=6 ...  # effect of a proposal
```

Run it bare to see the gap, then choose target quantities and re-run until the
result is right. A quantity is the total per root across expiries, so a roll
leaves it unchanged. For an instrument not currently held, pass
`SYM=QTY@UNIT_NOTIONAL`, with the price from `get_price_snapshot` × the
multiplier (see Contract specs below). The script prints the old → new table,
cash and leverage after the trades, tax effects, and flags. Positions with no
pillar stop it; ask Louie where they belong.

**This section is guidance, not strict rules.** Use judgment based on current NLV, prices, and contract sizes. The examples below assume a ~$1M NLV portfolio — a larger or smaller portfolio may call for more or fewer futures contracts, different ETF/futures splits, or additional micro contracts (MES, MGC, MCL) for granularity.

**General principles:**
- Prefer futures as primary instrument (capital-efficient, 60/40 tax treatment).
- Round futures down to whole contracts; fill remainder with the ETF equivalent.
- Never hold a fractional futures contract.
- If the pillar target is smaller than one futures contract, use the ETF only, or micro contracts.
- If the pillar target is much larger than a few contracts, scale up contracts first before adding ETF.

**Contract specs:** contract notional = price × multiplier: ES 50, MES 5,
ZN/ZB 1000, GC 100, MGC 10, CL 1000, MCL 100. A held contract's
`market_value / position` cross-checks it. If in doubt, CME Group has the
current specs: `https://www.cmegroup.com/markets/`.

**Typical instrument choices (adjust to NLV and prices):**
- Equities: ES contracts as leveraged core + VT shares for remainder (VT adds international exposure beyond ES's S&P 500)
- Bonds: ZN as primary (better carry than ZB) + 1 ZB for long-end duration + TLT for fine-tuning
- Gold: GC contracts + GLD shares for remainder (GLD ≈ GC price per oz ÷ 10)
- Commodities: CL contracts + PDBC for remainder (PDBC is diversified, no K-1)

**Taxes:** prefer tax-neutral levers first. Changing futures counts costs
nothing, because Section 1256 contracts are marked to market at year end
anyway. Selling an appreciated ETF realizes the gain the script prints. Where a
harvestable loss would conflict with buying more of the same ETF, it is flagged
as a wash sale. Surface these numbers, and say that it isn't tax advice.

**Known limitation, tabled:** bond pillar vol comes from TLT, but ZN carries far
less vol per dollar. Sizing by risk rather than notional would fix that, but
it's a separate decision. Don't change the method on your own.

## 5. Present: three tables, verbatim

The deliverable is **three tables, copied from the script's output exactly as it
printed them.** This is a hard requirement, not a default. Do not rebuild,
re-order, rename, merge or drop columns; do not drop rows; do not round or
re-derive the numbers; do not replace a table with prose. Commentary goes above
or below a table, never instead of it.

If you find yourself writing a table with columns the script did not print, stop
and paste the script's table instead. The notional and units columns are the
part Louie actually trades from, and a summary that loses them is not a
rebalance proposal.

**Table 1 — Close-outs and rolls**, from `closeouts`:

```
| Leg | Qty | Drops out because | Drop-out moment (PT) | Flat by | Roll into |
```

Every futures leg gets a row, including the ones with nothing due. Urgency
rides on the leg itself: ⚠️ means roll inside the horizon, 🚨 means the deadline
is today or past, ⛔ marks a candidate you should not open. Keep the legend
line and any 🚨 notes printed under the table. All times are Pacific.

**Table 2 — Vol inputs**, from `vols`:

```
| Pillar | Proxy | Raw vol | Floor | Cap | Used | Capped? | Weight |
```

Keep the line underneath saying whether a guardrail bound.

**Table 3 — Proposal**, from `plan` once you have settled on target quantities:

```
| Instrument | Weight | Target | Notional | Units |
```

Bold pillar rows, instrument rows prefixed with `–`, `old → new` on anything
that changes. Every instrument appears, unchanged ones included. Follow it with
the cash-and-leverage line and the tax lines the script printed under it.

Then add, in your own words: the trading window for each leg to be traded, and a
line or two on why you split the pillars the way you did. If you compared
alternatives, define each one where you first mention it.

Don't write the proposal into memory. Record only durable decisions Louie states
while discussing it.

## 6. Check the trading window

```bash
rp hours
```

Prints each market's liquid window in PT and ET, and whether it is open right
now. Run it whenever you propose a roll or stage anything, and **say in the
proposal when the window for each leg opens and closes, in PT**.

A CME product keeps trading outside its window, which is the trap: the order
fills, just against a thin book, and a roll pays the wider spread twice because
it is two legs. Gold is the sharp case — it settles at 13:30 ET, which is
**10:30 PT**, and after that half-hourly volume falls from 8K–11K contracts to
about 1–2K. A gold roll at 10:49 PT is 19 minutes past that edge.

Use `--at "2026-10-02T14:30"` (a PT datetime) to check a specific moment, such
as one Louie is asking about after the fact. Half-day sessions aren't modelled,
so check separately around Thanksgiving and Christmas.

## 7. Stage: only after Louie says go

`create_order_instruction` creates a non-binding **instruction**. It appears in
IBKR Desktop's AI Instructions tab, and becomes an order only when Louie clicks
Review & Submit there. Nothing you stage is live. Don't describe it as placed.

- First check `get_order_instructions` and `get_account_orders`, so nothing is
  staged twice.
- For futures, use `contract_id_ex` from `search_futures` verbatim. For ETFs,
  use the stringified conid.
- **Rolls** go in as two single-leg instructions, sell the old month and buy
  the new one. The connector can't express a futures calendar spread, so if
  Louie wants no legging risk, the spread has to be built by hand in IBKR.
- Use LIMIT for large ETF orders (the script flags them). Say which order type
  and time-in-force you chose for each leg.
- An instruction waits until Louie submits it, so staging outside the window is
  fine. Telling him which window to submit in is the part that matters.
