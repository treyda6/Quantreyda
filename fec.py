"""Election money from the FEC (Federal Election Commission), free.

  company PACs   What a company's political action committee gave to federal
                 candidates this election cycle, the Republican / Democrat
                 split, and the biggest recipients. Shown on stock pages.
  members        How much each member of Congress who trades has raised this
                 cycle and has in the bank. Shown in their hover cards.

Uses the OpenFEC API (api.open.fec.gov). It takes a free api.data.gov key, the
same kind as CONGRESS_API_KEY, so that key is reused unless FEC_API_KEY is set.
Each run refreshes a few dozen companies and members, so it stays far under
the key's 1,000 requests an hour.

    py fec.py company "Lockheed Martin Corp"
    py fec.py member Pelosi Nancy H CA
    py fec.py --selftest
"""

import datetime
import os
import re
import sys
import time

import requests

import companyintel

API = "https://api.open.fec.gov/v1"
PER_RUN = {"pac": 40, "member": 60}
EVERY = 14  # days before a refresh
PAC_WORDS = re.compile(r"\b(PAC|POLITICAL|ACTION|COMMITTEE|EMPLOYEES?|GOOD GOVERNMENT|FUND)\b")


def api_key():
    return (os.environ.get("FEC_API_KEY") or os.environ.get("CONGRESS_API_KEY") or "").strip()


def cycle(today=None):
    """FEC cycles are named by their even year: 2025 and 2026 are both '2026'."""
    y = (today or datetime.date.today()).year
    return y + (y % 2)


def ensure_tables(con):
    con.execute("""CREATE TABLE IF NOT EXISTS fec_member (
        bioguide VARCHAR PRIMARY KEY, checked_on DATE, candidate_id VARCHAR, name VARCHAR,
        cycle INTEGER, receipts DOUBLE, cash DOUBLE)""")
    companyintel.ensure_tables(con)


def _get(s, path, **params):
    params["api_key"] = api_key()
    for attempt in range(3):
        time.sleep(0.4)
        r = s.get(API + path, params=params, timeout=60)
        if r.status_code == 429:
            time.sleep(20 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()


# ------------------------------------------------------------- company PACs

def pick_pac(committees, name):
    """The company's own PAC from a committee search: a corporate connected
    committee whose name starts with the company's name."""
    key = companyintel.base_name(name).upper()
    if not key:
        return None
    best = None
    for c in committees:
        n = re.sub(r"[^\w& ]", " ", (c.get("name") or "").upper())
        n = re.sub(r"\s+", " ", n).strip()
        if not (n == key or n.startswith(key + " ")):
            continue
        if c.get("organization_type") not in (None, "C"):  # C = corporation
            continue
        if not PAC_WORDS.search(n[len(key):]):
            continue  # "APPLE FOR CONGRESS" is a candidate, not Apple's PAC
        if best is None or len(n) < len(best["name"]):
            best = c
    return best


def pac_summary(rows):
    """(total to candidates, Republican share or None, top recipients) from
    Schedule B disbursement rows."""
    total, rep, dem, by = 0.0, 0.0, 0.0, {}
    for r in rows:
        rc = r.get("recipient_committee") or {}
        ctype = rc.get("committee_type")
        if ctype and ctype not in ("H", "S", "P"):
            continue  # party committees, other PACs, refunds: not a candidate
        amt = float(r.get("disbursement_amount") or 0)
        if amt <= 0:
            continue
        total += amt
        party = (rc.get("party") or "").upper()
        rep += amt if party.startswith("REP") else 0
        dem += amt if party.startswith("DEM") else 0
        who = (r.get("recipient_name") or "?").title()
        by[who] = by.get(who, 0) + amt
    top = [k for k, _ in sorted(by.items(), key=lambda kv: -kv[1])[:3]]
    share = rep / (rep + dem) if rep + dem else None
    return total, share, top


def fetch_pac(s, name, today=None):
    """(amount, detail) for company_intel, or None if no PAC was found."""
    cy = cycle(today)
    found = _get(s, "/committees/", q=companyintel.base_name(name), per_page=20,
                 cycle=cy).get("results", [])
    pac = pick_pac(found, name)
    if not pac:
        return None
    rows, last = [], {}
    for _ in range(5):  # up to 500 of its biggest gifts
        j = _get(s, "/schedules/schedule_b/", committee_id=pac["committee_id"],
                 two_year_transaction_period=cy, sort="-disbursement_amount",
                 per_page=100, **last)
        rows += j.get("results", [])
        li = (j.get("pagination") or {}).get("last_indexes")
        if not li or not j.get("results"):
            break
        last = li
    total, share, top = pac_summary(rows)
    return total, "|".join([pac.get("name") or "", "" if share is None else f"{share:.2f}",
                            "; ".join(top)])


def pac_info(detail):
    name, share, top = ((detail or "") + "||").split("|")[:3]
    return {"pac_name": name, "pac_rep": float(share) if share else None,
            "pac_top": [x for x in top.split("; ") if x]}


# ------------------------------------------------------------------ members

def pick_candidate(results, last, first):
    last, first = (last or "").upper().strip(" ,."), (first or "").upper().strip()
    for c in sorted(results, key=lambda c: -(c.get("receipts") or 0)):
        n = (c.get("name") or "").upper()  # "PELOSI, NANCY"
        ln, _, fn = n.partition(",")
        if ln.strip() == last and (not first or fn.strip()[:2] == first[:2]):
            return c
    return None


def fetch_member(s, last, first, chamber, state, today=None):
    cy = cycle(today)
    res = _get(s, "/candidates/totals/", q=last, office=chamber, state=state, cycle=cy,
               election_full="false", per_page=20).get("results", [])
    return pick_candidate(res, last, first)


def refresh(con, names, today=None):
    """names: {tkr: company name} for the stock pages; members come from the
    roster of anyone who traded in the last year."""
    if not api_key():
        raise SystemExit("no FEC_API_KEY or CONGRESS_API_KEY (free: https://api.data.gov/signup/)")
    ensure_tables(con)
    today = today or datetime.date.today()
    s = requests.Session()
    s.headers["User-Agent"] = companyintel.UA

    have = dict(con.execute("SELECT tkr, checked_on FROM company_intel WHERE source = 'pac'").fetchall())
    due = sorted((t for t in names if t not in have or (today - have[t]).days >= EVERY),
                 key=lambda t: have.get(t, datetime.date.min))[:PER_RUN["pac"]]
    found = 0
    for t in due:
        try:
            got = fetch_pac(s, names[t], today)
        except requests.RequestException as e:
            print(f"  pac {t}: {type(e).__name__} {getattr(e.response, 'status_code', '')}")
            continue
        companyintel._save(con, t, "pac", got[0] if got else None, got[1] if got else None)
        found += bool(got)
    print(f"election money: checked {len(due)} companies, {found} have a PAC")

    try:
        members = con.execute("""SELECT DISTINCT m.bioguide, m.last, m.first, m.chamber, m.state
            FROM members m JOIN trades t ON t.chamber = m.chamber
              AND lower(trim(t.last_name)) = lower(m.last)
            WHERE t.filed_date >= ?""", [today - datetime.timedelta(days=365)]).fetchall()
    except Exception:  # rosters not loaded yet
        members = []
    have = dict(con.execute("SELECT bioguide, checked_on FROM fec_member").fetchall())
    due = sorted((m for m in members if m[0] not in have or (today - have[m[0]]).days >= EVERY),
                 key=lambda m: have.get(m[0], datetime.date.min))[:PER_RUN["member"]]
    hit = 0
    for b, last, first, chamber, state in due:
        try:
            c = fetch_member(s, last, first, chamber, state, today)
        except requests.RequestException as e:
            print(f"  member {last}: {type(e).__name__} {getattr(e.response, 'status_code', '')}")
            continue
        con.execute("INSERT OR REPLACE INTO fec_member VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [b, today, c and c.get("candidate_id"), c and c.get("name"), cycle(today),
                     c and c.get("receipts"), c and c.get("cash_on_hand_end_period")])
        hit += bool(c)
    print(f"election money: checked {len(due)} members of Congress, matched {hit}")


