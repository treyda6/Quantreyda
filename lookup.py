"""Look up any US stock, ETF or mutual fund, not just the tracked ones.

The site is static (GitHub Pages), and the free market-data sites don't let
a browser on another website read their data (no CORS), so the work happens
here, on the daily run, for the whole market at once:

  symbols    every listed stock and ETF (Nasdaq's symbol directory, which
             covers NYSE and the other exchanges too) and every mutual and
             money market fund (Nasdaq's fund list), plus SEC's ticker -> CIK
             map so companies can be matched to their filings
  prices     daily closes, dividends and splits from Yahoo (yfinance, free,
             unofficial). Kept per ticker as one compact row: daily for the
             last 5 years, month-end before that. First fill takes a few
             runs (each run spends at most `minutes`), then each day is a
             quick top-up.
  financials income statement, balance sheet and cash flow for every SEC
             filer, 10 years annual and the last 8 quarters, from SEC's
             companyfacts bulk file (one download a week)

write() turns that into static files the browser reads:

  look/index.json         every symbol: ticker, name, kind (for the search box)
  look/d/<SLUG>.json      one ticker's prices, dividends, splits, financials
  look.html               the lookup page; look.js draws it (charts, stats,
                          financial statements, Buy/Sell on Fidelity)

    py lookup.py             # refresh symbols, financials (weekly), prices
    py lookup.py --minutes 20
    py lookup.py --selftest  # offline
"""

import datetime
import io
import json
import os
import re
import sys
import tempfile
import time
import zipfile

import duckdb

import health
import value

NASDAQ_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
FUNDS_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/mfundslist.txt"
SEC_TICKERS = "https://www.sec.gov/files/company_tickers_exchange.json"
SEC_FUNDS = "https://www.sec.gov/files/company_tickers_mf.json"
FACTS_ZIP = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"

VALID = re.compile(r"[A-Z][A-Z0-9.\-]{0,7}")
EXCH = {"Q": "Nasdaq", "N": "NYSE", "A": "NYSE American", "P": "NYSE Arca", "Z": "Cboe BZX",
        "V": "IEX"}
KINDS = ("stock", "etf", "fund", "money")  # index.json stores the position
DAILY_YEARS = 5
FACTS_DAYS = 7
FACTS_VERSION = "2"  # bump when financials() keeps new lines: the next run re-reads the zip
REFETCH_DAYS = 30  # a ticker Yahoo didn't know is asked again after this


def ensure_tables(con):
    con.execute("""CREATE TABLE IF NOT EXISTS lk_symbols (
        tkr VARCHAR PRIMARY KEY, name VARCHAR, kind VARCHAR, exch VARCHAR,
        family VARCHAR, cik BIGINT, seen DATE)""")
    con.execute("""CREATE TABLE IF NOT EXISTS lk_px (
        tkr VARCHAR PRIMARY KEY, fetched DATE, last_d DATE, js VARCHAR)""")
    con.execute("""CREATE TABLE IF NOT EXISTS lk_fin (
        cik BIGINT PRIMARY KEY, updated DATE, js VARCHAR)""")
    con.execute("CREATE TABLE IF NOT EXISTS lk_meta (k VARCHAR PRIMARY KEY, v VARCHAR)")


def _meta(con, k, v=None):
    if v is None:
        r = con.execute("SELECT v FROM lk_meta WHERE k = ?", [k]).fetchone()
        return r[0] if r else None
    con.execute("INSERT OR REPLACE INTO lk_meta VALUES (?, ?)", [k, str(v)])


def yahoo_symbol(tkr):
    return tkr.replace(".", "-").replace("/", "-")


# ------------------------------------------------------------------ symbols

def _pipe_rows(text):
    """Rows of a Nasdaq symbol-directory file as dicts (header from line 1)."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    head = [h.strip() for h in lines[0].split("|")]
    out = []
    for ln in lines[1:]:
        if ln.startswith("File Creation Time"):
            continue
        parts = [p.strip() for p in ln.split("|")]
        out.append(dict(zip(head, parts)))
    return out


def _clean(sym):
    """'BRK.B', 'BRK/B', 'BRK-B' -> 'BRK.B'; None for preferreds, rights etc."""
    sym = (sym or "").strip().upper().replace("/", ".").replace("-", ".")
    return sym if VALID.fullmatch(sym) else None


def parse_listed(nasdaq_txt, other_txt):
    """{tkr: (name, kind, exchange)} for stocks and ETFs, test issues dropped."""
    out = {}
    for r in _pipe_rows(nasdaq_txt):
        t = _clean(r.get("Symbol"))
        if t and r.get("Test Issue") != "Y":
            out[t] = (r.get("Security Name", ""), "etf" if r.get("ETF") == "Y" else "stock", "Nasdaq")
    for r in _pipe_rows(other_txt):
        t = _clean(r.get("ACT Symbol") or r.get("CQS Symbol"))
        if t and r.get("Test Issue") != "Y":
            out[t] = (r.get("Security Name", ""), "etf" if r.get("ETF") == "Y" else "stock",
                      EXCH.get(r.get("Exchange"), r.get("Exchange") or ""))
    return out


def parse_funds(txt):
    """{tkr: (name, kind, family)} for open-end mutual funds and money market funds.
    Annuities, UITs and structured notes are left out: you can't buy those in a
    brokerage account by ticker."""
    out = {}
    for r in _pipe_rows(txt):
        t = _clean(r.get("Fund Symbol"))
        typ, cat = r.get("Type", ""), r.get("Category", "")
        if not t:
            continue
        if typ == "MF" and cat in ("O", ""):
            kind = "fund"
        elif typ == "$$":
            kind = "money"
        else:
            continue
        out[t] = (r.get("Fund Name", ""), kind, r.get("Fund Family Name", ""))
    return out


def parse_sec(data):
    """{tkr: (cik, name, exchange)} from SEC's company_tickers_exchange.json."""
    f = data.get("fields") or []
    out = {}
    for row in data.get("data") or []:
        r = dict(zip(f, row))
        t = _clean(r.get("ticker"))
        if t and r.get("cik"):
            out[t] = (int(r["cik"]), r.get("name") or "", r.get("exchange") or "")
    return out


