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
    # Fresh = fetched lately AND already reaching back to `start` (a quick
    # 10-day top-up for the health check mustn't stop a 2-year chart fetch).
    # A ticker fetched lately with no rows at all is delisted: also skip.
    fresh = {r[0] for r in con.execute("""
        SELECT f.tkr FROM price_fetches f
        LEFT JOIN (SELECT tkr, min(d) AS first_d FROM prices GROUP BY tkr) p USING (tkr)
        WHERE f.fetched_on >= ? AND (p.first_d IS NULL OR p.first_d <= ?)""",
        [today - datetime.timedelta(days=refresh_days),
         start + datetime.timedelta(days=10)]).fetchall()}
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


def selftest():
    import pandas as pd
    global _download
    real, calls = _download, []

    def fake(tickers, start):
        calls.append((tuple(tickers), start))
        idx = pd.bdate_range(start, periods=5)
        return {t: pd.Series([1.0] * 5, index=idx) for t in tickers}
    _download = fake
    try:
        con = duckdb.connect()
        today = datetime.date.today()
        fetch(con, ["AAA"], start=today - datetime.timedelta(days=10))  # health-style top-up
        fetch(con, ["AAA"], start=today - datetime.timedelta(days=740))  # chart needs 2 years
        assert len(calls) == 2, "a short top-up must not block a longer history fetch"
        fetch(con, ["AAA"], start=today - datetime.timedelta(days=740))
        assert len(calls) == 2, "already covered today: no refetch"
    finally:
        _download = real
    print("prices selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
        sys.exit()
    c = duckdb.connect(DB_PATH)
    fetch(c, sys.argv[1:] or [BENCHMARK], refresh_days=0)
    print(c.execute("SELECT tkr, count(*), min(d), max(d) FROM prices GROUP BY 1").df())
