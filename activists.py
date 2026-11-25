"""Activist and big-investor stakes: SEC Schedule 13D and 13G filings.

Anyone who buys more than 5% of a public company must tell the SEC:
  13D   they may try to influence the company (an "activist": push for a sale,
        board seats, cost cuts). Due within 5 business days of crossing 5%.
  13G   a passive investor (index funds, pensions) crossing 5%.

New 13Ds are the interesting ones: an investor putting serious money behind a
plan for the company. They're found in EDGAR's free daily form index, where
every filing is listed once for the company it's about (the "subject") and
once for each investor filing it.

  refresh(con, days=7)        fetch any daily indexes not yet read
  recent(con, start, end)     new 13D stakes in a date range, by ticker

Usage:
    py activists.py            # read the last 7 days of filings
    py activists.py --selftest
"""

import datetime
import re
import sys
import time

import requests

from schema import sec_user_agent

INDEX_URL = "https://www.sec.gov/Archives/edgar/daily-index/{y}/QTR{q}/form.{d:%Y%m%d}.idx"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FORMS = {"SC 13D": "13D", "SC 13D/A": "13D/A", "SCHEDULE 13D": "13D", "SCHEDULE 13D/A": "13D/A",
         "SC 13G": "13G", "SCHEDULE 13G": "13G"}
# Index funds and custodians file thousands of passive 13Gs; they say nothing
# about any one company.
PASSIVE = re.compile(r"VANGUARD|BLACKROCK|STATE STREET|FMR|FIDELITY|T\. ?ROWE|INVESCO|"
                     r"GEODE|NORTHERN TRUST|DIMENSIONAL|CHARLES SCHWAB|WELLINGTON|CAPITAL RESEARCH|"
                     r"MORGAN STANLEY|GOLDMAN SACHS|JPMORGAN|BANK OF AMERICA|CITIGROUP|UBS|"
                     r"BARCLAYS|ROYAL BANK|WELLS FARGO", re.I)
LINE_RE = re.compile(r"^(?P<form>.+?)\s{2,}(?P<name>.+?)\s{2,}(?P<cik>\d+)\s+(?P<date>\d{8})\s+"
                     r"(?P<file>edgar/data/\S+)\s*$")


def ensure_tables(con):
    con.execute("""CREATE TABLE IF NOT EXISTS stakes (
        file VARCHAR PRIMARY KEY, form VARCHAR, filed DATE, tkr VARCHAR, company VARCHAR,
        investor VARCHAR, url VARCHAR)""")
    con.execute("CREATE TABLE IF NOT EXISTS stake_days (d DATE PRIMARY KEY)")


def parse_index(text, cik_to_tkr):
    """Rows (accession file, form, filed, tkr, company, investor, url) from one form.idx."""
    by_file = {}
    for line in text.splitlines():
        m = LINE_RE.match(line)
        if not m or m.group("form").strip() not in FORMS:
            continue
        # the same filing is listed under each party's folder: group by accession number
        by_file.setdefault(m.group("file").rsplit("/", 1)[-1], []).append(m)
    out = []
    for f, ms in by_file.items():
        listed = [m for m in ms if int(m.group("cik")) in cik_to_tkr]
        others = [m for m in ms if int(m.group("cik")) not in cik_to_tkr]
        if len(listed) != 1 or not others:
            continue  # can't tell which side is the company
        subj = listed[0]
        investor = others[0].group("name").strip()
        form = FORMS[subj.group("form").strip()]
        if form == "13G" and PASSIVE.search(investor):
            continue
        d = subj.group("date")
        acc = f.replace(".txt", "")
        url = (f"https://www.sec.gov/Archives/edgar/data/{int(subj.group('cik'))}/"
               f"{acc.replace('-', '')}/{acc}-index.htm")
        out.append((f, form, datetime.date(int(d[:4]), int(d[4:6]), int(d[6:])),
                    cik_to_tkr[int(subj.group("cik"))], subj.group("name").strip().title(),
                    investor.title(), url))
    return out


