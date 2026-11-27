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


STOP = {"capital", "management", "group", "partners", "advisors", "advisers", "holdings",
        "fund", "funds", "trust", "investment", "investments", "investors", "associates",
        "global", "asset", "assets", "foundation", "company", "co", "corp", "inc", "llc",
        "lp", "ltd", "the", "and", "of", "financial", "value", "family", "office", "s"}


def _note(line):
    """'1067983 Berkshire  # Berkshire Hathaway (Warren Buffett / x)' ->
    (cik, label, firm, person) or None for comments and blank lines."""
    body, _, note = line.partition("#")
    body = body.strip()
    if not body:
        return None
    cik, _, label = body.partition(" ")
    m = re.search(r"\(([^)/]+)", note)
    firm = re.split(r"[(,]", note, maxsplit=1)[0].strip()
    return int(cik), label.strip() or cik, firm or label.strip(), m.group(1).strip() if m else None


def catalog(path="funds.txt"):
    """[{cik, label, firm, person, group}] in file order. '## Heading' lines
    set the group for the funds under them."""
    out, group = [], "Funds"
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            if line.startswith("##"):
                group = line.strip("# \n") or group
                continue
            n = _note(line)
            if n:
                out.append(dict(cik=n[0], label=n[1], firm=n[2], person=n[3], group=group))
    return out


def read_funds(path="funds.txt"):
    return [(f["cik"], f["label"]) for f in catalog(path)]


def managers(path="funds.txt"):
    """{cik: (label, person)} from funds.txt; the person is the first name in
    the comment's parentheses, e.g. '(Warren Buffett / successors)'."""
    return {f["cik"]: (f["label"], f["person"]) for f in catalog(path)}


def _words(s):
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if w not in STOP}


def name_matches(sec_name, label, firm=""):
    """Does SEC's official filer name share a real word with our label or the
    firm named in funds.txt? Guards against a mistyped CIK showing the wrong fund."""
    return bool(_words(sec_name) & (_words(label.replace("-", " ")) | _words(firm)))


def slug(label):
    return re.sub(r"[^A-Za-z0-9]+", "-", label).strip("-").lower() or "fund"


def href(label, root=""):
    return f"{root}f/{slug(label)}.html"


def last_quarter_end(today):
    """The most recent quarter end before `today`."""
    y, q = today.year, (today.month - 1) // 3  # quarters fully behind us this year
    if q == 0:
        return datetime.date(y - 1, 12, 31)
    return datetime.date(y, 3 * q, 31 if q in (1, 4) else 30)


