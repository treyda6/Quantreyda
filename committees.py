"""Flag congress trades in an industry the member's committee oversees.

A senator on Armed Services buying a defense contractor, or a House Energy &
Commerce member buying a drug maker, is the kind of trade most likely to be
informed by what they hear at work. Research on this is mixed, so
backtest.py tests it as its own signal instead of assuming it works.

How it works:
  - Committee rosters: the volunteer-maintained `unitedstates/congress-legislators`
    dataset on GitHub (current members only, refreshed weekly here). So a 2023
    trade is judged by the committees the member sits on *now*.
  - Company industry: the SIC code each company gives the SEC (from
    data.sec.gov/submissions; needs SEC_CONTACT_EMAIL like the other SEC calls).
  - Committee -> industry: the hand-written COMMITTEE_SECTORS map below. It's
    deliberately narrow; broad committees (Appropriations, Judiciary, Budget)
    are left out because they'd flag everything.

    py committees.py                # refresh rosters, print members we couldn't match
    py committees.py --selftest
"""

import datetime
import sys
import time
import unicodedata

import duckdb
import requests

from schema import DB_PATH, sec_user_agent

RAW = "https://raw.githubusercontent.com/unitedstates/congress-legislators/main/{}.yaml"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
ROSTER_DAYS = 7

# SIC code ranges (inclusive)
SECTORS = {
    "defense": [(3480, 3489), (3720, 3729), (3730, 3732), (3760, 3769), (3812, 3812)],
    "health": [(2830, 2836), (3841, 3851), (8000, 8099), (6324, 6324)],
    "energy": [(1200, 1241), (1300, 1399), (2900, 2999), (4900, 4991)],
    "mining": [(1000, 1099), (1400, 1499)],
    "finance": [(6000, 6799)],
    "agriculture & food": [(100, 999), (2000, 2099), (2870, 2879)],
    "telecom & media": [(4800, 4899)],
    "transportation": [(3710, 3716), (3720, 3729), (4000, 4799)],
    "tech": [(3570, 3579), (3670, 3679), (7370, 7379)],
    "construction": [(1500, 1799), (3270, 3299)],
    "education": [(8200, 8299)],
}

COMMITTEE_SECTORS = {
    "HSAG": ["agriculture & food"], "SSAF": ["agriculture & food"],
    "HSAS": ["defense"], "SSAS": ["defense"],
    "HLIG": ["defense"], "SLIN": ["defense"],
    "HSHM": ["defense"], "SSGA": ["defense"],
    "HSBA": ["finance"], "SSBK": ["finance"],
    "HSIF": ["health", "energy", "telecom & media", "tech"],
    "SSCM": ["transportation", "telecom & media", "tech"],
    "HSII": ["energy", "mining"], "SSEG": ["energy", "mining"],
    "HSPW": ["transportation", "construction"], "SSEV": ["transportation", "construction"],
    "HSSY": ["tech", "defense"],
    "SSHR": ["health"], "HSED": ["health", "education"],
    "HSWM": ["health"], "SSFI": ["health"],  # both oversee Medicare
    "HSZS": ["tech"],  # China select committee: chips, export controls
}


def sectors_for_sic(sic):
    if sic is None:
        return set()
    sic = int(sic)
    return {name for name, ranges in SECTORS.items() if any(a <= sic <= b for a, b in ranges)}


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return "".join(ch for ch in s.lower() if ch.isalpha() or ch == " ").strip()


SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def _last_key(last):
    words = _norm(str(last or "").replace(",", " ").replace(".", " ").replace("-", " ")).split()
    return " ".join(w for w in words if w not in SUFFIXES)


# ---------------------------------------------------------------- rosters