def _nice_name(n):
    """'Apple Inc. - Common Stock' -> 'Apple Inc.'"""
    n = re.sub(r"\s+-\s+(Class [A-Z] )?(Common Stock|Ordinary Shares|Common Shares|"
               r"American Depositary Shares).*$", lambda m: (" " + m.group(1).strip()) if m.group(1) else "",
               n or "", flags=re.I)
    return n.strip()


def refresh_symbols(con, fetch=None):
    """Rebuild lk_symbols from whichever lists download. A list that fails
    keeps yesterday's rows for its tickers."""
    ensure_tables(con)
    fetch = fetch or _http_text
    today = datetime.date.today()
    listed, funds, sec = {}, {}, {}
    try:
        listed = parse_listed(fetch(NASDAQ_LISTED), fetch(OTHER_LISTED))
    except Exception as e:
        print(f"lookup: stock/ETF list skipped ({type(e).__name__}: {e})")
    try:
        funds = parse_funds(fetch(FUNDS_LISTED))
    except Exception as e:
        print(f"lookup: fund list skipped ({type(e).__name__}: {e})")
    try:
        sec = parse_sec(json.loads(fetch(SEC_TICKERS, sec_ua=True)))
    except (Exception, SystemExit) as e:
        print(f"lookup: SEC ticker list skipped ({type(e).__name__}: {e})")
    rows = {}
    for t, (cik, name, ex) in sec.items():  # SEC first: companies with filings
        rows[t] = [t, name, "stock", ex, None, cik]
    for t, (name, kind, ex) in listed.items():
        r = rows.setdefault(t, [t, None, kind, ex, None, None])
        r[1] = _nice_name(name) or r[1]
        r[2], r[3] = kind, ex or r[3]
    for t, (name, kind, fam) in funds.items():
        if t not in rows:
            rows[t] = [t, name, kind, "Mutual fund", fam, None]
    if rows:
        con.executemany("INSERT OR REPLACE INTO lk_symbols VALUES (?,?,?,?,?,?,?)",
                        [r + [today] for r in rows.values()])
    print(f"lookup: {len(listed):,} listed, {len(funds):,} funds, {len(sec):,} SEC companies")
    return len(rows)


def _http_text(url, sec_ua=False):
    import requests
    from schema import sec_user_agent
    ua = sec_user_agent() if sec_ua else "Mozilla/5.0 (quantreyda symbol lookup)"
    r = requests.get(url, headers={"User-Agent": ua}, timeout=60)
    r.raise_for_status()
    return r.text


# ------------------------------------------------------------- financials

FLOW = {  # key: concepts tried in order (first wins on the same period)
    "rev": health.REVENUE,
    "gp": ["GrossProfit"],
    "oi": ["OperatingIncomeLoss"],
    "ni": health.NET_INCOME,
    "ocf": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
    "div": health.DIVIDENDS,
    "buy": health.BUYBACKS,
}
PER_SHARE = {"eps": ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted",
                     "EarningsPerShareBasic"]}
SHARE_FLOW = {"sh": ["WeightedAverageNumberOfDilutedSharesOutstanding",
                     "WeightedAverageNumberOfShareOutstandingBasicAndDiluted",
                     "WeightedAverageNumberOfSharesOutstandingBasic"]}
