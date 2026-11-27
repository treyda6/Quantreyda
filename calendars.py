"""Calendars: what's coming up that can move stocks, all from free public data.

  earnings   who reports in the next three weeks (date, before the open or
             after the close, the EPS analysts expect) and how the last two
             weeks of reports went against those estimates: beat, miss or in
             line. Source: Nasdaq's public earnings calendar, plus Yahoo
             Finance (yfinance) for the stocks that have a page here, which
             also fills in each one's past beats and misses.
  economy    Fed rate decisions (federalreserve.gov), the jobs report, CPI,
             PPI and other big releases (the BLS release calendar), and
             Nasdaq's economic calendar for what economists expect vs what
             came out.
  IPOs       upcoming, recently priced and newly filed IPOs (Nasdaq's IPO
             calendar), backed up by new S-1/F-1 registrations at the SEC
             from companies that don't trade yet.
  spinoffs   Form 10-12B registrations at the SEC: a company being split off
             from its parent and listed on its own.

Each source is optional: if one is down the others still fill the page.

    py calendars.py            # fetch everything and print what's next
    py calendars.py --selftest
"""

import datetime
import html
import re
import sys
import time

import requests

import links
import fmt

E = html.escape
try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:  # no tz database: fall back to a fixed offset (off by an hour in summer)
    ET = datetime.timezone(datetime.timedelta(hours=-5))

NASDAQ_EARNINGS = "https://api.nasdaq.com/api/calendar/earnings"
NASDAQ_ECON = "https://api.nasdaq.com/api/calendar/economicevents"
NASDAQ_IPO = "https://api.nasdaq.com/api/ipo/calendar"
FED_CAL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
BLS_ICS = "https://www.bls.gov/schedule/news_release/bls.ics"
SEC_INDEX = "https://www.sec.gov/Archives/edgar/daily-index/{y}/QTR{q}/form.{d:%Y%m%d}.idx"
SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"
# Nasdaq's API answers browsers only
BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/124.0 Safari/537.36",
           "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
           "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}

AHEAD, BEHIND = 21, 14  # earnings days fetched each run
ECON_AHEAD = 45

# Fed meetings for 2026 as published by the Fed, in case its page can't be read.
# (month, first day, last day, projections) -- the decision comes on the last day at 2 pm ET.
FOMC_FALLBACK = {2026: [(1, 27, 28, False), (3, 17, 18, True), (4, 28, 29, False), (6, 16, 17, True),
                        (7, 28, 29, False), (9, 15, 16, True), (10, 27, 28, False), (12, 8, 9, True)]}

# The releases worth knowing about. (key, pattern, headline-row pattern, label, why, big)
# Order matters: the first pattern that matches wins.
ECON = [
    ("minutes", r"fomc.*minutes|fed.*minutes", r".", "Fed meeting minutes",
     "The full notes from the last Fed meeting. Markets read them for hints about the next move.", False),
    ("fomc", r"fomc|fed(eral)? (funds|interest) rate|interest rate decision|federal open market",
     r"rate decision|funds rate|fomc meeting", "Fed rate decision",
     "The Fed sets short-term interest rates. A cut usually helps stocks and bonds; a surprise moves everything.",
     True),
    ("jobs", r"nonfarm payrolls|employment situation|^unemployment rate", r"^nonfarm payrolls$|employment situation",
     "Jobs report",
     "How many jobs the US added last month and the unemployment rate. Too hot can mean higher rates; "
     "too cold can mean a slowdown.", True),
    ("cpi", r"\bcpi\b|consumer price index", r"^cpi \(yoy\)|consumer price index", "Inflation (CPI)",
     "How fast prices are rising for shoppers. Hotter than expected makes rate cuts less likely.", True),
    ("pce", r"\bpce\b|personal income and outlays|personal consumption expenditures",
     r"core pce price index \(yoy\)|personal income and outlays", "Fed's favourite inflation gauge (PCE)",
     "The inflation number the Fed itself targets (2%). Released with personal income and spending.", True),
    ("gdp", r"\bgdp\b|gross domestic product", r"^gdp \(qoq\)|gross domestic product", "Economic growth (GDP)",
     "How fast the whole US economy grew last quarter, at an annual rate.", True),
    ("ppi", r"\bppi\b|producer price index", r"^ppi \(mom\)|producer price index", "Wholesale prices (PPI)",
     "What businesses pay for goods and services. It can show up in consumer prices later.", False),
    ("retail", r"^retail sales|advance monthly sales for retail", r"^retail sales \(mom\)|retail", "Retail sales",
     "How much Americans spent in stores and online last month. Spending is about two-thirds of the economy.",
     False),
    ("jolts", r"jolts|job openings and labor turnover", r".", "Job openings (JOLTS)",
     "How many jobs employers are trying to fill. Falling openings mean a cooling job market.", False),
    ("eci", r"employment cost index", r".", "Wage growth (ECI)",
     "How fast pay and benefits are rising. Fast wage growth can keep inflation up.", False),
    ("ism", r"ism manufacturing pmi", r"^ism manufacturing pmi$", "Factory activity (ISM)",
     "A survey of factory managers. Above 50 means manufacturing is growing, below 50 shrinking.", False),
    ("sentiment", r"michigan consumer sentiment", r"^michigan consumer sentiment$", "Consumer sentiment",
     "How confident shoppers feel (University of Michigan survey).", False),
]
SKIP_ECON = re.compile(r"\bfed(?:'s)? [a-z]+ speaks|speech|testif|ex food|ex autos|ex gas|"
                       r"index \(ecb\)|real earnings", re.I)


# ---------- small helpers ----------