def ensure_tables(con):
    con.execute("""CREATE TABLE IF NOT EXISTS committee_members (
        bioguide VARCHAR, chamber VARCHAR, first VARCHAR, nickname VARCHAR,
        last VARCHAR, committee_id VARCHAR, committee VARCHAR, fetched_on DATE)""")
    # every current member, for the dashboard's hover bios
    con.execute("""CREATE TABLE IF NOT EXISTS members (
        bioguide VARCHAR PRIMARY KEY, chamber VARCHAR, first VARCHAR, nickname VARCHAR,
        last VARCHAR, party VARCHAR, state VARCHAR, since INTEGER, committees VARCHAR)""")
    con.execute("""CREATE TABLE IF NOT EXISTS companies (
        tkr VARCHAR PRIMARY KEY, cik BIGINT, name VARCHAR, sic INTEGER,
        sic_desc VARCHAR, fetched_on DATE)""")


def build_roster(legislators, committees, membership, today):
    """Pure: the three parsed YAML files -> rows for committee_members."""
    people = {}
    for p in legislators:
        terms = p.get("terms") or [{}]
        people[p["id"]["bioguide"]] = dict(
            chamber="S" if terms[-1].get("type") == "sen" else "H",
            first=p["name"].get("first"), nickname=p["name"].get("nickname"),
            last=p["name"].get("last"))
    names = {c["thomas_id"]: c["name"] for c in committees}
    rows = []
    for cid, members in membership.items():
        if cid not in COMMITTEE_SECTORS:  # also skips subcommittees (HSAS02 etc.)
            continue
        for m in members:
            p = people.get(m.get("bioguide"))
            if p:
                rows.append((m["bioguide"], p["chamber"], p["first"], p["nickname"],
                             p["last"], cid, names.get(cid, cid), today))
    return rows


def build_members(legislators, committees, membership):
    """Pure: one row per current member with party, state, first year in
    their current chamber, and every full committee they sit on."""
    names = {c["thomas_id"]: c["name"] for c in committees}
    seats = {}
    for cid, members in membership.items():
        if cid in names:  # full committees only, not subcommittees
            for m in members:
                short = (names[cid].replace("House Committee on ", "")
                         .replace("Senate Committee on ", "").replace("Senate Select Committee on ", "")
                         .replace("House Permanent Select Committee on ", "")
                         .replace("Senate Special Committee on ", ""))
                seats.setdefault(m.get("bioguide"), []).append(short)
    rows = []
    for p in legislators:
        terms = p.get("terms") or [{}]
        last_t = terms[-1]
        same = [t for t in terms if t.get("type") == last_t.get("type")]
        since = int(same[0]["start"][:4]) if same and same[0].get("start") else None
        b = p["id"]["bioguide"]
        rows.append((b, "S" if last_t.get("type") == "sen" else "H", p["name"].get("first"),
                     p["name"].get("nickname"), p["name"].get("last"), last_t.get("party"),
                     last_t.get("state"), since, "; ".join(sorted(seats.get(b, [])))))
    return rows


def refresh_rosters(con, force=False):
    ensure_tables(con)
    today = datetime.date.today()
    last = con.execute("SELECT max(fetched_on) FROM committee_members").fetchone()[0]
    have_members = con.execute("SELECT count(*) FROM members").fetchone()[0]
    if not force and last and (today - last).days < ROSTER_DAYS and have_members:
        return
    import yaml
    get = lambda name: yaml.safe_load(requests.get(RAW.format(name), timeout=60).text)
    leg, com, mem = (get("legislators-current"), get("committees-current"),
                     get("committee-membership-current"))
    rows = build_roster(leg, com, mem, today)
    people = build_members(leg, com, mem)
    if people:
        con.execute("DELETE FROM members")
        con.executemany("INSERT INTO members VALUES (?,?,?,?,?,?,?,?,?)", people)
    if rows:
        con.execute("DELETE FROM committee_members")
        con.executemany("INSERT INTO committee_members VALUES (?,?,?,?,?,?,?,?)", rows)
        print(f"committees: {len(rows)} memberships loaded")