INSTANT = {
    "assets": ["Assets"], "liab": ["Liabilities"], "eq": health.EQUITY,
    "ca": ["AssetsCurrent"], "cl": ["LiabilitiesCurrent"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "debt": ["LongTermDebt", "LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations"],
}
ANNUAL_FORMS = ("10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A")
N_YEARS, N_QUARTERS = 10, 8


def _d(s):
    return datetime.date.fromisoformat(s)


def _periods(facts, concepts, unit):
    """{(start, end): val} for duration facts, latest filing winning; earlier
    concepts in the list win over later ones for the same period."""
    out = {}
    for c in concepts:
        got = {}
        for r in sorted(health._units(facts, c, unit), key=lambda r: r.get("filed", "")):
            if r.get("start") and r.get("end") and r.get("val") is not None:
                got[(r["start"], r["end"])] = r["val"]
        for k, v in got.items():
            out.setdefault(k, v)
    return out


def _instants(facts, concepts, unit="USD"):
    out = {}
    for c in concepts:
        got = {}
        for r in sorted(health._units(facts, c, unit), key=lambda r: r.get("filed", "")):
            if not r.get("start") and r.get("end") and r.get("val") is not None:
                got[r["end"]] = r["val"]
        for k, v in got.items():
            out.setdefault(k, v)
    return out


def _quarters(periods, subtract=True, q4=True):
    """{end: val} of single-quarter values. Uses 3-month facts directly and,
    for statements reported year-to-date (cash flow, and Q4 of everything),
    takes the difference of two year-to-date figures with the same start."""
    q = {}
    for (s, e), v in periods.items():
        if 80 <= (_d(e) - _d(s)).days <= 100:
            q[e] = v
    if subtract:
        by_start = {}
        for (s, e), v in periods.items():
            by_start.setdefault(s, []).append((e, v))
        for s, rows in by_start.items():
            rows.sort()
            for (e0, v0), (e1, v1) in zip(rows, rows[1:]):
                if e1 not in q and 80 <= (_d(e1) - _d(e0)).days <= 100 and \
                        (_d(e0) - _d(s)).days >= 80:
                    q[e1] = v1 - v0
    # fourth quarters reported only as the full year: year minus its three quarters
    for (s, e), v in (periods.items() if q4 else ()):
        if e in q or not 350 <= (_d(e) - _d(s)).days <= 380:
            continue
        inside = [qe for qe in q if _d(s) < _d(qe) < _d(e) and (_d(e) - _d(qe)).days >= 80]
        if len(inside) == 3:
            q[e] = v - sum(q[x] for x in inside)
    return q


def _annual(periods):
    """{end: val} for full fiscal years."""
    return {e: v for (s, e), v in periods.items() if 350 <= (_d(e) - _d(s)).days <= 380}


def _fiscal_ends(facts):
    """Fiscal year ends, from the annual reports' revenue/net income/assets."""
    ends = set()
    for c in health.REVENUE + health.NET_INCOME:
        for r in health._units(facts, c):
            if r.get("form") in ANNUAL_FORMS and r.get("start") and r.get("fp") == "FY" \
                    and 350 <= (_d(r["end"]) - _d(r["start"])).days <= 380:
                ends.add(r["end"])
    if not ends:
        for r in health._units(facts, "Assets"):
            if r.get("form") in ANNUAL_FORMS and r.get("fp") == "FY":
                ends.add(r["end"])
    # one end per fiscal year: drop ends within 300 days of a later one
    out = []
    for e in sorted(ends, reverse=True):
        if not out or (_d(out[-1]) - _d(e)).days > 300:
            out.append(e)
    return sorted(out)


def _near(series, end, days=10):
    """Value at `end`, or at a date within `days` of it (52/53-week years)."""
    if end in series:
        return series[end]
    best = None
    for k, v in series.items():
        gap = abs((_d(k) - _d(end)).days)
        if gap <= days and (best is None or gap < best[0]):
            best = (gap, v)
    return best[1] if best else None


def financials(facts):
    """Compact statements for one company, or None when it files no numbers.

      {"fy": [ends...], "a": {key: [values per fy]}, "qe": [...], "q": {...},
       "so": shares outstanding, "so_d": as-of date, "name": entity name}"""
    fy = _fiscal_ends(facts)[-N_YEARS:]
    flows = {}
    for key, cs in FLOW.items():
        flows[key] = _periods(facts, cs, "USD")
    for key, cs in PER_SHARE.items():
        flows[key] = _periods(facts, cs, "USD/shares")
    for key, cs in SHARE_FLOW.items():
        flows[key] = _periods(facts, cs, "shares")
    inst = {k: _instants(facts, cs) for k, cs in INSTANT.items()}
    qmap = {k: _quarters(p, subtract=k not in ("eps", "sh"), q4=k != "sh") for k, p in flows.items()}
    qends = sorted({e for k in ("rev", "ni", "ocf") for e in qmap[k]})
    # one end per quarter, most recent N
    qe = []
    for e in sorted(qends, reverse=True):
        if not qe or (_d(qe[-1]) - _d(e)).days > 60:
            qe.append(e)
    qe = sorted(qe[:N_QUARTERS])
    if not fy and not qe:
        return None

    def rnd(v):
        if v is None:
            return None
        return round(v, 2) if abs(v) < 1000 else round(v)

    a = {}
    for k, p in flows.items():
        ann = _annual(p)
        a[k] = [rnd(_near(ann, e)) for e in fy]
    for k, s in inst.items():
        a[k] = [rnd(_near(s, e)) for e in fy]
    q = {}
    for k in flows:
        q[k] = [rnd(_near(qmap[k], e, 6)) for e in qe]
    for k, s in inst.items():
        q[k] = [rnd(_near(s, e, 6)) for e in qe]
    so_d, so = health.latest_instant(facts, health.SHARES, "shares")
    out = {"fy": fy, "a": a, "qe": qe, "q": q, "name": facts.get("entityName") or ""}
    x = value.extras(facts, fy) if fy else {}
    if x:  # tax, stock pay, goodwill, total debt...: inputs for the value scores, not shown as rows
        out["x"] = x
    if so:
        out["so"], out["so_d"] = so, so_d
    # drop all-empty rows so the page doesn't show lines of dashes
    for blk in (out["a"], out["q"]):
        for k in [k for k, v in blk.items() if all(x is None for x in v)]:
            del blk[k]
    return out


def refresh_financials(con, force=False, download=None):
    """Weekly: SEC's companyfacts bulk zip -> lk_fin for every CIK we list."""
    ensure_tables(con)
    last = _meta(con, "facts_on")
    today = datetime.date.today()
    if not force and last and (today - _d(last)).days < FACTS_DAYS and _meta(con, "facts_v") == FACTS_VERSION:
        print(f"lookup: financials fresh (from {last})")
        return 0
    want = {r[0] for r in con.execute("SELECT DISTINCT cik FROM lk_symbols WHERE cik IS NOT NULL").fetchall()}
    if not want:
        return 0
    path = (download or _download_zip)()
    n = 0
    try:
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                m = re.match(r"CIK(\d{10})\.json$", os.path.basename(info.filename))
                if not m or int(m.group(1)) not in want:
                    continue
                try:
                    fin = financials(json.loads(z.read(info)))
                except Exception as e:  # one odd filer shouldn't stop the rest
                    print(f"lookup: CIK {m.group(1)} skipped ({type(e).__name__})")
                    continue
                if fin:
                    con.execute("INSERT OR REPLACE INTO lk_fin VALUES (?, ?, ?)",
                                [int(m.group(1)), today, json.dumps(fin, separators=(",", ":"))])
                    n += 1
    finally:
        if download is None:
            os.unlink(path)
    _meta(con, "facts_on", today)
    _meta(con, "facts_v", FACTS_VERSION)
    print(f"lookup: financials for {n:,} companies")
    return n


def _download_zip():
    import requests
    from schema import sec_user_agent
    fd, path = tempfile.mkstemp(suffix=".zip")
    with os.fdopen(fd, "wb") as f, requests.get(FACTS_ZIP, headers={"User-Agent": sec_user_agent()},
                                                stream=True, timeout=120) as r:
        r.raise_for_status()
        for chunk in r.iter_content(1 << 20):
            f.write(chunk)
    print(f"lookup: companyfacts.zip {os.path.getsize(path) / 1e6:,.0f} MB")
    return path


# ------------------------------------------------------------------ prices

def _yf_download(symbols, period):
    """{yahoo symbol: DataFrame with Close, Dividends, Stock Splits} or missing."""
    import yfinance as yf
    data = yf.download(symbols, period=period, interval="1d", auto_adjust=False, actions=True,
                       progress=False, threads=True, group_by="ticker")
    out = {}
    for s in symbols:
        try:
            df = data[s] if len(symbols) > 1 or s in data.columns.get_level_values(0) else data
        except (KeyError, AttributeError, IndexError):
            try:
                df = data[s]
            except Exception:
                continue
        if df is None or "Close" not in df:
            continue
        df = df[df["Close"].notna()]
        if len(df):
            out[s] = df
    return out


def _universe():
    try:
        import mine
        return mine.universe()
    except Exception:
        return []


def _fmt_px(v):
    v = float(v)
    return round(v, 2) if v >= 10 else round(v, 4)


def merge(old, df, today):
    """Fold a downloaded frame into a stored blob. Returns (blob, ok); ok is
    False when the overlap disagrees (a split re-scaled history: refetch)."""
    new = {i.date().isoformat(): _fmt_px(v) for i, v in df["Close"].items()}
    divs = {i.date().isoformat(): round(float(v), 4) for i, v in df.get("Dividends", {}).items() if v}
    spl = {i.date().isoformat(): float(v) for i, v in df.get("Stock Splits", {}).items() if v}
    blob = {"d": [], "m": [], "div": [], "spl": []} if old is None else old
    daily = dict(blob["d"])
    if old is not None:
        known = dict(blob["spl"])
        if any(k not in known for k in spl):
            return blob, False
        shared = [(daily[k], v) for k, v in new.items() if k in daily]
        if any(abs(a - b) > 0.02 * max(abs(a), 1e-9) for a, b in shared[-5:]):
            return blob, False
    daily.update(new)
    cut = (today - datetime.timedelta(days=365 * DAILY_YEARS)).isoformat()
    months = {d[:7]: (d, v) for d, v in blob["m"]}
    for k in sorted(daily):
        if k < cut:  # older than 5 years: keep only the month's last close
            months[k[:7]] = (k, daily.pop(k))
    blob["m"] = sorted(months.values())
    blob["d"] = sorted(daily.items())
    blob["div"] = sorted({**dict(blob["div"]), **divs}.items())
    blob["spl"] = sorted({**dict(blob["spl"]), **spl}.items())
    return blob, True


def _month_ends(rows):
    """[(date, close)] -> last row of each month."""
    m = {}
    for d, v in sorted(rows):
        m[d[:7]] = (d, v)
    return list(m.values())


def _store_blob(con, tkr, blob, today):
    blob["m"] = [list(x) for x in _month_ends([tuple(x) for x in blob["m"]])]
    blob["d"] = [list(x) for x in blob["d"]]
    last = blob["d"][-1][0] if blob["d"] else (blob["m"][-1][0] if blob["m"] else None)
    con.execute("INSERT OR REPLACE INTO lk_px VALUES (?, ?, ?, ?)",
                [tkr, today, last, json.dumps(blob, separators=(",", ":"))])


def refresh_prices(con, minutes=30, batch=150, download=None, priority=None):
    """Top up every ticker we have prices for, then fetch full history for
    new ones until the time runs out. Stocks and ETFs before funds."""
    ensure_tables(con)
    download = download or _yf_download
    today = datetime.date.today()
    stop = time.monotonic() + minutes * 60
    rows = con.execute("""
        SELECT s.tkr, s.kind, p.fetched, p.last_d, p.js IS NOT NULL AND p.last_d IS NOT NULL
        FROM lk_symbols s LEFT JOIN lk_px p USING (tkr)""").fetchall()
    # Tiers, so a run that runs out of time drops the long tail, not the names
    # people hold: universe.txt first, then stocks and ETFs, then funds; within
    # a tier the stalest first.
    first = set(priority if priority is not None else _universe())
    order = {"stock": 1, "etf": 1, "fund": 2, "money": 3}
    old = datetime.date(1970, 1, 1)

    def tier(r):
        return (0 if r[0] in first else order.get(r[1], 4), r[2] or old, r[0])
    topup = [r[0] for r in sorted((r for r in rows if r[4] and (r[2] is None or r[2] < today)), key=tier)]
    new = [r for r in rows if not r[4] and (r[2] is None or (today - r[2]).days >= REFETCH_DAYS)]
    new = [r[0] for r in sorted(new, key=tier)]
    stats = {"topped": 0, "new": 0, "missing": 0}
    redo = []

    def run(group, period, label):
        for i in range(0, len(group), batch):
            if time.monotonic() > stop:
                print(f"lookup prices: out of time, {len(group) - i:,} {label} left for next run")
                return False
            chunk = group[i:i + batch]
            sym = {yahoo_symbol(t): t for t in chunk}
            try:
                got = download(list(sym), period)
            except Exception as e:
                print(f"lookup prices: batch failed ({type(e).__name__}: {e}); pausing")
                time.sleep(20)
                continue
            for ys, t in sym.items():
                df = got.get(ys)
                if df is None or not len(df):
                    if label != "top-up":
                        con.execute("INSERT OR REPLACE INTO lk_px VALUES (?, ?, NULL, NULL)",
                                    [t, today])
                        stats["missing"] += 1
                    continue
                old = None
                if label == "top-up":
                    r = con.execute("SELECT js FROM lk_px WHERE tkr = ?", [t]).fetchone()
                    old = json.loads(r[0]) if r and r[0] else None
                blob, ok = merge(old, df, today)
                if not ok:
                    redo.append(t)
                    continue
                _store_blob(con, t, blob, today)
                stats["topped" if label == "top-up" else "new"] += 1
            print(f"lookup prices: {label} {i + len(chunk):,} of {len(group):,}", flush=True)
            time.sleep(0.5)
        return True

    if run(topup, "10d", "top-up"):
        run(redo, "max", "refetch")
        run(new, "max", "new history")
    print(f"lookup prices: {stats['topped']:,} topped up, {stats['new']:,} new, "
          f"{len(redo):,} refetched after a split, {stats['missing']:,} unknown to Yahoo")
    return stats


# ------------------------------------------------------------------ output

def _epoch(iso):
    return (_d(iso) - datetime.date(1970, 1, 1)).days


def _series(rows):
    """[(iso, close)] -> {"t": [first epoch day, then gaps], "c": [closes]}:
    about half the size of writing the dates out."""
    t, c, prev = [], [], None
    for d, v in rows:
        e = _epoch(d)
        t.append(e if prev is None else e - prev)
        c.append(v)
        prev = e
    return {"t": t, "c": c}


def page_data(blob, today):
    """Prices for the browser: daily for the last ~13 months, weekly for the
    5 years before, month-end before that."""
    d = [tuple(x) for x in blob.get("d", [])]
    if not d and not blob.get("m"):
        return None
    last = _d(d[-1][0]) if d else today
    cut1 = (last - datetime.timedelta(days=400)).isoformat()
    cut5 = (last - datetime.timedelta(days=365 * 5 + 7)).isoformat()
    daily = [x for x in d if x[0] >= cut1]
    weeks = {}
    for k, v in d:
        if cut5 <= k < cut1:
            y, w, _ = _d(k).isocalendar()
            weeks[(y, w)] = (k, v)
    weekly = sorted(weeks.values())
    monthly = [tuple(x) for x in blob.get("m", []) if x[0] < (weekly[0][0] if weekly else cut1)]
    # months between the stored month-ends and the weekly start, from the daily rows
    me = {}
    for k, v in d:
        if k < cut5:
            me[k[:7]] = (k, v)
    monthly = sorted({x[0][:7]: x for x in monthly + list(me.values())}.values())
    return {"d": _series(daily), "w": _series(weekly), "m": _series(monthly),
            "div": blob.get("div", []), "spl": blob.get("spl", [])}


def slug(tkr):
    return re.sub(r"[^A-Za-z0-9]+", "-", tkr).strip("-").upper()


def write(con, out, built=(), today=None):
    """look/index.json and look/d/*.json. Returns how many tickers have data."""
    ensure_tables(con)
    today = today or datetime.date.today()
    os.makedirs(f"{out}/look/d", exist_ok=True)
    built = set(built)
    fins = {}
    rows = con.execute("""
        SELECT s.tkr, s.name, s.kind, s.exch, s.family, s.cik, p.js, p.last_d
        FROM lk_symbols s LEFT JOIN lk_px p USING (tkr) ORDER BY s.tkr""").fetchall()
    index, n, screen = [], 0, []
    tracked = value.tracked_extras(con)
    for tkr, name, kind, exch, fam, cik, js, last_d in rows:
        blob = json.loads(js) if js else None
        px = page_data(blob, today) if blob else None
        fin = None
        if cik:
            if cik not in fins:
                r = con.execute("SELECT js FROM lk_fin WHERE cik = ?", [cik]).fetchone()
                fins[cik] = json.loads(r[0]) if r else None
            fin = fins[cik]
        has = bool(px or fin)
        index.append([tkr, name or "", KINDS.index(kind) if kind in KINDS else 0,
                      1 if tkr in built else 0, 1 if has else 0])
        if not has:
            continue
        doc = {"t": tkr, "n": name or (fin or {}).get("name") or "", "k": kind, "x": exch or "",
               "asof": str(last_d) if last_d else None}
        if fam:
            doc["fam"] = fam
        if px:
            doc["px"] = px
        if fin:
            doc["fin"] = {k: v for k, v in fin.items() if k != "x"}
            if kind == "stock":
                last = blob["d"][-1][1] if blob and blob.get("d") else None
                try:
                    sc = value.score(fin, last, tracked.get(tkr))
                except Exception as e:  # odd numbers in one filing shouldn't stop the rest
                    print(f"lookup: value scores for {tkr} skipped ({type(e).__name__}: {e})")
                    sc = None
                if sc:
                    doc["sc"] = sc
                    screen.append(value.screen_row(tkr, name or fin.get("name"), sc, last, tkr in built))
        with open(f"{out}/look/d/{slug(tkr)}.json", "w", encoding="utf-8") as f:
            json.dump(doc, f, separators=(",", ":"))
        n += 1
    with open(f"{out}/look/index.json", "w", encoding="utf-8") as f:
        json.dump({"asof": today.isoformat(), "kinds": KINDS, "s": index}, f, separators=(",", ":"))
    with open(f"{out}/look/screen.json", "w", encoding="utf-8") as f:  # the Value screen page
        json.dump({"asof": today.isoformat(), "cols": value.SCREEN_COLS, "r": screen}, f, separators=(",", ":"))
    print(f"lookup: index of {len(index):,} symbols, {n:,} with data")
    return n


CSS = """
.lk-search{position:relative;flex:0 1 240px;min-width:150px}
.lk-search input{width:100%;box-sizing:border-box;padding:8px 12px 8px 32px;border-radius:999px;
border:1px solid var(--line);background:var(--card);color:var(--fg);font:inherit;font-size:14px}
.lk-search svg{position:absolute;left:10px;top:50%;width:15px;height:15px;transform:translateY(-50%);
fill:none;stroke:var(--mut);stroke-width:2;stroke-linecap:round;pointer-events:none}
.lk-sugg{position:absolute;top:calc(100% + 6px);left:0;min-width:400px;z-index:40;background:var(--card);
border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow);padding:6px;max-height:60vh;overflow:auto}
.lk-sugg a{display:flex;gap:10px;align-items:baseline;padding:8px 10px;border-radius:10px;color:var(--fg);
text-decoration:none;font-size:14px}.lk-sugg a:hover,.lk-sugg a.on{background:var(--chip)}
.lk-sugg b{min-width:58px}.lk-sugg span{flex:1;color:var(--mut);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.lk-sugg em{font-style:normal;font-size:11px;color:var(--mut);text-transform:uppercase;letter-spacing:.4px}
@media(max-width:700px){.lk-search{flex:0 0 150px;min-width:130px}.lk-search input,.lk-big input{font-size:16px}.lk-sugg{position:fixed;left:12px;right:12px;top:58px;min-width:0}}
.lk-big input{width:100%;box-sizing:border-box;font:inherit;font-size:18px;padding:14px 18px;border-radius:16px;
border:1px solid var(--line);background:var(--card);color:var(--fg);margin:8px 0}
.lk-big{position:relative;max-width:620px}
.lk-trade{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:4px 0 12px}
.lk-trade .hint{flex:1 1 220px;font-size:12px}
.lk-btn{display:inline-flex;align-items:center;justify-content:center;min-width:84px;padding:9px 16px;border-radius:999px;
font:inherit;font-weight:700;font-size:14px;text-decoration:none;border:1px solid transparent;cursor:pointer}
.lk-btn.buy{background:var(--up);color:var(--acc-ink)}.lk-btn.sell{background:var(--down);color:#fff}
.lk-btn.ghost{background:var(--card2);color:var(--fg);border-color:var(--line);min-width:0}
.lk-btn:hover{filter:brightness(1.08)}
.lk-ranges{display:flex;gap:4px;align-items:center;flex-wrap:wrap;margin:6px 0}
.lk-ranges button,.lk-tabs button{font:inherit;font-size:13px;font-weight:600;padding:6px 11px;border-radius:999px;
border:0;background:transparent;color:var(--mut);cursor:pointer}
.lk-ranges button.on,.lk-tabs button.on{background:var(--chip);color:var(--fg)}
.lk-cmp{font-size:13px;color:var(--mut);margin-left:8px;display:flex;gap:5px;align-items:center}
.lk-chg{margin-left:auto;font-size:14px;color:var(--mut)}
.lk-plot{position:relative;touch-action:pan-y}
.lk-tv{height:420px;border-radius:12px;overflow:hidden;border:1px solid var(--line)}
.chart.lk-chart text{font-size:11px}.lk-chart .lk-x{stroke:var(--mut);stroke-width:1;stroke-dasharray:3 3}
.chart text.ev{fill:#000;font-size:9px;font-weight:800}
.lk-tip{position:absolute;top:0;width:130px;background:var(--card2);border:1px solid var(--line);border-radius:10px;
padding:6px 9px;font-size:13px;pointer-events:none;box-shadow:var(--shadow)}
.lk-kv{grid-template-columns:repeat(4,1fr)}.lk-kv b{font-size:14px}
.lk-tabs{display:flex;gap:4px;flex-wrap:wrap;margin:10px 0 6px;align-items:center}.lk-tabs .sp{flex:1}
table.lk-fin{font-size:13px;font-variant-numeric:tabular-nums;white-space:nowrap}
table.lk-fin td,table.lk-fin th{text-align:right;padding:7px 10px}
table.lk-fin th[scope=row]{text-align:left;color:var(--fg);text-transform:none;letter-spacing:0;font-size:13px;
position:sticky;left:0;background:var(--card)}
.lk-panel h3{font-size:14px;margin:12px 0 4px}#lk h3{font-size:14px;margin:12px 0 4px}
.lk-toast{position:fixed;left:50%;bottom:24px;transform:translate(-50%,30px);opacity:0;transition:.25s;z-index:60;
background:var(--fg);color:var(--bg);padding:12px 16px;border-radius:14px;max-width:min(520px,calc(100vw - 32px));
font-size:14px;box-shadow:var(--shadow);pointer-events:none}.lk-toast.on{opacity:1;transform:translate(-50%,0)}
@media(max-width:700px){.lk-tv{height:340px}.lk-kv{grid-template-columns:repeat(2,1fr)}.lk-toast{bottom:84px}.lk-chg{margin-left:0;width:100%}
.lk-btn{flex:1}.lk-btn.ghost{flex:0 0 auto}}
"""

SEARCH_HTML = ("<div class='lk-search'><svg viewBox='0 0 24 24' aria-hidden='true'><circle cx='11' cy='11' r='6.5'/>"
               "<path d='M16 16l5 5'/></svg><input id='lk-q' type='search' autocomplete='off' "
               "placeholder='Look up any stock or fund' aria-label='Look up any stock, ETF or mutual fund'>"
               "<div class='lk-sugg' hidden></div></div>")


def search_html(root=""):
    # value.js first (deferred scripts run in order): look.js draws its scorecards
    return SEARCH_HTML + f"<script src='{root}value.js' defer></script><script src='{root}look.js' defer></script>"


def lookup_page():
    """Body of look.html: look.js fills it from ?s=TICKER."""
    return ("<div id='lk' data-mode='page'><h1>Look up any stock or fund</h1>"
            "<p class='mut'>Type a ticker or a name in the search box at the top: any US-listed "
            "stock or ETF, or a mutual fund. You get its price history back to the start, its "
            "financial statements, dividends and splits, and Buy and Sell buttons that open the "
            "same symbol at Fidelity.</p>"
            "<div class='lk-big'><input id='lk-q2' type='search' autocomplete='off' "
            "placeholder='e.g. AAPL, Vanguard 500, FXAIX' aria-label='Ticker or name'></div>"
            "<p class='hint'>Charts use daily closes, updated every morning, plus a live 1D chart from TradingView for stocks and ETFs. "
            "Financials come from each company's SEC filings.</p></div>")


def stock_panel(tkr):
    """A placeholder on a tracked stock's page; look.js fills it with the
    long-range chart, statements and the Fidelity buttons."""
    t = tkr.replace("'", "")
    return (f"<div class='lk-panel' id='lk' data-mode='panel' data-tkr='{t}'><section class='panel'>"
            "<h2>Price history and financials</h2><p class='hint'>Loading…</p></section></div>")


def inject(body, tkr):
    """Put the panel right after the stock page's first chart (or at the end)."""
    panel = stock_panel(tkr)
    mark = "<section class='panel qa'>"
    k = body.find(mark)
    return body[:k] + panel + body[k:] if k >= 0 else body + panel


# ---------------------------------------------------------------- main

def main(minutes=40):
    """`minutes` is the whole budget; prices get whatever symbols and the
    weekly financials leave (at least 5 minutes)."""
    from schema import DB_PATH
    con = duckdb.connect(DB_PATH)
    ensure_tables(con)
    t_start = time.monotonic()
    left = lambda: max(5.0, minutes - (time.monotonic() - t_start) / 60)
    for label, step in (("symbols", lambda: refresh_symbols(con)),
                        ("financials", lambda: refresh_financials(con)),
                        ("prices", lambda: refresh_prices(con, minutes=left()))):
        t0 = time.monotonic()
        try:
            step()
        except (Exception, SystemExit) as e:  # each part is optional
            print(f"lookup {label} skipped ({type(e).__name__}: {e})")
        print(f"    (lookup {label}: {(time.monotonic() - t0) / 60:.1f} min)", flush=True)
    con.close()


# ---------------------------------------------------------------- selftest

def _fixture_facts():
    def f(start, end, val, form="10-K", fp="FY", filed="2025-02-01"):
        r = {"end": end, "val": val, "form": form, "fp": fp, "filed": filed}
        if start:
            r["start"] = start
        return r
    rev = [f("2022-01-01", "2022-12-31", 100), f("2023-01-01", "2023-12-31", 120),
           f("2024-01-01", "2024-12-31", 150),
           f("2024-01-01", "2024-03-31", 30, "10-Q", "Q1"), f("2024-04-01", "2024-06-30", 35, "10-Q", "Q2"),
           f("2024-07-01", "2024-09-30", 40, "10-Q", "Q3"),
           f("2024-01-01", "2024-09-30", 105, "10-Q", "Q3")]
    ocf = [f("2024-01-01", "2024-03-31", 10, "10-Q", "Q1"), f("2024-01-01", "2024-06-30", 25, "10-Q", "Q2"),
           f("2024-01-01", "2024-09-30", 45, "10-Q", "Q3"), f("2024-01-01", "2024-12-31", 70),
           f("2023-01-01", "2023-12-31", 60)]
    assets = [f(None, "2023-12-31", 500), f(None, "2024-12-31", 600), f(None, "2024-09-30", 580, "10-Q", "Q3")]
    eps = [f("2024-01-01", "2024-12-31", 1.5), f("2024-07-01", "2024-09-30", .4, "10-Q", "Q3")]
    return {"entityName": "Fixture Co", "facts": {
        "us-gaap": {"Revenues": {"units": {"USD": rev}},
                    "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": ocf}},
                    "Assets": {"units": {"USD": assets}},
                    "EarningsPerShareDiluted": {"units": {"USD/shares": eps}}},
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [f(None, "2025-01-20", 1000)]}}}}}


