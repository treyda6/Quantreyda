"""What a few proven long-term investors own, from their quarterly 13F filings.

Every fund managing $100M+ in US stocks files a 13F each quarter listing its
holdings. They're filed up to 45 days after the quarter ends, so this is
slow information -- useful for "long-term pros like this company too", not
for timing.

The funds to follow live in funds.txt (SEC CIK number + a label). The
defaults are long-term, concentrated investors. Edit freely; the script
prints each filer's official name so you can check the number is right.

    py funds.py               # fetch the latest two 13Fs per fund, print changes
    py funds.py --selftest

Tables:
  fund_holdings  -- one row per fund / quarter / stock
  fund_changes   -- view: latest quarter vs the one before (new, added, trimmed, sold)

13Fs list companies by name and CUSIP, not ticker. Tickers are matched by
name against SEC's company list (exact match after cleanup), so some
holdings stay unmatched -- they still show by name.
"""

import datetime
import os
import re
import sys
import time
import xml.etree.ElementTree as ET

import duckdb
import requests

from entities import load_sec_names, make_sec_resolver
from schema import DB_PATH, sec_user_agent

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
INDEX = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/index.json"
DOC = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{name}"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


def read_funds(path="funds.txt"):
    out = []
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.split("#", 1)[0].strip()
            if line:
                cik, _, label = line.partition(" ")
                out.append((int(cik), label.strip() or cik))
    return out


def managers(path="funds.txt"):
    """{cik: (label, person)} from funds.txt; the person is the first name in
    the comment's parentheses, e.g. '(Warren Buffett / successors)'."""
    out = {}
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            body, _, note = line.partition("#")
            body = body.strip()
            if not body:
                continue
            cik, _, label = body.partition(" ")
            m = re.search(r"\(([^)/]+)", note)
            out[int(cik)] = (label.strip() or cik, m.group(1).strip() if m else None)
    return out


def ensure_tables(con):
    con.execute("""CREATE TABLE IF NOT EXISTS fund_holdings (
        fund VARCHAR, cik BIGINT, period DATE, filed DATE, issuer VARCHAR,
        cusip VARCHAR, value DOUBLE, shares DOUBLE, tkr VARCHAR,
        PRIMARY KEY (cik, period, cusip))""")
    con.execute("""CREATE OR REPLACE VIEW fund_changes AS
        WITH p AS (SELECT cik, period, dense_rank() OVER (PARTITION BY cik ORDER BY period DESC) AS k
                   FROM (SELECT DISTINCT cik, period FROM fund_holdings)),
        cur AS (SELECT h.* FROM fund_holdings h JOIN p USING (cik, period) WHERE k = 1),
        prev AS (SELECT h.* FROM fund_holdings h JOIN p USING (cik, period) WHERE k = 2),
        has_prev AS (SELECT DISTINCT cik FROM prev),
        latest AS (SELECT cik, max(period) AS period, max(filed) AS filed FROM cur GROUP BY cik)
        SELECT coalesce(c.fund, pr.fund) AS fund, coalesce(c.cik, pr.cik) AS cik,
               coalesce(c.tkr, pr.tkr) AS tkr, coalesce(c.issuer, pr.issuer) AS issuer,
               l.period, l.filed, c.value, c.shares, pr.shares AS prev_shares,
               CASE WHEN pr.shares IS NULL THEN 'new'
                    WHEN c.shares IS NULL THEN 'sold'
                    WHEN c.shares > pr.shares * 1.10 THEN 'added'
                    WHEN c.shares < pr.shares * 0.90 THEN 'trimmed'
                    ELSE 'held' END AS change
        FROM cur c FULL JOIN prev pr ON c.cik = pr.cik AND c.cusip = pr.cusip
        JOIN latest l ON l.cik = coalesce(c.cik, pr.cik)
        WHERE coalesce(c.cik, pr.cik) IN (SELECT cik FROM has_prev)""")


def parse_info_table(xml_text):
    """13F information table XML -> [{issuer, cusip, value, shares}], stock
    positions only (puts/calls dropped), same CUSIP rows summed."""
    xml_text = re.sub(r"""\sxmlns(:\w+)?=("[^"]*"|'[^']*')""", "", xml_text)
    # prefixed attributes (xsi:schemaLocation=...) would leave an unbound prefix
    xml_text = re.sub(r"""\s\w+:\w+=("[^"]*"|'[^']*')""", "", xml_text)
    xml_text = re.sub(r"<(/?)\w+:", r"<\1", xml_text)  # drop ns prefixes
    root = ET.fromstring(xml_text)
    out = {}
    for it in root.iter("infoTable"):
        if (it.findtext("putCall") or "").strip():
            continue
        if (it.findtext("shrsOrPrnAmt/sshPrnamtType") or "SH").strip() != "SH":
            continue
        cusip = (it.findtext("cusip") or "").strip().upper()
        row = out.setdefault(cusip, {"issuer": (it.findtext("nameOfIssuer") or "").strip(),
                                     "cusip": cusip, "value": 0.0, "shares": 0.0})
        row["value"] += float(it.findtext("value") or 0)
        row["shares"] += float(it.findtext("shrsOrPrnAmt/sshPrnamt") or 0)
    return list(out.values())