def member(con, bioguide):
    try:
        r = con.execute("SELECT receipts, cash, cycle FROM fec_member WHERE bioguide = ?",
                        [bioguide]).fetchone()
    except Exception:
        return None
    return {"raised": r[0], "cash": r[1], "cycle": r[2]} if r and r[0] is not None else None


def selftest():
    import duckdb
    comms = [{"name": "APPLE FOR CONGRESS", "committee_id": "C1", "organization_type": None},
             {"name": "LOCKHEED MARTIN CORPORATION EMPLOYEES' POLITICAL ACTION COMMITTEE",
              "committee_id": "C2", "organization_type": "C"},
             {"name": "LOCKHEED MARTIN EMPLOYEES PAC", "committee_id": "C3", "organization_type": "C"}]
    assert pick_pac(comms, "Lockheed Martin Corp /MD/")["committee_id"] == "C3"
    assert pick_pac(comms, "Apple Inc.") is None
    rows = [{"recipient_name": "SMITH FOR SENATE", "disbursement_amount": 5000,
             "recipient_committee": {"committee_type": "S", "party": "REP"}},
            {"recipient_name": "JONES FOR CONGRESS", "disbursement_amount": 2500,
             "recipient_committee": {"committee_type": "H", "party": "DEM"}},
            {"recipient_name": "NRCC", "disbursement_amount": 15000,
             "recipient_committee": {"committee_type": "Y", "party": "REP"}},
            {"recipient_name": "SMITH FOR SENATE", "disbursement_amount": 1000,
             "recipient_committee": {"committee_type": "S", "party": "REP"}}]
    total, share, top = pac_summary(rows)
    assert total == 8500 and abs(share - 6000 / 8500) < 1e-9 and top[0] == "Smith For Senate", (total, share, top)
    assert pac_info("X PAC|0.71|A; B") == {"pac_name": "X PAC", "pac_rep": 0.71, "pac_top": ["A", "B"]}
    assert pac_info(None)["pac_rep"] is None
    cands = [{"name": "PELOSI, NANCY", "receipts": 2e6, "candidate_id": "H1"},
             {"name": "PELOSI, PAUL", "receipts": 10, "candidate_id": "H2"}]
    assert pick_candidate(cands, "Pelosi", "Nancy")["candidate_id"] == "H1"
    assert pick_candidate(cands, "Smith", "Al") is None
    assert cycle(datetime.date(2025, 3, 1)) == 2026 and cycle(datetime.date(2026, 11, 25)) == 2026
    con = duckdb.connect()
    ensure_tables(con)
    con.execute("INSERT INTO fec_member VALUES ('P1', '2026-11-01', 'H1', 'PELOSI, NANCY', 2026, 2e6, 5e5)")
    assert member(con, "P1")["raised"] == 2e6 and member(con, "nobody") is None
    print("fec selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    elif len(sys.argv) > 2 and sys.argv[1] == "company":
        se = requests.Session()
        se.headers["User-Agent"] = companyintel.UA
        print(fetch_pac(se, sys.argv[2]))
    elif len(sys.argv) > 5 and sys.argv[1] == "member":
        se = requests.Session()
        se.headers["User-Agent"] = companyintel.UA
        print(fetch_member(se, *sys.argv[2:6]))
    else:
        print(__doc__)
