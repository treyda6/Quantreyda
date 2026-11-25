"""Basic health check for a company, from its own SEC filings.

The point: insiders and politicians buy shaky companies too. Before treating
a buy as an idea for a long-term account, check that the business underneath
is growing, profitable, not drowning in debt, and not printing new shares.

Data: SEC's free XBRL "company facts" API -- the numbers companies report in
their 10-K / 10-Q filings, machine-readable. No key, but the same contact
email as the Form 4 scraper (SEC_CONTACT_EMAIL).

    py health.py AAPL INTC SGI      # check a few tickers, print plain-English verdicts
    py health.py --selftest

Results are cached in the `health` table for 7 days. Verdicts:
    Solid    -- no red flags
    Mixed    -- one red flag; read why before buying
    Shaky    -- two or more red flags
    Unknown  -- not enough data (foreign filers, funds/ETFs, brand-new listings)

Red flags (each explained in the output):
    revenue shrank more than 5% over the last fiscal year
    lost money over the last fiscal year
    debt more than 2x shareholders' equity (or equity below zero)
    share count up more than 5% in a year (your slice is being diluted)
    price/earnings above 40 (priced for perfection)

These are blunt rules of thumb, not analysis. Banks, utilities and REITs
normally carry lots of debt; young growth companies normally lose money.
"""

import datetime
import sys
import time

import duckdb
import requests

from schema import DB_PATH, sec_user_agent

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
CACHE_DAYS = 7

REVENUE = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
           "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax"]