def recent_13fs(submissions, n=2):
    """[(accession, period, filed)] newest first; amendments skipped."""
    r = submissions["filings"]["recent"]
    rows = [(a, p, f) for a, form, p, f in zip(r["accessionNumber"], r["form"],
                                                r["reportDate"], r["filingDate"])
            if form == "13F-HR"]
    seen, out = set(), []
    for a, p, f in sorted(rows, key=lambda x: (x[1], x[2]), reverse=True):
        if p not in seen:
            seen.add(p)
            out.append((a, p, f))
    return out[:n]


def fetch(con, funds=None):
    ensure_tables(con)
    funds = funds or read_funds()
    if not funds:
        print("funds.txt is empty")
        return
    s = requests.Session()
    s.headers["User-Agent"] = sec_user_agent()
    resolver = make_sec_resolver(load_sec_names(s.get(TICKERS_URL, timeout=30).json()))
    have = {(r[0], str(r[1])) for r in con.execute(
        "SELECT DISTINCT cik, period FROM fund_holdings").fetchall()}
    for cik, label in funds:
        time.sleep(0.15)
        sub = s.get(SUBMISSIONS.format(cik=cik), timeout=30)
        if sub.status_code != 200:
            print(f"{label}: CIK {cik} not found at SEC -- check funds.txt")
            continue
        sub = sub.json()
        print(f"{label}: SEC name '{sub.get('name')}'")
        for acc, period, filed in recent_13fs(sub):
            if (cik, period) in have:
                continue
            nodash = acc.replace("-", "")
            time.sleep(0.15)
            idx = s.get(INDEX.format(cik=cik, acc=nodash), timeout=30).json()
            xmls = [i["name"] for i in idx["directory"]["item"]
                    if i["name"].lower().endswith(".xml") and "primary_doc" not in i["name"].lower()]
            if not xmls:
                print(f"  {period}: no information table found")
                continue
            time.sleep(0.15)
            rows = parse_info_table(s.get(DOC.format(cik=cik, acc=nodash, name=xmls[0]),
                                          timeout=60).text)
            for r in rows:
                hit = resolver(r["issuer"])
                r["tkr"] = hit[0] if hit else None
            con.executemany("INSERT OR REPLACE INTO fund_holdings VALUES (?,?,?,?,?,?,?,?,?)",
                            [(label, cik, period, filed, r["issuer"], r["cusip"], r["value"],
                              r["shares"], r["tkr"]) for r in rows])
            print(f"  {period}: {len(rows)} positions "
                  f"({sum(1 for r in rows if r['tkr'])} matched to tickers)")


def changes(con, since=None):
    """Interesting moves (new / added / sold) from the latest 13Fs, optionally
    only those filed on or after `since`."""
    try:
        q = "SELECT * FROM fund_changes WHERE change IN ('new','added','sold')"
        params = []
        if since:
            q += " AND filed >= ?"
            params.append(since)
        from signals import clean
        return clean(con.execute(q + " ORDER BY fund, CASE change WHEN 'new' THEN 0 WHEN 'added' THEN 1 ELSE 2 END,"
                                 " value DESC NULLS LAST", params).df())
    except duckdb.CatalogException:
        return None


def holders(con, tickers):
    """{tkr: ['Berkshire (added)', ...]} from each fund's latest quarter."""
    try:
        rows = con.execute("""SELECT tkr, fund, change FROM fund_changes
                              WHERE tkr IN (SELECT unnest(?)) AND change <> 'sold'""",
                           [list(tickers)]).fetchall()
    except duckdb.CatalogException:
        return {}
    out = {}
    for t, fund, ch in rows:
        out.setdefault(t, []).append(fund + ("" if ch == "held" else f" ({ch})"))
    return out


# ------------------------------------------------------------------ selftest

SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
  <infoTable><nameOfIssuer>APPLE INC</nameOfIssuer><titleOfClass>COM</titleOfClass>
    <cusip>037833100</cusip><value>1000</value>
    <shrsOrPrnAmt><sshPrnamt>10</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt></infoTable>
  <infoTable><nameOfIssuer>APPLE INC</nameOfIssuer><titleOfClass>COM</titleOfClass>
    <cusip>037833100</cusip><value>500</value>
    <shrsOrPrnAmt><sshPrnamt>5</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt></infoTable>
  <infoTable><nameOfIssuer>APPLE INC</nameOfIssuer><cusip>037833100</cusip><value>9</value>
    <shrsOrPrnAmt><sshPrnamt>1</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>
    <putCall>Put</putCall></infoTable>
  <infoTable><nameOfIssuer>SOME BOND</nameOfIssuer><cusip>000000AA0</cusip><value>9</value>
    <shrsOrPrnAmt><sshPrnamt>1</sshPrnamt><sshPrnamtType>PRN</sshPrnamtType></shrsOrPrnAmt></infoTable>