def refresh(con, days=7):
    ensure_tables(con)
    today = datetime.date.today()
    done = {r[0] for r in con.execute("SELECT d FROM stake_days").fetchall()}
    todo = [today - datetime.timedelta(days=k) for k in range(1, days + 1)]
    todo = [d for d in todo if d.weekday() < 5 and d not in done]
    if not todo:
        return
    s = requests.Session()
    s.headers["User-Agent"] = sec_user_agent()
    cik_to_tkr = {}
    for v in s.get(TICKERS_URL, timeout=30).json().values():
        cik_to_tkr.setdefault(int(v["cik_str"]), v["ticker"].upper())
    n = 0
    for d in todo:
        time.sleep(0.2)
        r = s.get(INDEX_URL.format(y=d.year, q=(d.month - 1) // 3 + 1, d=d), timeout=60)
        if r.status_code == 404:  # holiday; nothing filed
            con.execute("INSERT OR REPLACE INTO stake_days VALUES (?)", [d])
            continue
        if r.status_code != 200:
            continue  # not published yet; try again tomorrow
        rows = parse_index(r.text, cik_to_tkr)
        if rows:
            con.executemany("INSERT OR REPLACE INTO stakes VALUES (?,?,?,?,?,?,?)", rows)
        con.execute("INSERT OR REPLACE INTO stake_days VALUES (?)", [d])
        n += len(rows)
    print(f"activists: {n} new 5%+ stake filings")


def recent(con, start, end, forms=("13D",)):
    """[(tkr, company, investor, form, filed, url)] newest first."""
    try:
        return con.execute("""SELECT tkr, company, investor, form, filed, url FROM stakes
                              WHERE form IN (SELECT unnest(?)) AND filed BETWEEN ? AND ?
                              ORDER BY filed DESC""", [list(forms), start, end]).fetchall()
    except Exception:  # table not created yet
        return []


def for_ticker(con, tkr, n=6):
    try:
        return con.execute("""SELECT filed, form, investor, url FROM stakes WHERE tkr = ?
                              ORDER BY filed DESC LIMIT ?""", [tkr, n]).fetchall()
    except Exception:
        return []


FORM_WORDS = {"13D": "took a 5%+ stake as an activist", "13D/A": "changed its activist stake",
              "13G": "passed 5% ownership (passive)"}


def selftest():
    import duckdb
    idx = (
        "Form Type   Company Name                                                  CIK         Date Filed  File Name\n"
        "-----------------------------------------------------------------------------------------------------------\n"
        "SC 13D           ACME WIDGETS INC                                              111         20250310    edgar/data/111/0000999-25-000001.txt\n"
        "SC 13D           PERSHING ROAD CAPITAL LP                                      999         20250310    edgar/data/999/0000999-25-000001.txt\n"
        "SC 13G           ACME WIDGETS INC                                              111         20250310    edgar/data/111/0000888-25-000002.txt\n"
        "SC 13G           VANGUARD GROUP INC                                            888         20250310    edgar/data/888/0000888-25-000002.txt\n"
        "10-K             ACME WIDGETS INC                                              111         20250310    edgar/data/111/0000111-25-000003.txt\n")
    rows = parse_index(idx, {111: "ACME"})
    assert len(rows) == 1, rows
    f, form, filed, tkr, company, investor, url = rows[0]
    assert (form, tkr, investor) == ("13D", "ACME", "Pershing Road Capital Lp")
    assert filed == datetime.date(2025, 3, 10) and url.endswith("/111/000099925000001/0000999-25-000001-index.htm"), url
    con = duckdb.connect()
    ensure_tables(con)
    con.executemany("INSERT INTO stakes VALUES (?,?,?,?,?,?,?)", rows)
    assert recent(con, datetime.date(2025, 3, 1), datetime.date(2025, 3, 31))[0][0] == "ACME"
    assert for_ticker(con, "ACME")[0][1] == "13D"
    assert recent(duckdb.connect(), filed, filed) == []
    print("activists selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        import duckdb
        from schema import DB_PATH, load_dotenv
        load_dotenv()
        refresh(duckdb.connect(DB_PATH))