def num(s):
    """'$1.23' -> 1.23, '($0.12)' -> -0.12, '$3,000,000' -> 3e6, 'N/A' -> None."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s).strip()
    neg = t.startswith("(") and t.endswith(")") or t.startswith("-")
    t = re.sub(r"[^0-9.]", "", t)
    if not t or t == ".":
        return None
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def mdy(s):
    """'11/20/2026' -> date, else None."""
    m = re.match(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})", str(s or ""))
    return datetime.date(int(m[3]), int(m[1]), int(m[2])) if m else None


def verdict(eps, est):
    """'beat', 'miss', 'inline' or None, by at least a cent either way."""
    if eps is None or est is None:
        return None
    d = round(eps - est, 4)
    return "beat" if d >= 0.01 else "miss" if d <= -0.01 else "inline"


def surprise(eps, est):
    if eps is None or est is None or abs(est) < 1e-9:
        return None
    return round((eps - est) / abs(est) * 100, 1)


money = fmt.money


def eps_s(x):
    return "—" if x is None else (f"-${-x:.2f}" if x < 0 else f"${x:.2f}")


def weekdays(start, n, step=1):
    d, out = start, []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += datetime.timedelta(days=step)
    return out


def et_time(dt_utc):
    """A UTC datetime -> (ET date, '8:30 AM')."""
    loc = dt_utc.replace(tzinfo=datetime.timezone.utc).astimezone(ET)
    return loc.date(), loc.strftime("%I:%M %p").lstrip("0")


# ---------- tables ----------

def ensure_tables(con):
    con.execute("""CREATE TABLE IF NOT EXISTS earnings (
        d DATE, tkr VARCHAR, name VARCHAR, slot VARCHAR, mcap DOUBLE, quarter VARCHAR,
        est DOUBLE, n_est INTEGER, eps DOUBLE, last_year DOUBLE, src VARCHAR, checked TIMESTAMP,
        PRIMARY KEY (d, tkr))""")
    con.execute("CREATE TABLE IF NOT EXISTS earnings_checked (tkr VARCHAR PRIMARY KEY, checked DATE)")
    con.execute("""CREATE TABLE IF NOT EXISTS econ_events (
        d DATE, key VARCHAR, slot VARCHAR, name VARCHAR, actual VARCHAR, consensus VARCHAR,
        previous VARCHAR, src VARCHAR, PRIMARY KEY (d, key))""")
    con.execute("""CREATE TABLE IF NOT EXISTS ipos (
        id VARCHAR PRIMARY KEY, tkr VARCHAR, company VARCHAR, exchange VARCHAR, price VARCHAR,
        shares DOUBLE, value DOUBLE, status VARCHAR, d DATE, src VARCHAR, url VARCHAR)""")
    con.execute("""CREATE TABLE IF NOT EXISTS new_issues (
        file VARCHAR PRIMARY KEY, kind VARCHAR, form VARCHAR, filed DATE, cik BIGINT,
        company VARCHAR, url VARCHAR)""")
    con.execute("CREATE TABLE IF NOT EXISTS new_issue_days (d DATE PRIMARY KEY)")


# ---------- earnings ----------

AT = {"time-pre-market": "Before open", "time-after-hours": "After close"}


def parse_earnings(j, day, now):
    """Rows for the earnings table from one day of Nasdaq's earnings calendar."""
    rows = ((j or {}).get("data") or {}).get("rows") or []
    out = []
    for r in rows:
        tkr = (r.get("symbol") or "").strip().upper()
        if not tkr:
            continue
        n = num(r.get("noOfEsts"))
        out.append((day, tkr, (r.get("name") or "").strip(), AT.get(r.get("time"), ""),
                    num(r.get("marketCap")), (r.get("fiscalQuarterEnding") or "").strip(),
                    num(r.get("epsForecast")), int(n) if n is not None else None,
                    num(r.get("eps")), num(r.get("lastYearEPS")), "nasdaq", now))
    return out


def parse_yahoo(tkr, df, now):
    """Rows from yfinance's get_earnings_dates() frame (index: tz-aware timestamps)."""
    out = []
    if df is None or getattr(df, "empty", True):
        return out
    for ts, r in df.iterrows():
        try:
            loc = ts.tz_convert(ET) if ts.tzinfo else ts
        except Exception:
            loc = ts
        d = loc.date()
        at = "Before open" if loc.hour and loc.hour < 12 else "After close" if loc.hour >= 15 else ""

        def g(k):
            v = r.get(k)
            try:
                return None if v is None or v != v else float(v)
            except (TypeError, ValueError):
                return None
        out.append((d, tkr, "", at, None, "", g("EPS Estimate"), None, g("Reported EPS"), None, "yahoo", now))
    return out