</informationTable>"""


def load_fixture(con):
    ensure_tables(con)
    D = datetime.date
    rows = [  # Berkshire: AAA new, BBB added, CCC trimmed, DDD sold, EEE held
        ("Berkshire", 1, D(2024, 12, 31), D(2025, 2, 14), "BBB", "b", 1, 100, "BBB"),
        ("Berkshire", 1, D(2024, 12, 31), D(2025, 2, 14), "CCC", "c", 1, 100, "CCC"),
        ("Berkshire", 1, D(2024, 12, 31), D(2025, 2, 14), "DDD", "d", 1, 100, "DDD"),
        ("Berkshire", 1, D(2024, 12, 31), D(2025, 2, 14), "EEE", "e", 1, 100, "EEE"),
        ("Berkshire", 1, D(2025, 3, 31), D(2025, 5, 15), "AAA", "a", 1, 50, "AAA"),
        ("Berkshire", 1, D(2025, 3, 31), D(2025, 5, 15), "BBB", "b", 1, 150, "BBB"),
        ("Berkshire", 1, D(2025, 3, 31), D(2025, 5, 15), "CCC", "c", 1, 50, "CCC"),
        ("Berkshire", 1, D(2025, 3, 31), D(2025, 5, 15), "EEE", "e", 1, 101, "EEE"),
        # a fund with only one quarter has no "changes" yet
        ("Solo", 2, D(2025, 3, 31), D(2025, 5, 15), "AAA", "a", 1, 50, "AAA"),
    ]
    con.executemany("INSERT INTO fund_holdings VALUES (?,?,?,?,?,?,?,?,?)", rows)


def selftest():
    # single-quoted namespaces + a prefixed xsi:schemaLocation (seen live on
    # Pershing Square's 13F) used to fail with "unbound prefix"
    odd = ("<?xml version='1.0'?>\n<ns1:informationTable xmlns:xsi='http://w3.org/x' "
           "xsi:schemaLocation='a b' xmlns:ns1='http://sec.gov/it'><ns1:infoTable>"
           "<ns1:nameOfIssuer>ALPHABET INC</ns1:nameOfIssuer><ns1:cusip>02079K107</ns1:cusip>"
           "<ns1:value>100</ns1:value><ns1:shrsOrPrnAmt><ns1:sshPrnamt>5</ns1:sshPrnamt>"
           "<ns1:sshPrnamtType>SH</ns1:sshPrnamtType></ns1:shrsOrPrnAmt></ns1:infoTable>"
           "</ns1:informationTable>")
    assert parse_info_table(odd)[0]["cusip"] == "02079K107"
    rows = parse_info_table(SAMPLE_XML)
    assert rows == [{"issuer": "APPLE INC", "cusip": "037833100", "value": 1500.0, "shares": 15.0}], rows
    sub = {"filings": {"recent": {
        "accessionNumber": ["a3", "a2", "a1", "x"], "form": ["13F-HR", "13F-HR", "13F-HR", "4"],
        "reportDate": ["2025-06-30", "2025-03-31", "2024-12-31", ""],
        "filingDate": ["2025-08-14", "2025-05-15", "2025-02-14", "2025-01-01"]}}}
    assert recent_13fs(sub) == [("a3", "2025-06-30", "2025-08-14"), ("a2", "2025-03-31", "2025-05-15")]

    con = duckdb.connect()
    load_fixture(con)
    ch = dict(con.execute("SELECT tkr, change FROM fund_changes").fetchall())
    assert ch == {"AAA": "new", "BBB": "added", "CCC": "trimmed", "DDD": "sold", "EEE": "held"}, ch
    assert holders(con, ["AAA", "EEE", "DDD"]) == {"AAA": ["Berkshire (new)"], "EEE": ["Berkshire"]}
    assert set(changes(con).tkr) == {"AAA", "BBB", "DDD"}
    assert changes(con, since=datetime.date(2025, 6, 1)).empty, "only 13Fs filed in the window"
    assert set(changes(con, since=datetime.date(2025, 5, 15)).tkr) == {"AAA", "BBB", "DDD"}
    print("funds selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        c = duckdb.connect(DB_PATH)
        fetch(c)
        df = changes(c)
        if df is not None and len(df):
            print(df[["fund", "change", "tkr", "issuer", "value"]].to_string(index=False))
