"""The two signals this build cares about, as plain queries over the DB.

Shared by digest.py (what's new this week) and backtest.py (did it ever
work?), so both read exactly the same definition of a "signal".

Every signal is dated by when the public could first see it -- the filing
date -- never by when the trade happened. Using the trade date would quietly
hand the backtest information nobody had yet (look-ahead bias).

1. Congress buys: a member of Congress (or spouse/dependent) disclosed a
   purchase of a stock or ETF with a known ticker, at or above a dollar
   bracket floor (default $15,001, i.e. skip the smallest "$1,001-$15,000"
   bracket, which is mostly noise and auto-investing).

2. Insider cluster buys: at least N different insiders (default 3) made
   open-market purchases (Form 4 code "P") of the same company's stock, with
   all of those filings landing inside a trailing window (default 30 days),
   adding up to at least $50k (drops token buys like dividend reinvestment).
   Known false positive: a small bank converting from mutual to stock form
   shows up as a big cluster, because its directors all buy at the IPO.
   Open-market buys are the one insider action that is almost always a
   deliberate bet with personal money; sales, grants and option exercises
   are mostly compensation mechanics and are ignored.
"""

import datetime

CONGRESS_BUYS = """
SELECT filed_date AS event_date, tkr, chamber, last_name, first_name, owner,
       asset_name, amount_low, amount_high, txn_date, lag_days
FROM trades
WHERE tx_type = 'Purchase'
  AND tkr IS NOT NULL
  AND (asset_type IS NULL OR asset_type ILIKE 'stock%'
       OR asset_type ILIKE 'exchange traded%')
  AND amount_low >= ?
  AND filed_date BETWEEN ? AND ?
ORDER BY filed_date DESC, amount_low DESC
"""

# Open-market purchases with a usable ticker and price.
_INSIDER_BUYS = """
SELECT upper(trim(ticker)) AS tkr, issuer_name, owner_cik, owner_name,
       owner_relationship, try_cast(filed_date AS DATE) AS fd,
       shares * price_per_share AS value
FROM insider_trades
WHERE trans_code = 'P' AND acquired_disposed = 'A'
  AND ticker IS NOT NULL AND upper(trim(ticker)) NOT IN ('', 'NONE', 'N/A', 'NA')
  AND price_per_share > 0 AND shares > 0
"""

# For every (ticker, day with a buy), look back `window` days and count the
# distinct insiders who bought. Python then keeps the first qualifying day of
# each burst, so one cluster is one event, not one event per extra filing.
INSIDER_CLUSTER_DAYS = f"""
WITH p AS ({_INSIDER_BUYS}),
d AS (SELECT DISTINCT tkr, fd FROM p WHERE fd BETWEEN ? AND ?)
SELECT d.tkr, d.fd AS event_date,
       count(DISTINCT p.owner_cik) AS insiders,
       sum(p.value) AS total_value,
       min(p.issuer_name) AS issuer_name,
       string_agg(DISTINCT p.owner_name, '; ') AS buyers,
       bool_or(coalesce(p.owner_relationship, '') LIKE '%OFFICER%') AS any_officer
FROM d JOIN p ON p.tkr = d.tkr
     AND p.fd BETWEEN d.fd - to_days(CAST(? AS INTEGER)) AND d.fd
GROUP BY d.tkr, d.fd
HAVING count(DISTINCT p.owner_cik) >= ? AND sum(p.value) >= ?
ORDER BY d.tkr, d.fd
"""

BIG_INSIDER_BUYS = f"""
WITH p AS ({_INSIDER_BUYS})
SELECT fd AS event_date, tkr, issuer_name, owner_name, owner_relationship,
       sum(value) AS total_value
FROM p WHERE fd BETWEEN ? AND ?
GROUP BY ALL HAVING sum(value) >= ?
ORDER BY total_value DESC
"""


def _df(con, sql, params):
    return con.execute(sql, params).df()


def congress_buys(con, start, end, min_amount=15001):
    return _df(con, CONGRESS_BUYS, [min_amount, start, end])


def insider_clusters(con, start, end, window=30, min_insiders=3, min_total=50_000):
    """One row per cluster: the first day N+ distinct insiders had bought
    within `window` days. Lookback reaches `window` days before `start` so a
    cluster that began just before the range is still counted correctly."""
    lookback = start - datetime.timedelta(days=window)
    df = _df(con, INSIDER_CLUSTER_DAYS, [lookback, end, window, min_insiders, min_total])
    keep, last = [], {}
    for i, row in df.iterrows():
        prev = last.get(row.tkr)
        d = row.event_date.date() if hasattr(row.event_date, "date") else row.event_date
        if prev is None or (d - prev).days > window:
            keep.append(i)
        last[row.tkr] = d
    out = df.loc[keep]
    return out[out.event_date.apply(lambda x: (x.date() if hasattr(x, "date") else x) >= start)] \
        .reset_index(drop=True)


def big_insider_buys(con, start, end, min_value=100_000):
    return _df(con, BIG_INSIDER_BUYS, [start, end, min_value])


def has_table(con, name):
    return con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
        [name]).fetchone()[0] > 0


# ------------------------------------------------------------------ selftest

