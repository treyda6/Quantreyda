"""Email me when something happens on a stock I own or watch.

Reads tickers from watchlist.txt. Looks at filings from the last 14 days
and alerts on anything not already sent:
  - a member of Congress bought or sold it (any amount)
  - an insider bought it on the open market
  - an insider sold $50k+ of it (one insider, one day). Insider sales are
    often routine (taxes, diversifying, pre-scheduled plans), so a single
    sale isn't a sell signal -- but several insiders selling at once is
    worth a look.

    py alerts.py              # check now, email anything new
    py alerts.py --selftest

Each alert is remembered in the `alerts_sent` table so you only hear about
it once. Without email set up (see mailer.py) alerts go to
reports/alerts-YYYY-MM-DD.md instead. daily.py runs this every day.
"""

import datetime
import os
import sys

import duckdb

import mailer
import signals
from schema import DB_PATH, ensure_schema
import fmt

LOOKBACK_DAYS = 14
MIN_INSIDER_SALE = 50_000


def read_watchlist(path="watchlist.txt"):
    """Tickers from the WATCHLIST environment variable (comma/space separated;
    how the GitHub run gets it, from a private repo secret) or watchlist.txt."""
    env = os.environ.get("WATCHLIST", "").replace(",", " ").split()
    if env:
        return sorted({t.strip().upper() for t in env})
    if not os.path.exists(path):
        return []
    out = []
    for line in open(path, encoding="utf-8"):
        t = line.split("#", 1)[0].strip().upper()
        if t:
            out.append(t)
    return sorted(set(out))


def ensure_table(con):
    con.execute("CREATE TABLE IF NOT EXISTS alerts_sent (key VARCHAR PRIMARY KEY, sent_on DATE)")


_money = fmt.money


def find_events(con, tickers, start, end):
    """[(key, ticker, sentence)] for every watchlist event filed in range."""
    if not tickers:
        return []
    ev = []
    if signals.has_table(con, "senate_trades"):
        rows = con.execute("""
            SELECT tkr, chamber, first_name, last_name, owner, tx_type,
                   min(amount_low), sum(amount_low), sum(amount_high), filed_date,
                   min(txn_date), count(*)
            FROM trades WHERE tkr IN (SELECT unnest(?)) AND filed_date BETWEEN ? AND ?
            GROUP BY ALL ORDER BY filed_date""", [tickers, start, end]).fetchall()
        for tkr, ch, fn, ln, owner, tx, _, lo, hi, filed, traded, n in rows:
            ln = (ln or "").strip(" ,.")
            who = f"{fn or ''} {ln}".strip() + f" ({'Senate' if ch == 'S' else 'House'})"
            if owner and str(owner).lower() not in ("self", "none"):
                who += f", {str(owner).lower()}"
            verb = "bought" if tx == "Purchase" else "sold" if "Sale" in (tx or "") else "exchanged"
            amt = f"{_money(lo)}+" if hi is None else f"{_money(lo)}–{_money(hi)}"
            late = (filed - traded).days if filed and traded else 0
            ev.append((f"c|{tkr}|{ln}|{tx}|{filed}|{owner}", tkr,
                       f"**{tkr}**: {who} {verb} {amt}{f' in {n} trades' if n > 1 else ''}, "
                       f"traded {str(traded)[:10]}, disclosed {str(filed)[:10]}"
                       + (f" (**{late} days late**, old news)." if late > 90 else ".")))
    if signals.has_table(con, "insider_trades"):
        rows = con.execute("""
            SELECT upper(trim(ticker)) AS tkr, owner_name, owner_relationship, trans_code,
                   try_cast(filed_date AS DATE) AS fd, sum(shares * price_per_share) AS v
            FROM insider_trades
            WHERE upper(trim(ticker)) IN (SELECT unnest(?))
              AND try_cast(filed_date AS DATE) BETWEEN ? AND ?
              AND trans_code IN ('P', 'S') AND price_per_share > 0
            GROUP BY ALL ORDER BY fd""", [tickers, start, end]).fetchall()
        for tkr, owner, rel, code, fd, v in rows:
            if code == "S" and (v or 0) < MIN_INSIDER_SALE:
                continue
            role = (rel or "insider").lower().replace(",", ", ")
            verb = "bought" if code == "P" else "sold"
            ev.append((f"i|{tkr}|{owner}|{code}|{fd}", tkr,
                       f"**{tkr}**: {owner} ({role}) {verb} {_money(v or 0)} of stock, "
                       f"filed {str(fd)[:10]}."))
    return ev


