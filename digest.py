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

import committees
import contracts
import funds
import health
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


def digest_tickers(con, today, days=7):
    """Every ticker the digest will mention, so health checks can run first."""
    start = today - datetime.timedelta(days=days)
    t = set()
    if signals.has_table(con, "senate_trades"):
        t |= set(signals.congress_buys(con, start, today).tkr)
        t |= set(signals.congress_buys(con, today - datetime.timedelta(days=30), today, 1001).tkr)
    if signals.has_table(con, "insider_trades"):
        t |= set(signals.insider_clusters(con, start, today).tkr)
        t |= set(signals.big_insider_buys(con, start, today).tkr)
    c = contracts.recent(con, start, today)
    if c is not None:
        t |= set(c.tkr)
    f = funds.changes(con, since=start)
    if f is not None:
        t |= set(f.tkr.dropna())
    return t


def agreement(con, today, days=30):
    """{tkr: [reasons]} for tickers with 2+ independent kinds of support in
    the last 30 days (congress buy, insider buy, top fund holding/adding,
    big contract). One kind is a hint; several pointing the same way is
    worth more of your attention."""
    start = today - datetime.timedelta(days=days)
    src = {}

    def add(t, why):
        src.setdefault(t, {})[why.split(":")[0]] = why
    if signals.has_table(con, "senate_trades"):
        cb = signals.congress_buys(con, start, today, min_amount=1001)
        cb = cb[~(cb.lag_days > MAX_LAG_DAYS)]
        for t, g in cb.groupby("tkr"):
            add(t, f"Congress: {g.last_name.str.strip(' ,.').nunique()} member(s) bought")
    if signals.has_table(con, "insider_trades"):
        rows = con.execute("""SELECT upper(trim(ticker)), count(DISTINCT owner_cik)
            FROM insider_trades WHERE trans_code = 'P' AND price_per_share > 0
              AND try_cast(filed_date AS DATE) BETWEEN ? AND ? GROUP BY 1""",
                           [start, today]).fetchall()
        for t, n in rows:
            add(t, f"Insiders: {n} bought on the open market")
    c = contracts.recent(con, start, today)
    if c is not None:
        for r in c.itertuples():
            add(r.tkr, f"Contract: won {_money(r.amount)} from {r.agency}")
    for t, names in funds.holders(con, list(src)).items():
        add(t, f"Funds: held by {', '.join(names)}")
    return {t: list(v.values()) for t, v in src.items() if len(v) >= 2}


