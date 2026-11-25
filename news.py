"""Recent company news, straight from the company's own SEC filings (free).

Every public company has to file an 8-K within four business days of anything
big: results, a new CEO, a major deal, a lawsuit settlement, new debt. The
8-K's "items" say what kind of news it is, so we can label each one in plain
English without reading the document or paying for a news API.

  refresh(con, tickers)   fetch the latest filings for each ticker (once a day)
  recent(con, tkr, n=8)   [(date, form, plain label, url), ...] newest first

Usage:
    py news.py AAPL MSFT   # fetch and print
    py news.py --selftest  # offline check
"""

import datetime
import sys
import time

import requests

from schema import sec_user_agent

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"

ITEMS = {
    "1.01": "Signed a major agreement",
    "1.02": "Ended a major agreement",
    "1.03": "Bankruptcy filing",
    "1.05": "Reported a cybersecurity incident",
    "2.01": "Bought or sold a business or major assets",
    "2.02": "Reported quarterly results",
    "2.03": "Took on new debt",
    "2.04": "A debt payment was sped up (often a warning sign)",
    "2.05": "Announced restructuring or layoff costs",
    "2.06": "Wrote down the value of an asset",
    "3.01": "Got a stock-exchange listing notice",
    "3.02": "Sold new shares privately",
    "3.03": "Changed shareholder rights",
    "4.01": "Changed auditors",
    "4.02": "Said past financial statements can't be relied on (red flag)",
    "5.01": "Change in who controls the company",
    "5.02": "Executive or board member change",
    "5.03": "Changed its bylaws or fiscal year",
    "5.07": "Shareholder vote results",
    "7.01": "Shared an investor presentation or update",
    "8.01": "Other company news",
}
FORMS = {
    "10-K": "Annual report (the full yearly financials)",
    "10-Q": "Quarterly report",
    "20-F": "Annual report (foreign company)",
    "6-K": "Update from a foreign company",
    "SC 13D": "An investor took a 5%+ stake and may push for changes",
    "SC 13D/A": "An activist investor changed their stake",
    "SC 13G": "A big investor passed 5% ownership",
    "SCHEDULE 13D": "An investor took a 5%+ stake and may push for changes",
    "SCHEDULE 13G": "A big investor passed 5% ownership",
    "S-1": "Registered to sell new shares",
    "S-3": "Registered to sell new shares or debt",
    "DEF 14A": "Annual meeting agenda (pay, board votes)",
    "144": "An insider filed plans to sell shares",
}
SKIP = {"3", "4", "5", "3/A", "4/A", "5/A", "SD", "11-K", "CORRESP", "UPLOAD", "S-8", "424B2",
        "FWP", "EFFECT"}


def ensure_table(con):
    con.execute("""CREATE TABLE IF NOT EXISTS news (
        tkr VARCHAR, filed DATE, form VARCHAR, what VARCHAR, url VARCHAR,
        PRIMARY KEY (tkr, url))""")
    con.execute("CREATE TABLE IF NOT EXISTS news_checked (tkr VARCHAR PRIMARY KEY, checked_on DATE)")


def label(form, items):
    """Plain-English label for one filing, or None to skip it."""
    if form in SKIP:
        return None
    if form.startswith("8-K"):
        codes = [c.strip() for c in (items or "").split(",") if c.strip() and c.strip() != "9.01"]
        words = [ITEMS[c] for c in codes if c in ITEMS]
        return "; ".join(words) if words else "Company news (8-K)"
    if form in FORMS:
        return FORMS[form]
    base = form.split("/")[0]
    return FORMS.get(base) and FORMS[base] + " (amended)"


def parse(sub, tkr, cik, keep=12):
    """Rows from one SEC submissions JSON, newest first."""
    r = (sub.get("filings") or {}).get("recent") or {}
    out = []
    for k, form in enumerate(r.get("form", [])):
        what = label(form, (r.get("items") or [""] * (k + 1))[k])
        if not what:
            continue
        acc = r["accessionNumber"][k].replace("-", "")
        doc = (r.get("primaryDocument") or [""] * (k + 1))[k]
        url = DOC_URL.format(cik=cik, acc=acc, doc=doc) if doc else \
            f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/"
        out.append((tkr, r["filingDate"][k], form, what, url))
        if len(out) >= keep:
            break
    return out


def refresh(con, tickers):
    """Fetch filings for tickers not checked today. Never fatal."""
    ensure_table(con)
    today = datetime.date.today()
    done = {r[0] for r in con.execute("SELECT tkr FROM news_checked WHERE checked_on >= ?",
                                      [today]).fetchall()}
    todo = sorted({t.upper() for t in tickers if t} - done)
    if not todo:
        return
    s = requests.Session()
    s.headers["User-Agent"] = sec_user_agent()
    ciks = {v["ticker"].upper(): int(v["cik_str"])
            for v in s.get(TICKERS_URL, timeout=30).json().values()}
    for t in todo:
        cik = ciks.get(t) or ciks.get(t.replace(".", "-"))
        if cik:
            time.sleep(0.15)  # SEC asks for <= 10 requests/sec
            r = s.get(SUBMISSIONS.format(cik=cik), timeout=30)
            if r.status_code == 200:
                rows = parse(r.json(), t, cik)
                con.execute("DELETE FROM news WHERE tkr = ?", [t])
                if rows:
                    con.executemany("INSERT OR REPLACE INTO news VALUES (?,?,?,?,?)", rows)
        con.execute("INSERT OR REPLACE INTO news_checked VALUES (?, ?)", [t, today])
    print(f"news: checked {len(todo)} companies")


def recent(con, tkr, n=8):
    try:
        return con.execute("SELECT filed, form, what, url FROM news WHERE tkr = ? "
                           "ORDER BY filed DESC LIMIT ?", [tkr, n]).fetchall()
    except Exception:  # table not created yet
        return []


def selftest():
    import duckdb
    sub = {"filings": {"recent": {
        "form": ["4", "8-K", "10-Q", "8-K", "SC 13G/A"],
        "filingDate": ["2026-08-10", "2026-08-01", "2026-07-30", "2026-07-02", "2026-06-01"],
        "accessionNumber": ["0001-26-000005", "0001-26-000004", "0001-26-000003",
                            "0001-26-000002", "0001-26-000001"],
        "items": ["", "2.02,9.01", "", "5.02", ""],
        "primaryDocument": ["f4.xml", "ex.htm", "q.htm", "", "g.htm"]}}}
    rows = parse(sub, "AAA", 123)
    assert [r[3] for r in rows] == ["Reported quarterly results", "Quarterly report",
                                    "Executive or board member change",
                                    "A big investor passed 5% ownership (amended)"], rows
    assert rows[0][4] == "https://www.sec.gov/Archives/edgar/data/123/000126000004/ex.htm"
    con = duckdb.connect()
    ensure_table(con)
    con.executemany("INSERT INTO news VALUES (?,?,?,?,?)", rows)
    assert recent(con, "AAA", 2)[0][2] == "Reported quarterly results"
    assert recent(duckdb.connect(), "AAA") == []
    print("news selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        import duckdb
        from schema import DB_PATH, load_dotenv
        load_dotenv()
        c = duckdb.connect(DB_PATH)
        refresh(c, sys.argv[1:])
        for t in sys.argv[1:]:
            for row in recent(c, t.upper()):
                print(t.upper(), *row)
