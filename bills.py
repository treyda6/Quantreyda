"""Bills moving in Congress, matched to the industries of the stocks we track.

Uses the free Congress.gov API (needs CONGRESS_API_KEY, a free api.data.gov
key). Each day it reads bills updated in the last 30 days and keeps the ones
that actually moved (reported by a committee, passed a chamber, signed), then
looks up each one's policy area and sponsor.

Two uses:
  - stock pages list moving bills in the company's industry
  - a Congress buy gets flagged when the buyer *sponsored* a moving bill in
    that stock's industry ("⚖ sponsored a related bill")

  refresh(con)                         fetch (daily)
  for_ticker(con, tkr, n=5)            moving bills in the company's industry
  sponsored(con, bioguide, sectors)    that member's related bills

Usage:
    py bills.py            # fetch
    py bills.py --selftest
"""

import datetime
import os
import sys
import time

import requests

import committees

API = "https://api.congress.gov/v3"
MOVED = ("passed", "became public law", "signed by president", "presented to president",
         "reported", "ordered to be reported", "agreed to", "cloture", "conference")
POLICY_SECTORS = {
    "Armed Forces and National Security": ["defense"],
    "Health": ["health"],
    "Energy": ["energy"],
    "Environmental Protection": ["energy", "mining"],
    "Public Lands and Natural Resources": ["energy", "mining"],
    "Finance and Financial Sector": ["finance"],
    "Agriculture and Food": ["agriculture & food"],
    "Transportation and Public Works": ["transportation", "construction"],
    "Science, Technology, Communications": ["tech", "telecom & media"],
    "Housing and Community Development": ["construction", "finance"],
    "Education": ["education"],
    "Foreign Trade and International Finance": ["tech", "agriculture & food"],
}
WEB_TYPE = {"HR": "house-bill", "S": "senate-bill", "HJRES": "house-joint-resolution",
            "SJRES": "senate-joint-resolution", "HRES": "house-resolution",
            "SRES": "senate-resolution", "HCONRES": "house-concurrent-resolution",
            "SCONRES": "senate-concurrent-resolution"}


def ordinal(n):
    n = int(n)
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def web_url(congress, btype, number):
    return (f"https://www.congress.gov/bill/{ordinal(congress)}-congress/"
            f"{WEB_TYPE.get(btype.upper(), 'house-bill')}/{number}")


def moved(text):
    t = (text or "").lower()
    return any(w in t for w in MOVED)


def ensure_table(con):
    con.execute("""CREATE TABLE IF NOT EXISTS bills (
        congress INTEGER, btype VARCHAR, number VARCHAR, title VARCHAR, policy VARCHAR,
        sectors VARCHAR, sponsor VARCHAR, sponsor_name VARCHAR, action_date DATE,
        action VARCHAR, url VARCHAR, updated VARCHAR,
        PRIMARY KEY (congress, btype, number))""")


def from_detail(b):
    """Row values from a congress.gov bill detail record."""
    policy = (b.get("policyArea") or {}).get("name")
    sp = (b.get("sponsors") or [{}])[0]
    la = b.get("latestAction") or {}
    return (int(b["congress"]), b["type"].upper(), str(b["number"]), b.get("title"), policy,
            ",".join(POLICY_SECTORS.get(policy, [])), sp.get("bioguideId"),
            sp.get("fullName"), la.get("actionDate"), la.get("text"),
            web_url(b["congress"], b["type"], b["number"]), b.get("updateDate"))


