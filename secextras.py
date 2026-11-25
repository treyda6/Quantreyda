"""Short interest and new risk warnings for the stock pages, free.

  short    Shares sold short, twice a month, from FINRA's public consolidated
           short interest data (all U.S. exchanges; no key). "Days to cover" is
           how many days of normal trading it would take short sellers to buy
           their shares back. High numbers mean many people are betting the
           price falls (and a squeeze is possible).
  risks    The "Risk factors" section (Item 1A) of the latest two annual
           reports (10-K) from SEC EDGAR, compared: which warnings are new
           this year. A new warning is often the first plain statement of a
           problem the company sees coming.

CEO pay and stock splits come with the SEC health check (health.py).

    py secextras.py short AAPL MSFT
    py secextras.py risks AAPL
    py secextras.py --selftest
"""

import datetime
import difflib
import html
import json
import re
import sys
import time

import requests

import companyintel

FINRA_URL = "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
RISK_EVERY = 60      # days before re-checking a company's 10-K
RISK_PER_RUN = 15    # 10-Ks are big; a few companies a day


# ------------------------------------------------------------ short interest

def parse_short(rows):
    """{tkr: dict} from FINRA rows, keeping each symbol's latest settlement date."""
    out = {}
    for r in rows:
        t = (r.get("symbolCode") or "").upper()
        d = r.get("settlementDate") or ""
        if not t or (t in out and out[t]["date"] >= d):
            continue
        out[t] = {"date": d, "short": r.get("currentShortPositionQuantity"),
                  "prev": r.get("previousShortPositionQuantity"),
                  "days": r.get("daysToCoverQuantity"),
                  "chg": r.get("changePercent"),
                  "adv": r.get("averageDailyVolumeQuantity")}
    return out


def fetch_short(s, tickers, today=None):
    today = today or datetime.date.today()
    syms = sorted({t.upper().replace(".", " ") for t in tickers if t})
    out = {}
    for i in range(0, len(syms), 200):
        body = {"limit": 5000,
                "fields": ["symbolCode", "settlementDate", "currentShortPositionQuantity",
                           "previousShortPositionQuantity", "daysToCoverQuantity",
                           "changePercent", "averageDailyVolumeQuantity"],
                "domainFilters": [{"fieldName": "symbolCode", "values": syms[i:i + 200]}],
                "dateRangeFilters": [{"fieldName": "settlementDate",
                                      "startDate": str(today - datetime.timedelta(days=45)),
                                      "endDate": str(today)}]}
        time.sleep(0.5)
        r = s.post(FINRA_URL, json=body, headers={"Accept": "application/json"}, timeout=60)
        r.raise_for_status()
        out.update(parse_short(r.json() if r.text.strip() else []))
    return {t.replace(" ", "."): v for t, v in out.items()}


def refresh_short(con, tickers, today=None):
    companyintel.ensure_tables(con)
    s = requests.Session()
    s.headers["User-Agent"] = companyintel.UA
    got = fetch_short(s, tickers, today)
    for t, v in got.items():
        companyintel._save(con, t, "short", v["short"], json.dumps(v))
    print(f"short interest: {len(got)} of {len(set(tickers))} stocks "
          f"(settled {max((v['date'] for v in got.values()), default='—')})")


# -------------------------------------------------------------- risk factors

HEAD_RE = re.compile(r"item\s*1a\.?[\s\-–—:.]*risk\s+factors", re.I)
END_RE = re.compile(r"item\s*(1b|1c|2)\.?[\s\-–—:.]*(unresolved|cybersecurity|properties)", re.I)


def html_text(doc):
    """Readable text with one paragraph per line."""
    doc = re.sub(r"(?is)<(script|style).*?</\1>", " ", doc)
    doc = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d|table)>", "\n", doc)
    doc = re.sub(r"<[^>]+>", " ", doc)
    doc = html.unescape(doc).replace("\xa0", " ")
    lines = [re.sub(r"\s+", " ", x).strip() for x in doc.split("\n")]
    return "\n".join(x for x in lines if x)


def risk_section(text):
    """Item 1A's text: of every 'Item 1A. Risk Factors' heading (the table of
    contents has one too), the one followed by the longest section."""
    best = ""
    for m in HEAD_RE.finditer(text):
        stops = [x.start() for x in (END_RE.search(text, m.end()), HEAD_RE.search(text, m.end())) if x]
        part = text[m.end(): min(stops) if stops else m.end() + 400_000]
        if len(part) > len(best):
            best = part
    return best


def headings(section):
    """Risk headings: the short, sentence-like lines between long paragraphs."""
    out = []
    for line in section.split("\n"):
        if 40 <= len(line) <= 320 and line[0].isupper() and line.rstrip()[-1:] in ".:" \
                and not re.match(r"(?i)(table of contents|page \d|part i)", line):
            out.append(line.strip())
    return list(dict.fromkeys(out))


def _key(h):
    return re.sub(r"[^a-z ]", "", h.lower())[:160]


def new_risks(latest, prior):
    """Headings in `latest` with no close match in `prior`."""
    old = [_key(h) for h in prior]
    return [h for h in latest
            if not difflib.get_close_matches(_key(h), old, n=1, cutoff=0.75)]


