"""Extra company intel for the stock pages, all from free public sources.

  lobbying     Senate Lobbying Disclosure Act filings: what the company spent
               on lobbying over the last four quarters, and on which issues.
               (lda.senate.gov API; no key needed at a slow pace)
  contracts    Federal contract dollars the company received over the last
               12 months (USAspending.gov API, no key).
  buzz         English Wikipedia page views: is the public suddenly reading
               about this company? (Wikimedia pageviews API, no key)
  buybacks     Stock buybacks and dividends come with the SEC health check
               (health.py), not from here.

Each source is cached and refreshed on a slow cycle, a few dozen companies a
day, so the daily run stays quick and nobody's API gets hammered.

  refresh(con, tickers, names)   {tkr: company name}
  intel(con, tkr)                dict for the stock page and cards

Usage:
    py companyintel.py AAPL "Apple Inc."   # fetch one and print
    py companyintel.py --selftest
"""

import datetime
import re
import sys
import time
import urllib.parse

import requests

# Wikimedia asks bots for "Name/version (contact URL) library/version"; vaguer
# user agents get 403s or tight rate limits.
UA = ("QuantreydaBot/1.0 (https://github.com/treyda6/Quantreyda; personal research tool) "
      f"python-requests/{requests.__version__}")
LDA_HOSTS = ["https://lda.senate.gov", "https://lda.gov"]
USA_URL = "https://api.usaspending.gov/api/v2/search/spending_by_category/recipient/"
WIKI_SEARCH = "https://en.wikipedia.org/w/api.php"
WIKI_VIEWS = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/"
              "all-access/user/{title}/daily/{start:%Y%m%d}/{end:%Y%m%d}")
PER_DAY = {"lobbying": 30, "contracts": 60, "buzz": 300}   # companies refreshed per run
EVERY = {"lobbying": 30, "contracts": 14, "buzz": 1}       # days before a refresh

SUFFIX = re.compile(r"\b(INC|INCORPORATED|CORP|CORPORATION|CO|COMPANY|LTD|PLC|LLC|LP|HOLDINGS?|"
                    r"GROUP|THE|N\.?V|S\.?A|AG|SE|CLASS [A-Z])\b\.?", re.I)


def base_name(name):
    """'Lockheed Martin Corp /DE/' -> 'Lockheed Martin' (for searching other databases)."""
    s = re.sub(r"/[A-Za-z]{2,3}/?", " ", name or "")  # SEC's "/DE/" state tags
    s = SUFFIX.sub(" ", s)
    s = re.sub(r"[^\w&' .-]", " ", s)
    return re.sub(r"\s+", " ", s).strip(" .,-")


def ensure_tables(con):
    con.execute("""CREATE TABLE IF NOT EXISTS company_intel (
        tkr VARCHAR, source VARCHAR, checked_on DATE, amount DOUBLE, detail VARCHAR,
        PRIMARY KEY (tkr, source))""")


def _save(con, tkr, source, amount, detail):
    con.execute("INSERT OR REPLACE INTO company_intel VALUES (?, ?, ?, ?, ?)",
                [tkr, source, datetime.date.today(), amount, detail])


# ------------------------------------------------------------------ lobbying

def lobbying_total(results, name):
    """(dollars, top issues) from LDA filing records for one client."""
    key = base_name(name).upper()
    total, issues = 0.0, {}
    for f in results:
        client = base_name((f.get("client") or {}).get("name") or "").upper()
        if client != key:
            continue
        total += float(f.get("income") or 0) + float(f.get("expenses") or 0)
        for a in f.get("lobbying_activities") or []:
            i = a.get("general_issue_code_display")
            if i:
                issues[i] = issues.get(i, 0) + 1
    top = [k for k, _ in sorted(issues.items(), key=lambda kv: -kv[1])[:3]]
    return total, top


def fetch_lobbying(s, name, today):
    years = sorted({today.year, (today - datetime.timedelta(days=365)).year})
    cutoff = today - datetime.timedelta(days=365)
    results = []
    for host in LDA_HOSTS:
        try:
            for y in years:
                url = f"{host}/api/v1/filings/"
                params = {"client_name": base_name(name), "filing_year": y, "page_size": 100}
                while url:
                    time.sleep(4.5)  # anonymous use allows ~15 requests a minute
                    r = s.get(url, params=params, timeout=60)
                    r.raise_for_status()
                    j = r.json()
                    results += [f for f in j.get("results", [])
                                if (f.get("dt_posted") or "9999")[:10] >= str(cutoff)]
                    url, params = j.get("next"), None
            return lobbying_total(results, name)
        except requests.RequestException:
            results = []
            continue
    return None