def refresh_earnings(con, s, today, now):
    n, fails = 0, 0
    for day in weekdays(today - datetime.timedelta(days=BEHIND), BEHIND * 5 // 7 + AHEAD * 5 // 7 + 2):
        if day > today + datetime.timedelta(days=AHEAD):
            break
        try:
            r = s.get(NASDAQ_EARNINGS, params={"date": day.isoformat()}, headers=BROWSER, timeout=30)
            r.raise_for_status()
            rows = parse_earnings(r.json(), day, now)
        except (requests.RequestException, ValueError):
            fails += 1
            if fails >= 3 and n == 0:
                raise
            continue
        if rows:
            if day >= today:  # the day's list is the latest word: drop anyone who moved off it
                con.execute("DELETE FROM earnings WHERE d = ? AND src = 'nasdaq'", [day])
            con.executemany("INSERT OR REPLACE INTO earnings VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            n += len(rows)
        time.sleep(0.4)
    return n


def merge_yahoo(con, rows):
    """Add Yahoo rows without clobbering Nasdaq's: fill gaps only."""
    for row in rows:
        d, tkr = row[0], row[1]
        # Yahoo and Nasdaq can disagree by a day on when a report "is"
        near = con.execute("SELECT d FROM earnings WHERE tkr = ? AND d BETWEEN ? AND ? AND src = 'nasdaq'",
                           [tkr, d - datetime.timedelta(days=1), d + datetime.timedelta(days=1)]).fetchone()
        if near:
            con.execute("UPDATE earnings SET est = coalesce(est, ?), eps = coalesce(eps, ?) WHERE d = ? AND tkr = ?",
                        [row[6], row[8], near[0], tkr])
        else:
            con.execute("INSERT OR IGNORE INTO earnings VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", list(row))


def refresh_tickers(con, tickers, minutes=8, today=None):
    """Next report date and past beats/misses for these tickers (Yahoo, weekly per ticker)."""
    ensure_tables(con)
    try:
        import yfinance as yf
    except ImportError:
        print("earnings history skipped: yfinance not installed")
        return
    today = today or datetime.date.today()
    done = {t for t, in con.execute("SELECT tkr FROM earnings_checked WHERE checked > ?",
                                    [today - datetime.timedelta(days=7)]).fetchall()}
    todo = [t for t in dict.fromkeys(tickers) if t and t not in done]
    stop, now, n = time.monotonic() + minutes * 60, datetime.datetime.utcnow().replace(microsecond=0), 0
    for t in todo:
        if time.monotonic() > stop:
            break
        try:
            df = yf.Ticker(t.replace(".", "-")).get_earnings_dates(limit=12)
            merge_yahoo(con, parse_yahoo(t, df, now))
            n += 1
        except Exception:
            pass  # ETFs and funds have no earnings; mark them checked anyway
        con.execute("INSERT OR REPLACE INTO earnings_checked VALUES (?, ?)", [t, today])
        time.sleep(0.3)
    print(f"earnings history: {n} of {len(todo)} stocks checked")


# ---------- economy ----------

def classify(name):
    """(key, label, why, big) for a release we care about, else None."""
    n = (name or "").strip()
    if not n or SKIP_ECON.search(n):
        return None
    for key, pat, _, label, why, big in ECON:
        if re.search(pat, n, re.I):
            return key, label, why, big
    return None


def headline(key, name):
    for k, _, main, *_ in ECON:
        if k == key:
            return bool(re.search(main, name or "", re.I))
    return False


def parse_fed(text, today=None):
    """[(decision date, 'projections' or '')] from the Fed's FOMC calendar page."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(text, "lxml")
    months = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug",
                                          "sep", "oct", "nov", "dec"], 1)}
    out = []
    for panel in soup.select("div.panel"):
        h = panel.find(["h4", "h3", "h5", "a"], string=re.compile(r"\d{4} FOMC Meetings"))
        if not h:
            continue
        year = int(re.search(r"(\d{4})", h.get_text())[1])
        for row in panel.select("div.fomc-meeting"):
            mo = row.select_one(".fomc-meeting__month")
            dt = row.select_one(".fomc-meeting__date")
            if not mo or not dt:
                continue
            mtxt = mo.get_text(" ", strip=True).lower()
            dtxt = dt.get_text(" ", strip=True)
            if "cancel" in dtxt.lower() or "notation" in dtxt.lower():
                continue
            parts = [months[p[:3]] for p in re.split(r"[/\-]", mtxt) if p[:3] in months]
            days = [int(x) for x in re.findall(r"\d+", dtxt)]
            if not parts or not days:
                continue
            month = parts[-1] if len(parts) > 1 and days[-1] < days[0] else parts[0]
            try:
                out.append((datetime.date(year, month, days[-1]), "projections" if "*" in dtxt else ""))
            except ValueError:
                continue
    return sorted(set(out))


def fed_fallback(years):
    return [(datetime.date(y, m, d2), "projections" if sep else "")
            for y in years for m, d1, d2, sep in FOMC_FALLBACK.get(y, [])]


def parse_ics(text):
    """[(date, 'HH:MM AM' ET, summary)] from an iCalendar file."""
    text = re.sub(r"\r?\n[ \t]", "", text)  # unfold long lines
    out = []
    for ev in text.split("BEGIN:VEVENT")[1:]:
        summ = re.search(r"^SUMMARY[^:]*:(.*)$", ev, re.M)
        start = re.search(r"^DTSTART([^:]*):(\d{8})(?:T(\d{4,6})(Z?))?", ev, re.M)
        if not summ or not start:
            continue
        ymd, hms = start[2], start[3]
        d = datetime.date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8]))
        at = ""
        if hms:
            t = datetime.datetime.combine(d, datetime.time(int(hms[:2]), int(hms[2:4])))
            if start[4] == "Z":
                d, at = et_time(t)
            else:
                at = t.strftime("%I:%M %p").lstrip("0")
        out.append((d, at, summ[1].strip().replace("\\,", ",")))
    return out


def parse_nasdaq_econ(j, day):
    """[(date, at ET, name, actual, consensus, previous)] for US releases on one day."""
    rows = ((j or {}).get("data") or {}).get("rows") or []
    out = []
    for r in rows:
        if "united states" not in (r.get("country") or "").lower():
            continue
        name = (r.get("eventName") or "").strip()
        d, at = day, ""
        m = re.match(r"(\d{1,2}):(\d{2})", r.get("gmt") or "")
        if m:
            d, at = et_time(datetime.datetime.combine(day, datetime.time(int(m[1]), int(m[2]))))

        def clean(v):
            v = html.unescape(str(v or "")).strip()
            return "" if v in ("-", "&nbsp;", "N/A") else v
        out.append((d, at, name, clean(r.get("actual")), clean(r.get("consensus")), clean(r.get("previous"))))
    return out


def merge_econ(fed, bls, nasdaq):
    """One row per (date, release): official dates, with Nasdaq's numbers where it has them.
    -> {(d, key): (d, key, at, name, actual, consensus, previous, src)}"""
    out = {}
    for d, proj in fed:
        name = "Fed rate decision" + (" + economic projections" if proj else "")
        out[(d, "fomc")] = (d, "fomc", "2:00 PM", name, "", "", "", "fed")
    for d, at, summ in bls:
        c = classify(summ)
        if c and (d, c[0]) not in out:
            out[(d, c[0])] = (d, c[0], at, summ, "", "", "", "bls")
    best = {}
    for d, at, name, act, cons, prev in nasdaq:
        c = classify(name)
        if not c:
            continue
        k = (d, c[0])
        score = (headline(c[0], name), bool(cons or act))
        if k not in best or score > best[k][0]:
            best[k] = (score, (d, c[0], at, name, act, cons, prev))
    for k, (_, (d, key, at, name, act, cons, prev)) in best.items():
        old = out.get(k)
        if key == "fomc" and old:  # keep the Fed's own wording and time, add the numbers
            out[k] = old[:4] + (act, cons, prev, "fed+nasdaq")
        else:
            out[k] = (d, key, at or (old[2] if old else ""), name, act, cons, prev, "nasdaq")
    return out


def refresh_econ(con, s, today, ua):
    notes, fed, bls, nas = [], [], [], []
    try:
        r = s.get(FED_CAL, headers={"User-Agent": ua}, timeout=30)
        r.raise_for_status()
        fed = parse_fed(r.text)
    except Exception as e:
        notes.append(f"Fed {type(e).__name__}")
    if not fed:
        fed = fed_fallback({today.year, today.year + 1})
    try:
        r = s.get(BLS_ICS, headers={"User-Agent": ua}, timeout=30)
        r.raise_for_status()
        bls = parse_ics(r.text)
    except Exception as e:
        notes.append(f"BLS {type(e).__name__}")
    fails = 0
    for day in weekdays(today - datetime.timedelta(days=BEHIND), 45):
        if day > today + datetime.timedelta(days=ECON_AHEAD):
            break
        try:
            r = s.get(NASDAQ_ECON, params={"date": day.isoformat()}, headers=BROWSER, timeout=30)
            r.raise_for_status()
            nas += parse_nasdaq_econ(r.json(), day)
        except (requests.RequestException, ValueError) as e:
            fails += 1
            if fails >= 3 and not nas:
                notes.append(f"Nasdaq economic calendar {type(e).__name__}")
                break
        time.sleep(0.3)
    lo, hi = today - datetime.timedelta(days=BEHIND), today + datetime.timedelta(days=400)
    rows = [v for v in merge_econ(fed, bls, nas).values() if lo <= v[0] <= hi]
    if rows:
        # past rows Nasdaq no longer covers keep their numbers; the window we just fetched is replaced
        con.execute("DELETE FROM econ_events WHERE d >= ?", [lo])
        con.executemany("INSERT OR REPLACE INTO econ_events VALUES (?,?,?,?,?,?,?,?)", rows)
    return len(rows), notes


# ---------- IPOs and spinoffs ----------

def parse_ipos(j):
    """Rows for the ipos table from one month of Nasdaq's IPO calendar."""
    data = (j or {}).get("data") or {}
    out = []

    def rows(part, *path):
        x = data.get(part) or {}
        for p in path:
            x = (x or {}).get(p) or {}
        return (x or {}).get("rows") or []

    for status, part, path, dkey in (("priced", "priced", (), "pricedDate"),
                                     ("upcoming", "upcoming", ("upcomingTable",), "expectedPriceDate"),
                                     ("filed", "filed", (), "filedDate"),
                                     ("withdrawn", "withdrawn", (), "withdrawDate")):
        for r in rows(part, *path):
            tkr = (r.get("proposedTickerSymbol") or "").strip().upper()
            name = (r.get("companyName") or "").strip()
            if not name:
                continue
            deal = r.get("dealID") or f"{tkr}-{name}"
            out.append((f"nasdaq-{deal}", tkr, name, (r.get("proposedExchange") or "").strip(),
                        (r.get("proposedSharePrice") or "").strip(), num(r.get("sharesOffered")),
                        num(r.get("dollarValueOfSharesOffered")), status, mdy(r.get(dkey)), "nasdaq",
                        f"https://www.nasdaq.com/market-activity/ipos/overview?dealId={deal}"))
    return out


INDEX_LINE = re.compile(r"^(?P<form>.+?)\s{2,}(?P<name>.+?)\s{2,}(?P<cik>\d+)\s+(?P<date>\d{8})\s+"
                        r"(?P<file>edgar/data/\S+)\s*$")
ISSUE_FORMS = {"10-12B": "spinoff", "10-12B/A": "spinoff", "S-1": "ipo", "F-1": "ipo"}
NOT_IPO = re.compile(r"\b(trust|fund|etf|bitcoin|ether|acquisition corp|capital corp\b.*\bi+\b|"
                     r"blank check|spac|royalty|portfolio|income|bdc|lp\b|l\.p\.)", re.I)


def parse_issue_index(text, listed):
    """Spinoff (Form 10) and IPO (S-1/F-1 from unlisted companies) filings in one daily form.idx."""
    out = []
    for line in text.splitlines():
        m = INDEX_LINE.match(line)
        if not m:
            continue
        form = m["form"].strip()
        kind = ISSUE_FORMS.get(form)
        if not kind:
            continue
        cik, name = int(m["cik"]), m["name"].strip()
        if kind == "ipo" and (cik in listed or NOT_IPO.search(name)):
            continue  # already trades, or a fund/SPAC rather than an operating company
        f = m["file"].rsplit("/", 1)[-1]
        dd = m["date"]
        out.append((f, kind, form, datetime.date(int(dd[:4]), int(dd[4:6]), int(dd[6:])), cik, name.title(),
                    f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type={form.split('/')[0]}"))
    return out


def refresh_sec(con, s, today, days=90, minutes=6):
    try:
        from schema import sec_user_agent
        ua = sec_user_agent()
    except SystemExit as e:
        return 0, [f"SEC skipped ({e})"]
    done = {r[0] for r in con.execute("SELECT d FROM new_issue_days").fetchall()}
    todo = [d for d in (today - datetime.timedelta(days=k) for k in range(1, days + 1))
            if d.weekday() < 5 and d not in done]
    if not todo:
        return 0, []
    h = {"User-Agent": ua}
    listed = {int(v["cik_str"]) for v in s.get(SEC_TICKERS, headers=h, timeout=30).json().values()}
    stop, n = time.monotonic() + minutes * 60, 0
    for d in todo:  # newest first, so a cut-short first run still has the latest
        if time.monotonic() > stop:
            break
        time.sleep(0.2)
        r = s.get(SEC_INDEX.format(y=d.year, q=(d.month - 1) // 3 + 1, d=d), headers=h, timeout=60)
        if r.status_code not in (200, 404):
            continue  # not published yet; try again tomorrow
        rows = parse_issue_index(r.text, listed) if r.status_code == 200 else []
        if rows:
            con.executemany("INSERT OR REPLACE INTO new_issues VALUES (?,?,?,?,?,?,?)", rows)
            n += len(rows)
        con.execute("INSERT OR REPLACE INTO new_issue_days VALUES (?)", [d])
    return n, []


def refresh_ipos(con, s, today):
    n = 0
    months = {(today.replace(day=1) + datetime.timedelta(days=k)).strftime("%Y-%m") for k in (-20, 0, 40)}
    for m in sorted(months):
        r = s.get(NASDAQ_IPO, params={"date": m}, headers=BROWSER, timeout=30)
        r.raise_for_status()
        rows = parse_ipos(r.json())
        if rows:
            con.executemany("INSERT OR REPLACE INTO ipos VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
            n += len(rows)
        time.sleep(0.4)
    return n


# ---------- the daily step ----------

def refresh(con, today=None):
    ensure_tables(con)
    today = today or datetime.date.today()
    now = datetime.datetime.utcnow().replace(microsecond=0)
    s = requests.Session()
    try:
        import companyintel
        ua = companyintel.UA
    except Exception:
        ua = "Quantreyda personal research tool (github.com/treyda6/Quantreyda)"
    s.headers["User-Agent"] = ua
    notes, counts = [], {}
    for label, step in (("earnings", lambda: refresh_earnings(con, s, today, now)),
                        ("IPOs", lambda: refresh_ipos(con, s, today))):
        try:
            counts[label] = step()
        except Exception as e:
            notes.append(f"{label} {type(e).__name__}")
    try:
        counts["economy"], more = refresh_econ(con, s, today, ua)
        notes += more
    except Exception as e:
        notes.append(f"economy {type(e).__name__}")
    try:
        counts["SEC filings"], more = refresh_sec(con, s, today)
        notes += more
    except Exception as e:
        notes.append(f"SEC {type(e).__name__}")
    print("calendars: " + ", ".join(f"{v} {k}" for k, v in counts.items())
          + (f" ({'; '.join(notes)})" if notes else ""))


# ---------- reading back ----------

def _q(con, sql, args=()):
    try:
        return con.execute(sql, list(args)).fetchall()
    except Exception:
        return []


def upcoming_earnings(con, today, days=14):
    return _q(con, """SELECT d, tkr, name, slot, mcap, quarter, est, n_est, last_year FROM earnings
                      WHERE d BETWEEN ? AND ? ORDER BY d, mcap DESC NULLS LAST, tkr""",
              [today, today + datetime.timedelta(days=days)])


def recent_results(con, today, days=14):
    return _q(con, """SELECT d, tkr, name, slot, mcap, est, eps FROM earnings
                      WHERE d BETWEEN ? AND ? AND eps IS NOT NULL AND est IS NOT NULL
                      ORDER BY mcap DESC NULLS LAST, d DESC""",
              [today - datetime.timedelta(days=days), today])


def econ(con, start, end):
    return _q(con, """SELECT d, key, slot, name, actual, consensus, previous FROM econ_events
                      WHERE d BETWEEN ? AND ? ORDER BY d, slot""", [start, end])


def ipo_rows(con, status, start, end):
    return _q(con, """SELECT tkr, company, exchange, price, shares, value, d, url FROM ipos
                      WHERE status = ? AND d BETWEEN ? AND ? ORDER BY d DESC, value DESC NULLS LAST""",
              [status, start, end])


def issues(con, kind, start):
    """Latest filing per company: (company, first filed, latest filed, filings, form, url)."""
    return _q(con, """SELECT company, min(filed), max(filed), count(*), arg_max(form, filed), arg_max(url, filed)
                      FROM new_issues WHERE kind = ? GROUP BY company HAVING max(filed) >= ?
                      ORDER BY max(filed) DESC""", [kind, start])


def for_ticker(con, tkr, today):
    """(next report or None, [past reports newest first])."""
    nxt = _q(con, "SELECT d, slot, est FROM earnings WHERE tkr = ? AND d >= ? ORDER BY d LIMIT 1", [tkr, today])
    past = _q(con, """SELECT d, est, eps FROM earnings WHERE tkr = ? AND d < ? AND eps IS NOT NULL
                      ORDER BY d DESC LIMIT 8""", [tkr, today + datetime.timedelta(days=1)])
    return (nxt[0] if nxt else None), past


# ---------- HTML ----------

def _stock(tkr, built, root=""):
    return f"<a href='{E(links.stock(tkr, built, root))}'><b>{E(tkr)}</b></a>"


def _badge(v):
    if not v:
        return ""
    word = {"beat": "Beat", "miss": "Miss", "inline": "In line"}[v]
    return f"<span class='vd {v}'>{word}</span>"


def _econ_why(key):
    for k, _, _, label, why, big in ECON:
        if k == key:
            return label, why, big
    return key, "", False


PER_DAY = 12


def earnings_html(con, today, built):
    up = upcoming_earnings(con, today, 14)
    out = ["<h2 id='earnings'>Earnings calendar</h2><p class='hint'>Who reports in the next two weeks, "
           "biggest companies first. “Expected” is the earnings per share (EPS) analysts forecast; a company "
           "that beats it often jumps, one that misses often drops. Stocks with a page here are linked.</p>"]
    radar = [r for r in up if r[1] in built]
    if radar:
        out.append("<div class='cal-radar'><b>On your radar</b> <span class='hint'>stocks people here "
                   "have been trading</span><div class='chips'>" + "".join(
                       f"<span class='chip'>{_stock(r[1], built)} {r[0]:%a %b %d}"
                       f"{' · ' + E(r[3].lower()) if r[3] else ''}</span>" for r in radar[:30]) + "</div></div>")
    if not up:
        out.append("<p class='hint'>No upcoming reports saved yet. They're fetched on the daily run.</p>")
    days = []
    for r in up:
        if not days or days[-1][0] != r[0]:
            days.append((r[0], []))
        days[-1][1].append(r)
    for d, rows in days:
        show = [r for r in rows[:PER_DAY]] + [r for r in rows[PER_DAY:] if r[1] in built]
        more = len(rows) - len(show)
        items = "".join(
            f"<tr><td>{_stock(tkr, built)}<small>{E(name[:44])}</small></td><td>{E(at) or '—'}</td>"
            f"<td class='num'>{eps_s(est)}<small>{f'{n} analysts' if n else ''}</small></td>"
            f"<td class='num'>{eps_s(ly)}</td><td class='num'>{money(mc) if mc else '—'}</td></tr>"
            for _, tkr, name, at, mc, q, est, n, ly in show)
        out.append(f"<details class='cal-day'{' open' if d <= today + datetime.timedelta(days=3) else ''}>"
                   f"<summary><b>{d:%A, %b %d}</b> <span class='hint'>{len(rows)} reporting</span></summary>"
                   "<div class='wrap'><table class='ct'><thead><tr><th>Company</th><th>When</th>"
                   "<th class='num'>Expected EPS</th><th class='num'>Year ago</th><th class='num'>Size</th>"
                   f"</tr></thead><tbody>{items}</tbody></table></div>"
                   + (f"<p class='hint'>+{more} smaller companies</p>" if more > 0 else "") + "</details>")
    res = recent_results(con, today, 14)
    if res:
        beats = sum(verdict(eps, est) == "beat" for *_, est, eps in res)
        out.append(f"<h3>Recent results</h3><p class='hint'>Last two weeks: {beats} of {len(res)} companies "
                   "beat what analysts expected. Most companies beat, so a miss stands out more.</p>")
        items = "".join(
            f"<tr><td>{_stock(tkr, built)}<small>{E(name[:44])}</small></td><td>{d:%b %d}</td>"
            f"<td class='num'>{eps_s(eps)}<small>vs {eps_s(est)}</small></td>"
            f"<td class='num'>{_badge(verdict(eps, est))}<small>{'' if surprise(eps, est) is None else f'{surprise(eps, est):+.0f}%'}</small></td></tr>"
            for d, tkr, name, at, mc, est, eps in res[:25])
        out.append("<div class='wrap'><table class='ct'><thead><tr><th>Company</th><th>Reported</th>"
                   "<th class='num'>EPS vs expected</th><th class='num'>Result</th></tr></thead>"
                   f"<tbody>{items}</tbody></table></div>")
    return "".join(out)


def econ_html(con, today):
    up = econ(con, today, today + datetime.timedelta(days=ECON_AHEAD))
    past = [r for r in econ(con, today - datetime.timedelta(days=BEHIND), today - datetime.timedelta(days=1))
            if r[4]]
    out = ["<h2 id='economy'>Economic calendar</h2><p class='hint'>The reports that move the whole market: "
           "Fed rate decisions, inflation and jobs. Times are US Eastern. ★ marks the biggest ones. "
           "“Expected” is what economists forecast; the surprise is what moves prices.</p>"]
    fed = _q(con, "SELECT d, name FROM econ_events WHERE key = 'fomc' AND d >= ? ORDER BY d LIMIT 1", [today])
    if fed:
        days = (fed[0][0] - today).days
        out.append(f"<div class='cal-next'><span class='hint'>Next Fed decision</span><b>{fed[0][0]:%A, %b %d}</b>"
                   f"<span class='hint'>{'today' if days == 0 else f'in {days} days'}</span></div>")
    if not up:
        out.append("<p class='hint'>No dates saved yet. They're fetched on the daily run.</p>")
    else:
        items = []
        for d, key, at, name, act, cons, prev in up:
            label, why, big = _econ_why(key)
            nums = " · ".join(x for x in (f"expected {E(cons)}" if cons else "",
                                          f"last {E(prev)}" if prev else "") if x)
            items.append(f"<li class='{'big' if big else ''}'><div class='d'><b>{d:%a %b %d}</b>"
                         f"<small>{E(at)}</small></div><div><b>{'★ ' if big else ''}{E(label)}</b>"
                         f"<small>{E(why)}</small>{f'<small class=nums>{nums}</small>' if nums else ''}</div></li>")
        out.append("<ul class='ev'>" + "".join(items) + "</ul>")
    if past:
        out.append("<h3>Just released</h3><div class='wrap'><table class='ct'><thead><tr><th>Release</th>"
                   "<th>Date</th><th class='num'>Actual</th><th class='num'>Expected</th>"
                   "<th class='num'>Before</th></tr></thead><tbody>" + "".join(
                       f"<tr><td><b>{E(_econ_why(key)[0])}</b><small>{E(name)}</small></td><td>{d:%b %d}</td>"
                       f"<td class='num'><b>{E(act)}</b></td><td class='num'>{E(cons) or '—'}</td>"
                       f"<td class='num'>{E(prev) or '—'}</td></tr>"
                       for d, key, at, name, act, cons, prev in reversed(past)) + "</tbody></table></div>")
    return "".join(out)


def ipo_html(con, today):
    out = ["<h2 id='ipos'>IPOs and spinoffs</h2><p class='hint'>New companies coming to the stock market. "
           "IPOs often swing wildly in their first months; many long-term investors wait a quarter or two "
           "for the price to settle. Spinoffs, a piece of a company split off on its own, have a "
           "better long-term record: index funds often sell them right away for reasons that have "
           "nothing to do with the business.</p>"]
    up = ipo_rows(con, "upcoming", today - datetime.timedelta(days=3), today + datetime.timedelta(days=60))
    priced = ipo_rows(con, "priced", today - datetime.timedelta(days=45), today)
    filed = ipo_rows(con, "filed", today - datetime.timedelta(days=60), today)

    def table(rows, when, price_word):
        return ("<div class='wrap'><table class='ct'><thead><tr><th>Company</th>"
                f"<th>{when}</th><th class='num'>{price_word}</th><th class='num'>Raising</th></tr></thead><tbody>"
                + "".join(f"<tr><td><b>{E(t) or '—'}</b> <span class='hint'>{E(ex)}</span><small>"
                          f"<a href='{E(u)}'>{E(c[:50])}</a></small></td><td>{d:%b %d}</td>"
                          f"<td class='num'>{('$' + E(p)) if p else '—'}</td><td class='num'>{money(v) if v else '—'}</td></tr>"
                          for t, c, ex, p, sh, v, d, u in rows if d) + "</tbody></table></div>")
    if up:
        out.append("<h3>Coming soon</h3>" + table(up, "Expected", "Price range"))
    if priced:
        out.append("<h3>Just listed</h3>" + table(priced[:25], "Priced", "IPO price"))
    if filed:
        out.append("<h3>Filed to go public</h3>" + table(filed[:20], "Filed", "Price"))
    sec_ipo = issues(con, "ipo", today - datetime.timedelta(days=45))
    if sec_ipo and not (up or priced or filed):
        out.append("<h3>Filed to go public (SEC)</h3><p class='hint'>New S-1 and F-1 registrations from "
                   "companies that don't trade yet. Not every filing ends in an IPO.</p><ul class='list'>"
                   + "".join(f"<li><span class='d'>{last:%b %d}</span><a href='{E(u)}'>{E(c)}</a> "
                             f"<span class='hint'>({E(form)})</span></li>" for c, first, last, n, form, u in sec_ipo[:25])
                   + "</ul>")
    spins = issues(con, "spinoff", today - datetime.timedelta(days=120))
    out.append("<h3>Spinoffs in the works</h3>")
    if spins:
        out.append("<p class='hint'>Companies that registered with the SEC to be split off and listed "
                   "(Form 10). Each amendment usually means the split is getting closer.</p><ul class='list'>"
                   + "".join(f"<li><span class='d'>{last:%b %d}</span><a href='{E(u)}'>{E(c)}</a> "
                             f"<span class='hint'>first filed {first:%b %d, %Y}"
                             f"{f' · {n - 1} update' + ('s' if n > 2 else '') if n > 1 else ''}</span></li>"
                             for c, first, last, n, form, u in spins[:25]) + "</ul>")
    else:
        out.append("<p class='hint'>No spinoff filings saved yet. The SEC's filing index is checked on the "
                   "daily run.</p>")
    if not (up or priced or filed or sec_ipo):
        out.append("<p class='hint'>No IPOs saved yet. They're fetched on the daily run.</p>")
    return "".join(out)


def page(con, today, built=()):
    built = set(built)
    return ("<div class='sec-top'><h1>Calendar</h1><p class='hint'>What's coming that can move "
            "stocks: company earnings, the economy, and new listings.</p>"
            "<div class='chips cal-jump'><a class='chip' href='#earnings'>Earnings</a>"
            "<a class='chip' href='#economy'>Economy</a><a class='chip' href='#ipos'>IPOs &amp; spinoffs</a>"
            "</div></div>" + earnings_html(con, today, built) + econ_html(con, today) + ipo_html(con, today))


def stock_panel(con, tkr, today):
    """Next report date and the beat/miss record, for a stock page."""
    nxt, past = for_ticker(con, tkr, today)
    if not nxt and not past:
        return ""
    head = ""
    if nxt:
        d, at, est = nxt
        days = (d - today).days
        head = (f"<p><b>Next report: {d:%A, %b %d}</b>{' (' + E(at.lower()) + ')' if at else ''}"
                f" <span class='hint'>{'today' if days == 0 else f'in {days} days'}"
                f"{f' · analysts expect {eps_s(est)} a share' if est is not None else ''}</span></p>")
    rec = ""
    if past:
        vs = [verdict(eps, est) for _, est, eps in past]
        beats = vs.count("beat")
        rec = (f"<p class='hint'>Beat estimates {beats} of the last {len(past)} times.</p><ul class='list'>"
               + "".join(f"<li><span class='d'>{d:%b %d, %Y}</span>{eps_s(eps)} vs {eps_s(est)} expected "
                         f"{_badge(v)}</li>" for (d, est, eps), v in zip(past, vs)) + "</ul>")
    return f"<section class='panel'><h2>Earnings</h2>{head}{rec}</section>"


def home_teaser(con, today=None):
    """A few lines for the home page's Calendar tile: next Fed decision, next big release,
    the biggest companies reporting this week."""
    today = today or datetime.date.today()
    bits = []
    fed = _q(con, "SELECT d FROM econ_events WHERE key = 'fomc' AND d >= ? ORDER BY d LIMIT 1", [today])
    if fed:
        bits.append(f"Fed decision {fed[0][0]:%b %d}")
    big = [k for k, *_, b in ECON if b and k != "fomc"]
    nxt = _q(con, "SELECT d, key FROM econ_events WHERE d >= ? AND key IN (SELECT unnest(?)) ORDER BY d LIMIT 1",
             [today, big])
    if nxt:
        bits.append(f"{_econ_why(nxt[0][1])[0]} {nxt[0][0]:%b %d}")
    up = upcoming_earnings(con, today, 7)
    top = sorted(up, key=lambda r: -(r[4] or 0))[:3]
    if top:
        bits.append("Reporting: " + ", ".join(f"{r[1]} {r[0]:%a}" for r in top))
    return "<br>".join(E(b) for b in bits)


CSS = """
.cal-jump{margin:4px 0 6px}.cal-jump a{color:var(--fg);text-decoration:none}
.cal-radar{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:12px 14px;margin:10px 0 14px}
.cal-radar .chips{margin-top:8px}.cal-radar a{color:var(--fg)}
.cal-day{background:var(--card);border:1px solid var(--line);border-radius:16px;margin:8px 0;padding:4px 14px}
.cal-day summary{cursor:pointer;padding:10px 0;display:flex;gap:10px;align-items:baseline}
.cal-day[open] summary{border-bottom:1px solid var(--line)}
.ct td small{display:block;color:var(--mut);font-size:11.5px}.ct td a{color:var(--fg)}
.ct .num{text-align:right;font-variant-numeric:tabular-nums}.ct th{position:static}
.vd{font-size:12px;font-weight:700;border-radius:6px;padding:1px 7px}
.vd.beat{color:var(--up);background:color-mix(in srgb,var(--up) 14%,transparent)}
.vd.miss{color:var(--down);background:color-mix(in srgb,var(--down) 14%,transparent)}
.vd.inline{color:var(--mut);background:var(--chip)}
.cal-next{display:inline-flex;flex-direction:column;background:var(--card);border:1px solid var(--line);
border-radius:16px;padding:12px 16px;margin:4px 0 12px}.cal-next b{font-size:20px}
.ev{list-style:none;padding:0;margin:0;background:var(--card);border:1px solid var(--line);border-radius:16px}
.ev li{display:grid;grid-template-columns:110px 1fr;gap:12px;padding:10px 14px;border-bottom:1px solid var(--line)}
.ev li:last-child{border-bottom:0}.ev li.big{box-shadow:inset 3px 0 0 var(--acc)}
.ev small{display:block;color:var(--mut);font-size:12.5px}.ev small.nums{color:var(--fg);margin-top:2px}
.ev .d small{font-variant-numeric:tabular-nums}
@media(max-width:640px){.ev li{grid-template-columns:84px 1fr;gap:8px;padding:10px}.cal-day{padding:2px 10px}}
"""


# ---------- offline check ----------

def selftest():
    import duckdb
    assert num("$1.23") == 1.23 and num("($0.12)") == -0.12 and num("$3,000,000") == 3e6 and num("N/A") is None
    assert verdict(1.10, 1.00) == "beat" and verdict(0.95, 1.0) == "miss" and verdict(1.004, 1.0) == "inline"
    assert surprise(1.1, 1.0) == 10.0 and surprise(1, None) is None
    assert money(3.2e12) == "$3.2T" and money(5e8) == "$500M"
    now = datetime.datetime(2026, 11, 27, 12)
    today = datetime.date(2026, 11, 27)

    # earnings
    up = {"data": {"rows": [
        {"time": "time-after-hours", "symbol": "AAA", "name": "Aaa Corp", "marketCap": "$2,000,000,000,000",
         "fiscalQuarterEnding": "Oct/2026", "epsForecast": "$1.50", "noOfEsts": "12", "lastYearEPS": "$1.20"},
        {"time": "time-pre-market", "symbol": "ZZZ", "name": "Zzz Inc", "marketCap": "$900,000,000",
         "epsForecast": "($0.05)", "noOfEsts": "2", "lastYearEPS": "N/A"}]}}
    er = parse_earnings(up, datetime.date(2026, 12, 2), now)
    assert er[0][1] == "AAA" and er[0][3] == "After close" and er[0][4] == 2e12 and er[0][6] == 1.5 and er[0][7] == 12
    assert er[1][6] == -0.05 and er[1][9] is None
    past = {"data": {"rows": [
        {"time": "time-pre-market", "symbol": "BBB", "name": "Bbb Co", "marketCap": "$50,000,000,000",
         "epsForecast": "$2.00", "eps": "$2.30", "surprise": "15"},
        {"time": "time-after-hours", "symbol": "CCC", "name": "Ccc Co", "marketCap": "$40,000,000,000",
         "epsForecast": "$1.00", "eps": "$0.80"}]}}
    con = duckdb.connect()
    ensure_tables(con)
    con.executemany("INSERT OR REPLACE INTO earnings VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    er + parse_earnings(past, datetime.date(2026, 11, 20), now))
    import pandas as pd
    df = pd.DataFrame({"EPS Estimate": [1.5, 1.4, 1.3], "Reported EPS": [float("nan"), 1.45, 1.2],
                       "Surprise(%)": [float("nan"), 3.5, -7.7]},
                      index=pd.to_datetime(["2026-12-02 16:05", "2026-08-30 16:05", "2026-05-30 16:05"]).tz_localize(ET))
    yr = parse_yahoo("AAA", df, now)
    assert yr[0][0] == datetime.date(2026, 12, 2) and yr[0][3] == "After close" and yr[1][8] == 1.45, yr
    merge_yahoo(con, yr)
    assert con.execute("SELECT count(*) FROM earnings WHERE tkr = 'AAA'").fetchone()[0] == 3
    assert con.execute("SELECT src FROM earnings WHERE tkr='AAA' AND d='2026-12-02'").fetchone()[0] == "nasdaq"
    nxt, hist = for_ticker(con, "AAA", today)
    assert nxt[0] == datetime.date(2026, 12, 2) and [verdict(e, s) for _, s, e in hist] == ["beat", "miss"]
    sp = stock_panel(con, "AAA", today)
    assert "Next report: Wednesday, Dec 02" in sp and "Beat estimates 1 of the last 2" in sp
    assert stock_panel(con, "NOPE", today) == ""

    # economy
    fed_html = """<div class="panel panel-default"><div class="panel-heading"><h4><a>2026 FOMC Meetings</a></h4></div>
      <div class="row fomc-meeting"><div class="fomc-meeting__month"><strong>January</strong></div>
      <div class="fomc-meeting__date">27-28</div></div>
      <div class="row fomc-meeting"><div class="fomc-meeting__month"><strong>Apr/May</strong></div>
      <div class="fomc-meeting__date">30-1</div></div>
      <div class="row fomc-meeting"><div class="fomc-meeting__month"><strong>December</strong></div>
      <div class="fomc-meeting__date">8-9*</div></div></div>"""
    fed = parse_fed(fed_html)
    assert fed == [(datetime.date(2026, 1, 28), ""), (datetime.date(2026, 5, 1), ""),
                   (datetime.date(2026, 12, 9), "projections")], fed
    assert datetime.date(2026, 12, 9) in [d for d, _ in fed_fallback([2026])]
    ics = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nDTSTART;TZID=US-Eastern:20261204T083000\r\n"
           "SUMMARY:Employment Situation for Nove\r\n mber 2026\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\n"
           "DTSTART:20261210T133000Z\r\nSUMMARY:Consumer Price Index for November 2026\r\nEND:VEVENT\r\n"
           "BEGIN:VEVENT\r\nDTSTART;VALUE=DATE:20261211\r\nSUMMARY:Real Earnings for November 2026\r\nEND:VEVENT\r\n")
    bls = parse_ics(ics)
    assert bls[0] == (datetime.date(2026, 12, 4), "8:30 AM", "Employment Situation for November 2026"), bls
    assert bls[1][:2] == (datetime.date(2026, 12, 10), "8:30 AM"), bls
    assert classify("Real Earnings for November 2026") is None and classify("Nonfarm Payrolls")[0] == "jobs"
    assert classify("FOMC Meeting Minutes")[0] == "minutes" and classify("Fed Interest Rate Decision")[0] == "fomc"
    assert classify("Core CPI (MoM)")[0] == "cpi" and classify("Fed Governor Waller Speaks") is None
    nj = {"data": {"rows": [
        {"gmt": "13:30", "country": "United States", "eventName": "Nonfarm Payrolls", "actual": "",
         "consensus": "150K", "previous": "119K"},
        {"gmt": "13:30", "country": "United States", "eventName": "Average Hourly Earnings (MoM)", "consensus": "0.3%"},
        {"gmt": "13:30", "country": "United States", "eventName": "Unemployment Rate", "consensus": "4.4%"},
        {"gmt": "09:00", "country": "Germany", "eventName": "German CPI", "consensus": "2%"}]}}
    nas = parse_nasdaq_econ(nj, datetime.date(2026, 12, 4))
    assert len(nas) == 3 and nas[0][:3] == (datetime.date(2026, 12, 4), "8:30 AM", "Nonfarm Payrolls"), nas
    pastj = {"data": {"rows": [{"gmt": "13:30", "country": "United States", "eventName": "CPI (YoY)",
                                "actual": "2.9%", "consensus": "3.0%", "previous": "3.0%"}]}}
    nas += parse_nasdaq_econ(pastj, datetime.date(2026, 11, 13))
    m = merge_econ(fed_fallback([2026]), bls, nas)
    assert m[(datetime.date(2026, 12, 4), "jobs")][3] == "Nonfarm Payrolls"  # headline row wins
    assert m[(datetime.date(2026, 12, 4), "jobs")][5] == "150K"
    assert m[(datetime.date(2026, 12, 9), "fomc")][3].startswith("Fed rate decision")
    assert (datetime.date(2026, 12, 10), "cpi") in m
    con.executemany("INSERT OR REPLACE INTO econ_events VALUES (?,?,?,?,?,?,?,?)", list(m.values()))

    # IPOs and spinoffs
    ij = {"data": {"priced": {"rows": [{"dealID": "1", "proposedTickerSymbol": "NEWC", "companyName": "NewCo Inc",
                                        "proposedExchange": "NASDAQ Global", "proposedSharePrice": "17.00",
                                        "sharesOffered": "10,000,000", "pricedDate": "11/20/2026",
                                        "dollarValueOfSharesOffered": "$170,000,000"}]},
                   "upcoming": {"upcomingTable": {"rows": [{"dealID": "2", "proposedTickerSymbol": "SOON",
                                                            "companyName": "Soon Corp", "proposedExchange": "NYSE",
                                                            "proposedSharePrice": "14.00-16.00",
                                                            "expectedPriceDate": "12/04/2026",
                                                            "dollarValueOfSharesOffered": "$300,000,000"}]}},
                   "filed": {"rows": [{"dealID": "3", "companyName": "Later Ltd", "filedDate": "11/14/2026"}]},
                   "withdrawn": None}}
    ip = parse_ipos(ij)
    assert [(r[1], r[7], r[8]) for r in ip] == [("NEWC", "priced", datetime.date(2026, 11, 20)),
                                                ("SOON", "upcoming", datetime.date(2026, 12, 4)),
                                                ("", "filed", datetime.date(2026, 11, 14))], ip
    con.executemany("INSERT OR REPLACE INTO ipos VALUES (?,?,?,?,?,?,?,?,?,?,?)", ip)
    idx = ("Form Type   Company Name   CIK   Date Filed  File Name\n" + "-" * 40 + "\n"
           "10-12B          Spinco Holdings Inc                                  2000001     20261120    edgar/data/2000001/0001-26-000001.txt\n"
           "10-12B/A        Spinco Holdings Inc                                  2000001     20261125    edgar/data/2000001/0001-26-000009.txt\n"
           "S-1             Brand New Robotics Inc                               2000002     20261121    edgar/data/2000002/0001-26-000002.txt\n"
           "S-1             Old Listed Corp                                      320193      20261121    edgar/data/320193/0001-26-000003.txt\n"
           "S-1             Ishares Bitcoin Trust                                2000003     20261121    edgar/data/2000003/0001-26-000004.txt\n"
           "4               Someone                                              2000004     20261121    edgar/data/2000004/0001-26-000005.txt\n")
    iss = parse_issue_index(idx, {320193})
    assert [(r[1], r[5]) for r in iss] == [("spinoff", "Spinco Holdings Inc"), ("spinoff", "Spinco Holdings Inc"),
                                           ("ipo", "Brand New Robotics Inc")], iss
    con.executemany("INSERT OR REPLACE INTO new_issues VALUES (?,?,?,?,?,?,?)", iss)
    assert issues(con, "spinoff", today - datetime.timedelta(days=30))[0][3] == 2

    pg = page(con, today, {"AAA"})
    for s in ("Earnings calendar", "On your radar", "href='s/AAA.html'", "After close", "$1.50",
              "Recent results", "1 of 2 companies", "Miss", "Economic calendar", "Next Fed decision",
              "Jobs report", "expected 150K", "Just released", "2.9%", "Coming soon", "SOON", "$14.00-16.00",
              "Just listed", "NEWC", "$170M", "Spinoffs in the works", "Spinco Holdings Inc", "1 update"):
        assert s in pg, s
    assert "<script" not in pg
    ht = home_teaser(con, today)
    assert "Fed decision Dec 09" in ht and "Jobs report Dec 04" in ht and "Reporting: AAA Wed" in ht, ht
    assert home_teaser(duckdb.connect(), today) == ""
    empty = duckdb.connect()
    assert "fetched on the daily run" in page(empty, today)  # no tables yet: still renders
    print("calendars selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        import duckdb
        from schema import DB_PATH
        c = duckdb.connect(DB_PATH)
        refresh(c)
        t = datetime.date.today()
        for r in upcoming_earnings(c, t, 7)[:20]:
            print(f"{r[0]}  {r[1]:6} {r[3]:12} est {eps_s(r[6])}")
        for r in econ(c, t, t + datetime.timedelta(days=30)):
            print(f"{r[0]}  {r[2]:9} {r[3]}")