def build(con, tickers, today):
    ensure_table(con)
    start = today - datetime.timedelta(days=LOOKBACK_DAYS)
    sent = {r[0] for r in con.execute("SELECT key FROM alerts_sent").fetchall()}
    new = [e for e in find_events(con, tickers, start, today) if e[0] not in sent]
    if not new:
        return None, []
    lines = [f"# Watchlist alerts, {today}", "",
             "New filings on stocks you own or watch:", ""]
    lines += [f"- {text}" for _, _, text in new]
    lines += ["", "_Buys by insiders are a good sign more often than sales are a bad one: "
                  "insiders sell for lots of personal reasons. Several selling at once, "
                  "or a CEO selling most of their stake, is the kind worth digging into._"]
    return "\n".join(lines), [k for k, _, _ in new]


def mark_sent(con, keys, today):
    con.executemany("INSERT OR IGNORE INTO alerts_sent VALUES (?, ?)", [(k, today) for k in keys])


def main():
    tickers = read_watchlist()
    if not tickers:
        print("watchlist.txt is empty; add tickers to get alerts")
        return
    today = datetime.date.today()
    con = duckdb.connect(DB_PATH)
    ensure_schema(con)
    text, keys = build(con, tickers, today)
    if not text:
        print(f"no new filings on your {len(tickers)} watchlist tickers")
        return
    print(text)
    if mailer.send(f"Watchlist: {len(keys)} new filing{'s' * (len(keys) > 1)}", text):
        print("emailed")
    else:
        os.makedirs("reports", exist_ok=True)
        path = f"reports/alerts-{today}.md"
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n\n")
        print(f"email not set up; saved to {path}")
    mark_sent(con, keys, today)


def selftest():
    con = duckdb.connect()
    signals.make_fixture(con)
    con.execute("""INSERT INTO insider_trades (accession_number, trans_seq, filed_date, ticker,
        owner_name, owner_relationship, trans_code, acquired_disposed, shares, price_per_share)
        VALUES ('s1', 0, '2025-03-12', 'AAA', 'Big Seller', 'OFFICER', 'S', 'D', 10000, 10),
               ('s2', 0, '2025-03-12', 'AAA', 'Small Seller', 'DIRECTOR', 'S', 'D', 10, 10)""")
    today = datetime.date(2025, 3, 20)
    text, keys = build(con, ["AAA", "XYZ"], today)
    assert "Alpha (Senate) bought $15k–$50k" in text, text
    assert "Beta (Senate) bought" in text, "watchlist alerts include small buys"
    assert "Big Seller (officer) sold $100k" in text, text
    assert "Small Seller" not in text, "sales under $50k are skipped"
    assert "**XYZ**: Cy (director) bought" in text, text
    mark_sent(con, keys, today)
    assert build(con, ["AAA", "XYZ"], today) == (None, []), "already-sent alerts never repeat"
    assert build(con, [], today) == (None, [])

    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("# comment\naapl  # owned\n\nMSFT\nAAPL\n")
    assert read_watchlist(f.name) == ["AAPL", "MSFT"]
    os.environ["WATCHLIST"] = "vti, msft  aapl"
    try:
        assert read_watchlist(f.name) == ["AAPL", "MSFT", "VTI"], "env var wins over the file"
    finally:
        del os.environ["WATCHLIST"]
    os.unlink(f.name)
    print(text)
    print("alerts selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
