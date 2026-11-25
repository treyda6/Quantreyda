"""White House and cabinet trades: OGE Form 278-T periodic transaction reports.

Senior executive-branch officials (the President, the Vice President,
cabinet secretaries and other Senate-confirmed appointees) must report each
stock, bond or fund trade over $1,000 within 45 days on OGE Form 278-T, in
the same dollar ranges Congress uses.

Where the filings come from: OGE itself only hands out the President's and
VP's reports by emailed request, so this reads ProPublica's free public
"Trump Team Financial Disclosures" archive, which mirrors every report as a
PDF on DocumentCloud. The PDF-reading approach and the OCR clean-up rules are
adapted from Quantgress's scrape_trump.py (MIT License, (c) 2026 Dhruv
Mulajkar), generalised here to any official.

Most of what senior officials trade is bonds (the President's reports are
almost entirely municipal bonds), so every row gets a `kind`:
  stock           shares of a company        -> matched to a ticker
  corporate bond  a company's debt           -> matched to a ticker if possible
  muni bond       a state/city/school bond   -> no ticker
  fund / other    funds, Treasuries, unclear
Only `stock` buys feed the ideas and the conviction score; bonds are shown
as plain totals.

Who is tracked: whitehouse_officials.txt (one per line: "slug | name | title",
slug may be left blank to look it up by name).

Usage:
    py whitehouse.py               # fetch new reports for everyone listed
    py whitehouse.py --limit 3     # a few reports per person (testing)
    py whitehouse.py --selftest    # offline checks
"""

import datetime
import io
import re
import sys
import time

import requests

from schema import sec_user_agent

SITE = "https://projects.propublica.org/trump-team-financial-disclosures"
INDEX_PAGE = SITE + "/appointees/{slug}"
SEARCH_PAGE = SITE + "/search"
PDF_URL = "https://s3.documentcloud.org/documents/{doc_id}/{slug}.pdf"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
OFFICIALS_FILE = "whitehouse_officials.txt"
UA = "Quantreyda personal research tool (github.com/treyda6/Quantreyda)"

# One filing record in the appointee page's inline data (unquoted keys, so a
# regex rather than a JSON parser).
DOC_RE = re.compile(
    r'\{id:"\d+",name:"[^"]*",title:"[^"]*",slug:"(?P<slug>[^"]*)".*?'
    r'did:"(?P<did>\d+)",url:"(?P<url>[^"]*)",file:"(?P<file>[^"]*)",'
    r'form_type:"(?P<form_type>[^"]*)"')
DOC_URL_RE = re.compile(r"documents/(\d+)-(.+?)/?$")
SLUG_RE = re.compile(r"/appointees/([a-z0-9-]+)")

BRACKETS = {1001: 15000, 15001: 50000, 50001: 100000, 100001: 250000, 250001: 500000,
            500001: 1000000, 1000001: 5000000, 5000001: 25000000, 25000001: 50000000,
            50000001: None}

# ------------------------------------------------------------------ parsing
# (adapted from Quantgress scrape_trump.py, MIT)

TYPE_RE = r"\S*(?:[uo]rch\S*|sal[eo]\S*|xch[ae]ng\S*)"
TAIL_RE = re.compile(
    rf"(?P<type>{TYPE_RE})\s+(?P<date>\d{{1,2}}/\d{{1,2}}/\d{{2,4}})\s+"
    r"(?P<flag>\S+)\s+(?P<amt>[\$sS].*\d.*)$", re.I)
NUM_RE = re.compile(r"^\s*(\d{1,3})\s+")
NUM_ONLY_RE = re.compile(r"^\s*(\d{1,3})\s*$")
RECEIVED_RE = re.compile(r"OGE RECEIVED:?\s*(\d{1,2}/\d{1,2}/\d{2,4})")
ROW_START_RE = re.compile(r"^(\d{1,3})\s+\S")
SKIP_RE = re.compile(
    r"^(OGE Form 278|If [Yy]o[uy] n[o0]{2}d|N[o0]te[:.·]|Fl[il]e[rc]'?[sc]|"
    r"Trans[au]d?[l1]ion|Notification|Received O|D[hn]crl|Page \d|"
    r"Summary of Contents|Privacy Act|U\.S\. [O0]|"
    r"C.OVERNM|Exocutivo Branch|Periodic Transaction|OGE RECEIVED|"
    r"#\s+DESCRIPTION|[-_=•]+$)", re.I)