NET_INCOME = ["NetIncomeLoss", "ProfitLoss"]
EQUITY = ["StockholdersEquity",
          "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]
DEBT_PARTS = [["LongTermDebt"], ["LongTermDebtNoncurrent", "LongTermDebtCurrent"]]
CASH = ["CashAndCashEquivalentsAtCarryingValue"]
SHARES = ["EntityCommonStockSharesOutstanding"]  # dei, cover page


def ensure_table(con):
    con.execute("""CREATE TABLE IF NOT EXISTS health (
        tkr VARCHAR PRIMARY KEY, checked_on DATE, company VARCHAR,
        fiscal_year_end DATE, revenue DOUBLE, revenue_growth DOUBLE,
        net_income DOUBLE, debt DOUBLE, equity DOUBLE, cash DOUBLE,
        share_growth DOUBLE, pe DOUBLE, verdict VARCHAR, reasons VARCHAR)""")


# ----------------------------------------------------------------- parsing

def _units(facts, concept, unit="USD"):
    for ns in ("us-gaap", "dei"):
        node = facts.get("facts", {}).get(ns, {}).get(concept)
        if node and unit in node.get("units", {}):
            return node["units"][unit]
    return []


def _days(a, b):
    return (datetime.date.fromisoformat(b) - datetime.date.fromisoformat(a)).days


def annual_series(facts, concepts):
    """{fiscal period end: value} for full-year figures from annual reports.
    Tries each concept in order and merges, first concept winning on ties --
    companies switch revenue tags over the years (e.g. after ASC 606)."""
    out = {}
    for c in concepts:
        rows = [r for r in _units(facts, c)
                if r.get("form") in ("10-K", "10-K/A", "20-F", "40-F")
                and r.get("start") and 350 <= _days(r["start"], r["end"]) <= 380]
        rows.sort(key=lambda r: r.get("filed", ""))  # later filings (restated) win
        found = {}
        for r in rows:
            found[r["end"]] = r["val"]
        for end, v in found.items():
            out.setdefault(end, v)
    return dict(sorted(out.items()))


def latest_instant(facts, concepts, unit="USD"):
    """(end, value) of the most recent point-in-time figure (balance sheet)."""
    best = None
    for c in concepts:
        for r in _units(facts, c, unit):
            if "start" in r:
                continue
            key = (r["end"], r.get("filed", ""))
            if best is None or key > best[0]:
                best = (key, r["val"])
    return (best[0][0], best[1]) if best else (None, None)


def instant_year_ago(facts, concepts, end, unit="USD"):
    """Value closest to one year before `end` (within 45 days), or None."""
    if not end:
        return None
    target = datetime.date.fromisoformat(end) - datetime.timedelta(days=365)
    best = None
    for c in concepts:
        for r in _units(facts, c, unit):
            if "start" in r:
                continue
            gap = abs((datetime.date.fromisoformat(r["end"]) - target).days)
            if gap <= 45 and (best is None or gap < best[0]):
                best = (gap, r["val"])
    return best[1] if best else None


def debt_total(facts):
    for parts in DEBT_PARTS:
        vals = [latest_instant(facts, [p])[1] for p in parts]
        if any(v is not None for v in vals):
            return sum(v for v in vals if v is not None)
    return None


def analyze(facts, price=None):
    """Pure: company facts JSON (+ optional share price) -> metrics + verdict."""
    rev = annual_series(facts, REVENUE)
    ni = annual_series(facts, NET_INCOME)
    m = {"company": facts.get("entityName")}
    ends = sorted(rev)
    m["fiscal_year_end"] = ends[-1] if ends else (sorted(ni)[-1] if ni else None)
    m["revenue"] = rev[ends[-1]] if ends else None
    m["revenue_growth"] = None
    if len(ends) >= 2 and rev[ends[-2]] and 300 <= _days(ends[-2], ends[-1]) <= 430:
        m["revenue_growth"] = rev[ends[-1]] / rev[ends[-2]] - 1
    m["net_income"] = ni[sorted(ni)[-1]] if ni else None
    m["equity"] = latest_instant(facts, EQUITY)[1]
    m["debt"] = debt_total(facts)
    m["cash"] = latest_instant(facts, CASH)[1]
    s_end, shares = latest_instant(facts, SHARES, unit="shares")
    prev = instant_year_ago(facts, SHARES, s_end, unit="shares")
    m["share_growth"] = shares / prev - 1 if shares and prev else None
    m["pe"] = None
    if price and shares and m["net_income"] and m["net_income"] > 0:
        m["pe"] = price * shares / m["net_income"]

    reasons = []
    g = m["revenue_growth"]
    if g is not None and g < -0.05:
        reasons.append(f"sales shrank {abs(g):.0%} last year")
    if m["net_income"] is not None and m["net_income"] < 0:
        reasons.append("lost money last year")
    eq, debt = m["equity"], m["debt"]
    if eq is not None and eq <= 0:
        reasons.append("owes more than it owns (negative equity)")
    elif eq and debt is not None and debt / eq > 2:
        reasons.append(f"heavy debt ({debt / eq:.1f}x equity)")
    if m["share_growth"] is not None and m["share_growth"] > 0.05:
        reasons.append(f"share count up {m['share_growth']:.0%} in a year (dilution)")
    if m["pe"] is not None and m["pe"] > 40:
        reasons.append(f"expensive (P/E {m['pe']:.0f})")

    known = sum(x is not None for x in (m["revenue"], m["net_income"], eq))
    if known < 2:
        m["verdict"], reasons = "Unknown", ["not enough SEC data (fund, ETF, foreign or new company?)"]
    else:
        m["verdict"] = ("Solid", "Mixed")[len(reasons)] if len(reasons) < 2 else "Shaky"
    if m["verdict"] == "Solid":
        bits = []
        if g is not None:
            bits.append(f"sales {'up' if g >= 0 else 'down'} {abs(g):.0%}")
        if m["net_income"] is not None:
            bits.append("profitable")
        if m["pe"] is not None:
            bits.append(f"P/E {m['pe']:.0f}")
        reasons = [", ".join(bits) or "no red flags"]
    m["reasons"] = "; ".join(reasons)
    return m


def plain(m):
    """One line a beginner can read."""
    return f"{m['verdict']}: {m['reasons']}"


# --------------------------------------------------------------- networking

def _session():
    s = requests.Session()
    s.headers["User-Agent"] = sec_user_agent()
    return s


def ticker_ciks(s):
    data = s.get(TICKERS_URL, timeout=30).json()
    return {v["ticker"].upper(): (int(v["cik_str"]), v["title"]) for v in data.values()}


def latest_price(con, tkr):
    try:
        r = con.execute("SELECT close FROM prices WHERE tkr = ? ORDER BY d DESC LIMIT 1",
                        [tkr]).fetchone()
        return r[0] if r else None
    except duckdb.CatalogException:
        return None


def refresh(con, tickers, force=False):
    """Check each ticker unless checked in the last CACHE_DAYS. Returns
    {tkr: row dict} for every requested ticker that has a result."""
    ensure_table(con)
    tickers = sorted({t.upper() for t in tickers if t})
    today = datetime.date.today()
    fresh = set() if force else {r[0] for r in con.execute(
        "SELECT tkr FROM health WHERE checked_on >= ?",
        [today - datetime.timedelta(days=CACHE_DAYS)]).fetchall()}
    todo = [t for t in tickers if t not in fresh]
    if todo:
        try:  # a current price lets us compute P/E; optional, so never fatal
            import prices
            prices.fetch(con, todo, start=today - datetime.timedelta(days=10))
        except Exception as e:
            print(f"health: no prices this run ({type(e).__name__}); P/E skipped")
        s = _session()
        ciks = ticker_ciks(s)
        for t in todo:
            # filings write BRK.B; SEC's list writes BRK-B
            hit = ciks.get(t) or ciks.get(t.replace(".", "-")) or ciks.get(t.replace("/", "-"))
            if not hit:
                m = {"company": None, "verdict": "Unknown",
                     "reasons": "not in SEC's company list (ETF, fund or foreign?)"}
            else:
                time.sleep(0.15)  # SEC asks for <= 10 requests/sec
                r = s.get(FACTS_URL.format(cik=hit[0]), timeout=60)
                if r.status_code != 200:
                    m = {"company": hit[1], "verdict": "Unknown",
                         "reasons": "no financial data filed with SEC"}
                else:
                    m = analyze(r.json(), latest_price(con, t))
                    m["company"] = m.get("company") or hit[1]
            cols = ["company", "fiscal_year_end", "revenue", "revenue_growth", "net_income",
                    "debt", "equity", "cash", "share_growth", "pe", "verdict", "reasons"]
            con.execute(f"INSERT OR REPLACE INTO health (tkr, checked_on, {', '.join(cols)}) "
                        f"VALUES (?, ?, {', '.join('?' * len(cols))})",
                        [t, today] + [m.get(c) for c in cols])
    return lookup(con, tickers)


def lookup(con, tickers):
    """Cached results only, no network. {} if the table doesn't exist yet."""
    try:
        df = con.execute("SELECT * FROM health WHERE tkr IN (SELECT unnest(?))",
                         [list(tickers)]).df()
    except duckdb.CatalogException:
        return {}
    from signals import clean
    return {r["tkr"]: r for r in clean(df).to_dict("records")}


# ------------------------------------------------------------------ selftest

def _fact(end, val, start=None, form="10-K", filed=None):
    r = {"end": end, "val": val, "form": form, "filed": filed or end}
    if start:
        r["start"] = start
    return r


def sample_facts(rev=(100e6, 120e6), ni=10e6, equity=50e6, debt=40e6,
                 shares=(10e6, 10.2e6)):
    return {"entityName": "Sample Co", "facts": {
        "us-gaap": {
            "Revenues": {"units": {"USD": [
                _fact("2024-12-31", rev[0], "2024-01-01"),
                _fact("2025-12-31", rev[1], "2025-01-01"),
                _fact("2025-09-30", 30e6, "2025-07-01", form="10-Q"),  # quarter: ignored
            ]}},
            "NetIncomeLoss": {"units": {"USD": [_fact("2025-12-31", ni, "2025-01-01")]}},
            "StockholdersEquity": {"units": {"USD": [_fact("2025-12-31", equity)]}},
            "LongTermDebt": {"units": {"USD": [_fact("2025-12-31", debt)]}},
        },
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            _fact("2025-02-01", shares[0]), _fact("2026-02-01", shares[1])]}}},
    }}