def ensure_tables(con):
    con.execute("""CREATE TABLE IF NOT EXISTS fund_holdings (
        fund VARCHAR, cik BIGINT, period DATE, filed DATE, issuer VARCHAR,
        cusip VARCHAR, value DOUBLE, shares DOUBLE, tkr VARCHAR,
        PRIMARY KEY (cik, period, cusip))""")
    # weight / prev_weight: share of the fund's reported portfolio, so a 0.01%
    # position in a 900-stock fund doesn't count the same as a top holding
    con.execute("""CREATE OR REPLACE VIEW fund_changes AS
        WITH p AS (SELECT cik, period, dense_rank() OVER (PARTITION BY cik ORDER BY period DESC) AS k
                   FROM (SELECT DISTINCT cik, period FROM fund_holdings)),
        cur AS (SELECT h.*, h.value / nullif(sum(h.value) OVER (PARTITION BY h.cik), 0) AS w
                FROM fund_holdings h JOIN p USING (cik, period) WHERE k = 1),
        prev AS (SELECT h.*, h.value / nullif(sum(h.value) OVER (PARTITION BY h.cik), 0) AS w
                 FROM fund_holdings h JOIN p USING (cik, period) WHERE k = 2),
        has_prev AS (SELECT DISTINCT cik FROM prev),
        latest AS (SELECT cik, max(period) AS period, max(filed) AS filed FROM cur GROUP BY cik)
        SELECT coalesce(c.fund, pr.fund) AS fund, coalesce(c.cik, pr.cik) AS cik,
               coalesce(c.tkr, pr.tkr) AS tkr, coalesce(c.issuer, pr.issuer) AS issuer,
               l.period, l.filed, c.value, c.shares, pr.shares AS prev_shares,
               CASE WHEN pr.shares IS NULL THEN 'new'
                    WHEN c.shares IS NULL THEN 'sold'
                    WHEN c.shares > pr.shares * 1.10 THEN 'added'
                    WHEN c.shares < pr.shares * 0.90 THEN 'trimmed'
                    ELSE 'held' END AS change,
               coalesce(c.cusip, pr.cusip) AS cusip, c.w AS weight, pr.w AS prev_weight
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


def share_tickers(con):
    """A CUSIP is one security everywhere: if any fund's row for it got a
    ticker (funds name the same company differently), give it to all rows."""
    con.execute("""UPDATE fund_holdings h SET tkr = m.tkr
        FROM (SELECT cusip, min(tkr) AS tkr FROM fund_holdings WHERE tkr IS NOT NULL
              GROUP BY cusip HAVING count(DISTINCT tkr) = 1) m
        WHERE h.cusip = m.cusip AND h.tkr IS NULL""")


def fetch(con, funds=None, today=None):
    """Fetch the latest two 13Fs of each fund we don't have yet. A fund that
    already reported the last finished quarter is skipped without asking SEC,
    so most days this costs a few seconds even with dozens of funds."""
    ensure_tables(con)
    cat = {f["cik"]: f for f in catalog()}
    funds = funds or [(c, f["label"]) for c, f in cat.items()]
    if not funds:
        print("funds.txt is empty")
        return
    due = last_quarter_end(today or datetime.date.today())
    have = {(r[0], str(r[1])) for r in con.execute(
        "SELECT DISTINCT cik, period FROM fund_holdings").fetchall()}
    newest = dict(con.execute("SELECT cik, max(period) FROM fund_holdings GROUP BY 1").fetchall())
    todo = [(c, l) for c, l in funds if not (newest.get(c) and newest[c] >= due)]
    print(f"{len(funds)} funds; {len(funds) - len(todo)} already reported the quarter to {due}")
    if not todo:
        return
    s = requests.Session()
    s.headers["User-Agent"] = sec_user_agent()
    resolver = make_sec_resolver(load_sec_names(s.get(TICKERS_URL, timeout=30).json()))
    added = 0
    for cik, label in todo:
        time.sleep(0.12)
        try:
            sub = s.get(SUBMISSIONS.format(cik=cik), timeout=30)
        except requests.RequestException as e:
            print(f"{label}: SEC didn't answer ({type(e).__name__})")
            continue
        if sub.status_code != 200:
            print(f"{label}: CIK {cik} not found at SEC -- check funds.txt")
            continue
        sub = sub.json()
        name = sub.get("name") or ""
        if not name_matches(name, label, cat.get(cik, {}).get("firm", "")):
            print(f"{label}: CIK {cik} is '{name}' at SEC, which doesn't look like the same firm -- "
                  "skipped; check the number in funds.txt")
            continue
        for acc, period, filed in recent_13fs(sub):
            if (cik, period) in have:
                continue
            nodash = acc.replace("-", "")
            try:
                time.sleep(0.12)
                idx = s.get(INDEX.format(cik=cik, acc=nodash), timeout=30).json()
                xmls = [i["name"] for i in idx["directory"]["item"]
                        if i["name"].lower().endswith(".xml") and "primary_doc" not in i["name"].lower()]
                if not xmls:
                    print(f"  {label} {period}: no information table found")
                    continue
                time.sleep(0.12)
                rows = parse_info_table(s.get(DOC.format(cik=cik, acc=nodash, name=xmls[0]),
                                              timeout=90).text)
            except (requests.RequestException, ValueError, ET.ParseError, KeyError) as e:
                print(f"  {label} {period}: skipped ({type(e).__name__})")
                continue
            for r in rows:
                hit = resolver(r["issuer"])
                r["tkr"] = hit[0] if hit else None
            con.executemany("INSERT OR REPLACE INTO fund_holdings VALUES (?,?,?,?,?,?,?,?,?)",
                            [(label, cik, period, filed, r["issuer"], r["cusip"], r["value"],
                              r["shares"], r["tkr"]) for r in rows])
            added += 1
            print(f"  {label} ({name}) {period}: {len(rows)} positions "
                  f"({sum(1 for r in rows if r['tkr'])} matched to tickers)")
    share_tickers(con)
    print(f"{added} new 13F reports saved")


BIG = 0.01  # a position is "real money" at 1%+ of the fund's reported portfolio


def changes(con, since=None, min_weight=BIG):
    """Interesting moves (new / added / sold) from the latest 13Fs, optionally
    only those filed on or after `since`. Only moves of at least `min_weight`
    of the portfolio count, so big diversified funds don't flood the list."""
    try:
        q = ("SELECT * FROM fund_changes WHERE ((change IN ('new','added') AND weight >= ?)"
             " OR (change = 'sold' AND prev_weight >= ?))")
        params = [min_weight, min_weight]
        if since:
            q += " AND filed >= ?"
            params.append(since)
        from signals import clean
        return clean(con.execute(q + " ORDER BY fund, CASE change WHEN 'new' THEN 0 WHEN 'added' THEN 1 ELSE 2 END,"
                                 " value DESC NULLS LAST", params).df())
    except duckdb.CatalogException:
        return None


def holders(con, tickers, min_weight=BIG):
    """{tkr: ['Berkshire (added)', ...]} from each fund's latest quarter,
    counting only positions worth `min_weight` of the fund or more."""
    try:
        rows = con.execute("""SELECT tkr, fund, change FROM fund_changes
                              WHERE tkr IN (SELECT unnest(?)) AND change <> 'sold' AND weight >= ?
                              ORDER BY weight DESC""",
                           [list(tickers), min_weight]).fetchall()
    except duckdb.CatalogException:
        return {}
    out = {}
    for t, fund, ch in rows:
        out.setdefault(t, []).append(fund + ("" if ch == "held" else f" ({ch})"))
    return out


# ------------------------------------------------------------------ for the pages

def latest(con):
    """Each fund's latest quarter, one row per position, with the change from
    the quarter before (funds with a single quarter on file count as 'held')."""
    try:
        return con.execute("""
            WITH p AS (SELECT cik, period, dense_rank() OVER (PARTITION BY cik ORDER BY period DESC) AS k
                       FROM (SELECT DISTINCT cik, period FROM fund_holdings)),
            cur AS (SELECT h.* FROM fund_holdings h JOIN p USING (cik, period) WHERE k = 1)
            SELECT c.cik, c.fund, c.period, c.filed, c.issuer, c.cusip, c.value, c.shares, c.tkr,
                   c.value / nullif(sum(c.value) OVER (PARTITION BY c.cik), 0) AS weight,
                   coalesce(f.change, 'held') AS change, f.prev_shares
            FROM cur c LEFT JOIN fund_changes f ON f.cik = c.cik AND f.cusip = c.cusip
            ORDER BY c.cik, c.value DESC""").fetchall()
    except duckdb.CatalogException:
        return []


def sold(con):
    """{cik: [(tkr, issuer, prev_weight)]}: positions closed in the latest quarter."""
    try:
        rows = con.execute("""SELECT cik, tkr, issuer, prev_weight FROM fund_changes
                              WHERE change = 'sold' ORDER BY prev_weight DESC NULLS LAST""").fetchall()
    except duckdb.CatalogException:
        return {}
    out = {}
    for cik, t, i, w in rows:
        out.setdefault(cik, []).append((t, i, w))
    return out


def portfolios(con):
    """{cik: {"period", "filed", "total", "rows": [dict, ...]}} biggest position first."""
    out = {}
    for (cik, fund, period, filed, issuer, cusip, value, shares, tkr, w, ch, prev) in latest(con):
        p = out.setdefault(cik, {"period": period, "filed": filed, "total": 0.0, "rows": []})
        p["total"] += value or 0
        p["rows"].append(dict(issuer=issuer, cusip=cusip, value=value or 0, shares=shares,
                              tkr=tkr, weight=w or 0, change=ch, prev_shares=prev))
    return out


def popular(ports, names, min_weight=BIG, top=20):
    """(most_owned, most_bought): [(tkr, issuer, [fund labels])] across funds,
    counting real positions only (min_weight of the fund or more)."""
    owned, bought = {}, {}
    for cik, p in ports.items():
        for r in p["rows"]:
            key = r["tkr"] or r["cusip"]
            if r["weight"] < min_weight or not key:
                continue
            owned.setdefault(key, [r["tkr"], r["issuer"], []])[2].append(names.get(cik, str(cik)))
            if r["change"] in ("new", "added"):
                bought.setdefault(key, [r["tkr"], r["issuer"], []])[2].append(names.get(cik, str(cik)))
    rank = lambda d: sorted((tuple(v) for v in d.values() if len(v[2]) >= 2),
                            key=lambda v: (-len(v[2]), v[0] or v[1]))[:top]
    return rank(owned), rank(bought)


def page_tickers(con, per_fund=25):
    """Tickers worth a stock page because a tracked fund owns them in size:
    each fund's top holdings plus anything worth 1%+ of a fund."""
    out = []
    for p in portfolios(con).values():
        out += [r["tkr"] for i, r in enumerate(p["rows"]) if r["tkr"] and (i < per_fund or r["weight"] >= BIG)]
    return list(dict.fromkeys(out))


_OWNERS = {}


def owners(con, tkr):
    """[(fund, weight, change)] for funds holding `tkr` in their latest quarter,
    biggest bet first. Built once per database connection (stock pages ask a lot)."""
    try:
        key = (id(con),) + con.execute("SELECT count(*), max(filed) FROM fund_holdings").fetchone()
    except duckdb.CatalogException:
        return []
    if key not in _OWNERS:
        _OWNERS.clear()
        idx = {}
        for r in latest(con):
            if r[8]:
                idx.setdefault(r[8], []).append((r[1], r[9] or 0, r[10]))
        _OWNERS[key] = {t: sorted(v, key=lambda x: -x[1]) for t, v in idx.items()}
    return _OWNERS[key].get(tkr, [])


def owners_panel(con, tkr, root="../"):
    """'Big investors who own it' for a stock page ('' when none do)."""
    import html
    rows = owners(con, tkr)
    if not rows:
        return ""
    word = {"new": "new this quarter", "added": "added", "trimmed": "trimmed", "held": ""}
    items = "".join(
        f"<li><a href='{root}{href(f)}'><b>{html.escape(f)}</b></a> "
        f"<span class='hint'>· {w * 100:.1f}% of its portfolio"
        f"{' · ' + word.get(c, c) if word.get(c, c) else ''}</span></li>" for f, w, c in rows[:12])
    more = f"<li class='hint'>…and {len(rows) - 12} more.</li>" if len(rows) > 12 else ""
    return ("<section class='panel'><h2>Big investors who own it</h2><ul class='list'>" + items + more
            + "</ul><p class='hint'>From each fund's latest quarterly 13F report (filed up to 45 days "
              f"after the quarter ends). <a href='{root}funds.html'>All tracked funds</a></p></section>")


def home_teaser(con, today=None):
    """A few lines for the home page's Funds tile: the latest notable buys."""
    import html
    f = changes(con)
    if f is None or not len(f):
        return "<p class='hint'>Quarterly holdings from top investors fill in after the next 13F run.</p>"
    f = f[f.change.isin(["new", "added"])].sort_values("filed", ascending=False).head(4)
    word = {"new": "started buying", "added": "bought more"}
    return "<ul class='teaser'>" + "".join(
        f"<li><a href='{href(r.fund)}'><b>{html.escape(r.fund)}</b></a> {word[r.change]} "
        f"<b>{html.escape(r.tkr or (r.issuer or '').title())}</b></li>" for r in f.itertuples()) + "</ul>"


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
    # the weight filters: a 0.5% position doesn't count as a fund "owning" it
    con.execute("INSERT INTO fund_holdings VALUES ('Berkshire', 1, DATE '2025-03-31', DATE '2025-05-15', "
                "'TINY CO', 't', 0.01, 1, 'TNY')")
    assert "TNY" not in holders(con, ["TNY"]) and "TNY" not in set(changes(con).tkr)
    assert "TNY" in set(changes(con, min_weight=0).tkr)
    ports = portfolios(con)
    assert ports[1]["rows"][0]["tkr"] in ("AAA", "BBB", "CCC", "EEE") and ports[1]["rows"][-1]["tkr"] == "TNY"
    assert ports[2]["rows"][0]["change"] == "held", "a fund with one quarter still shows its holdings"
    assert {s[0] for s in sold(con)[1]} == {"DDD"}
    owned, bought = popular(ports, {1: "Berkshire", 2: "Solo"})
    assert owned == [("AAA", "AAA", ["Berkshire", "Solo"])] and bought == [], (owned, bought)
    assert "AAA" in page_tickers(con) and [o[0] for o in owners(con, "AAA")] == ["Solo", "Berkshire"]
    assert "Big investors who own it" in owners_panel(con, "AAA") and owners_panel(con, "ZZZ") == ""
    assert "started buying" in home_teaser(con)
    con.execute("INSERT INTO fund_holdings VALUES ('Solo', 2, DATE '2025-03-31', DATE '2025-05-15', "
                "'B B B CORP', 'b', 1, 1, NULL)")
    share_tickers(con)
    assert con.execute("SELECT tkr FROM fund_holdings WHERE cik = 2 AND cusip = 'b'").fetchone()[0] == "BBB"

    assert last_quarter_end(datetime.date(2026, 11, 27)) == datetime.date(2026, 9, 30)
    assert last_quarter_end(datetime.date(2026, 2, 1)) == datetime.date(2025, 12, 31)
    assert last_quarter_end(datetime.date(2026, 7, 1)) == datetime.date(2026, 6, 30)
    assert last_quarter_end(datetime.date(2026, 4, 15)) == datetime.date(2026, 3, 31)
    assert name_matches("BERKSHIRE HATHAWAY INC", "Berkshire")
    assert name_matches("ICAHN CARL C", "Icahn", "Icahn")
    assert name_matches("Dalal Street, LLC", "Pabrai", "Dalal Street")
    assert not name_matches("SOME OTHER CAPITAL MANAGEMENT LLC", "Baupost", "Baupost Group")
    assert slug("Tweedy-Browne") == "tweedy-browne" and href("D1", "../") == "../f/d1.html"
    cat = catalog()
    assert len(cat) >= 50 and len({f["cik"] for f in cat}) == len(cat), "no duplicate CIKs"
    assert len({slug(f["label"]) for f in cat}) == len(cat), "each fund gets its own page"
    b = next(f for f in cat if f["label"] == "Berkshire")
    assert b["firm"] == "Berkshire Hathaway" and b["person"] == "Warren Buffett" and b["group"] == "Value investors"
    assert all(name_matches(f["firm"], f["label"], f["firm"]) for f in cat)
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