def selftest():
    import pandas as pd

    # symbol lists
    nas = ("Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\n"
           "AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N\nQQQ|Invesco QQQ Trust, Series 1|G|N|N|100|Y|N\n"
           "ZZZT|Test Co|S|Y|N|100|N|N\nFile Creation Time: 1127202508:30|||||||\n")
    oth = ("ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\n"
           "BRK.B|Berkshire Hathaway Inc. Class B|N|BRK.B|N|100|N|BRK=B\n"
           "SPY|SPDR S&P 500 ETF Trust|P|SPY|Y|100|N|SPY\nABR$D|Arbor pref|N|ABRpD|N|100|N|ABR-D\n")
    mf = ("Fund Symbol|Fund Name|Fund Family Name|Type|Category|Pricing Agent\n"
          "FXAIX|Fidelity 500 Index Fund|Fidelity|MF|O|x\nMMKTX|Example Government Money Market|Example Funds|$$|G|x\n"
          "ANNUX|Some annuity|Ins Co|AN|M|x\n")
    sec = {"fields": ["cik", "name", "ticker", "exchange"],
           "data": [[320193, "Apple Inc.", "AAPL", "Nasdaq"], [1067983, "BERKSHIRE HATHAWAY INC", "BRK-B", "NYSE"]]}
    listed = parse_listed(nas, oth)
    assert set(listed) == {"AAPL", "QQQ", "BRK.B", "SPY"}, listed
    assert listed["QQQ"][1] == "etf" and listed["SPY"][2] == "NYSE Arca"
    funds = parse_funds(mf)
    assert funds == {"FXAIX": ("Fidelity 500 Index Fund", "fund", "Fidelity"),
                     "MMKTX": ("Example Government Money Market", "money", "Example Funds")}
    assert _nice_name("Apple Inc. - Common Stock") == "Apple Inc."
    con = duckdb.connect()
    files = {NASDAQ_LISTED: nas, OTHER_LISTED: oth, FUNDS_LISTED: mf, SEC_TICKERS: json.dumps(sec)}
    refresh_symbols(con, fetch=lambda u, sec_ua=False: files[u])
    got = dict((r[0], r[1:]) for r in con.execute("SELECT tkr, name, kind, cik FROM lk_symbols").fetchall())
    assert got["AAPL"] == ("Apple Inc.", "stock", 320193), got["AAPL"]
    assert got["BRK.B"][2] == 1067983 and got["FXAIX"][1] == "fund" and got["QQQ"][1] == "etf"

    # financials: annual, quarters (incl. Q4 and cash flow from year-to-date figures)
    fin = financials(_fixture_facts())
    assert fin["fy"] == ["2022-12-31", "2023-12-31", "2024-12-31"], fin["fy"]
    assert fin["a"]["rev"] == [100, 120, 150] and fin["a"]["assets"] == [None, 500, 600]
    qi = {e: i for i, e in enumerate(fin["qe"])}
    assert fin["q"]["rev"][qi["2024-12-31"]] == 45, fin  # 150 - 105
    assert [fin["q"]["ocf"][qi[e]] for e in ("2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31")] \
        == [10, 15, 20, 25], fin["q"]["ocf"]
    assert _quarters({("2024-01-01", "2024-03-31"): 1, ("2024-04-01", "2024-06-30"): 2,
                      ("2024-07-01", "2024-09-30"): 3, ("2024-01-01", "2024-12-31"): 10})["2024-12-31"] == 4
    assert fin["so"] == 1000 and fin["name"] == "Fixture Co" and "gp" not in fin["a"]
    zpath = os.path.join(tempfile.mkdtemp(), "cf.zip")
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("CIK0000320193.json", json.dumps(_fixture_facts()))
        z.writestr("CIK0000000001.json", "{}")
    assert refresh_financials(con, force=True, download=lambda: zpath) == 1
    assert refresh_financials(con) == 0, "fresh for a week"

    # prices: new history, top-up, split -> refetch
    today = datetime.date.today()

    def frame(start, n, close=100.0, split_on=None):
        idx = pd.bdate_range(start, periods=n)
        df = pd.DataFrame({"Close": [close + (d.toordinal() % 50) * 0.01 for d in idx], "Dividends": 0.0,
                           "Stock Splits": 0.0}, index=idx)
        df.iloc[n // 2, df.columns.get_loc("Dividends")] = 0.25
        if split_on is not None:
            df.iloc[split_on, df.columns.get_loc("Stock Splits")] = 4.0
        return df
    calls = []

    def dl(symbols, period):
        calls.append((tuple(symbols), period))
        start = today - datetime.timedelta(days=365 * 8) if period == "max" else today - datetime.timedelta(days=14)
        n = 365 * 8 * 5 // 7 if period == "max" else 9
        return {s: frame(start, n) for s in symbols if s not in ("ANNUX", "MMKTX")}
    st = refresh_prices(con, minutes=1, download=dl, batch=2, priority={"FXAIX"})
    assert st["new"] >= 5 and st["missing"] == 1, st  # MMKTX unknown in this fake
    assert calls[0] == (("FXAIX", "AAPL"), "max"), "universe.txt names first, then stocks, then funds"
    assert calls[-1][0][-1] == "MMKTX", "money market funds last"
    blob = json.loads(con.execute("SELECT js FROM lk_px WHERE tkr = 'AAPL'").fetchone()[0])
    assert blob["m"] and blob["d"] and blob["div"], "monthly + daily + dividends"
    assert blob["d"][0][0] >= (today - datetime.timedelta(days=365 * 5 + 1)).isoformat()
    assert len({m[0][:7] for m in blob["m"]}) == len(blob["m"]), "one row per month"
    con.execute("UPDATE lk_px SET fetched = fetched - INTERVAL 1 DAY")
    calls.clear()
    st = refresh_prices(con, minutes=1, download=dl, priority=set())
    assert calls[0][1] == "10d" and st["topped"] >= 5, (calls, st)
    # a split in the top-up window: history must be refetched in full
    old = json.loads(con.execute("SELECT js FROM lk_px WHERE tkr = 'AAPL'").fetchone()[0])
    _, ok = merge(old, frame(today + datetime.timedelta(days=3), 3, split_on=1), today)
    assert not ok
    _, ok = merge(old, frame(today - datetime.timedelta(days=10), 5, close=25.0), today)
    assert not ok, "closes that moved 4x mean a split re-scaled the history"

    # output
    out = tempfile.mkdtemp()
    n = write(con, out, built={"AAPL"}, today=today)
    idx = json.load(open(f"{out}/look/index.json"))
    row = next(r for r in idx["s"] if r[0] == "AAPL")
    assert row[3] == 1 and row[4] == 1 and n >= 5
    doc = json.load(open(f"{out}/look/d/AAPL.json"))
    assert doc["fin"]["fy"][-1] == "2024-12-31" and doc["px"]["d"]["c"] and doc["px"]["w"]["c"]
    assert doc["px"]["m"]["c"] and doc["n"] == "Apple Inc."
    assert "x" not in doc["fin"] and doc["sc"]["fy"] == "2024-12-31", doc.get("sc")
    scr = json.load(open(f"{out}/look/screen.json"))
    srow = dict(zip(scr["cols"], next(r for r in scr["r"] if r[0] == "AAPL")))
    assert srow["page"] == 1 and srow["p"], srow
    assert not any(r[0] == "QQQ" for r in scr["r"]), "ETFs aren't scored"
    t = doc["px"]["d"]["t"]
    assert t[0] > 19000 and all(1 <= g <= 5 for g in t[1:]), "delta-encoded trading days"
    assert os.path.exists(f"{out}/look/d/BRK-B.json") and not os.path.exists(f"{out}/look/d/MMKTX.json")
    os.path.getsize(f"{out}/look/d/AAPL.json") < 20000 or print("note: big file")
    body = inject("<div class='sh'></div><section class='panel'>c</section><section class='panel qa'>q",
                  "AAPL")
    assert body.index("lk-panel") < body.index("panel qa") and "data-tkr='AAPL'" in body
    assert "lk-panel" in inject("<p>basic</p>", "X")
    print("lookup selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        m = 40
        if "--minutes" in sys.argv:
            m = float(sys.argv[sys.argv.index("--minutes") + 1])
        main(minutes=m)