def member_committees(con, last, first, chamber):
    """[(committee_id, committee name)] for a trade's filer, or [] if unknown.
    Matches on last name + chamber; if that's ambiguous, the first name (or
    nickname: "Tommy" Tuberville files as "Thomas") breaks the tie."""
    rows = con.execute("SELECT bioguide, first, nickname, last, committee_id, committee "
                       "FROM committee_members WHERE chamber = ?", [chamber]).fetchall()
    return sorted({(r[4], r[5]) for r in match_people(rows, last, first)})


def match_people(rows, last, first):
    """Rows (bioguide, first, nickname, last, ...) that are this filer, or []."""
    ln = _last_key(last)
    if not ln:
        return []
    # filings say "King, Jr." / "Delaney" where the roster says "King" /
    # "McClain Delaney": drop suffixes, and accept a match on the final word
    hits = [r for r in rows if _last_key(r[3]) == ln]
    if not hits:
        hits = [r for r in rows if _last_key(r[3]).split(" ")[-1] == ln.split(" ")[-1]]
    if len({r[0] for r in hits}) > 1:
        fn = _norm(first).split(" ")[0] if first else ""
        hits = [r for r in hits if fn and (_norm(r[1]).startswith(fn) or
                                           _norm(r[2]).startswith(fn) or
                                           fn.startswith(_norm(r[1])))]
        if len({r[0] for r in hits}) != 1:
            return []
    return hits


# -------------------------------------------------------------- companies

def refresh_companies(con, tickers):
    """Fetch SIC industry codes for tickers we haven't looked up yet."""
    ensure_tables(con)
    have = {r[0] for r in con.execute("SELECT tkr FROM companies").fetchall()}
    todo = sorted({t.upper() for t in tickers if t} - have)
    if not todo:
        return
    s = requests.Session()
    s.headers["User-Agent"] = sec_user_agent()
    ciks = {v["ticker"].upper(): int(v["cik_str"])
            for v in s.get(TICKERS_URL, timeout=30).json().values()}
    today = datetime.date.today()
    for i, t in enumerate(todo):
        cik = ciks.get(t) or ciks.get(t.replace(".", "-"))
        name = sic = desc = None
        if cik:
            time.sleep(0.15)
            r = s.get(SUBMISSIONS.format(cik=cik), timeout=30)
            if r.status_code == 200:
                j = r.json()
                name, desc = j.get("name"), j.get("sicDescription")
                sic = int(j["sic"]) if str(j.get("sic") or "").isdigit() else None
        con.execute("INSERT OR REPLACE INTO companies VALUES (?,?,?,?,?,?)",
                    [t, cik, name, sic, desc, today])
        if (i + 1) % 200 == 0:
            print(f"companies: {i + 1}/{len(todo)} industry codes fetched")


def committee_flag(con, tkr, last, first, chamber):
    """Plain-English flag like 'Armed Services oversees defense', or ''."""
    try:
        r = con.execute("SELECT sic FROM companies WHERE tkr = ?", [tkr]).fetchone()
    except duckdb.CatalogException:
        return ""
    sec = sectors_for_sic(r[0] if r else None)
    if not sec:
        return ""
    out = []
    for cid, cname in member_committees(con, last, first, chamber):
        overlap = sec & set(COMMITTEE_SECTORS[cid])
        if overlap:
            short = (cname.replace("House Committee on ", "").replace("Senate Committee on ", "")
                     .replace("Senate Select Committee on ", "").replace("House Permanent Select Committee on ", ""))
            out.append(f"{short} ({', '.join(sorted(overlap))})")
    return "; ".join(out)


def ensure_flags_ready(con, tickers):
    """Best-effort refresh of rosters + industries. Never fatal."""
    try:
        refresh_rosters(con)
        refresh_companies(con, tickers)
    except SystemExit as e:
        print(f"committee flags limited: {e}")
    except Exception as e:
        print(f"committee flags skipped this run ({type(e).__name__}: {e})")


# ------------------------------------------------------------------ selftest