def selftest():
    m = analyze(sample_facts(), price=20.0)
    assert abs(m["revenue_growth"] - 0.2) < 1e-9, m
    assert m["verdict"] == "Solid", m
    assert abs(m["pe"] - 20 * 10.2e6 / 10e6) < 1e-9, m
    assert "sales up 20%" in m["reasons"] and "profitable" in m["reasons"], m

    m = analyze(sample_facts(ni=-5e6))
    assert m["verdict"] == "Mixed" and "lost money" in m["reasons"], m

    m = analyze(sample_facts(rev=(100e6, 80e6), ni=-5e6, debt=150e6, shares=(10e6, 12e6)))
    assert m["verdict"] == "Shaky", m
    for bit in ("shrank 20%", "lost money", "heavy debt", "dilution"):
        assert bit in m["reasons"], (bit, m)

    m = analyze(sample_facts(equity=-1e6))
    assert "negative equity" in m["reasons"], m

    m = analyze(sample_facts(), price=100.0)  # P/E 102
    assert m["verdict"] == "Mixed" and "expensive" in m["reasons"], m

    assert analyze({"entityName": "SPDR ETF", "facts": {}})["verdict"] == "Unknown"

    con = duckdb.connect()
    ensure_table(con)
    assert lookup(con, ["AAA"]) == {}
    print(plain(analyze(sample_facts(), price=20.0)))
    print("health selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        c = duckdb.connect(DB_PATH)
        args = [a for a in sys.argv[1:] if not a.startswith("--")]
        for tkr, row in refresh(c, args, force="--force" in sys.argv).items():
            print(f"{tkr:<6} {row['company'] or '':<40.40} {row['verdict']}: {row['reasons']}")