def last_two_10k(s, cik):
    j = s.get(SUBMISSIONS.format(cik=cik), timeout=60).json()
    rec = j.get("filings", {}).get("recent", {})
    out = []
    for k, form in enumerate(rec.get("form", [])):
        if form in ("10-K", "20-F") and rec["primaryDocument"][k]:
            out.append((rec["filingDate"][k], DOC_URL.format(
                cik=cik, acc=rec["accessionNumber"][k].replace("-", ""),
                doc=rec["primaryDocument"][k])))
        if len(out) == 2:
            break
    return out


def fetch_risks(s, cik):
    docs = last_two_10k(s, cik)
    if len(docs) < 2:
        return None
    secs = []
    for _, url in docs:
        time.sleep(0.2)
        secs.append(risk_section(html_text(s.get(url, timeout=120).text)))
    now, before = headings(secs[0]), headings(secs[1])
    if len(now) < 5 or len(before) < 5:
        return None  # couldn't find the headings in this filing's layout
    words = len(secs[0].split()), len(secs[1].split())
    return {"filed": docs[0][0], "url": docs[0][1], "n": len(now), "prev_n": len(before),
            "words": words[0], "prev_words": words[1], "new": new_risks(now, before)[:8]}


def refresh_risks(con, tickers, today=None):
    import health
    companyintel.ensure_tables(con)
    today = today or datetime.date.today()
    have = dict(con.execute("SELECT tkr, checked_on FROM company_intel "
                            "WHERE source = 'risks'").fetchall())
    due = sorted((t for t in set(tickers) if t not in have or (today - have[t]).days >= RISK_EVERY),
                 key=lambda t: have.get(t, datetime.date.min))[:RISK_PER_RUN]
    if not due:
        return
    s = health._session()  # SEC wants a contact in the user agent
    ciks = health.ticker_ciks(s)
    found = 0
    for t in due:
        hit = ciks.get(t) or ciks.get(t.replace(".", "-"))
        got = None
        if hit:
            try:
                got = fetch_risks(s, hit[0])
            except requests.RequestException as e:
                print(f"  risks {t}: {type(e).__name__}")
                continue
        companyintel._save(con, t, "risks", len(got["new"]) if got else None,
                           json.dumps(got) if got else None)
        found += bool(got)
    print(f"risk factors: compared {found} of {len(due)} companies' last two annual reports")


def intel(con, tkr):
    out = {}
    try:
        rows = con.execute("SELECT source, detail FROM company_intel WHERE tkr = ? "
                           "AND source IN ('short', 'risks') AND detail IS NOT NULL", [tkr]).fetchall()
    except Exception:
        return out
    for source, detail in rows:
        out[source] = json.loads(detail)
    return out


def selftest():
    rows = [{"symbolCode": "AAA", "settlementDate": "2026-10-31", "currentShortPositionQuantity": 5e6,
             "daysToCoverQuantity": 2.1, "changePercent": 4.0},
            {"symbolCode": "AAA", "settlementDate": "2026-11-14", "currentShortPositionQuantity": 6e6,
             "daysToCoverQuantity": 7.5, "changePercent": 20.0},
            {"symbolCode": "BRK B", "settlementDate": "2026-11-14", "currentShortPositionQuantity": 1}]
    got = parse_short(rows)
    assert got["AAA"]["date"] == "2026-11-14" and got["AAA"]["days"] == 7.5, got
    doc = ("<html><p>Table of contents</p><p>Item 1A. Risk Factors</p><p>12</p>"
           "<p>Item 1. Business</p><p>We make things.</p>"
           "<p><b>Item 1A. Risk Factors</b></p>"
           "<p><i>Our business depends on a small number of large customers.</i></p>"
           "<p>" + "Long paragraph explaining the risk in detail. " * 20 + "</p>"
           "<p><i>New tariffs on imported parts could raise our costs significantly.</i></p>"
           "<p>" + "More detail. " * 60 + "</p>"
           "<p>Item 1B. Unresolved Staff Comments</p><p>None.</p></html>")
    sec = risk_section(html_text(doc))
    hs = headings(sec)
    assert hs == ["Our business depends on a small number of large customers.",
                  "New tariffs on imported parts could raise our costs significantly."], hs
    assert "Unresolved" not in sec and "We make things" not in sec
    prior = ["Our business depends on a small number of large customers!",
             "Competition is intense."]
    assert new_risks(hs, prior) == ["New tariffs on imported parts could raise our costs significantly."]
    import duckdb
    con = duckdb.connect()
    companyintel.ensure_tables(con)
    companyintel._save(con, "AAA", "short", 6e6, json.dumps(got["AAA"]))
    assert intel(con, "AAA")["short"]["days"] == 7.5 and intel(con, "ZZZ") == {}
    print("secextras selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    elif len(sys.argv) > 2 and sys.argv[1] == "short":
        se = requests.Session()
        se.headers["User-Agent"] = companyintel.UA
        print(json.dumps(fetch_short(se, sys.argv[2:]), indent=1))
    elif len(sys.argv) > 2 and sys.argv[1] == "risks":
        import health
        se = health._session()
        c = health.ticker_ciks(se)[sys.argv[2].upper()][0]
        print(json.dumps(fetch_risks(se, c), indent=1))
    else:
        print(__doc__)