def load_fixture(con):
    ensure_tables(con)
    legislators = [
        {"id": {"bioguide": "T1"}, "name": {"first": "Thomas", "nickname": "Tommy", "last": "Tuberville"},
         "terms": [{"type": "sen"}]},
        {"id": {"bioguide": "S1"}, "name": {"first": "Adam", "last": "Smith"}, "terms": [{"type": "rep"}]},
        {"id": {"bioguide": "S2"}, "name": {"first": "Jason", "last": "Smith"}, "terms": [{"type": "rep"}]},
        {"id": {"bioguide": "A1"}, "name": {"first": "Alpha", "last": "Alpha"}, "terms": [{"type": "sen"}]},
    ]
    committees = [{"thomas_id": "SSAS", "name": "Senate Committee on Armed Services"},
                  {"thomas_id": "HSAS", "name": "House Committee on Armed Services"},
                  {"thomas_id": "HSWM", "name": "House Committee on Ways and Means"},
                  {"thomas_id": "SSAP", "name": "Senate Committee on Appropriations"}]
    membership = {"SSAS": [{"bioguide": "T1"}], "HSAS": [{"bioguide": "S1"}],
                  "HSWM": [{"bioguide": "S2"}], "SSAP": [{"bioguide": "A1"}],
                  "SSAS02": [{"bioguide": "A1"}]}
    rows = build_roster(legislators, committees, membership, datetime.date(2025, 1, 1))
    con.executemany("INSERT INTO committee_members VALUES (?,?,?,?,?,?,?,?)", rows)
    con.executemany("INSERT INTO companies VALUES (?,?,?,?,?,?)", [
        ("LMT", 1, "Lockheed", 3760, "Guided missiles", None),
        ("UNH", 2, "UnitedHealth", 6324, "Hospital & medical service plans", None),
        ("AAA", 3, "Aaa Inc", 3761, "Guided missiles", None),
        ("SPY", None, None, None, None, None)])


def selftest():
    con = duckdb.connect()
    load_fixture(con)
    assert sectors_for_sic(3760) == {"defense"}
    assert sectors_for_sic(3721) == {"defense", "transportation"}
    assert sectors_for_sic(None) == set()
    assert committee_flag(con, "LMT", "Tuberville", "Thomas H", "S") == "Armed Services (defense)"
    assert committee_flag(con, "UNH", "Tuberville", "Thomas H", "S") == ""
    # two Smiths in the House: first name decides
    assert committee_flag(con, "LMT", "Smith", "Adam", "H") == "Armed Services (defense)"
    assert committee_flag(con, "UNH", "Smith", "Jason T", "H") == "Ways and Means (health)"
    assert committee_flag(con, "LMT", "Smith", "", "H") == "", "ambiguous -> no guess"
    assert committee_flag(con, "LMT", "Tuberville", "Thomas", "H") == "", "wrong chamber"
    assert committee_flag(con, "LMT", "Alpha", "A", "S") == "", "Appropriations and subcommittees ignored"
    assert committee_flag(con, "SPY", "Tuberville", "Thomas", "S") == ""
    assert committee_flag(con, "LMT", "Tuberville, Jr.", "Thomas", "S") == "Armed Services (defense)"
    assert _last_key("Hagerty, IV") == "hagerty" and _last_key("McClain Delaney") == "mcclain delaney"
    assert committee_flag(con, "ZZZ", "Tuberville", "Thomas", "S") == ""
    print("committees selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        c = duckdb.connect(DB_PATH)
        refresh_rosters(c, force=True)
        unmatched = c.execute("""SELECT DISTINCT chamber, last_name, first_name FROM trades
                                 WHERE filed_date > current_date - 365""").fetchall()
        miss = [u for u in unmatched if not member_committees(c, u[1], u[2], u[0])]
        print(f"{len(unmatched) - len(miss)} of {len(unmatched)} recent filers matched to committees")
        for ch, ln, fn in miss[:40]:
            print(f"  no match: {fn} {ln} ({ch}) -- not on a mapped committee, or left Congress")
