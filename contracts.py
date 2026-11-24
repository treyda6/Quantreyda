"""Big new federal contracts won by public companies.

A large multi-year government contract can mean years of locked-in revenue,
and the award often lands before analysts update their numbers. This pulls
contracts of $50M+ (default) whose start date falls in the window, from
USAspending.gov (free, no key), and matches the winner to a ticker.

    py contracts.py              # last 14 days
    py contracts.py --days 60
    py contracts.py --selftest

Caveat: "award amount" is the total that could be paid over the contract's
life, often across many years and option periods, so compare it with the
company's yearly revenue (health.py shows it) before getting excited.
A $100M award is huge for a $500M company and a rounding error for Lockheed.

Adapted from Quantgress's scrape_contracts.py (MIT), narrowed to large awards.
"""

import datetime
import sys
import time

import duckdb
import requests

from entities import load_sec_names, make_sec_resolver, normalize_name
from schema import DB_PATH, load_dotenv

API = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
MIN_AMOUNT = 50_000_000
FIELDS = ["Award ID", "Recipient Name", "Awarding Agency", "Awarding Sub Agency",
          "Start Date", "End Date", "Award Amount", "Description"]

# Contracts often go to a subsidiary whose name isn't the listed company's.
# Prefix of the normalized recipient name -> parent ticker.
SUBSIDIARIES = {
    "ELECTRIC BOAT": "GD", "BATH IRON WORKS": "GD", "GENERAL DYNAMICS": "GD",
    "NATIONAL STEEL AND SHIPBUILDING": "GD", "GULFSTREAM": "GD",
    "RAYTHEON": "RTX", "PRATT & WHITNEY": "RTX", "PRATT AND WHITNEY": "RTX",
    "COLLINS AEROSPACE": "RTX", "SIKORSKY": "LMT", "LOCKHEED MARTIN": "LMT",
    "NORTHROP GRUMMAN": "NOC", "BOEING": "BA", "HUNTINGTON INGALLS": "HII",
    "BAE SYSTEMS": "BAESY", "L3HARRIS": "LHX",
    "LEIDOS": "LDOS", "BOOZ ALLEN HAMILTON": "BAH", "SCIENCE APPLICATIONS INTERNATIONAL": "SAIC",
    "CACI": "CACI", "KBR": "KBR", "PARSONS": "PSN", "HONEYWELL": "HON",
    "GENERAL ELECTRIC": "GE", "GE AEROSPACE": "GE", "MCKESSON": "MCK",
    "CARDINAL HEALTH": "CAH", "HUMANA": "HUM", "CENTENE": "CNC", "UNITEDHEALTH": "UNH",
    "PALANTIR": "PLTR", "MICROSOFT": "MSFT", "AMAZON WEB SERVICES": "AMZN",
    "ORACLE": "ORCL", "IBM": "IBM", "INTERNATIONAL BUSINESS MACHINES": "IBM",
    "ACCENTURE FEDERAL": "ACN", "PFIZER": "PFE", "MODERNA": "MRNA",
    "FLUOR": "FLR", "JACOBS": "J", "AECOM": "ACM", "TEXTRON": "TXT", "BELL TEXTRON": "TXT",
    "OSHKOSH": "OSK", "AEROVIRONMENT": "AVAV", "KRATOS": "KTOS", "MERCURY SYSTEMS": "MRCY",
}


def ensure_table(con):
    con.execute("""CREATE TABLE IF NOT EXISTS big_contracts (
        award_id VARCHAR PRIMARY KEY, recipient VARCHAR, tkr VARCHAR,
        agency VARCHAR, sub_agency VARCHAR, start_date DATE, end_date DATE,
        amount DOUBLE, description VARCHAR, fetched_on DATE)""")


def make_matcher(sec_lookup):
    exact = make_sec_resolver(sec_lookup)

    def match(name):
        n = normalize_name(name or "")
        for prefix, tkr in SUBSIDIARIES.items():
            p = normalize_name(prefix)
            if n == p or n.startswith(p + " "):  # whole words: JACOBS, not JACOBSON
                return tkr
        hit = exact(name or "")
        return hit[0] if hit else None
    return match