def make_fixture(con):
    """Tiny in-memory DB with known answers -- used by every selftest."""
    from schema import ensure_schema
    ensure_schema(con)
    con.execute("""INSERT INTO senate_trades (first_name, last_name, filed, tx_date,
        ticker, asset_name, asset_type, tx_type, amount_low, amount_high) VALUES
        ('A','Alpha','03/10/2025','03/01/2025','AAA','Aaa Inc (AAA)','Stock','Purchase',15001,50000),
        ('B','Beta','03/12/2025','03/02/2025','AAA','Aaa Inc (AAA)','Stock','Purchase',1001,15000),
        ('C','Gamma','03/15/2025','02/20/2025','BBB','Bbb Inc','Stock','Sale (Full)',50001,100000),
        ('D','Delta','02/09/2025','12/26/2025','CCC','Ccc Inc','Stock','Purchase',100001,250000),
        ('E','Eps','03/11/2025','03/01/2025','--','Muni bond','Municipal Security','Purchase',50001,100000)""")
    con.execute("""CREATE TABLE insider_trades (accession_number VARCHAR, trans_seq INTEGER,
        filed_date VARCHAR, trans_date VARCHAR, issuer_cik VARCHAR, issuer_name VARCHAR,
        ticker VARCHAR, owner_cik VARCHAR, owner_name VARCHAR, owner_relationship VARCHAR,
        security_title VARCHAR, trans_code VARCHAR, acquired_disposed VARCHAR,
        shares DOUBLE, price_per_share DOUBLE, shares_owned_following DOUBLE)""")
    rows = [  # XYZ: 3 insiders inside 30 days -> one cluster on 2025-03-20
        ("a1", "2025-03-01", "XYZ", "1", "Ann", "OFFICER", "P", "A", 3000, 10),
        ("a2", "2025-03-10", "XYZ", "2", "Bob", "DIRECTOR", "P", "A", 1000, 10),
        ("a3", "2025-03-20", "XYZ", "3", "Cy", "DIRECTOR", "P", "A", 1000, 10),
        ("a4", "2025-03-25", "XYZ", "4", "Di", "DIRECTOR", "P", "A", 1000, 10),   # same burst
        # TNY: 3 insiders but only $3k together -> token buys, no cluster
        ("t1", "2025-03-02", "TNY", "11", "Ka", "DIRECTOR", "P", "A", 100, 10),
        ("t2", "2025-03-03", "TNY", "12", "Lu", "DIRECTOR", "P", "A", 100, 10),
        ("t3", "2025-03-04", "TNY", "13", "Mo", "DIRECTOR", "P", "A", 100, 10),
        # QQQ-like: 3 insiders but spread over 90 days -> no cluster
        ("b1", "2025-01-01", "SPR", "5", "Ed", "DIRECTOR", "P", "A", 100, 10),
        ("b2", "2025-02-15", "SPR", "6", "Fi", "DIRECTOR", "P", "A", 100, 10),
        ("b3", "2025-04-01", "SPR", "7", "Gu", "DIRECTOR", "P", "A", 100, 10),
        # sales and grants never count
        ("c1", "2025-03-05", "SEL", "8", "Ha", "OFFICER", "S", "D", 9e6, 10),
        ("c2", "2025-03-06", "SEL", "9", "Io", "OFFICER", "A", "A", 9e6, 0),
        ("c3", "2025-03-07", "SEL", "10", "Jo", "OFFICER", "P", "A", 20000, 10),
    ]
    con.executemany("""INSERT INTO insider_trades (accession_number, trans_seq, filed_date,
        ticker, owner_cik, owner_name, owner_relationship, trans_code, acquired_disposed,
        shares, price_per_share) VALUES (?,0,?,?,?,?,?,?,?,?,?)""", rows)


def selftest():
    import duckdb
    con = duckdb.connect()
    make_fixture(con)
    D = datetime.date
    cb = congress_buys(con, D(2025, 1, 1), D(2025, 12, 31))
    assert list(cb.tkr) == ["AAA", "CCC"], cb
    assert "BBB" not in set(cb.tkr), "sales are not buys"
    assert len(cb[cb.tkr == "AAA"]) == 1, "$1,001-$15,000 bracket is filtered out"
    assert len(congress_buys(con, D(2025, 1, 1), D(2025, 12, 31), min_amount=1001)) == 3
    sus = con.execute("SELECT tkr FROM trades WHERE txn_date_suspect").fetchall()
    assert sus == [("CCC",)], sus

    cl = insider_clusters(con, D(2025, 1, 1), D(2025, 12, 31))
    assert list(cl.tkr) == ["XYZ"], cl
    assert str(cl.event_date[0])[:10] == "2025-03-20", cl
    assert cl.insiders[0] == 3 and bool(cl.any_officer[0])
    # a range starting after the cluster's first day must not re-emit it
    assert insider_clusters(con, D(2025, 3, 21), D(2025, 12, 31)).empty

    big = big_insider_buys(con, D(2025, 1, 1), D(2025, 12, 31), min_value=100_000)
    assert list(big.tkr) == ["SEL"], big
    print("signals selftest ok")


if __name__ == "__main__":
    selftest()
