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


TOPUP_DAYS = 40  # a ticker we already hold history for only needs its last few weeks


def _have(con):
    """{tkr: (first_d, last_d)} for every ticker with stored prices."""
    return {t: (a, b) for t, a, b in con.execute(
        "SELECT tkr, min(d), max(d) FROM prices GROUP BY tkr").fetchall()}


def _matches(con, t, s):
    """Do freshly downloaded closes agree with the stored ones on shared days?
    Yahoo's adjusted closes shift after a split or dividend; if they moved,
    the old history must be refetched or returns would jump at the seam."""
    old = dict(con.execute("SELECT d, close FROM prices WHERE tkr = ? AND d >= ?",
                           [t, s.index[0].date()]).fetchall())
    shared = [(old[i.date()], float(v)) for i, v in s.items() if i.date() in old]
    return all(abs(a - b) <= 0.02 * max(abs(a), 1e-9) for a, b in shared[-5:])


def _store(con, t, s, today):
    n = 0
    if s is not None and len(s):
        rows = [(t, idx.date(), float(v)) for idx, v in s.items()]
        con.executemany("INSERT OR REPLACE INTO prices VALUES (?,?,?)", rows)
        n = len(rows)
    con.execute("INSERT OR REPLACE INTO price_fetches VALUES (?,?,?)", [t, today, n])


def fetch(con, tickers, start=datetime.date(2014, 1, 1), batch=50, refresh_days=1,
          minutes=None):
    """Make sure `prices` covers each ticker from `start` to about today.

    Tickers that already reach back to `start` only download their last
    TOPUP_DAYS, so a weekly run takes minutes instead of re-downloading years
    for every ticker. `minutes` caps the time spent; whatever is left over is
    fetched next run (the caller works with what's cached)."""
    ensure_tables(con)
    today = datetime.date.today()
    stop = time.monotonic() + minutes * 60 if minutes else None
    # Fresh = fetched lately AND already reaching back to `start` (a quick
    # 10-day top-up for the health check mustn't stop a 2-year chart fetch).
    # A ticker fetched lately with no rows at all is delisted: also skip.
    fresh = {r[0] for r in con.execute("""
        SELECT f.tkr FROM price_fetches f
        LEFT JOIN (SELECT tkr, min(d) AS first_d FROM prices GROUP BY tkr) p USING (tkr)
        WHERE (f.fetched_on >= ? AND (p.first_d IS NULL OR p.first_d <= ?))
           OR (f.n_rows = 0 AND f.fetched_on >= ?)""",
        [today - datetime.timedelta(days=refresh_days),
         start + datetime.timedelta(days=10),
         today - datetime.timedelta(days=30)]).fetchall()}  # delisted: retry monthly
    todo = sorted({t.upper() for t in tickers} - fresh)
    have = _have(con)
    recent = today - datetime.timedelta(days=TOPUP_DAYS - 10)
    topup_start = today - datetime.timedelta(days=TOPUP_DAYS)
    topup = [t for t in todo if t in have and have[t][0] <= start + datetime.timedelta(days=10)
             and have[t][1] >= recent and topup_start > start]
    full = [t for t in todo if t not in set(topup)]
    redo = []
    for label, group, since in (("top-up", topup, topup_start), ("history", full, start),
                                ("refetch", redo, start)):
        for i in range(0, len(group), batch):
            if stop and time.monotonic() > stop:
                print(f"prices: out of time; {len(group) - i} {label} tickers left for next run",
                      flush=True)
                return
            chunk = group[i:i + batch]
            print(f"prices: {label} {i + 1}-{i + len(chunk)} of {len(group)}", flush=True)
            got = _download(chunk, since)
            for t, s in got.items():
                if label == "top-up" and s is not None and len(s) and not _matches(con, t, s):
                    redo.append(t)  # split or dividend re-adjusted the history
                    continue
                _store(con, t, s, today)
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
        # a week later: only the last few weeks are downloaded
        con.execute("UPDATE price_fetches SET fetched_on = fetched_on - 7")
        con.execute("DELETE FROM prices WHERE d > ?", [today - datetime.timedelta(days=20)])
        con.execute("INSERT INTO prices VALUES ('AAA', ?, 1.0)", [today - datetime.timedelta(days=25)])
        last_start = lambda: calls[-1][1]
        fetch(con, ["AAA"], start=today - datetime.timedelta(days=740))
        assert last_start() == today - datetime.timedelta(days=TOPUP_DAYS), last_start()
        # a split re-adjusts history: the top-up disagrees, so refetch it all
        con.execute("UPDATE price_fetches SET fetched_on = fetched_on - 7")
        con.execute("UPDATE prices SET close = 2.0")
        n = len(calls)
        fetch(con, ["AAA"], start=today - datetime.timedelta(days=740))
        assert len(calls) == n + 2 and last_start() == today - datetime.timedelta(days=740)
        # out of time: nothing downloaded, no crash
        n = len(calls)
        fetch(con, ["BBB"], start=today - datetime.timedelta(days=740), minutes=-1)
        assert len(calls) == n
        # a ticker Yahoo doesn't know isn't asked again for a month
        _download = lambda tickers, start: (calls.append(1), {t: None for t in tickers})[1]
        fetch(con, ["DEAD"], start=today - datetime.timedelta(days=740))
        con.execute("UPDATE price_fetches SET fetched_on = fetched_on - 7 WHERE tkr = 'DEAD'")
        n = len(calls)
        fetch(con, ["DEAD"], start=today - datetime.timedelta(days=740))
        assert len(calls) == n, "delisted ticker re-requested"
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