def fetch_awards(start, end, min_amount=MIN_AMOUNT, max_pages=20):
    s = requests.Session()
    s.headers["User-Agent"] = "quantreyda/0.1 (personal research)"
    page = 1
    while page <= max_pages:
        body = {"limit": 100, "page": page, "sort": "Award Amount", "order": "desc",
                "filters": {"award_type_codes": ["A", "B", "C", "D"],
                            "award_amounts": [{"lower_bound": min_amount}],
                            "time_period": [{"start_date": start.isoformat(),
                                             "end_date": end.isoformat()}]},
                "fields": FIELDS}
        r = s.post(API, json=body, timeout=60)
        r.raise_for_status()
        data = r.json()
        yield from data["results"]
        if not data["page_metadata"].get("hasNext"):
            return
        page += 1
        time.sleep(0.3)


def store(con, awards, start, match, today):
    """Keep awards that actually *started* in the window (the search also
    returns old contracts that merely had a modification) and that we can
    tie to a ticker."""
    ensure_table(con)
    n = 0
    for a in awards:
        sd = a.get("Start Date")
        if not sd or sd < start.isoformat():
            continue
        tkr = match(a.get("Recipient Name"))
        if not tkr:
            continue
        con.execute("INSERT OR REPLACE INTO big_contracts VALUES (?,?,?,?,?,?,?,?,?,?)",
                    [a.get("Award ID") or a.get("generated_internal_id"), a.get("Recipient Name"),
                     tkr, a.get("Awarding Agency"), a.get("Awarding Sub Agency"), sd,
                     a.get("End Date"), a.get("Award Amount"), a.get("Description"), today])
        n += 1
    return n


def main(days=14):
    load_dotenv()
    from schema import sec_user_agent
    today = datetime.date.today()
    start = today - datetime.timedelta(days=days)
    s = requests.Session()
    s.headers["User-Agent"] = sec_user_agent()
    match = make_matcher(load_sec_names(s.get(TICKERS_URL, timeout=30).json()))
    con = duckdb.connect(DB_PATH)
    n = store(con, fetch_awards(start, today), start, match, today)
    print(f"contracts: {n} new awards of ${MIN_AMOUNT / 1e6:.0f}M+ to public companies since {start}")


def recent(con, start, end):
    try:
        return con.execute("""SELECT * FROM big_contracts WHERE start_date BETWEEN ? AND ?
                              ORDER BY amount DESC""", [start, end]).df()
    except duckdb.CatalogException:
        return None


def selftest():
    sec = {"0": {"cik_str": 1, "ticker": "LMT", "title": "Lockheed Martin Corp"},
           "1": {"cik_str": 2, "ticker": "PLTR", "title": "Palantir Technologies Inc."},
           "2": {"cik_str": 3, "ticker": "ACME", "title": "Acme Widgets Inc"}}
    match = make_matcher(load_sec_names(sec))
    assert match("ELECTRIC BOAT CORPORATION") == "GD"
    assert match("LOCKHEED MARTIN CORPORATION") == "LMT"
    assert match("ACME WIDGETS, INC.") == "ACME"
    assert match("SOME PRIVATE LLC") is None
    assert match("JACOBSON BROTHERS LLC") is None, "prefixes match whole words only"
    assert match("JACOBS TECHNOLOGY INC") == "J"

    con = duckdb.connect()
    start = datetime.date(2025, 3, 1)
    awards = [
        {"Award ID": "1", "Recipient Name": "ACME WIDGETS INC", "Awarding Agency": "DoD",
         "Start Date": "2025-03-05", "End Date": "2030-03-05", "Award Amount": 250e6,
         "Description": "WIDGETS"},
        {"Award ID": "2", "Recipient Name": "ACME WIDGETS INC", "Start Date": "2019-01-01",
         "Award Amount": 900e6},  # old contract, just modified -> skipped
        {"Award ID": "3", "Recipient Name": "PRIVATE CO LLC", "Start Date": "2025-03-06",
         "Award Amount": 500e6},  # no ticker -> skipped
    ]
    assert store(con, awards, start, match, start) == 1
    df = recent(con, start, datetime.date(2025, 3, 31))
    assert list(df.tkr) == ["ACME"] and df.amount[0] == 250e6
    print("contracts selftest ok")


def arg(flag, default=None):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main(int(arg("--days", 14)))