# ----------------------------------------------------------------- contracts

def contracts_total(results, name):
    key = base_name(name).upper()

    def same(n):
        # "LOCKHEED MARTIN ROTARY..." is a division of Lockheed Martin, but
        # "APPLE HOSPITALITY" isn't Apple: only multi-word names match as a prefix
        n = base_name(n or "").upper()
        return n == key or (" " in key and n.startswith(key + " "))
    return sum(float(r.get("amount") or 0) for r in results if same(r.get("name")))


def fetch_contracts(s, name, today):
    body = {"category": "recipient", "limit": 20, "filters": {
        "recipient_search_text": [base_name(name)],
        "award_type_codes": ["A", "B", "C", "D"],
        "time_period": [{"start_date": str(today - datetime.timedelta(days=365)),
                         "end_date": str(today)}]}}
    time.sleep(0.5)
    r = s.post(USA_URL, json=body, timeout=60)
    r.raise_for_status()
    return contracts_total(r.json().get("results", []), name)


# ---------------------------------------------------------------------- buzz

def buzz_ratio(views):
    """Last 7 days' average vs the 60 days before. views: [(date, n)] oldest first."""
    if len(views) < 30:
        return None, None
    recent = [n for _, n in views[-7:]]
    base = [n for _, n in views[-67:-7]]
    avg_r, avg_b = sum(recent) / len(recent), sum(base) / max(1, len(base))
    return avg_r, (avg_r / avg_b if avg_b >= 20 else None)  # tiny pages swing wildly


def _wiki_get(s, url, **kw):
    """GET with a polite pause, retrying when Wikimedia says slow down (429/503)."""
    for attempt in range(3):
        time.sleep(0.3)
        r = s.get(url, timeout=30, **kw)
        if r.status_code not in (429, 503):
            return r
        wait = r.headers.get("Retry-After", "")
        time.sleep(min(30, int(wait)) if wait.isdigit() else 5 * (attempt + 1))
    return r


def wiki_title(s, name):
    r = _wiki_get(s, WIKI_SEARCH, params={"action": "query", "list": "search", "format": "json",
                                   "srsearch": f"{base_name(name)} company", "srlimit": 3})
    r.raise_for_status()
    key = base_name(name).split(" ")[0].lower()
    for hit in r.json().get("query", {}).get("search", []):
        if key and key in hit["title"].lower():
            return hit["title"]
    return None


def fetch_buzz(s, name, today, title=None):
    title = title or wiki_title(s, name)
    if not title:
        return None
    start = today - datetime.timedelta(days=75)
    url = WIKI_VIEWS.format(title=urllib.parse.quote(title.replace(" ", "_"), safe=""),
                            start=start, end=today - datetime.timedelta(days=1))
    r = _wiki_get(s, url)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    views = [(i["timestamp"][:8], i["views"]) for i in r.json().get("items", [])]
    avg, ratio = buzz_ratio(views)
    return avg, ratio, title


# ------------------------------------------------------------------ driver

def refresh(con, names):
    """names: {tkr: company name}. Refreshes the stalest few of each source."""
    ensure_tables(con)
    today = datetime.date.today()
    s = requests.Session()
    s.headers["User-Agent"] = UA
    for source in ("buzz", "contracts", "lobbying"):
        have = {t: d for t, d in con.execute(
            "SELECT tkr, checked_on FROM company_intel WHERE source = ?", [source]).fetchall()}
        due = [t for t in names if t not in have
               or (today - have[t]).days >= EVERY[source]]
        due.sort(key=lambda t: have.get(t, datetime.date.min))
        done = fails = 0
        for t in due[:PER_DAY[source]]:
            if fails >= 8:  # the source is down or blocking us; try again next run
                print(f"  {source}: stopping after {fails} errors in a row")
                break
            name = names[t]
            if not base_name(name):
                continue
            try:
                if source == "lobbying":
                    got = fetch_lobbying(s, name, today)
                    if got is not None:
                        _save(con, t, source, got[0], "; ".join(got[1]))
                elif source == "contracts":
                    _save(con, t, source, fetch_contracts(s, name, today), None)
                else:
                    old = con.execute("SELECT detail FROM company_intel WHERE tkr = ? AND "
                                      "source = 'buzz'", [t]).fetchone()
                    title = old[0].split("|", 1)[0] if old and old[0] else None
                    got = fetch_buzz(s, name, today, title)
                    if got:
                        avg, ratio, title = got
                        _save(con, t, source, avg, f"{title}|{'' if ratio is None else round(ratio, 2)}")
                    else:
                        _save(con, t, source, None, None)
                done += 1
                fails = 0
            except requests.RequestException as e:
                fails += 1
                code = getattr(getattr(e, "response", None), "status_code", None)
                print(f"  {source} {t}: {type(e).__name__}{f' {code}' if code else ''}")
        print(f"company intel: {source} refreshed for {done} companies")