def clean_amount_ocr(raw):
    s = re.sub(r"[oO]", "0", raw)
    s = re.sub(r"(^|[-\s])[sS]+(?=\d)", r"\1$", s)
    return s.replace(".", ",").replace(";", ",")


def parse_amount_ocr(raw):
    """'$250,001 - $500,000' (or an OCR-garbled variant) -> (250001, 500000)."""
    nums = [int(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", clean_amount_ocr(raw))]
    if not nums:
        return None, None
    low = nums[0]
    if low in BRACKETS:
        return low, BRACKETS[low]
    return low, (nums[1] if len(nums) > 1 else None)


def classify_type(token):
    t = token.lower()
    if "sal" in t:
        return "Sale"
    if "xch" in t:
        return "Exchange"
    if re.search(r"[uo]rch", t):
        return "Purchase"
    return None


def classify_late(token):
    t = token.lower()
    if t == "no":
        return False
    if re.match(r"^[vy][eo0]s$", t):
        return True
    return None


def parse_text(text):
    """(rows, filed_date, expected_n) from a 278-T's text. Scanned reports have
    certification pages before the table, gated by "OGE RECEIVED"; e-filed
    reports don't have that line, so the table is read from the top."""
    rows, pending_desc, pending_num, filed = [], [], None, None
    expected = set()
    started = not RECEIVED_RE.search(text)
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        rm = RECEIVED_RE.search(line)
        if rm:
            filed, started = rm.group(1), True
            continue
        if not started:
            continue
        rsm = ROW_START_RE.match(line)
        if rsm:
            expected.add(int(rsm.group(1)))
        m = TAIL_RE.search(line)
        if m:
            num_m = NUM_RE.match(line)
            head = line[:m.start()].strip()
            row_num = pending_num
            if num_m and head.startswith(num_m.group(1)):
                head = head[len(num_m.group(1)):].strip()
                row_num = int(num_m.group(1))
            pending_desc.append(head)
            low, high = parse_amount_ocr(m.group("amt"))
            rows.append({"row_num": row_num,
                         "description": " ".join(d for d in pending_desc if d).strip(),
                         "tx_type": classify_type(m.group("type")), "tx_date": m.group("date"),
                         "notified_late": classify_late(m.group("flag")),
                         "amount_low": low, "amount_high": high})
            pending_desc, pending_num = [], None
        elif SKIP_RE.match(line):
            continue
        elif NUM_ONLY_RE.match(line):
            pending_num = int(NUM_ONLY_RE.match(line).group(1))
        else:
            pending_desc.append(line)
    return rows, filed, len(expected)


def to_date(s):
    """'12/10/25' or '12/10/2025' -> date, None if garbled."""
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", s or "")
    if not m:
        return None
    mo, d, y = map(int, m.groups())
    y = y + 2000 if y < 100 else y
    try:
        return datetime.date(y, mo, d)
    except ValueError:
        return None


# ------------------------------------------------------------ classifying

SUFFIX = {"INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "PLC", "LLC",
          "LP", "L", "P", "SA", "NV", "AG", "SE", "THE", "HOLDINGS", "HLDGS", "GROUP", "CLASS",
          "CL", "A", "B", "COM", "COMMON", "STOCK", "SHS", "NEW", "DEL", "ORD"}
MUNI = re.compile(r"\b(CNTY|COUNTY|SCH|SCHS|DIST|INDPT|REV|RFDG|AUTH|MUN|MUNI|CITY|TWP|"
                  r"TPK|WTR|SWR|HSG|ARPT|UNIV|PUB|IMPT|OBLIG|GO|ST INTRCPT|COMMN|PARISH)\b")
BOND = re.compile(r"\d\s*%|%|\bDUE\b|\bDu[oe]\b|\bNTS\b|\bNOTES?\b|\bBONDS?\b|\bDEB\b|B/E|8/E|"
                  r"\b\d{6}\b|\bMTN\b|\bSR\b|\bSUB\b", re.I)
FUND = re.compile(r"\b(FUND|FD|ETF|TRUST|INDEX|PORTFOLIO|MONEY MARKET|TREAS|TREASURY|T-BILL|"
                  r"BILLS?|CD|CERTIFICATE)\b", re.I)


def _key(s):
    s = re.sub(r"/[A-Z]{2,3}/?", " ", (s or "").upper())  # SEC's "/DE/" state tags
    words = re.sub(r"[^A-Z0-9 ]", " ", s.replace("&", " AND ")).split()
    return [w for w in words if w not in SUFFIX]


def company_index(sec_json):
    """{first word: [(key words, ticker), ...]} from SEC's company_tickers.json.
    The file is ordered by size, so the first ticker per company wins (the main class)."""
    idx, seen = {}, set()
    for v in sec_json.values():
        if v["cik_str"] in seen:
            continue
        seen.add(v["cik_str"])
        k = _key(v["title"])
        if k and (len(k) > 1 or len(k[0]) >= 4):
            idx.setdefault(k[0], []).append((k, v["ticker"].upper()))
    for lst in idx.values():
        lst.sort(key=lambda kt: -len(kt[0]))  # longest name first: "BANK OF AMERICA" before "BANK"
    return idx


def match_ticker(desc, idx):
    m = re.search(r"\(([A-Z]{1,5}(?:[.\-][A-Z])?)\)", desc or "")
    if m:
        return m.group(1)
    words = _key(desc)
    for i in range(min(3, len(words))):  # a stray row number or OCR blip can lead the line
        for k, t in idx.get(words[i], []):
            if words[i:i + len(k)] == k:
                return t
    return None


def classify(desc, idx):
    """(kind, ticker) for one row."""
    d = desc or ""
    tkr = match_ticker(d, idx)
    if BOND.search(d) or re.search(r"\bPFD\b|\bPR\b", d):
        if MUNI.search(d.upper()) and not tkr:
            return "muni bond", None
        return ("corporate bond", tkr) if tkr else ("bond", None)
    if FUND.search(d) and not tkr:
        return "fund / other", None
    if tkr:
        return "stock", tkr
    return "fund / other", None


# ------------------------------------------------------------- networking

def ensure_tables(con):
    con.execute("""CREATE TABLE IF NOT EXISTS wh_filings (
        doc_id VARCHAR PRIMARY KEY, slug VARCHAR, file VARCHAR, filed DATE, link VARCHAR,
        status VARCHAR, n_rows INTEGER)""")
    con.execute("""CREATE TABLE IF NOT EXISTS wh_trades (
        doc_id VARCHAR, row_num INTEGER, slug VARCHAR, name VARCHAR, title VARCHAR,
        description VARCHAR, kind VARCHAR, tkr VARCHAR, tx_type VARCHAR, tx_date DATE,
        filed DATE, amount_low BIGINT, amount_high BIGINT, notified_late BOOLEAN, link VARCHAR)""")


def group(title):
    """'Fed' for Federal Reserve officials, else 'White House'."""
    return "Fed" if "federal reserve" in (title or "").lower() else "White House"


def officials(path=OFFICIALS_FILE):
    out = []
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if line.count("|") >= 2:
            slug, name, title = [x.strip() for x in line.split("|", 2)]
            out.append({"slug": slug, "name": name, "title": title})
    return out


def find_slug(session, name):
    """Look a name up on the archive's search page: '<last>-<first...>'."""
    parts = re.sub(r"[^a-z ]", "", name.lower().replace("-", " ")).split()
    parts = [p for p in parts if p not in ("jr", "sr", "ii", "iii")]
    if len(parts) < 2:
        return None
    first, last = parts[0], parts[-1]
    for q in (name, last):  # nicknames ("Chris") can miss; the last name alone rarely does
        r = session.get(SEARCH_PAGE, params={"q": q}, timeout=30)
        if r.status_code != 200:
            continue
        for slug in dict.fromkeys(SLUG_RE.findall(r.text)):
            bits = slug.split("-")
            if last in bits[:2] and any(b.startswith(first[:2]) for b in bits if b != last):
                return slug
    return None


def list_documents(session, slug):
    """[(doc_id, doc_slug, file name)] for each 278-T on file."""
    r = session.get(INDEX_PAGE.format(slug=slug), timeout=30)
    r.raise_for_status()
    out, seen = [], set()
    for m in DOC_RE.finditer(r.text):
        if m.group("slug") != slug or "278 Transaction" not in m.group("form_type"):
            continue
        um = DOC_URL_RE.search(m.group("url"))
        if um and um.group(1) not in seen:
            seen.add(um.group(1))
            out.append((um.group(1), um.group(2), m.group("file")))
    return out


def file_date(name):
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", name or "")
    if m:
        try:
            return datetime.date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    return None


def main(limit=None, max_minutes=15):
    """Fetch new reports. Stops after `max_minutes` (the President alone has dozens of
    long scanned reports); the next run picks up where this one stopped."""
    import duckdb
    import pdfplumber
    from schema import DB_PATH
    con = duckdb.connect(DB_PATH)
    ensure_tables(con)
    done = {r[0] for r in con.execute("SELECT doc_id FROM wh_filings").fetchall()}
    s = requests.Session()
    s.headers["User-Agent"] = UA
    sec = requests.Session()
    sec.headers["User-Agent"] = sec_user_agent()
    idx = company_index(sec.get(TICKERS_URL, timeout=30).json())
    deadline = time.time() + max_minutes * 60
    for o in officials():
        if time.time() > deadline:
            print("  time budget used up; continuing on the next run")
            break
        slug = o["slug"] or find_slug(s, o["name"])
        if not slug:
            print(f"  {o['name']}: not found in the archive")
            continue
        try:
            docs = list_documents(s, slug)
        except Exception as e:
            print(f"  {o['name']}: {type(e).__name__}")
            continue
        new = [d for d in docs if d[0] not in done][:limit]
        print(f"  {o['name']}: {len(docs)} transaction reports, {len(new)} new")
        for doc_id, doc_slug, file in new:
            if time.time() > deadline:
                break
            time.sleep(1)
            link = f"https://www.documentcloud.org/documents/{doc_id}-{doc_slug}/"
            try:
                pdf = s.get(PDF_URL.format(doc_id=doc_id, slug=doc_slug), timeout=90)
                pdf.raise_for_status()
                with pdfplumber.open(io.BytesIO(pdf.content)) as p:
                    text = "\n".join(pg.extract_text() or "" for pg in p.pages)
                rows, filed, _ = parse_text(text)
                status = "ok" if rows else "empty"
            except Exception as e:
                rows, filed, status = [], None, "error"
                print(f"    {file}: {type(e).__name__}")
            fd = to_date(filed) or file_date(file)
            for r in rows:
                if r["amount_low"] not in BRACKETS or "$" in r["description"]:
                    continue  # OCR mangled this row beyond trusting
                kind, tkr = classify(r["description"], idx)
                con.execute("INSERT INTO wh_trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            [doc_id, r["row_num"], slug, o["name"], o["title"],
                             r["description"][:300], kind, tkr, r["tx_type"],
                             to_date(r["tx_date"]), fd, r["amount_low"], r["amount_high"],
                             r["notified_late"], link])
            con.execute("INSERT OR REPLACE INTO wh_filings VALUES (?,?,?,?,?,?,?)",
                        [doc_id, slug, file, fd, link, status, len(rows)])
    n = con.execute("SELECT count(*), count(*) FILTER (WHERE kind = 'stock') FROM wh_trades").fetchone()
    print(f"White House and cabinet: {n[0]} trades on file, {n[1]} of them stocks")


# ------------------------------------------------------------- for pages

def stock_buys(con, start, end):
    """Stock purchases by tracked officials, filed in [start, end]."""
    try:
        return con.execute("""SELECT slug, name, title, tkr, min(tx_date), max(filed),
                                     sum(amount_low), sum(amount_high), count(*)
                              FROM wh_trades WHERE kind = 'stock' AND tx_type = 'Purchase'
                                AND tkr IS NOT NULL AND filed BETWEEN ? AND ?
                              GROUP BY 1, 2, 3, 4 ORDER BY 7 DESC""", [start, end]).fetchall()
    except Exception:  # table not created yet
        return []


def official(con, slug):
    """Profile for a hover card: title and what's on file."""
    try:
        rows = con.execute("""SELECT max(name), max(title), count(*),
                count(*) FILTER (WHERE kind = 'stock'),
                count(*) FILTER (WHERE kind LIKE '%bond%'),
                min(tx_date), max(filed) FROM wh_trades WHERE slug = ?""", [slug]).fetchone()
        top = [t for t, _ in con.execute("""SELECT tkr, count(*) FROM wh_trades WHERE slug = ?
                AND tkr IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 3""", [slug]).fetchall()]
    except Exception:
        return {}
    if not rows or not rows[2]:
        return {}
    return {"name": rows[0], "title": rows[1], "trades": rows[2], "stocks": rows[3],
            "bonds": rows[4], "since": rows[5], "last": rows[6], "top": top}


def official_lines(o):
    if not o:
        return ["No reports on file yet."]
    lines = [o["title"]]
    lines.append(f"Reports on file: {o['trades']} trades since {o['since']:%b %Y}"
                 if o.get("since") else f"Reports on file: {o['trades']} trades")
    lines.append(f"{o['stocks']} stock trades, {o['bonds']} bond trades")
    if o["top"]:
        lines.append("Trades most: " + ", ".join(o["top"]))
    lines.append("Reported on OGE Form 278-T, in dollar ranges.")
    return lines


def summary(con, start, end):
    """Plain totals by official and kind, for the section on the front page."""
    try:
        return con.execute("""SELECT name, title, kind, tx_type, count(*), sum(amount_low),
                                     sum(amount_high), max(filed)
                              FROM wh_trades WHERE filed BETWEEN ? AND ?
                              GROUP BY 1, 2, 3, 4 ORDER BY 1, 6 DESC""", [start, end]).fetchall()
    except Exception:
        return []


def selftest():
    import duckdb
    assert parse_amount_ocr("s1.ooo,001 -ss,000,000") == (1000001, 5000000)
    assert classify_type("lourchoso") == "Purchase" and classify_late("VOS") is True
    scanned = ("OGE RECEIVED: 1/14/2026 OGE Form 278-T\n"
               "1 MIAMI-DADE CNTY Fl WTR & SR B fN BE/R/ 5 DUE 100133 2.610% "
               "lourchoso 11/14/2025 VOS $250,001 • $500,000\n")
    rows, filed, _ = parse_text(scanned)
    assert filed == "1/14/2026" and rows[0]["amount_low"] == 250001
    efiled = ("Periodic Transaction Report\n# DESCRIPTION EIF TYPE DATE NOTIFICATION AMOUNT\n"
              "1 Apple Inc. (AAPL) N/A Sale 03/04/2025 No $15,001 - $50,000\n"
              "2 Bank of America Corporation N/A Purchase 03/05/25 No $1,001 - $15,000\n"
              "3 Fifth Third Bancorp 7.5938% 12/31/49 Purchase 03/05/2025 No $100,001 - $250,000\n"
              "4 HARRIS CNTY TX MUD 391 RFDG 3.00% Due Sep 1 2026 Purchase 03/06/2025 No "
              "$50,001 - $100,000\n")
    rows, filed, n = parse_text(efiled)
    assert len(rows) == 4 and filed is None and n == 4, rows
    sec = {"0": {"cik_str": 1, "ticker": "AAPL", "title": "Apple Inc."},
           "1": {"cik_str": 2, "ticker": "BAC", "title": "BANK OF AMERICA CORP /DE/"},
           "2": {"cik_str": 2, "ticker": "BAC-PL", "title": "BANK OF AMERICA CORP /DE/"},
           "3": {"cik_str": 3, "ticker": "FITB", "title": "FIFTH THIRD BANCORP"},
           "4": {"cik_str": 4, "ticker": "BK", "title": "BANK"}}
    idx = company_index(sec)
    kinds = [classify(r["description"], idx) for r in rows]
    assert kinds == [("stock", "AAPL"), ("stock", "BAC"), ("corporate bond", "FITB"),
                     ("muni bond", None)], kinds
    assert to_date("12/10/25") == datetime.date(2025, 12, 10) and to_date("12110/2025") is None
    assert file_date("Donald J. Trump 12.18.2025 278-T") == datetime.date(2025, 12, 18)
    page = ('{id:"1",name:"JD Vance",title:"VP",slug:"vance-jd",x:1,did:"9",'
            'url:"https://www.documentcloud.org/documents/123-jd-vance-278t/",file:"JD Vance 278-T",'
            'form_type:"278 Transaction"}')
    m = DOC_RE.search(page)
    assert m and DOC_URL_RE.search(m.group("url")).groups() == ("123", "jd-vance-278t")

    con = duckdb.connect()
    ensure_tables(con)
    con.execute("""INSERT INTO wh_trades VALUES ('d1',1,'vance-jd','JD Vance','Vice President',
        'Apple Inc. (AAPL)','stock','AAPL','Purchase','2025-03-04','2025-03-20',15001,50000,false,'l')""")
    b = stock_buys(con, datetime.date(2025, 3, 1), datetime.date(2025, 3, 31))
    assert b[0][3] == "AAPL" and b[0][6] == 15001
    o = official(con, "vance-jd")
    assert official_lines(o)[0] == "Vice President" and "1 stock trades" in official_lines(o)[2]
    assert stock_buys(duckdb.connect(), b[0][4], b[0][5]) == []
    assert officials("no-such-file") == []
    assert group("Governor, Federal Reserve") == "Fed" and group("President") == "White House"
    print("whitehouse selftest ok")


def arg(flag, default=None):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        from schema import load_dotenv
        load_dotenv()
        n = arg("--limit")
        main(int(n) if n else None)
