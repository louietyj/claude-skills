# risk-parity-rebalance

Rebalances a leveraged four-pillar inverse-vol risk-parity portfolio at IBKR. It
reads the live account through the IBKR connector, checks every futures leg's
close-out deadline, computes pillar targets from 60-month vols, and shows the
plan. Once approved, it stages the trades as non-binding IBKR order
instructions.

```
SKILL.md                  the workflow Claude follows; judgement calls stay here
setup.sh                  puts `rp` on PATH, finding the skill in either layout
bin/rp.py                 closeouts / vols / plan / hours -- all the arithmetic (stdlib only)
test_rp.py                offline tests, no network
testdata/                 a real close-out table snapshot, trimmed to held products
package.py                builds risk-parity-rebalance.zip for upload
```

It depends on `local-mcps` (the `mcp-alphavantage` server) and on the IBKR
connector. It holds no credentials, so the zip is safe to share.

## Design

- **Live input.** The input is the live `get_account_positions` and
  `get_account_balances` results. A futures position's `market_value` is
  already notional. IBKR's own `leverage` and `get_pa_allocation` count futures
  at margin/NAV impact, so the skill never uses them.
- **Close-outs are read, never derived.** Rules derived from FND or expiry get
  this wrong. For GC, ZN and ZB the long cutoff is the exchange's First
  Position Day, and for CL it is the last trade day. IBKR's window before the
  cutoff is product-specific: 2 business days for CL and GC, 2 hours for ZN and
  ZB. IBKR publishes the actual per-contract times as public JSON behind its
  close-out page, keyed by the same conid the positions carry.
  - Cash-settled contracts are absent from that table, so their deadline falls
    back to `search_futures` `last_trading_date`.
  - A leg found in neither source stops the run, because "not in the table" is
    not proof of cash settlement.
- **Flat-by date.** The flat-by date is the close of the last business day
  before liquidation may begin, which matches IBKR's worked example. The script
  skips US exchange holidays and treats CME's abbreviated sessions as closed.
  That only ever moves a deadline earlier. Thanksgiving is the case that
  matters: ZN/ZB Dec'26 liquidation starts Fri 11-27, and GC Dec'26 liquidation
  starts on Thanksgiving itself. Both must be flat by Wed 11-25.
- **The three tables are the deliverable, and the format is prescribed.** The
  script prints them and `SKILL.md` requires them pasted verbatim. Rendering
  lives in the script so the arithmetic cannot drift, but a printed table the
  model then paraphrases is worse than none: two runs produced two different
  layouts, one of which silently dropped a leg from the trade list. So the
  close-out, vol and proposal tables each carry a column spec in the skill body
  as well.
- **Roll into a liquid month, not the next one.** A product lists delivery
  months outside its cycle that almost nobody trades, so the next contract on
  the calendar can be the wrong one. Gold runs Feb/Apr/Jun/Aug/Oct/Dec, so Oct
  rolls to Dec. Measured on 2026-10-04: GC Nov'26 held 4,289 open interest and
  297 contracts of volume, against Dec'26's 324,685 and 22,989 — and Feb'27,
  which is further out, held 41,139. Liquidity follows the cycle, not the
  distance, which is why a "nearest month beyond the deadline" rule picks
  badly. `closeouts` tags each month and suggests the first that is both liquid
  and outside the horizon; a root whose cycle isn't recorded is reported as
  unrecorded rather than guessed at.
- **Liquid hours, measured rather than specified.** `hours` prints each
  market's window and whether it is open. The windows come from 30-minute
  volume bars on Fri 2026-10-02: gold 08:20–13:30 ET, crude 09:00–14:30,
  treasuries 08:20–15:00, equity index and ETFs 09:30–16:00. The CME products
  keep trading outside their window, which is the trap worth a command of its
  own — a gold roll at 10:49 PT fills against a quarter of the depth (8K–11K
  contracts per half-hour inside the window, ~1–2K after), twice, once per leg.
  ET/PT conversion uses the US DST rule directly, since the sandbox may carry no
  tz database.
- **Vols come through `lmcps`, not a connector.** A remote connector's output
  lands in the conversation, so computing on it means re-typing a few hundred
  numbers, and nothing catches a typo. `lmcps` output lands in the sandbox and
  goes straight into the calculation.
  - Alpha Vantage REST, its MCP, and IBKR `get_price_history` with dividends
    all agree to within 0.2pp.
  - The window is 60 *complete* months, so the partial current month never
    counts as a monthly return.
- **Futures move no cash.** Only ETF trades change the cash line. Changing
  futures counts is also tax-neutral, because Section 1256 contracts are marked
  to market at year end anyway.
- **Idempotent, not a journal.** The proposal is a function of today's account
  and vols, not of whether a past proposal was acted on.
- **Staging, not trading.** `create_order_instruction` creates an instruction,
  which becomes an order only once it is reviewed in IBKR Desktop. Futures
  spreads can't be expressed through the connector, so a roll is staged as two
  single legs.

**Tabled:** sizing by risk rather than notional. The bond pillar's vol comes
from TLT, but the pillar is filled mostly with ZN, which carries far less vol
per dollar. So bonds get less risk than an equal-risk split intends.

## Testing

```
python test_rp.py    # 44 offline tests
```

The close-out tests pin the deadlines that were verified by hand against the
exchange rules on 2026-09-18. Vols are tested on synthetic series with a known
answer. `hours` is tested through `--at`, which takes a PT datetime, so the
cases are fixed moments rather than whatever the clock says — including the
10:49 PT gold roll, a Saturday, Sunday's Globex reopen, and Thanksgiving.

For a live check, run the subcommands against a real `account.json`. On Windows,
set `RP_LMCPS="python <repo>/local-mcps/bin/lmcps.py"` and `RP_HOME` to a
scratch directory. `dev/sandbox.sh` covers both skill layouts: the default
container for chat, and `SANDBOX_LAYOUT=cowork` for the synced one.