def intel(con, tkr):
    out = {}
    try:
        rows = con.execute("SELECT source, amount, detail, checked_on FROM company_intel "
                           "WHERE tkr = ?", [tkr]).fetchall()
    except Exception:  # table not created yet
        rows = []
    for source, amount, detail, checked in rows:
        if source == "lobbying" and amount is not None:
            out["lobbying"] = amount
            out["lobbying_issues"] = [x for x in (detail or "").split("; ") if x]
        elif source == "contracts" and amount is not None:
            out["contracts"] = amount
        elif source == "buzz" and amount is not None:
            title, _, ratio = (detail or "").partition("|")
            out["views"] = amount
            out["wiki"] = title
            out["buzz"] = float(ratio) if ratio else None
    try:
        r = con.execute("SELECT buybacks, dividends FROM health WHERE tkr = ?", [tkr]).fetchone()
        if r:
            out["buybacks"], out["dividends"] = r
    except Exception:  # older database without these columns yet
        pass
    return out


def selftest():
    import duckdb
    assert base_name("Lockheed Martin Corp /DE/") == "Lockheed Martin", base_name("Lockheed Martin Corp /DE/")
    assert base_name("The Boeing Company") == "Boeing"
    lda = [{"client": {"name": "LOCKHEED MARTIN CORPORATION"}, "income": None, "expenses": "3200000.00",
            "lobbying_activities": [{"general_issue_code_display": "Defense"},
                                    {"general_issue_code_display": "Budget/Appropriations"}]},
           {"client": {"name": "Lockheed Martin Corp"}, "income": "120000", "expenses": None,
            "lobbying_activities": [{"general_issue_code_display": "Defense"}]},
           {"client": {"name": "Not Lockheed Fan Club"}, "income": "999", "expenses": None}]
    total, top = lobbying_total(lda, "Lockheed Martin Corp")
    assert total == 3_320_000 and top[0] == "Defense", (total, top)
    usa = [{"name": "LOCKHEED MARTIN CORPORATION", "amount": 4.1e10},
           {"name": "LOCKHEED MARTIN ROTARY AND MISSION SYSTEMS", "amount": 1e9},
           {"name": "SIKORSKY", "amount": 5e9}]
    assert contracts_total(usa, "Lockheed Martin Corp") == 4.2e10
    assert contracts_total([{"name": "APPLE HOSPITALITY REIT", "amount": 5}], "Apple Inc.") == 0
    days = [(f"d{k}", 100) for k in range(60)] + [(f"e{k}", 400) for k in range(7)]
    avg, ratio = buzz_ratio(days)
    assert avg == 400 and ratio == 4.0
    assert buzz_ratio(days[:10]) == (None, None)
    con = duckdb.connect()
    ensure_tables(con)
    _save(con, "LMT", "lobbying", 3.3e6, "Defense; Budget/Appropriations")
    _save(con, "LMT", "buzz", 400, "Lockheed Martin|4.0")
    i = intel(con, "LMT")
    assert i["lobbying"] == 3.3e6 and i["lobbying_issues"][0] == "Defense" and i["buzz"] == 4.0
    assert intel(duckdb.connect(), "LMT") == {}
    print("companyintel selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        import duckdb
        from schema import DB_PATH
        c = duckdb.connect(DB_PATH)
        refresh(c, {sys.argv[1].upper(): sys.argv[2]})
        print(intel(c, sys.argv[1].upper()))
