"""Daily closing prices, cached in the DB so a backtest re-run is offline.

Source is Yahoo Finance through the `yfinance` package: free and no key, but
unofficial -- it can rate-limit or change without notice, and it has no
prices for delisted tickers (so returns skew a little optimistic:
companies that went bust drop out of the sample). Good enough for a personal
"is there anything here?" check; not good enough to bet a career on.

    py prices.py AAPL MSFT SPY       # fetch/refresh a few tickers
"""

import datetime
import sys
import time

import duckdb

from schema import DB_PATH

BENCHMARK = "SPY"


def ensure_tables(con):
    con.execute("""CREATE TABLE IF NOT EXISTS prices (
        tkr VARCHAR, d DATE, close DOUBLE, PRIMARY KEY (tkr, d))""")
    # One row per ticker we've asked Yahoo about, so a ticker it doesn't know
    # isn't re-requested on every run.
    con.execute("""CREATE TABLE IF NOT EXISTS price_fetches (
        tkr VARCHAR PRIMARY KEY, fetched_on DATE, n_rows INTEGER)""")


def yahoo_symbol(tkr):
    """Filings write class shares as BRK.B / BRK/B; Yahoo wants BRK-B."""
    return tkr.replace(".", "-").replace("/", "-")


def _download(tickers, start):
    import yfinance as yf  # lazy: selftests and the digest don't need it
    data = yf.download([yahoo_symbol(t) for t in tickers], start=start.isoformat(),
                       auto_adjust=True, progress=False, threads=True,
                       group_by="ticker")
    out = {}
    for t in tickers:
        try:
            s = data[yahoo_symbol(t)]["Close"] if len(tickers) > 1 else data["Close"]
            if hasattr(s, "columns"):  # newer yfinance returns a 1-col frame
                s = s.iloc[:, 0]
            s = s.dropna()
        except KeyError:
            s = None
        out[t] = s
    return out


def fetch(con, tickers, start=datetime.date(2014, 1, 1), batch=50, refresh_days=1):
    """Make sure `prices` covers each ticker from `start` to about today."""
    ensure_tables(con)
    today = datetime.date.today()
    fresh = {r[0] for r in con.execute(
        "SELECT tkr FROM price_fetches WHERE fetched_on >= ?",
        [today - datetime.timedelta(days=refresh_days)]).fetchall()}
    todo = sorted({t.upper() for t in tickers} - fresh)
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        print(f"prices: fetching {i + 1}-{i + len(chunk)} of {len(todo)}")
        got = _download(chunk, start)
        for t, s in got.items():
            n = 0
            if s is not None and len(s):
                rows = [(t, idx.date(), float(v)) for idx, v in s.items()]
                con.executemany("INSERT OR REPLACE INTO prices VALUES (?,?,?)", rows)
                n = len(rows)
            con.execute("INSERT OR REPLACE INTO price_fetches VALUES (?,?,?)", [t, today, n])
        time.sleep(1)  # be gentle; Yahoo rate-limits bursts


if __name__ == "__main__":
    c = duckdb.connect(DB_PATH)
    fetch(c, sys.argv[1:] or [BENCHMARK], refresh_days=0)
    print(c.execute("SELECT tkr, count(*), min(d), max(d) FROM prices GROUP BY 1").df())
