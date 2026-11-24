"""What's new: the short list worth looking at today.

    py digest.py                 # filings from the last 7 days -> reports/digest-YYYY-MM-DD.md
    py digest.py --days 30
    py digest.py --selftest

Three sections, all keyed by filing date (when the public could see it):
  1. Congress buys of $15k+ (stocks/ETFs with a ticker), biggest first.
  2. Tickers more than one member of Congress bought in the last 30 days.
  3. Insider cluster buys (3+ insiders buying on the open market within 30
     days, $50k+ combined) and single insider buys of $100k+.

This is a list of things to research, not a buy list. Congress trades are
disclosed up to 45 days late, so by the time you see one the move may be over;
check `lag_days`. Written for long-term buyers (e.g. a Roth IRA): every section
says in plain words what it means.
"""

import datetime
import os
import sys

import duckdb

import signals
from schema import DB_PATH, ensure_schema


MAX_LAG_DAYS = 90


def _who(r):
    name = f"{r.first_name or ''} {str(r.last_name).strip(' ,.')}".strip()
    who = f"{name} ({r.chamber})"
    if r.owner and str(r.owner).lower() not in ("self", "none", "nan"):
        who += f", {str(r.owner).lower()}"
    return who


def _money(x):
    if x is None or x != x:
        return "?"
    return f"${x / 1e6:.1f}M" if x >= 1e6 else f"${x / 1e3:.0f}k"


def _bracket(lo, hi):
    return f"{_money(lo)}+" if hi is None or hi != hi else f"{_money(lo)}–{_money(hi)}"


def _co(name):
    return f" ({name})" if name and name == name else ""


def _d(x):
    return str(x)[:10] if x is not None and x == x else "?"


def build(con, today, days=7):
    start = today - datetime.timedelta(days=days)
    out = [f"# Digest for {today}", "",
           f"New public filings from {start} to {today}.", "",
           "**How to use this:** these are companies that people with an inside view "
           "just put their own money into. Treat each one as a lead to research, not a "
           "buy signal. Before adding anything to a long-term account, check whether you'd "
           "still want to own it in 5 years if you'd never seen this list, and look at "
           "`reports/backtest.md` to see whether each kind of signal has actually beaten "
           "an index fund.", ""]

    if signals.has_table(con, "senate_trades"):
        cb = signals.congress_buys(con, start, today)
        out += ["## Congress buys ($15k+)", "",
                "Members of Congress (or their spouses) who disclosed buying a stock. "
                "They only report a dollar range, and up to 45 days after the trade, so "
                "check \"Days late\": the price may already have moved.", ""]
        # Filings years late (amendments, slow filers) are history, not news.
        stale = cb[cb.lag_days > MAX_LAG_DAYS]
        cb = cb[~(cb.lag_days > MAX_LAG_DAYS)]
        if cb.empty:
            out += ["None this period.", ""]
        else:
            # One line per person+ticker: a member often buys the same stock
            # several days running, which is one decision, not five.
            cb = cb.assign(who=cb.apply(_who, axis=1))
            g = (cb.groupby(["tkr", "who"], sort=False)
                 .agg(filed=("event_date", "max"), trades=("tkr", "size"),
                      lo=("amount_low", "sum"), hi=("amount_high", "sum"),
                      first=("txn_date", "min"), last=("txn_date", "max"),
                      lag=("lag_days", "min"))
                 .reset_index().sort_values(["lo", "filed"], ascending=False))
            out += ["| Ticker | Who | Amount (total) | Traded | Days late | Filed |",
                    "|---|---|---|---|---|---|"]
            for r in g.itertuples():
                traded = _d(r.first) if _d(r.first) == _d(r.last) else f"{_d(r.first)} to {_d(r.last)}"
                n = f" ({r.trades} buys)" if r.trades > 1 else ""
                out.append(f"| **{r.tkr}** | {r.who} | {_bracket(r.lo, r.hi)}{n} | {traded} | "
                           f"{'' if r.lag != r.lag else int(r.lag)} | {_d(r.filed)} |")
            out.append("")
        if len(stale):
            out += [f"_Left out {len(stale)} buys filed more than {MAX_LAG_DAYS} days after "
                    "the trade; too old to act on._", ""]

        multi = signals.congress_buys(con, today - datetime.timedelta(days=30), today,
                                      min_amount=1001)
        multi = multi[~(multi.lag_days > MAX_LAG_DAYS)]
        multi = multi.assign(last_name=multi.last_name.str.strip(" ,."))
        multi = (multi.groupby("tkr").agg(members=("last_name", "nunique"),
                                          names=("last_name", lambda s: ", ".join(sorted(set(s)))),
                                          floor=("amount_low", "sum"))
                 .query("members >= 2").sort_values(["members", "floor"], ascending=False))
        out += ["## Bought by 2+ members (last 30 days, any size)", "",
                "Several members buying the same stock independently is a stronger hint "
                "than one.", ""]
        out += (["None."] if multi.empty else
                [f"- **{t}**: {r.members} members ({r.names}), at least {_money(r.floor)}"
                 for t, r in multi.iterrows()])
        out.append("")

    if signals.has_table(con, "insider_trades"):
        cl = signals.insider_clusters(con, start, today)
        out += ["## Insider cluster buys (3+ insiders, 30 days)", "",
                "Three or more executives or directors of the same company bought its "
                "stock on the open market within a month. Insiders sell for many reasons "
                "(taxes, a house) but usually buy for one: they think it's cheap. "
                "In published research, clustered insider buying has been one of the more reliable public signals.", ""]
        out += (["None this period."] if cl.empty else
                [f"- **{r.tkr}**{_co(r.issuer_name)}: {r.insiders} insiders, "
                 f"{_money(r.total_value)} total, triggered {_d(r.event_date)}"
                 f"{' (incl. an officer)' if r.any_officer else ''}. Buyers: {r.buyers}"
                 for r in cl.itertuples()])
        out.append("")
        big = signals.big_insider_buys(con, start, today)
        out += ["## Single insider buys of $100k+", "",
                "One insider putting at least $100k of their own money in.", ""]
        out += (["None this period."] if big.empty else
                [f"- **{r.tkr}**{_co(r.issuer_name)}: {r.owner_name} "
                 f"[{(r.owner_relationship or '?').lower()}] bought {_money(r.total_value)}, "
                 f"filed {_d(r.event_date)}" for r in big.head(25).itertuples()])
        out.append("")
    else:
        out += ["_No insider data yet: set SEC_CONTACT_EMAIL in .env and run "
                "`py scrape_insiders.py --live --days 30`._", ""]
    return "\n".join(out)


def selftest():
    con = duckdb.connect()
    signals.make_fixture(con)
    text = build(con, datetime.date(2025, 3, 31), days=30)
    assert "**AAA**" in text and "Alpha" in text
    assert "**XYZ**" in text and "3 insiders" in text
    assert "**SEL**" in text, "a $200k single buy shows in the big-buys section"
    assert "Gamma" not in text, "sales never appear"
    print(text)
    print("digest selftest ok")


def arg(flag, default=None):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


def main(days=7):
    today = datetime.date.today()
    con = duckdb.connect(DB_PATH)
    ensure_schema(con)
    text = build(con, today, days)
    os.makedirs("reports", exist_ok=True)
    path = f"reports/digest-{today}.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    print(f"\nsaved {path}")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main(int(arg("--days", 7)))
