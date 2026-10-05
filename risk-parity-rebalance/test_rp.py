#!/usr/bin/env python3
"""Offline tests for bin/rp.py. No network, no lmcps.

The close-out fixture is a real snapshot of IBKR's table (2026-09-18), trimmed
to the products held. The dates it must produce were verified by hand against
the exchange rules and IBKR's worked example; they are the regression this
suite exists for, because deriving them by rule was wrong more than once.
"""

import contextlib
import datetime as dt
import io
import json
import math
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TABLE = os.path.join(HERE, "testdata", "closeouts-2026-09-18.json")
sys.path.insert(0, os.path.join(HERE, "bin"))

import rp  # noqa: E402

TODAY = "2026-09-19"


def position(desc, conid, qty, mv, avg, fut):
    return {"contract_id": conid, "contract_description": desc, "position": qty,
            "market_value": mv, "average_price": avg, "asset_class": "FUT" if fut else "STK"}


POSITIONS = [
    position("CL Nov'26 @NYMEX", 304037511, 2, 190960.0, 97.51, True),
    position("GC Oct'26 @COMEX", 744880148, 1, 438230.0, 4671.63, True),
    position("ZN Dec'26 @CBOT", 866514750, 3, 317484.0, 108.19, True),
    position("ZB Dec'26 @CBOT", 866514747, 1, 107031.0, 108.95, True),
    position("VT", 52197301, 4398, 697303.0, 114.10, False),
    position("TLT", 15547841, 671, 54539.0, 147.62, False),
    position("GLD", 51529211, 633, 254143.0, 431.54, False),
    position("PDBC", 320227565, 2856, 56263.0, 18.79, False),
]
BALANCES = {"balances": [{"currency": "BASE", "cash_balance": 57738.0,
                          "net_liquidation_value": 1122680.0}]}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        rp.RP_HOME = self.tmp.name
        rp.ACCOUNT = os.path.join(self.tmp.name, "account.json")
        rp.VOLS = os.path.join(self.tmp.name, "vols.json")
        self.write_account(POSITIONS)

    def tearDown(self):
        self.tmp.cleanup()

    def write_account(self, positions):
        with open(rp.ACCOUNT, "w", encoding="utf-8") as fh:
            json.dump({"positions": {"positions": positions}, "balances": BALANCES}, fh)

    def run_rp(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = rp.main(list(argv))
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()


class Calendar(unittest.TestCase):
    def test_holidays_2026(self):
        h = rp.us_holidays(2026)
        for d in ("2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
                  "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25"):
            self.assertIn(dt.date.fromisoformat(d), h, d)

    def test_saturday_christmas_is_observed_friday(self):
        self.assertIn(dt.date(2027, 12, 24), rp.us_holidays(2027))

    def test_prev_business_day_skips_thanksgiving(self):
        self.assertEqual(rp.prev_business_day(dt.date(2026, 11, 27)), dt.date(2026, 11, 25))

    def test_prev_business_day_skips_weekend(self):
        self.assertEqual(rp.prev_business_day(dt.date(2026, 9, 28)), dt.date(2026, 9, 25))

    def test_business_days_between(self):
        self.assertEqual(rp.business_days_between(dt.date(2026, 9, 19), dt.date(2026, 9, 25)), 5)
        self.assertEqual(rp.business_days_between(dt.date(2026, 9, 25), dt.date(2026, 9, 25)), 0)
        self.assertEqual(rp.business_days_between(dt.date(2026, 9, 25), dt.date(2026, 9, 24)), -1)


class Closeouts(Base):
    def closeouts(self, *extra):
        return self.run_rp("closeouts", "--table", TABLE, "--today", TODAY, *extra)

    def row(self, out, leg):
        return next(line for line in out.splitlines()
                    if line.startswith("|") and leg in line)

    def test_verified_deadlines(self):
        code, out, _ = self.closeouts()
        self.assertEqual(code, 0)
        self.assertIn("Fri 9/25", self.row(out, "GC Oct'26"))
        self.assertIn("Fri 10/16", self.row(out, "CL Nov'26"))
        # IBKR's line is 13:15 CT on Fri 11-27; Thanksgiving pushes flat-by to Wed.
        self.assertIn("Wed 11/25", self.row(out, "ZN Dec'26"))
        self.assertIn("Wed 11/25", self.row(out, "ZB Dec'26"))

    def test_drop_out_moments_are_pacific(self):
        _, out, _ = self.closeouts()
        self.assertIn("Mon 9/28 05:20", self.row(out, "GC Oct'26"))     # 08:20 ET
        self.assertIn("Fri 11/27 11:15", self.row(out, "ZN Dec'26"))    # 13:15 CT
        self.assertNotIn(" ET", self.row(out, "GC Oct'26"))

    def test_year_shown_only_when_it_is_not_this_year(self):
        _, out, _ = self.closeouts()
        self.assertIn("flat by Wed 2/24/27", out)        # ZN Mar'27, next year
        self.assertIn("flat by Wed 11/25 ", out)         # this year, no suffix

    def test_urgency_rides_on_the_leg(self):
        _, out, _ = self.closeouts()
        self.assertTrue(self.row(out, "GC Oct'26").startswith("| ⚠️"))
        self.assertTrue(self.row(out, "ZN Dec'26").startswith("| ZN"))
        self.assertIn("⚠️ = roll within 15 business days", out)

    def test_past_and_today_get_an_alarm_and_a_note(self):
        _, out, _ = self.run_rp("closeouts", "--table", TABLE, "--today", "2026-09-28")
        self.assertTrue(self.row(out, "GC Oct'26").startswith("| 🚨"))
        self.assertIn("GC Oct'26 @COMEX: PAST its deadline", out)
        _, out, _ = self.run_rp("closeouts", "--table", TABLE, "--today", "2026-09-25")
        self.assertTrue(self.row(out, "GC Oct'26").startswith("| 🚨"))
        self.assertIn("GC Oct'26 @COMEX: TODAY is the last session", out)

    def test_table_has_no_conid_or_dropped_columns(self):
        _, out, _ = self.closeouts()
        header = next(l for l in out.splitlines() if l.startswith("| Leg |"))
        self.assertEqual(header, "| Leg | Qty | Drops out because | "
                                 "Drop-out moment (PT) | Flat by | Roll into |")
        self.assertIn("| Dec'26 |", self.row(out, "CL Nov'26"))   # month only
        self.assertNotIn("conid", self.row(out, "CL Nov'26"))

    def test_ladder_lists_later_months(self):
        _, out, _ = self.closeouts()
        self.assertIn("Dec'26  conid 462941472  flat by Wed 11/25", out)
        self.assertIn("Nov'26  conid 760200541", out)

    def test_gold_serial_month_marked_and_december_suggested(self):
        """GC Nov'26 held 4.3K open interest against Dec'26's 325K."""
        _, out, _ = self.closeouts()
        nov = next(l for l in out.splitlines() if "Nov'26  conid 760200541" in l)
        dec = next(l for l in out.splitlines() if "Dec'26  conid 462941472" in l)
        self.assertIn("SERIAL month -- thin, avoid", nov)
        self.assertIn("liquid cycle", dec)
        self.assertIn("→ suggested roll: Dec'26 (conid 462941472), "
                      "flat by Wed 11/25", out)

    def test_every_crude_month_is_liquid(self):
        _, out, _ = self.closeouts()
        self.assertIn("→ suggested roll: Dec'26 (conid 296574787)", out)
        self.assertNotIn("SERIAL", next(l for l in out.splitlines()
                                        if "Dec'26  conid 296574787" in l))

    def test_treasury_suggestion_is_the_next_quarterly(self):
        _, out, _ = self.closeouts()
        self.assertIn("→ suggested roll: Mar'27 (conid 893091637)", out)

    def test_serial_candidate_rejected_even_when_far_out(self):
        code, out, _ = self.closeouts("--candidate", "760200541")
        self.assertEqual(code, 0)
        row = self.row(out, "GC Nov'26")
        self.assertTrue(row.startswith("| ⛔"))
        self.assertIn("[candidate: serial month -- thin]", row)

    def test_unrecorded_cycle_says_so_rather_than_guessing(self):
        table = os.path.join(self.tmp.name, "hg.json")
        rows = [dict(date="2026-09-18T14:00:02.000+00:00", exchange="COMEX",
                     tradingClass="HG", type="F", contractMonth=m, conid=c,
                     description="Copper", cashAccountsOnly="0", liquidTimeLimit="2",
                     holidayDates=None, longFutCallsShortPutsCutOff="20261229 1600",
                     shortFutCallsLongPutsCutOff="20261229 1600",
                     longFutCallsShortPutsLiq=liq, shortFutCallsLongPutsLiq=liq)
                for m, c, liq in (("202612", 901, "20261228 820"),
                                  ("202701", 902, "20270128 820"))]
        with open(table, "w", encoding="utf-8") as fh:
            json.dump(rows, fh)
        self.write_account([position("HG Dec'26 @COMEX", 901, 1, 100000.0, 4.5, True)])
        code, out, _ = self.run_rp("closeouts", "--table", table, "--today", TODAY)
        self.assertEqual(code, 0)
        self.assertIn("cycle unrecorded", out)
        self.assertIn("confirm with future_open_interest", out)

    def test_short_leg_uses_short_column(self):
        self.write_account([position("GC Oct'26 @COMEX", 744880148, -1, -438230.0, 4671.63, True)])
        _, out, _ = self.closeouts()
        self.assertIn("Tue 10/27 05:20", self.row(out, "GC Oct'26"))

    def test_cash_settled_without_ltd_is_blocked(self):
        self.write_account(POSITIONS + [position("ES Dec'26 @CME", 111, 1, 382000.0, 7640, True)])
        code, out, _ = self.closeouts()
        self.assertEqual(code, 2)
        self.assertIn("--cash 111=ES:", out)

    def test_cash_settled_with_ltd(self):
        self.write_account(POSITIONS + [position("ES Dec'26 @CME", 111, 1, 382000.0, 7640, True)])
        code, out, _ = self.closeouts("--cash", "111=ES:20261218")
        self.assertEqual(code, 0)
        row = self.row(out, "ES Dec'26")
        self.assertIn("Fri 12/18 06:30", row)       # 09:30 ET, in PT
        self.assertIn("Thu 12/17", row)
        self.assertIn("next quarterly", row)

    def test_unknown_contract_refuses_to_guess(self):
        self.write_account([position("HG Dec'26 @COMEX", 999, 1, 100000.0, 4.5, True)])
        code, out, _ = self.closeouts()
        self.assertEqual(code, 2)
        self.assertIn("Do not guess", out)

    def test_cash_flag_for_physical_root_is_blocked(self):
        self.write_account([position("HG Dec'26 @COMEX", 999, 1, 100000.0, 4.5, True)])
        code, out, _ = self.closeouts("--cash", "999=HG:20261229")
        self.assertEqual(code, 2)
        self.assertIn("not a known cash-settled product", out)

    def test_candidate_inside_horizon_rejected(self):
        code, out, _ = self.run_rp("closeouts", "--table", TABLE, "--today", TODAY,
                                   "--no-account", "--candidate", "744880148",
                                   "--candidate", "462941472")
        self.assertEqual(code, 0)
        self.assertIn("[candidate: too close to open]", self.row(out, "GC Oct'26"))
        self.assertTrue(self.row(out, "GC Oct'26").startswith("| ⛔"))
        self.assertIn("[candidate: ok to open]", self.row(out, "GC Dec'26"))

    def test_stale_snapshot_warns(self):
        _, out, _ = self.run_rp("closeouts", "--table", TABLE, "--today", "2026-10-01")
        self.assertIn("WARNING: snapshot is 13 days old", out)


class Hours(Base):
    def hours(self, at):
        return self.run_rp("hours", "--at", at)

    def row(self, out, market):
        return next(l for l in out.splitlines() if l.startswith(f"| {market}"))

    def test_the_1049_gold_roll_reads_as_thin(self):
        """Louie rolled GC at 10:49 PT, 19 minutes after the 13:30 ET close."""
        code, out, _ = self.hours("2026-10-02T10:49")
        self.assertEqual(code, 0)
        self.assertIn("Now: Fri 2026-10-02 10:49 PT (13:49 ET)", out)
        self.assertIn("thin (Globex)", self.row(out, "Gold"))
        self.assertIn("opens Mon 05:20 PT", self.row(out, "Gold"))
        # The same minute is still inside the equity and treasury windows.
        self.assertIn("**liquid**", self.row(out, "Equity index"))

    def test_inside_the_gold_window(self):
        _, out, _ = self.hours("2026-10-02T07:00")
        self.assertIn("**liquid**, closes 10:30 PT", self.row(out, "Gold"))

    def test_windows_in_both_zones(self):
        _, out, _ = self.hours("2026-10-02T07:00")
        self.assertIn("| 05:20–10:30 | 08:20–13:30 |", self.row(out, "Gold"))
        self.assertIn("| 06:00–11:30 | 09:00–14:30 |", self.row(out, "Crude"))

    def test_weekend_closes_globex_too(self):
        _, out, _ = self.hours("2026-10-03T09:00")      # Saturday
        self.assertIn("closed — opens Mon", self.row(out, "Gold"))
        self.assertIn("closed — opens Mon", self.row(out, "US ETFs"))

    def test_sunday_evening_reopens_globex_but_not_etfs(self):
        _, out, _ = self.hours("2026-10-04T16:00")      # Sun 19:00 ET
        self.assertIn("thin (Globex)", self.row(out, "Gold"))
        self.assertIn("closed", self.row(out, "US ETFs"))

    def test_holiday_is_flagged_and_skipped(self):
        _, out, _ = self.hours("2026-11-26T07:00")      # Thanksgiving
        self.assertIn("exchange holiday or weekend", out)
        self.assertNotIn("**liquid**", out)
        self.assertIn("opens Fri 05:20 PT", self.row(out, "Gold"))

    def test_dst_boundaries(self):
        self.assertFalse(rp.is_dst(dt.date(2026, 3, 7)))
        self.assertTrue(rp.is_dst(dt.date(2026, 3, 8)))
        self.assertTrue(rp.is_dst(dt.date(2026, 10, 31)))
        self.assertFalse(rp.is_dst(dt.date(2026, 11, 1)))


def synthetic_csv(end_month, n, ret, partial=True):
    """n month-ends ending at end_month (YYYY-MM), alternating +ret/-ret log
    returns, newest first as Alpha Vantage returns them."""
    y, m = map(int, end_month.split("-"))
    rows, price = [], 100.0
    months = []
    for _ in range(n):
        months.append(f"{y:04d}-{m:02d}-28")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    months.reverse()
    prices = []
    for i in range(n):
        prices.append(price)
        price *= math.exp(ret if i % 2 == 0 else -ret)
    for d, p in zip(months, prices):
        rows.append(f"{d},0,0,0,{p:.6f},{p:.6f},0,0")
    if partial:  # a wild partial month that must be ignored
        rows.append(f"{TODAY},0,0,0,1000,1000,0,0")
    return "timestamp,open,high,low,close,adjusted close,volume,dividend amount\n" + \
        "\n".join(reversed(rows)) + "\n"


class Vols(Base):
    def write_csvs(self, **kw):
        d = os.path.join(self.tmp.name, "csv")
        os.makedirs(d, exist_ok=True)
        for sym in ("VT", "TLT", "GLD", "USO"):
            with open(os.path.join(d, f"{sym}.csv"), "w", encoding="utf-8") as fh:
                fh.write(kw.get(sym) or synthetic_csv("2026-08", 80, 0.04))
        return d

    def test_drops_partial_month_and_uses_60_returns(self):
        d = self.write_csvs()
        code, out, _ = self.run_rp("vols", "--from-dir", d, "--today", TODAY)
        self.assertEqual(code, 0, out)
        self.assertIn("Vol inputs (60 complete months to 2026-08, fetched live)", out)
        self.assertIn("| Pillar | Proxy | Raw vol | Floor | Cap | Used | Capped? | Weight |", out)
        with open(rp.VOLS, encoding="utf-8") as fh:
            vols = json.load(fh)
        # 60 alternating +-r returns: sample stdev = r * sqrt(60/59).
        expected = 0.04 * math.sqrt(60 / 59) * math.sqrt(12) * 100
        self.assertAlmostEqual(vols["raw"]["EQ"], expected, places=3)

    def test_guardrail_caps_and_weights(self):
        d = self.write_csvs(USO=synthetic_csv("2026-08", 80, 0.15))
        _, out, _ = self.run_rp("vols", "--from-dir", d, "--today", TODAY)
        with open(rp.VOLS, encoding="utf-8") as fh:
            vols = json.load(fh)
        self.assertIn("A guardrail bound this run. Weights on raw vols would be:", out)
        self.assertGreater(vols["raw"]["CM"], 40)
        self.assertEqual(vols["capped"]["CM"], 40)
        self.assertAlmostEqual(sum(vols["weights_capped"].values()), 1.0)
        self.assertLess(vols["weights_raw"]["CM"], vols["weights_capped"]["CM"])

    def test_stale_series_refused(self):
        d = self.write_csvs(TLT=synthetic_csv("2026-06", 80, 0.04, partial=False))
        code, _, err = self.run_rp("vols", "--from-dir", d, "--today", TODAY)
        self.assertEqual(code, 1)
        self.assertIn("TLT: latest complete month is 2026-06, expected 2026-08", err)

    def test_short_history_refused(self):
        d = self.write_csvs(GLD=synthetic_csv("2026-08", 40, 0.04))
        code, _, err = self.run_rp("vols", "--from-dir", d, "--today", TODAY)
        self.assertEqual(code, 1)
        self.assertIn("only 40 complete months, need 61", err)

    def test_quota_message_names_rotate(self):
        d = self.write_csvs(VT='{"error": {"type": "rate_limit", "message": "25 requests per day"}}')
        code, _, err = self.run_rp("vols", "--from-dir", d, "--today", TODAY)
        self.assertEqual(code, 1)
        self.assertIn("lmcps rotate mcp-alphavantage", err)


class Plan(Base):
    def setUp(self):
        super().setUp()
        w = {"EQ": 0.297, "BN": 0.308, "GLD": 0.267, "CM": 0.128}
        with open(rp.VOLS, "w", encoding="utf-8") as fh:
            json.dump({"weights_capped": w, "weights_raw": w}, fh)

    def test_no_targets_shows_gap(self):
        code, out, _ = self.run_rp("plan")
        self.assertEqual(code, 0, out)
        self.assertIn("leverage 1.88x", out)
        self.assertIn("| **Bonds** | 22.6% | 30.8% · $864K | $479K | |", out)
        self.assertIn("Underinvested", out)

    def test_futures_move_no_cash(self):
        _, out, _ = self.run_rp("plan", "ZN=6", "CL=3")
        self.assertIn("Net cash from ETF trades: +$0K", out)
        self.assertIn("| – ZN | 15.0% → 25.1% | | $317K → $635K | 3 → 6 |", out)
        self.assertIn("leverage after: 2.25x", out)

    def test_etf_sale_realizes_gain_and_raises_cash(self):
        _, out, _ = self.run_rp("plan", "VT=3000")
        self.assertIn("VT: sell 1,398 realizes ≈ +$62K", out)
        self.assertIn("Net cash from ETF trades: +$222K", out)

    def test_buying_a_loss_position_flags_wash_sale(self):
        _, out, _ = self.run_rp("plan", "TLT=900")
        self.assertIn("TLT: unrealized −$45K is a harvest candidate, but this plan buys 229 more", out)

    def test_new_instrument_needs_unit_notional(self):
        code, _, err = self.run_rp("plan", "ES=1")
        self.assertEqual(code, 1)
        self.assertIn("ES=QTY@NOTIONAL", err)
        code, out, _ = self.run_rp("plan", "ES=1@382000")
        self.assertEqual(code, 0)
        self.assertIn("| – ES | 0.0% → 15.3% | | $0K → $382K | 0 → 1 |", out)

    def test_fractional_futures_refused(self):
        code, _, err = self.run_rp("plan", "ZN=4.5")
        self.assertEqual(code, 1)
        self.assertIn("whole contracts", err)

    def test_unmapped_position_refused(self):
        self.write_account(POSITIONS + [position("AAPL", 265598, 10, 2300.0, 150, False)])
        code, _, err = self.run_rp("plan")
        self.assertEqual(code, 2)
        self.assertIn("Positions with no pillar: AAPL", err)
        code, _, _ = self.run_rp("plan", "--ignore", "AAPL")
        self.assertEqual(code, 0)

    def test_large_etf_order_flagged(self):
        _, out, _ = self.run_rp("plan", "PDBC=9000")
        self.assertIn("PDBC: 6,144 shares -- use a limit order or tranches.", out)


if __name__ == "__main__":
    unittest.main()