def build(con, today, days=7):
    start = today - datetime.timedelta(days=days)
    hl = health.lookup(con, digest_tickers(con, today, days))

    def hp(tkr):
        """' Health: Solid (sales up 8%, profitable)' or '' if never checked."""
        h = hl.get(tkr)
        return f"{h['verdict']} ({h['reasons']})" if h else "not checked"
    out = [f"# Digest for {today}", "",
           f"New public filings from {start} to {today}.", "",
           "**How to use this:** these are companies that people with an inside view "
           "just put their own money into. Treat each one as a lead to research, not a "
           "buy signal. Before adding anything to a long-term account, check whether you'd "
           "still want to own it in 5 years if you'd never seen this list, and look at "
           "`reports/backtest.md` to see whether each kind of signal has actually beaten "
           "an index fund.", "",
           "**Company health** is a quick check of the company's own SEC filings: "
           "*Solid* = growing or steady sales, profitable, reasonable debt; *Mixed* = one "
           "warning sign; *Shaky* = two or more; *Unknown* = ETFs, funds and foreign "
           "companies the SEC doesn't have numbers for. For a long-term account, start "
           "with the Solid ones.", ""]
    window = max(days, 30)
    agree = agreement(con, today, window)
    out += [f"## Where signals agree (last {window} days)", "",
            "Stocks with two or more different kinds of support: Congress buying, "
            "insiders buying, a top long-term fund holding it, or a big new government "
            "contract. These are the best starting points for research.", ""]
    out += (["None right now."] if not agree else
            [f"- **{t}**: " + "; ".join(why) + f". Health: {hp(t)}"
             for t, why in sorted(agree.items(), key=lambda kv: -len(kv[1]))])
    out.append("")

    if signals.has_table(con, "senate_trades"):
        cb = signals.congress_buys(con, start, today)
        out += ["## Congress buys ($15k+)", "",
                "Members of Congress (or their spouses) who disclosed buying a stock. "
                "They only report a dollar range, and up to 45 days after the trade, so "
                "check \"Days late\": the price may already have moved. A ⚑ means the "
                "member sits on a committee that oversees that company's industry "
                "(e.g. Armed Services and a defense contractor), which is when they're "
                "most likely to know something.", ""]
        # Filings years late (amendments, slow filers) are history, not news.
        stale = cb[cb.lag_days > MAX_LAG_DAYS]
        cb = cb[~(cb.lag_days > MAX_LAG_DAYS)]
        if cb.empty:
            out += ["None this period.", ""]
        else:
            # One line per person+ticker: a member often buys the same stock
            # several days running, which is one decision, not five.
            cb = cb.assign(who=cb.apply(_who, axis=1),
                           flag=cb.apply(lambda r: committees.committee_flag(
                               con, r.tkr, r.last_name, r.first_name, r.chamber), axis=1))
            g = (cb.groupby(["tkr", "who"], sort=False)
                 .agg(filed=("event_date", "max"), trades=("tkr", "size"),
                      lo=("amount_low", "sum"), hi=("amount_high", "sum"),
                      first=("txn_date", "min"), last=("txn_date", "max"),
                      lag=("lag_days", "min"), flag=("flag", "first"))
                 .reset_index().sort_values(["lo", "filed"], ascending=False))
            out += ["| Ticker | Who | Amount (total) | Traded | Days late | Their committee covers this industry | Company health |",
                    "|---|---|---|---|---|---|---|"]
            for r in g.itertuples():
                traded = _d(r.first) if _d(r.first) == _d(r.last) else f"{_d(r.first)} to {_d(r.last)}"
                n = f" ({r.trades} buys)" if r.trades > 1 else ""
                out.append(f"| **{r.tkr}** | {r.who} | {_bracket(r.lo, r.hi)}{n} | {traded} | "
                           f"{'' if r.lag != r.lag else int(r.lag)} | {('⚑ ' + r.flag) if r.flag else ''} | {hp(r.tkr)} |")
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
                [f"- **{t}**: {r.members} members ({r.names}), at least {_money(r.floor)}. "
                 f"Health: {hp(t)}"
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
                 f"{' (incl. an officer)' if r.any_officer else ''}. Buyers: {r.buyers}. "
                 f"Health: {hp(r.tkr)}"
                 for r in cl.itertuples()])
        out.append("")
        big = signals.big_insider_buys(con, start, today)
        out += ["## Single insider buys of $100k+", "",
                "One insider putting at least $100k of their own money in.", ""]
        out += (["None this period."] if big.empty else
                [f"- **{r.tkr}**{_co(r.issuer_name)}: {r.owner_name} "
                 f"[{(r.owner_relationship or '?').lower()}] bought {_money(r.total_value)}, "
                 f"filed {_d(r.event_date)}. Health: {hp(r.tkr)}"
                 for r in big.head(25).itertuples()])
        out.append("")
    else:
        out += ["_No insider data yet: set SEC_CONTACT_EMAIL in .env and run "
                "`py scrape_insiders.py --live --days 30`._", ""]

    c = contracts.recent(con, start, today)
    if c is not None:
        out += [f"## Big new government contracts (${contracts.MIN_AMOUNT / 1e6:.0f}M+)", "",
                "Public companies that just started a large federal contract. The amount "
                "is the total over the contract's whole life, often several years, so "
                "it matters most when it's big compared to the company's yearly sales.", ""]
        out += (["None this period."] if c.empty else
                [f"- **{r.tkr}**: {_money(r.amount)} from {r.agency}"
                 f"{' / ' + r.sub_agency if r.sub_agency and r.sub_agency != r.agency else ''}, "
                 f"{_d(r.start_date)} to {_d(r.end_date)}. {str(r.description or '').capitalize()[:90]}. "
                 f"Health: {hp(r.tkr)}" for r in c.head(20).itertuples()])
        out.append("")

    f = funds.changes(con, since=start)
    if f is not None and len(f):
        out += ["## What top funds changed (13F filings this period)", "",
                "Quarterly holdings reports from the long-term investors in `funds.txt`. "
                "These are filed up to 45 days after the quarter ends, so they show what "
                "the fund believed then, not today.", ""]
        label = {"new": "new position", "added": "added 10%+", "sold": "sold out"}
        for fund, g in f.groupby("fund"):
            bits = [f"{label[r.change]}: **{r.tkr or r.issuer}**"
                    f"{' (' + _money(r.value) + ')' if r.value == r.value and (r.value or 0) >= 1e3 else ''}"
                    for r in g.itertuples()]
            out.append(f"- **{fund}**, quarter ending {_d(g.period.iloc[0])}: " + "; ".join(bits))
        out.append("")
    return "\n".join(out)


def selftest():
    con = duckdb.connect()
    signals.make_fixture(con)
    health.ensure_table(con)
    con.execute("INSERT INTO health (tkr, checked_on, verdict, reasons) VALUES "
                "('AAA', '2025-03-30', 'Solid', 'sales up 12%, profitable')")
    text = build(con, datetime.date(2025, 3, 31), days=30)
    assert "Solid (sales up 12%, profitable)" in text
    assert "XYZ**: 3 insiders" in text and "Health: not checked" in text
    assert "**AAA**" in text and "Alpha" in text
    assert "**XYZ**" in text and "3 insiders" in text
    assert "**SEL**" in text, "a $200k single buy shows in the big-buys section"
    assert "Gamma" not in text, "sales never appear"
    assert "None right now." in text, "one kind of support isn't agreement"

    # now add a committee seat, a fund holding and a contract for AAA
    committees.load_fixture(con)
    con.execute("INSERT INTO committee_members VALUES ('A1','S','A',NULL,'Alpha','SSAS',"
                "'Senate Committee on Armed Services','2025-01-01')")
    funds.load_fixture(con)
    contracts.ensure_table(con)
    con.execute("INSERT INTO big_contracts VALUES ('k1','AAA INC','AAA','Department of Defense',"
                "'Department of the Navy','2025-03-20','2030-03-20',250e6,'MISSILES','2025-03-30')")
    text = build(con, datetime.date(2025, 5, 20), days=90)
    assert "⚑ Armed Services (defense)" in text, text
    assert "- **AAA**: Congress:" in text and "Contract: won $250.0M" in text \
        and "Funds: held by Berkshire (new)" in text, text
    assert "**Berkshire**, quarter ending 2025-03-31: new position: **AAA**" in text, text
    assert "**AAA**: $250.0M from Department of Defense / Department of the Navy" in text
    print(text)
    print("digest selftest ok")


def arg(flag, default=None):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


def main(days=7):
    today = datetime.date.today()
    con = duckdb.connect(DB_PATH)
    ensure_schema(con)
    tickers = digest_tickers(con, today, days)
    committees.ensure_flags_ready(con, tickers)
    try:
        health.refresh(con, tickers)
    except SystemExit as e:  # no SEC_CONTACT_EMAIL: digest still works
        print(f"health check skipped: {e}")
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