def refresh(con, days=30, max_pages=8):
    key = os.environ.get("CONGRESS_API_KEY")
    if not key:
        raise SystemExit("CONGRESS_API_KEY not set (free key: https://api.data.gov/signup/)")
    ensure_table(con)
    s = requests.Session()
    since = (datetime.datetime.utcnow() - datetime.timedelta(days=days)).strftime("%Y-%m-%dT00:00:00Z")
    have = {(c, t, n): u for c, t, n, u in con.execute(
        "SELECT congress, btype, number, updated FROM bills").fetchall()}
    todo = []
    for page in range(max_pages):
        r = s.get(f"{API}/bill", timeout=60, params={
            "api_key": key, "format": "json", "limit": 250, "offset": page * 250,
            "sort": "updateDate+desc", "fromDateTime": since})
        r.raise_for_status()
        items = r.json().get("bills", [])
        for b in items:
            k = (int(b["congress"]), b["type"].upper(), str(b["number"]))
            if moved((b.get("latestAction") or {}).get("text")) and have.get(k) != b.get("updateDate"):
                todo.append(b)
        if len(items) < 250:
            break
    for b in todo:
        time.sleep(0.8)  # 5,000 requests an hour allowed; stay far below
        r = s.get(f"{API}/bill/{b['congress']}/{b['type'].lower()}/{b['number']}", timeout=60,
                  params={"api_key": key, "format": "json"})
        if r.status_code != 200:
            continue
        con.execute("INSERT OR REPLACE INTO bills VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    list(from_detail(r.json()["bill"])))
    print(f"bills: {len(todo)} moving bills updated")


def ticker_sectors(con, tkr):
    try:
        r = con.execute("SELECT sic FROM companies WHERE tkr = ?", [tkr]).fetchone()
    except Exception:
        return set()
    return committees.sectors_for_sic(r[0] if r else None)


def _match(sectors_csv, sectors):
    return bool(set((sectors_csv or "").split(",")) & set(sectors))


def for_ticker(con, tkr, n=5, days=120):
    sec = ticker_sectors(con, tkr)
    if not sec:
        return []
    try:
        rows = con.execute("""SELECT title, policy, sectors, action_date, action, url, sponsor_name
                              FROM bills WHERE action_date >= ? ORDER BY action_date DESC""",
                           [datetime.date.today() - datetime.timedelta(days=days)]).fetchall()
    except Exception:
        return []
    return [r for r in rows if _match(r[2], sec)][:n]


def sponsored(con, bioguide, sectors, days=180):
    """Moving bills this member sponsored in any of `sectors`."""
    if not bioguide or not sectors:
        return []
    try:
        rows = con.execute("""SELECT title, sectors, url FROM bills WHERE sponsor = ?
                              AND action_date >= ?""",
                           [bioguide, datetime.date.today() - datetime.timedelta(days=days)]).fetchall()
    except Exception:
        return []
    return [(t, u) for t, sc, u in rows if _match(sc, sectors)]


def member_bioguide(con, last, first, chamber):
    try:
        rows = con.execute("SELECT bioguide, first, nickname, last FROM members WHERE chamber = ?",
                           [chamber]).fetchall()
    except Exception:
        return None
    hit = committees.match_people(rows, last, first)
    return hit[0][0] if hit else None


def bill_flag(con, tkr, last, first, chamber):
    """'Sponsored a related bill: <title>' (with its url), or (None, None)."""
    sec = ticker_sectors(con, tkr)
    got = sponsored(con, member_bioguide(con, last, first, chamber), sec)
    return got[0] if got else (None, None)


def selftest():
    import duckdb
    assert web_url(119, "HR", 1234) == "https://www.congress.gov/bill/119th-congress/house-bill/1234"
    assert ordinal(111) == "111th" and ordinal(121) == "121st"
    assert moved("Passed House (Roll no. 210).") and not moved("Referred to the Committee on X.")
    today = datetime.date.today()
    detail = {"congress": 119, "type": "S", "number": "77", "title": "Shipbuilding Act",
              "policyArea": {"name": "Armed Forces and National Security"},
              "sponsors": [{"bioguideId": "A1", "fullName": "Sen. Alpha, Ann [R-TX]"}],
              "latestAction": {"actionDate": str(today), "text": "Passed Senate without amendment."},
              "updateDate": "x"}
    row = from_detail(detail)
    assert row[5] == "defense" and row[6] == "A1"
    con = duckdb.connect()
    ensure_table(con)
    con.execute("INSERT INTO bills VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", list(row))
    committees.ensure_tables(con)
    con.execute("INSERT INTO companies VALUES ('LMT', 1, 'Lockheed', 3760, 'guided missiles', NULL)")
    con.execute("INSERT INTO members VALUES ('A1','S','Ann',NULL,'Alpha','Republican','TX',2019,'Armed Services')")
    assert for_ticker(con, "LMT")[0][0] == "Shipbuilding Act"
    assert bill_flag(con, "LMT", "Alpha", "Ann", "S")[0] == "Shipbuilding Act"
    assert bill_flag(con, "LMT", "Beta", "Bo", "S") == (None, None)
    assert for_ticker(duckdb.connect(), "LMT") == []
    print("bills selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        import duckdb
        from schema import DB_PATH, load_dotenv
        load_dotenv()
        refresh(duckdb.connect(DB_PATH))
