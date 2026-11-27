"""Readiness levels: a DEFCON-style read on how calm or stressed the world is
right now, for investing and for everyday preparedness.

Seven areas, each scored from free public data (no keys, no paid feeds):

  markets         fear gauge, junk-bond premium, Fed stress index, S&P 500
                  drawdown, yield curve, Sahm rule (FRED)
  cyber           CISA's Known Exploited Vulnerabilities list, SEC 8-K
                  Item 1.05 "material cyber incident" filings, CISA joint
                  advisories
  geopolitics     conflict coverage in world news vs normal (GDELT), State
                  Department travel advisories raised, oil volatility, news-
                  based policy uncertainty (FRED)
  infrastructure  space weather that can hit the power grid (NOAA SWPC), CISA
                  industrial-control-system advisories, extreme heat/cold
                  warnings that strain the grid (National Weather Service)
  hazards         extreme weather warnings (NWS), big earthquakes (USGS
                  PAGER), hurricanes (NHC), new federal disasters (FEMA)
  supply          NY Fed supply-chain pressure index, oil, natural gas,
                  gasoline and diesel price jumps (FRED)
  health          CDC travel health notices, WHO disease outbreak news

Every signal maps its latest value onto 0-100 points between a "calm" and a
"severe" mark, both shown on the page. An area's score is half its worst
signal plus half the average (one alarming number moves it; the top levels
need several). The overall score is 60% the worst area plus 40% the average.
Scores map to five levels, numbered like DEFCON: 5 is calm, 1 is the most
serious. This is our own yardstick, not any official government level.

Runs twice:
  daily (daily.py)       refresh(con) stores a snapshot; the site build renders
                         the page with stock moves and links (dashboard.py)
  hourly (news.yml)      py readiness.py feed/readiness.json old.json ...
                         re-checks every source and publishes the snapshot,
                         with the page's HTML, to the news-feed branch; the
                         page swaps it in when it's newer

    py readiness.py --selftest
"""

import csv
import datetime
import html
import io
import json
import os
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile

import links

E = html.escape
UA = "QuantreydaBot/1.0 (https://github.com/treyda6/Quantreyda; personal research tool)"
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
GNEWS = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
KEV = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
CISA_CSA = "https://www.cisa.gov/cybersecurity-advisories/cybersecurity-advisories.xml"
CISA_ICS = "https://www.cisa.gov/cybersecurity-advisories/ics-advisories.xml"
EFTS = "https://efts.sec.gov/LATEST/search-index"
NWS = "https://api.weather.gov/alerts/active?status=actual&message_type=alert"
USGS = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_month.geojson"
NHC = "https://www.nhc.noaa.gov/CurrentStorms.json"
FEMA = "https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries"
SWPC = "https://services.swpc.noaa.gov/products/noaa-scales.json"
GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"
TRAVEL = "https://travel.state.gov/_res/rss/TAsTWs.xml"
GSCPI = "https://www.newyorkfed.org/medialibrary/research/interactives/gscpi/downloads/gscpi_data.xlsx"
CDC_TRAVEL = "https://wwwnc.cdc.gov/travel/rss/notices.xml"
WHO_DON = "https://www.who.int/api/news/diseaseoutbreaknews"
HISTORY_DAYS = 60

# level: (name, color, what it means)
LEVELS = {
    5: ("Normal", "#2fa36b", "Nothing unusual. Stick to the plan."),
    4: ("Watch", "#3d8bd9", "A few things are off normal. Worth knowing, not worth acting on."),
    3: ("Elevated", "#d9a521", "Several signals are up. Check your plan and your basics."),
    2: ("High", "#e0702b", "Clear, broad stress. Expect volatility and disruption in this area."),
    1: ("Severe", "#d9373e", "Rare, serious conditions. Act on the preparedness notes."),
}


def level_for(score):
    if score is None:
        return None
    return 5 if score < 20 else 4 if score < 40 else 3 if score < 60 else 2 if score < 80 else 1


# key, name, what it covers, related [(ticker, why)], notes by band {calm, up, high}:
# (investing, preparedness)
DOMAINS = [
    ("markets", "Markets & money",
     "Fear and stress in stocks, bonds and banks, and the classic recession alarms.",
     [("SPY", "S&P 500 fund"), ("XLP", "Consumer staples: people still buy groceries"),
      ("XLU", "Utilities: steady demand"), ("TLT", "Long Treasuries: often rise when stocks fall"),
      ("GLD", "Gold: a classic hedge"), ("BIL", "T-bills: cash that pays interest")],
     {"calm": ("Calm markets are the time to rebalance and add on schedule, not to chase.",
               "Keep three to six months of expenses in savings."),
      "up": ("Volatility is up. Check that your stock/bond mix still fits how long until you need the money; "
             "avoid selling on a bad day.",
             "Top up your emergency fund before you need it; know which bills are fixed."),
      "high": ("Real stress. History says panic-selling locks in losses; rebalancing into weakness has paid "
               "for patient investors. Make sure near-term cash needs are not in stocks.",
               "Hold off on big optional purchases; keep cash for 6+ months of expenses.")}),
    ("cyber", "Cyber",
     "Hacking actually happening: actively exploited software flaws, companies disclosing material "
     "breaches, and government warnings about campaigns.",
     [("CIBR", "Cybersecurity stocks fund"), ("CRWD", "CrowdStrike: endpoint security, breach response"),
      ("PANW", "Palo Alto Networks: firewalls and security platform"), ("FTNT", "Fortinet: network security"),
      ("ZS", "Zscaler: cloud security"), ("OKTA", "Okta: logins and identity"),
      ("NET", "Cloudflare: blocks attacks at the network edge")],
     {"calm": ("Background noise. Security stocks trade on earnings, not headlines.",
               "Keep devices updated and turn on two-factor logins (an authenticator app beats texts)."),
      "up": ("More attacks than usual. Breach headlines often lift security stocks for days; a hacked "
             "company's own stock usually dips, then recovers if the damage is contained.",
             "Install updates now (phones, browsers, routers). Check haveibeenpwned.com for your email; "
             "change reused passwords; use a password manager."),
      "high": ("Broad campaign or a big flaw being exploited everywhere. Expect outages at some companies; "
               "security names tend to outperform for a while.",
               "Freeze your credit at Equifax, Experian and TransUnion (free). Back up important files "
               "offline. Be extra wary of urgent emails and texts asking you to log in or pay.")}),
    ("geopolitics", "Geopolitics",
     "Wars, military escalation and policy shocks, measured by world news coverage against normal, "
     "travel warnings and what oil traders are pricing.",
     [("ITA", "US defense stocks fund"), ("LMT", "Lockheed Martin: defense"), ("RTX", "RTX: missiles and engines"),
      ("NOC", "Northrop Grumman: defense"), ("XLE", "Energy: oil spikes on conflict"),
      ("GLD", "Gold: bought in a crisis")],
     {"calm": ("No unusual tension. Defense and energy trade on their own fundamentals.",
               "Nothing to do. Keep passports current if you travel."),
      "up": ("Tension is rising. Oil, gold and defense stocks often jump first; most conflict sell-offs in "
             "the broad market have reversed within weeks.",
             "If you travel abroad, check the State Department advisory and enroll in STEP (step.state.gov)."),
      "high": ("Serious escalation. Expect oil and gold spikes and a risk-off market for a while. Avoid "
               "betting the farm on headlines either way.",
               "Postpone travel to affected regions. Keep a little cash at home and a full tank of gas.")}),
    ("infrastructure", "Grid & infrastructure",
     "Threats to power and industrial systems: solar storms that can hit the grid, flaws in "
     "industrial control systems, and heat or cold that strains supply.",
     [("XLU", "Utilities fund"), ("GEV", "GE Vernova: grid equipment and turbines"),
      ("ETN", "Eaton: electrical equipment"), ("PWR", "Quanta Services: builds and repairs grids"),
      ("GNRC", "Generac: home generators, demand jumps with outages"), ("VRT", "Vertiv: backup power for data centers")],
     {"calm": ("Grid spending (data centers, upgrades) is a long-term theme regardless of the level.",
               "Have flashlights and a phone power bank charged."),
      "up": ("Strain or a solar storm on the way. Generator and grid-repair stocks can pop on outages.",
             "Charge power banks, keep a few days of water and any medicine that needs a fridge plan."),
      "high": ("Real risk of wide outages. Utilities can face costs; equipment makers often benefit later.",
               "Fill water containers, fuel up, have cash (card readers fail without power), and know how to "
               "open your garage door by hand.")}),
    ("hazards", "Weather & disasters",
     "Extreme weather warnings, big earthquakes, hurricanes and new federal disaster declarations.",
     [("TRV", "Travelers: insurer, pays storm claims"), ("ALL", "Allstate: home and auto insurer"),
      ("HD", "Home Depot: rebuilding demand"), ("LOW", "Lowe's: rebuilding demand"),
      ("GNRC", "Generac: generators"), ("CAT", "Caterpillar: cleanup and rebuilding equipment")],
     {"calm": ("Nothing big. Insurers trade on pricing and rates.",
               "Keep a basic kit: water (a gallon per person per day for 3 days), food, meds, flashlight, radio."),
      "up": ("Big storms or disasters under way. Insurers can dip on losses; home-improvement and "
             "generator makers often see a bump.",
             "Check alerts for your area (weather.gov or a NOAA weather radio), refill prescriptions, and "
             "photograph your home for insurance."),
      "high": ("A major disaster. Insurer losses and local disruption; rebuilding stocks benefit later.",
               "If you're in the path: follow local officials, know your evacuation route, and go early.")}),
    ("supply", "Supply chain & prices",
     "Shipping and supply bottlenecks and sudden jumps in fuel prices that feed into everything else.",
     [("XLE", "Energy producers"), ("ZIM", "ZIM: container shipping rates"), ("MATX", "Matson: Pacific shipping"),
      ("FDX", "FedEx: freight volumes"), ("DBA", "Farm commodities fund"), ("WMT", "Walmart: shoppers trade down")],
     {"calm": ("Supply is flowing; prices are steady.", "Nothing special. Normal shopping."),
      "up": ("Bottlenecks or fuel jumps. Producers gain, transport and retailers feel the squeeze; "
             "inflation can tick up a month or two later.",
             "Buy what you'll use anyway a bit earlier; no need to hoard."),
      "high": ("A real shock. Expect inflation pressure and choppy markets; energy and shipping names swing hardest.",
               "Keep a couple of weeks of staples and meds on hand. Panic buying makes shortages worse.")}),
    ("health", "Health",
     "Disease outbreaks and travel health warnings from the CDC and the World Health Organization.",
     [("XLV", "Health care fund"), ("ABT", "Abbott: diagnostics and tests"),
      ("TMO", "Thermo Fisher: lab equipment"), ("PFE", "Pfizer: vaccines and drugs"),
      ("MRNA", "Moderna: vaccines"), ("CLX", "Clorox: cleaning supplies")],
     {"calm": ("Nothing unusual. Health stocks trade on drugs and policy.",
               "Stay current on routine vaccines (flu, COVID, measles if unsure)."),
      "up": ("More outbreak news. Test and vaccine makers can jump on headlines; most such moves fade.",
             "Check CDC travel notices before trips; keep fever reducers, tests and a few masks at home."),
      "high": ("A serious, spreading outbreak. Expect travel and leisure stocks to fall and health names to swing.",
               "Follow public-health guidance, have two weeks of meds and supplies, and limit crowds if advised.")}),
]
DOM = {d[0]: d for d in DOMAINS}


def band(level):
    return "calm" if level is None or level >= 4 else "up" if level == 3 else "high"


# ------------------------------------------------------------------ scoring

def points(value, calm, severe):
    """0 at `calm`, 100 at `severe`, straight line between, clamped. Works
    either way round (for signals where lower is worse, severe < calm)."""
    if value is None:
        return None
    if severe == calm:
        return 100.0 if value >= severe else 0.0
    p = (value - calm) / (severe - calm) * 100
    return round(max(0.0, min(100.0, p)), 1)


def sig(key, label, value, shown, calm, severe, note, src, url="", pts=None, marks=None):
    """One signal. `shown` is the value in words; `marks` describes calm and severe."""
    p = pts if pts is not None else points(value, calm, severe)
    return {"key": key, "label": label, "v": value, "shown": shown, "pts": p,
            "marks": marks or f"calm {calm:g}, severe {severe:g}", "note": note, "src": src, "url": url}


def score(signals):
    ps = [s["pts"] for s in signals if s.get("pts") is not None]
    if not ps:
        return None
    return round(.5 * max(ps) + .5 * sum(ps) / len(ps), 1)


def overall(domains):
    ss = [d["score"] for d in domains if d.get("score") is not None]
    if not ss:
        return None
    return round(.6 * max(ss) + .4 * sum(ss) / len(ss), 1)


# ------------------------------------------------------------------ fetch

class Fetch:
    """Small wrapper so every source is optional and tests can fake the web."""

    def __init__(self, get=None):
        self.status = {}
        if get is None:
            import requests
            s = requests.Session()
            s.headers["User-Agent"] = UA
            get = lambda url, headers=None, params=None: s.get(url, headers=headers, params=params, timeout=40)
        self._get = get

    def raw(self, name, url, headers=None, params=None):
        try:
            r = self._get(url, headers=headers, params=params)
            if r.status_code != 200:
                self.status[name] = f"HTTP {r.status_code}"
                return None
            self.status.setdefault(name, "ok")
            return r.content
        except Exception as e:  # any one source being down is fine
            self.status[name] = type(e).__name__
            return None

    def text(self, name, url, **kw):
        b = self.raw(name, url, **kw)
        return b.decode("utf-8", "replace") if isinstance(b, bytes) else b

    def json(self, name, url, **kw):
        t = self.text(name, url, **kw)
        if not t:
            return None
        try:
            return json.loads(t)
        except ValueError:
            self.status[name] = "bad JSON"
            return None

    def fred(self, fid, start):
        t = self.text(f"FRED {fid}", FRED_CSV, params={"id": fid, "cosd": start.isoformat()})
        out = []
        for row in list(csv.reader(io.StringIO(t or "")))[1:]:
            try:
                out.append((datetime.date.fromisoformat(row[0][:10]), float(row[1])))
            except (ValueError, IndexError):
                continue
        return out


def _date(s):
    """Many date styles -> aware UTC datetime, or None."""
    import email.utils
    if not s:
        return None
    s = str(s).strip()
    try:
        d = email.utils.parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = datetime.datetime.fromisoformat(s.replace("Z", "+00:00")[:25] if "T" in s else s[:10])
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=datetime.timezone.utc)
    return d.astimezone(datetime.timezone.utc)


def rss_items(xml):
    """[{t, u, d}] from an RSS or Atom feed."""
    out = []
    try:
        root = ET.fromstring(xml)
    except (ET.ParseError, TypeError, ValueError):
        return out
    clean = lambda s: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(s or ""))).strip()
    for it in root.iter("item"):
        t, u = clean(it.findtext("title")), (it.findtext("link") or "").strip()
        src = clean(it.findtext("source"))
        if src and t.endswith(" - " + src):
            t = t[: -len(src) - 3]
        d = _date(it.findtext("pubDate") or it.findtext("{http://purl.org/dc/elements/1.1/}date"))
        if t and u.startswith("http"):
            out.append({"t": t, "u": u, "d": d, "s": src})
    a = "{http://www.w3.org/2005/Atom}"
    for e in root.iter(a + "entry"):
        ln = e.find(a + "link")
        u = ln.get("href", "") if ln is not None else ""
        t = clean(e.findtext(a + "title"))
        if t and u.startswith("http"):
            out.append({"t": t, "u": u, "d": _date(e.findtext(a + "updated") or e.findtext(a + "published")), "s": ""})
    return out


def _iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ") if d else ""


def _since(items, now, days):
    cut = now - datetime.timedelta(days=days)
    return [i for i in items if i.get("d") and i["d"] >= cut]


def headlines(f, q, n=4, now=None):
    items = rss_items(f.raw("Google News", GNEWS.format(q=urllib.parse.quote(q))) or b"")
    items.sort(key=lambda i: i["d"] or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc), reverse=True)
    seen, out = set(), []
    for i in items:
        if not i["s"] and " - " in i["t"]:  # Google News: "Title - Publisher"
            i["t"], i["s"] = i["t"].rsplit(" - ", 1)
        k = re.sub(r"\W+", "", i["t"].lower())[:60]
        if k not in seen:
            seen.add(k)
            out.append({"t": i["t"], "u": i["u"], "s": i["s"], "d": _iso(i["d"])})
        if len(out) >= n:
            break
    return out


def _pct(a, b):
    return None if a is None or not b else (a / b - 1) * 100


def _last(s):
    return s[-1][1] if s else None


def _ago(s, days):
    if not s:
        return None
    cut = s[-1][0] - datetime.timedelta(days=days)
    prior = [v for d, v in s if d <= cut]
    return prior[-1] if prior else None


def _asof(s):
    return f" ({s[-1][0]:%b %d})" if s else ""


# ------------------------------------------------------------------ areas

def area_markets(f, now):
    start = now.date() - datetime.timedelta(days=400)
    out = []
    vix = f.fred("VIXCLS", start)
    if vix:
        out.append(sig("vix", "Fear gauge (VIX)", _last(vix), f"{_last(vix):.1f}{_asof(vix)}", 16, 40,
                       "Expected stock swings over the next month. Under 20 is calm; over 30 is fear.",
                       "FRED VIXCLS", "https://fred.stlouisfed.org/series/VIXCLS"))
    hy = f.fred("BAMLH0A0HYM2", start)
    if hy:
        out.append(sig("hy", "Junk-bond risk premium", _last(hy), f"{_last(hy):.2f} pts{_asof(hy)}", 3.5, 8,
                       "Extra interest risky companies pay. Lenders demand more when they smell defaults.",
                       "FRED BAMLH0A0HYM2", "https://fred.stlouisfed.org/series/BAMLH0A0HYM2"))
    st = f.fred("STLFSI4", start)
    if st:
        out.append(sig("stress", "Financial stress index", _last(st), f"{_last(st):+.2f}{_asof(st)}", -.5, 2.5,
                       "The St. Louis Fed's weekly blend of 18 market signals. Zero is normal.",
                       "FRED STLFSI4", "https://fred.stlouisfed.org/series/STLFSI4"))
    sp = f.fred("SP500", start)
    if sp:
        hi = max(v for d, v in sp if d >= sp[-1][0] - datetime.timedelta(days=365))
        dd = (_last(sp) / hi - 1) * 100
        out.append(sig("drawdown", "S&P 500 below its high", dd, f"{dd:.1f}%{_asof(sp)}", -5, -30,
                       "How far stocks are below their best level of the past year. 10% is a correction, 20% a bear market.",
                       "FRED SP500", "https://fred.stlouisfed.org/series/SP500"))
    cv = f.fred("T10Y3M", start)
    if cv:
        out.append(sig("curve", "Yield curve (10y minus 3m)", _last(cv), f"{_last(cv):+.2f} pts{_asof(cv)}", .5, -1,
                       "Below zero (inverted) has come before every recession since the 1960s, usually by a year or so.",
                       "FRED T10Y3M", "https://fred.stlouisfed.org/series/T10Y3M"))
    sahm = f.fred("SAHMREALTIME", start)
    if sahm:
        out.append(sig("sahm", "Recession alarm (Sahm rule)", _last(sahm), f"{_last(sahm):.2f}{_asof(sahm)}", .2, .7,
                       "Rise in unemployment from its low. At 0.5 the US has almost always been in a recession.",
                       "FRED SAHMREALTIME", "https://fred.stlouisfed.org/series/SAHMREALTIME"))
    return out, [], headlines(f, '"stock market" sell-off OR "market turmoil" OR "credit stress" OR recession when:2d')


def kev_signals(data, now):
    """CISA's Known Exploited Vulnerabilities: how many were added lately vs normal."""
    vulns = (data or {}).get("vulnerabilities") or []
    days = []
    for v in vulns:
        try:
            days.append((datetime.date.fromisoformat(v.get("dateAdded", "")[:10]), v))
        except ValueError:
            continue
    if not days:
        return [], []
    today = now.date()
    wk = [v for d, v in days if d > today - datetime.timedelta(days=7)]
    yr = [v for d, v in days if today - datetime.timedelta(days=372) < d <= today - datetime.timedelta(days=7)]
    usual = len(yr) / 52.1
    ratio = (len(wk) + 1) / (usual + 1)
    mo = [v for d, v in days if d > today - datetime.timedelta(days=30)]
    rw = [v for v in mo if str(v.get("knownRansomwareCampaignUse", "")).lower() == "known"]
    out = [sig("kev", "Software flaws hackers are using", ratio,
               f"{len(wk)} added this week (usual {usual:.0f})", 1, 4,
               "New entries on CISA's list of vulnerabilities being exploited in real attacks, against the "
               "weekly average of the past year. Scored on the ratio: 1x normal is calm, 4x is severe.",
               "CISA KEV", "https://www.cisa.gov/known-exploited-vulnerabilities-catalog",
               marks="calm 1x normal, severe 4x"),
           sig("ransom", "Flaws tied to ransomware", len(rw), f"{len(rw)} in 30 days", 1, 8,
               "Of the flaws added in the last 30 days, how many CISA says ransomware gangs use.",
               "CISA KEV", "https://www.cisa.gov/known-exploited-vulnerabilities-catalog")]
    items = [{"t": f"{v.get('vendorProject', '')} {v.get('product', '')}: {v.get('vulnerabilityName', '')}".strip(),
              "u": f"https://nvd.nist.gov/vuln/detail/{v.get('cveID', '')}", "s": v.get("cveID", ""),
              "d": d.isoformat(), "tag": "ransomware" if v in rw else ""}
             for d, v in sorted(days, key=lambda x: x[0], reverse=True)[:5]]
    return out, items


TICKER = re.compile(r"\(([A-Z][A-Z.\-]{0,6})(?:,\s*[A-Z.\-]+)*\)")


def sec_hits(data):
    """EDGAR full-text search hits -> [{name, tkr, date, url}] for 8-Ks with Item 1.05."""
    out, seen = [], set()
    for h in ((data or {}).get("hits") or {}).get("hits") or []:
        s = h.get("_source") or {}
        items = s.get("items") or []
        if items and "1.05" not in items:
            continue
        name = (s.get("display_names") or [""])[0]
        m = TICKER.search(name)
        clean = re.sub(r"\s*\(.*$", "", name).strip()
        adsh, _, fn = str(h.get("_id", "")).partition(":")
        cik = (s.get("ciks") or [""])[0].lstrip("0")
        url = (f"https://www.sec.gov/Archives/edgar/data/{cik}/{adsh.replace('-', '')}/{fn}"
               if cik and adsh and fn else "https://efts.sec.gov/LATEST/search-index")
        key = (clean, s.get("file_date"))
        if clean and key not in seen:
            seen.add(key)
            out.append({"name": clean, "tkr": m.group(1) if m else "", "date": s.get("file_date", ""), "url": url})
    return out


def _sec_total(data):
    t = ((data or {}).get("hits") or {}).get("total") or {}
    return t.get("value") if isinstance(t, dict) else t


def sec_signal(f, now):
    email_ = os.environ.get("SEC_CONTACT_EMAIL", "").strip()
    if "@" not in email_:
        f.status["SEC 8-K Item 1.05"] = "skipped (no SEC_CONTACT_EMAIL)"
        return [], []
    hdr = {"User-Agent": f"quantreyda/0.1 ({email_})"}
    today = now.date()
    q = '"Item 1.05" "cybersecurity incident"'

    def search(days_back, days_end=0):
        return f.json("SEC 8-K Item 1.05", EFTS, headers=hdr, params={
            "q": q, "forms": "8-K,8-K/A", "dateRange": "custom",
            "startdt": (today - datetime.timedelta(days=days_back)).isoformat(),
            "enddt": (today - datetime.timedelta(days=days_end)).isoformat()})
    recent = search(30)
    year = search(395, 30)
    if recent is None:
        return [], []
    hits = sec_hits(recent)
    n = len(hits) if hits else (_sec_total(recent) or 0)
    usual = (_sec_total(year) or 0) / 12 if year else None
    if usual:
        ratio = (n + 1) / (usual + 1)
        s = sig("sec105", "Companies reporting a material hack", ratio, f"{n} in 30 days (usual {usual:.1f}/month)" if usual < 3 else f"{n} in 30 days (usual {usual:.0f}/month)",
                1, 3, "SEC 8-K filings under Item 1.05, which public companies must file within four business days "
                "of deciding a cyber incident is material. Scored on the ratio to the past year's monthly average.",
                "SEC EDGAR", "https://www.sec.gov/edgar/search/#/q=%22Item%201.05%22&forms=8-K",
                marks="calm 1x normal, severe 3x")
    else:
        s = sig("sec105", "Companies reporting a material hack", n, f"{n} in 30 days", 3, 12,
                "SEC 8-K filings under Item 1.05 (a material cyber incident at a public company).",
                "SEC EDGAR", "https://www.sec.gov/edgar/search/#/q=%22Item%201.05%22&forms=8-K")
    return [s], hits[:12]


def area_cyber(f, now):
    out, items = kev_signals(f.json("CISA KEV", KEV), now)
    s2, hits = sec_signal(f, now)
    out += s2
    csa = _since(rss_items(f.raw("CISA advisories", CISA_CSA) or b""), now, 30)
    if csa or "CISA advisories" in f.status and f.status["CISA advisories"] == "ok":
        out.append(sig("csa", "Government cyber warnings", len(csa), f"{len(csa)} in 30 days", 2, 8,
                       "Joint advisories from CISA, the FBI and partners about active hacking campaigns, often by "
                       "nation-state groups.", "CISA", "https://www.cisa.gov/news-events/cybersecurity-advisories"))
    extra = {"hacked": hits}
    return out, items, headlines(f, 'ransomware OR cyberattack OR "data breach" OR hackers when:2d'), extra


def gdelt_ratio(data):
    """Last 7 days of conflict coverage vs the whole window's average."""
    try:
        pts = [p.get("value") for p in data["timeline"][0]["data"]]
    except (KeyError, IndexError, TypeError):
        return None
    pts = [float(p) for p in pts if p is not None]
    if len(pts) < 21:
        return None
    base = sum(pts[:-7]) / len(pts[:-7])
    return (sum(pts[-7:]) / 7) / base if base else None


def travel_signals(xml, now):
    items = rss_items(xml or b"")
    if not items:
        return None, []
    lvl = lambda t: int(m.group(1)) if (m := re.search(r"Level\s*(\d)", t)) else 0
    l4 = [i for i in items if lvl(i["t"]) == 4]
    raised = [i for i in _since(items, now, 30) if lvl(i["t"]) >= 3]
    s = sig("travel", "Travel warnings raised", len(raised),
            f"{len(raised)} countries at Level 3-4 updated in 30 days ({len(l4)} at Do Not Travel)", 1, 6,
            "State Department travel advisories set or reissued at Level 3 (reconsider travel) or Level 4 "
            "(do not travel) in the last month.", "US State Department",
            "https://travel.state.gov/content/travel/en/traveladvisories/traveladvisories.html")
    its = [{"t": i["t"], "u": i["u"], "s": "State Dept", "d": _iso(i["d"])}
           for i in sorted(raised, key=lambda i: i["d"], reverse=True)[:5]]
    return s, its


def area_geopolitics(f, now):
    out, items = [], []
    g = f.json("GDELT", GDELT, params={
        "query": '(war OR airstrike OR missile OR invasion OR "military escalation") sourcelang:english',
        "mode": "timelinevol", "format": "json", "timespan": "3months"})
    r = gdelt_ratio(g)
    if r is not None:
        out.append(sig("gdelt", "Conflict in world news", r, f"{r:.2f}x the 3-month average", 1, 2,
                       "Share of the world's online news about war and military escalation over the last week, "
                       "against the past three months (GDELT, which reads news in 100+ languages).",
                       "GDELT", "https://www.gdeltproject.org/", marks="calm 1x normal, severe 2x"))
    s, its = travel_signals(f.raw("State Dept advisories", TRAVEL), now)
    if s:
        out.append(s)
        items += its
    start = now.date() - datetime.timedelta(days=120)
    ovx = f.fred("OVXCLS", start)
    if ovx:
        out.append(sig("ovx", "Oil fear gauge (OVX)", _last(ovx), f"{_last(ovx):.0f}{_asof(ovx)}", 30, 70,
                       "Expected swings in oil prices. Supply scares from conflict show up here first.",
                       "FRED OVXCLS", "https://fred.stlouisfed.org/series/OVXCLS"))
    epu = f.fred("USEPUINDXD", start)
    if len(epu) >= 7:
        avg = sum(v for _, v in epu[-7:]) / 7
        out.append(sig("epu", "Policy uncertainty in the news", avg, f"{avg:.0f} (7-day average)", 150, 450,
                       "How often US newspapers mention economic policy uncertainty (tariffs, sanctions, budget "
                       "fights). Around 100 is a typical long-run level.",
                       "FRED USEPUINDXD", "https://fred.stlouisfed.org/series/USEPUINDXD"))
    return out, items, headlines(f, 'military OR missile OR troops OR sanctions OR ceasefire when:2d')


SCALE_PTS = {0: 0, 1: 15, 2: 40, 3: 70, 4: 90, 5: 100}


def _scale(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return 0


def swpc_signals(data):
    if not isinstance(data, dict) or "0" not in data:
        return []
    now_ = data.get("0") or {}
    g_now = _scale((now_.get("G") or {}).get("Scale"))
    g_fc = max([_scale(((data.get(k) or {}).get("G") or {}).get("Scale")) for k in ("1", "2", "3")] or [0])
    s_now = _scale((now_.get("S") or {}).get("Scale"))
    g = max(g_now, g_fc)
    words = {0: "quiet", 1: "minor", 2: "moderate", 3: "strong", 4: "severe", 5: "extreme"}
    return [sig("geomag", "Solar storm (geomagnetic)", g, f"G{g_now} now, up to G{g_fc} forecast in 3 days ({words[g]})",
                0, 5, "NOAA's G1-G5 scale. G4-G5 storms can trip power grids and disrupt GPS and satellites "
                "(the 1989 Quebec blackout was a G5).", "NOAA SWPC", "https://www.swpc.noaa.gov/noaa-scales-explanation",
                pts=SCALE_PTS[g], marks="G0 calm, G3 70 pts, G5 severe"),
            sig("radiation", "Solar radiation storm", s_now, f"S{s_now} now", 0, 5,
                "NOAA's S1-S5 scale. High levels affect satellites, polar flights and spacecraft.",
                "NOAA SWPC", "https://www.swpc.noaa.gov/noaa-scales-explanation",
                pts=SCALE_PTS[s_now], marks="S0 calm, S5 severe")]


GRID_EVENTS = {"Extreme Heat Warning", "Excessive Heat Warning", "Extreme Cold Warning", "Wind Chill Warning",
               "Ice Storm Warning", "Blizzard Warning", "Hard Freeze Warning"}


def _states(p):
    st = {u[:2] for u in ((p.get("geocode") or {}).get("UGC") or []) if len(u) >= 2}
    return st


def area_infrastructure(f, now, nws=None):
    out = swpc_signals(f.json("NOAA space weather", SWPC))
    ics = _since(rss_items(f.raw("CISA ICS advisories", CISA_ICS) or b""), now, 7)
    if ics or f.status.get("CISA ICS advisories") == "ok":
        out.append(sig("ics", "Industrial control system flaws", len(ics), f"{len(ics)} advisories in 7 days", 15, 40,
                       "CISA advisories about flaws in the equipment that runs power plants, water systems and "
                       "factories. A steady stream is normal; a surge means more to patch.",
                       "CISA ICS", "https://www.cisa.gov/news-events/cybersecurity-advisories"))
    items = []
    if nws is not None:
        grid = [x.get("properties") or {} for x in nws if (x.get("properties") or {}).get("event") in GRID_EVENTS]
        states = set().union(*[_states(p) for p in grid]) if grid else set()
        out.append(sig("strain", "Heat or cold straining the grid", len(states),
                       f"{len(states)} state{'s' * (len(states) != 1)} under extreme heat, cold or ice warnings",
                       1, 12, "Extreme temperatures push power demand to peaks and ice brings down lines.",
                       "National Weather Service", "https://www.weather.gov/"))
        evs = {}
        for p in grid:
            evs.setdefault(p.get("event"), set()).update(_states(p))
        items = [{"t": f"{ev}: {', '.join(sorted(s)[:8])}{'…' if len(s) > 8 else ''}", "u": "https://alerts.weather.gov/",
                  "s": "NWS", "d": ""} for ev, s in sorted(evs.items(), key=lambda kv: -len(kv[1]))[:4]]
    return out, items, headlines(f, '"power outage" OR blackout OR "power grid" OR "water system" when:2d')


PAGER = {"green": 10, "yellow": 40, "orange": 75, "red": 100}


def quake_signals(data, now):
    feats = (data or {}).get("features") or []
    if not isinstance(data, dict) or "features" not in data:
        return None, []
    wk = []
    items = []
    for x in feats:
        p = x.get("properties") or {}
        t = p.get("time")
        d = datetime.datetime.fromtimestamp(t / 1000, datetime.timezone.utc) if t else None
        items.append({"t": p.get("title") or f"M{p.get('mag')} {p.get('place', '')}", "u": p.get("url") or "",
                      "s": f"PAGER {p['alert']}" if p.get("alert") else "USGS", "d": _iso(d)})
        if d and d >= now - datetime.timedelta(days=7):
            wk.append(p)
    worst = max(wk, key=lambda p: PAGER.get(p.get("alert"), 0), default=None)
    al = (worst or {}).get("alert")
    pts = PAGER.get(al, 0) if wk else 0
    shown = (f"PAGER {al} (M{worst.get('mag')}, {worst.get('place', '')})" if al
             else f"{len(wk)} significant quake{'s' * (len(wk) != 1)} this week, no damage alert")
    s = sig("quake", "Damaging earthquakes", pts, shown, 0, 100,
            "USGS PAGER alerts estimate deaths and damage within minutes of a quake: green (little), yellow, "
            "orange, red (major disaster). Worst of the last 7 days.", "USGS",
            "https://earthquake.usgs.gov/earthquakes/map/", pts=pts, marks="none 0, yellow 40, orange 75, red 100")
    return s, items[:4]


def storm_signal(data):
    if not isinstance(data, dict) or "activeStorms" not in data:
        return None, []
    best, items = (0, None, ""), []
    for s in data.get("activeStorms") or []:
        kt = _scale(s.get("intensity"))
        c = (s.get("classification") or "").upper()
        if c == "HU":
            cat = 5 if kt >= 137 else 4 if kt >= 113 else 3 if kt >= 96 else 2 if kt >= 83 else 1
            pts, what = (90 if cat >= 3 else 60), f"Category {cat} hurricane"
        elif c in ("TS", "STS"):
            pts, what = 30, "Tropical storm"
        else:
            pts, what = 10, "Tropical depression"
        items.append({"t": f"{what} {s.get('name', '')} ({kt} kt winds)", "u": "https://www.nhc.noaa.gov/",
                      "s": "NHC", "d": s.get("lastUpdate", "")})
        if pts > best[0]:
            best = (pts, s, what)
    pts, s, what = best
    shown = f"{what} {s.get('name', '')}" if s else "No active Atlantic or Pacific storms"
    return sig("storm", "Hurricanes", pts, shown, 0, 100,
               "The strongest active storm the National Hurricane Center is tracking (Atlantic and eastern Pacific). "
               "Category 3+ is a major hurricane.", "NOAA NHC", "https://www.nhc.noaa.gov/",
               pts=pts, marks="none 0, storm 30, hurricane 60, major 90"), items[:3]


def area_hazards(f, now, nws=None):
    out, items = [], []
    if nws is not None:
        props = [x.get("properties") or {} for x in nws]
        ext = [p for p in props if p.get("severity") == "Extreme"]
        sev = set().union(*[_states(p) for p in props if p.get("severity") in ("Severe", "Extreme")] or [set()])
        out.append(sig("extreme", "Extreme weather warnings", len(ext), f"{len(ext)} active", 0, 15,
                       "National Weather Service alerts rated Extreme: an extraordinary threat to life or "
                       "property (tornado emergencies, major hurricanes, extreme wind).",
                       "National Weather Service", "https://www.weather.gov/"))
        out.append(sig("severe", "States under severe-weather warnings", len(sev), f"{len(sev)} states", 6, 30,
                       "States with any alert rated Severe or Extreme. A handful is a normal day somewhere in "
                       "the country.", "National Weather Service", "https://alerts.weather.gov/"))
        evs = {}
        for p in ext or [p for p in props if p.get("severity") == "Severe"]:
            evs.setdefault(p.get("event"), set()).update(_states(p))
        items += [{"t": f"{ev}: {', '.join(sorted(s)[:8])}{'…' if len(s) > 8 else ''}",
                   "u": "https://alerts.weather.gov/", "s": "NWS", "d": ""}
                  for ev, s in sorted(evs.items(), key=lambda kv: -len(kv[1]))[:3]]
    q, qi = quake_signals(f.json("USGS earthquakes", USGS), now)
    if q:
        out.append(q)
        items += qi[:2]
    st, si = storm_signal(f.json("NHC storms", NHC))
    if st:
        out.append(st)
        items += si
    since = (now - datetime.timedelta(days=30)).strftime("%Y-%m-%dT00:00:00.000Z")
    fema = f.json("FEMA declarations", FEMA, params={
        "$filter": f"declarationDate ge '{since}' and declarationType eq 'DR'",
        "$select": "disasterNumber,state,declarationDate,incidentType,declarationTitle", "$top": "1000"})
    if isinstance(fema, dict) and "DisasterDeclarationsSummaries" in fema:
        dis = {}
        for r in fema["DisasterDeclarationsSummaries"]:
            dis.setdefault(r.get("disasterNumber"), r)
        out.append(sig("fema", "New federal disasters", len(dis), f"{len(dis)} major disaster declarations in 30 days",
                       2, 10, "Presidential major-disaster declarations, which unlock federal aid. A few a month is normal.",
                       "OpenFEMA", "https://www.fema.gov/disaster/declarations"))
        items += [{"t": f"{r.get('state')}: {str(r.get('declarationTitle', '')).title()} ({r.get('incidentType')})",
                   "u": f"https://www.fema.gov/disaster/{r.get('disasterNumber')}", "s": "FEMA",
                   "d": str(r.get("declarationDate", ""))[:10]}
                  for r in sorted(dis.values(), key=lambda r: str(r.get("declarationDate")), reverse=True)[:3]]
    return out, items, headlines(f, 'hurricane OR earthquake OR wildfire OR tornado OR flooding when:2d')


def xlsx_rows(data):
    """First worksheet of an .xlsx as lists of cell values (numbers or strings). No extra libraries."""
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    z = zipfile.ZipFile(io.BytesIO(data))
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(ns + "si"):
            shared.append("".join(t.text or "" for t in si.iter(ns + "t")))
    sheets = sorted(n for n in z.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
    rows = []
    for row in ET.fromstring(z.read(sheets[0])).iter(ns + "row"):
        vals = []
        for c in row.iter(ns + "c"):
            v = c.findtext(ns + "v")
            if v is None:
                v = "".join(t.text or "" for t in c.iter(ns + "t")) or None
            elif c.get("t") == "s":
                v = shared[int(v)]
            elif c.get("t") not in ("str", "inlineStr", "b"):
                try:
                    v = float(v)
                except ValueError:
                    pass
            vals.append(v)
        rows.append(vals)
    return rows


def gscpi_latest(data):
    """(value, 'Mon YYYY') from the NY Fed's GSCPI workbook, or None."""
    try:
        rows = xlsx_rows(data)
    except Exception:
        return None
    best = None
    for r in rows:
        if len(r) >= 2 and isinstance(r[1], float):
            d = r[0]
            if isinstance(d, float) and 20000 < d < 80000:  # Excel serial date
                d = datetime.date(1899, 12, 30) + datetime.timedelta(days=int(d))
            elif isinstance(d, str):
                try:
                    d = datetime.datetime.strptime(d.strip()[:11], "%d-%b-%Y").date()
                except ValueError:
                    try:
                        d = datetime.date.fromisoformat(d.strip()[:10])
                    except ValueError:
                        continue
            else:
                continue
            best = (r[1], f"{d:%b %Y}")
    return best


def area_supply(f, now):
    out = []
    raw = f.raw("NY Fed GSCPI", GSCPI)
    g = gscpi_latest(raw) if raw else None
    if g:
        out.append(sig("gscpi", "Global supply-chain pressure", g[0], f"{g[0]:+.2f} ({g[1]})", .3, 2.5,
                       "The New York Fed's index of shipping costs, delivery times and backlogs worldwide, in "
                       "standard deviations from normal. It peaked above 4 in late 2021.", "NY Fed GSCPI",
                       "https://www.newyorkfed.org/research/policy/gscpi"))
    start = now.date() - datetime.timedelta(days=120)
    for fid, key, label, calm, severe, why in (
            ("DCOILWTICO", "oil", "Oil price jump (1 month)", 5, 40, "Crude oil feeds into gasoline, diesel, "
             "jet fuel and plastics. Big spikes have come before several recessions."),
            ("DHHNGSP", "gas", "Natural gas jump (1 month)", 15, 80, "Heats homes and makes about 40% of US electricity."),
            ("GASREGW", "pump", "Gas at the pump (1 month)", 3, 20, "Average US price of regular gasoline, weekly."),
            ("GASDESW", "diesel", "Diesel (1 month)", 3, 20, "Trucks, trains and farms run on diesel, so it "
             "shows up in the price of almost everything.")):
        s = f.fred(fid, start)
        c = _pct(_last(s), _ago(s, 30))
        if c is not None:
            out.append(sig(key, label, c, f"{c:+.1f}% (now {_last(s):,.2f}{_asof(s)})", calm, severe, why,
                           f"FRED {fid}", f"https://fred.stlouisfed.org/series/{fid}",
                           marks=f"calm +{calm:g}%, severe +{severe:g}%"))
    return out, [], headlines(f, '"supply chain" OR shortage OR "port strike" OR "shipping disruption" when:3d')


def cdc_signals(xml, now):
    items = rss_items(xml or b"")
    if not items:
        return [], []
    lvl = lambda t: int(m.group(1)) if (m := re.search(r"Level\s*(\d)", t)) else 0
    hi = [i for i in items if lvl(i["t"]) >= 3]
    l2 = [i for i in items if lvl(i["t"]) == 2]
    out = [sig("cdc3", "CDC travel notices at Level 3-4", len(hi), f"{len(hi)} active", 0, 4,
               "Level 3 means avoid nonessential travel; Level 4 is very high risk. Usually zero or one.",
               "CDC", "https://wwwnc.cdc.gov/travel/notices"),
           sig("cdc2", "CDC travel notices at Level 2", len(l2), f"{len(l2)} active", 4, 14,
               "Level 2 means take enhanced precautions (an outbreak where travelers should be careful).",
               "CDC", "https://wwwnc.cdc.gov/travel/notices")]
    its = [{"t": i["t"], "u": i["u"], "s": "CDC", "d": _iso(i["d"])}
           for i in sorted(hi + l2, key=lambda i: (-lvl(i["t"]), -(i["d"].timestamp() if i["d"] else 0)))[:4]]
    return out, its


def who_items(data):
    rows = (data or {}).get("value") if isinstance(data, dict) else data
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        t = r.get("Title") or r.get("title") or ""
        d = _date(r.get("PublicationDateAndTime") or r.get("PublicationDate") or r.get("DateCreated") or "")
        u = r.get("ItemDefaultUrl") or r.get("UrlName") or ""
        if u and not u.startswith("http"):
            u = "https://www.who.int/emergencies/disease-outbreak-news/item/" + u.strip("/").split("/")[-1]
        if t and d:
            out.append({"t": t, "u": u or "https://www.who.int/emergencies/disease-outbreak-news", "d": d, "s": "WHO"})
    return out


def area_health(f, now):
    out, items = cdc_signals(f.raw("CDC travel notices", CDC_TRAVEL), now)
    who = f.json("WHO outbreak news", WHO_DON, params={"$orderby": "PublicationDateAndTime desc", "$top": "40"})
    don = who_items(who)
    if don:
        recent = _since(don, now, 30)
        out.append(sig("who", "WHO outbreak reports", len(recent), f"{len(recent)} in 30 days", 6, 15,
                       "Disease Outbreak News: the WHO's notices about unusual outbreaks anywhere in the world.",
                       "WHO", "https://www.who.int/emergencies/disease-outbreak-news"))
        items += [{"t": i["t"], "u": i["u"], "s": "WHO", "d": _iso(i["d"])} for i in sorted(
            recent, key=lambda i: i["d"], reverse=True)[:3]]
    return out, items, headlines(f, 'outbreak OR "bird flu" OR pandemic OR measles OR "public health emergency" when:3d')


# ------------------------------------------------------------------ collect

def collect(get=None, now=None):
    """Check every source and score every area. Returns the snapshot dict."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    f = Fetch(get)
    nws_data = f.json("NWS alerts", NWS, headers={"User-Agent": UA, "Accept": "application/geo+json"})
    nws = (nws_data or {}).get("features") if isinstance(nws_data, dict) else None
    runs = {"markets": lambda: area_markets(f, now), "cyber": lambda: area_cyber(f, now),
            "geopolitics": lambda: area_geopolitics(f, now),
            "infrastructure": lambda: area_infrastructure(f, now, nws),
            "hazards": lambda: area_hazards(f, now, nws), "supply": lambda: area_supply(f, now),
            "health": lambda: area_health(f, now)}
    doms = []
    for key, *_ in DOMAINS:
        try:
            got = runs[key]()
        except Exception as e:  # one broken parser shouldn't lose the rest
            print(f"readiness {key} failed ({type(e).__name__}: {e})")
            got = ([], [], [])
        signals, items, news = got[:3]
        extra = got[3] if len(got) > 3 else {}
        sc = score(signals)
        doms.append({"key": key, "score": sc, "level": level_for(sc), "signals": signals,
                     "items": items, "news": news, **extra})
    o = overall(doms)
    return {"generated": _iso(now), "overall": {"score": o, "level": level_for(o)},
            "domains": doms, "sources": f.status}


def history_point(snap):
    return {"d": snap["generated"][:10], "o": snap["overall"]["level"],
            "l": {d["key"]: d["level"] for d in snap["domains"]}}


def merge_history(snap, *olds):
    """Add this snapshot's day to the history from older snapshots (one point a day, latest wins)."""
    days = {}
    for o in olds:
        for p in (o or {}).get("history") or []:
            days[p["d"]] = p
    if snap.get("overall", {}).get("level") is not None:
        days[snap["generated"][:10]] = history_point(snap)
    keep = sorted(days)[-HISTORY_DAYS:]
    snap["history"] = [days[d] for d in keep]
    return snap


# ------------------------------------------------------------------ store (daily)

def ensure_table(con):
    con.execute("CREATE TABLE IF NOT EXISTS readiness (d DATE PRIMARY KEY, j VARCHAR)")


def related_tickers():
    return sorted({t for d in DOMAINS for t, _ in d[3]})


def refresh(con, get=None, now=None):
    """Daily: check every source, store today's snapshot, fetch prices for the related stocks."""
    ensure_table(con)
    snap = collect(get, now)
    old = load(con)
    merge_history(snap, old)
    con.execute("INSERT OR REPLACE INTO readiness VALUES (?, ?)", [snap["generated"][:10], json.dumps(snap)])
    ok = sum(1 for d in snap["domains"] if d["score"] is not None)
    print(f"readiness: overall level {snap['overall']['level']}, {ok}/{len(DOMAINS)} areas scored; "
          f"sources: {', '.join(f'{k} {v}' for k, v in snap['sources'].items() if v != 'ok') or 'all ok'}")
    if get is None:
        import prices
        try:
            prices.fetch(con, related_tickers(), start=datetime.date.today() - datetime.timedelta(days=400), minutes=3)
        except Exception as e:
            print(f"readiness prices skipped ({type(e).__name__}: {e})")
    return snap


def load(con):
    try:
        r = con.execute("SELECT j FROM readiness ORDER BY d DESC LIMIT 1").fetchone()
    except Exception:
        return None
    return json.loads(r[0]) if r else None


def context(con, built=()):
    """What only the daily build knows: which stocks have pages, and each related stock's month."""
    ret = {}
    for t in related_tickers() + sorted({h["tkr"] for h in _hacked(load(con)) if h.get("tkr")}):
        try:
            rows = con.execute("SELECT d, close FROM prices WHERE tkr = ? AND d >= current_date - 45 ORDER BY d",
                               [t]).fetchall()
        except Exception:
            rows = []
        if len(rows) >= 2:
            last_d, last = rows[-1]
            prior = [c for d, c in rows if (last_d - d).days >= 30]
            if prior and prior[-1]:
                ret[t] = round(last / prior[-1] - 1, 4)
    return {"pages": sorted(built), "ret": ret}


def _hacked(snap):
    for d in (snap or {}).get("domains") or []:
        if d["key"] == "cyber":
            return d.get("hacked") or []
    return []


# ------------------------------------------------------------------ render

def badge(level, big=False):
    if level is None:
        return "<span class='rd-b na'>–</span>"
    name, color, _ = LEVELS[level]
    return (f"<span class='rd-b{' big' if big else ''}' style='--c:{color}'><b>{level}</b>"
            f"<span>{E(name)}</span></span>")


def _ago_words(iso, now):
    d = _date(iso)
    if not d:
        return ""
    m = max(0, (now - d).total_seconds() / 60)
    return f"{m:.0f} min ago" if m < 60 else f"{m / 60:.0f} h ago" if m < 2880 else f"{m / 1440:.0f} days ago"


def _href(tkr, pages):
    return E(links.stock(tkr, pages))


def _ret(r):
    if r is None:
        return ""
    return f" <em class='{'up' if r >= 0 else 'down'}'>{r * 100:+.1f}%</em>"


def _meter(p):
    if p is None:
        return "<div class='rd-m'><i style='width:0'></i></div>"
    lv = level_for(p)
    return f"<div class='rd-m' title='{p:.0f} of 100'><i style='width:{max(p, 2):.0f}%;--c:{LEVELS[lv][1]}'></i></div>"


def _list(items, now):
    if not items:
        return ""
    return "<ul class='rd-l'>" + "".join(
        f"<li><a href='{E(i.get('u') or '#')}' target='_blank' rel='noopener'>{E(i.get('t', ''))}</a>"
        f"<small>{E(i.get('s') or '')}{' · ' + E(i['tag']) if i.get('tag') else ''}"
        f"{' · ' + _ago_words(i['d'], now) if i.get('d') and _date(i['d']) else ''}</small></li>"
        for i in items) + "</ul>"


def _history(hist, key=None):
    pts = (hist or [])[-30:]
    if len(pts) < 2:
        return ""
    cells = "".join(
        f"<i title='{E(p['d'])}: level {v}' style='--c:{LEVELS[v][1]}'></i>" if (v := (p["o"] if key is None else p["l"].get(key)))
        else f"<i class='na' title='{E(p['d'])}'></i>" for p in pts)
    return f"<div class='rd-h' aria-label='Level by day, oldest first'>{cells}</div>"


def domain_card(d, ctx, now, hist):
    key, name, about, related, notes = DOM[d["key"]]
    lv = d.get("level")
    inv, prep = notes[band(lv)]
    pages, ret = set(ctx.get("pages") or ()), ctx.get("ret") or {}
    sig_rows = "".join(
        f"<div class='rd-s'><div class='rd-st'><b>{E(s['label'])}</b><span>{E(s['shown'])}</span></div>"
        f"{_meter(s.get('pts'))}<p class='hint'>{E(s['note'])} <span class='rd-mk'>Scale: {E(s['marks'])}. "
        f"<a href='{E(s['url'])}' target='_blank' rel='noopener'>{E(s['src'])}</a></span></p></div>"
        for s in d.get("signals") or [])
    worst = max(d.get("signals") or [], key=lambda s: s.get("pts") or 0, default=None)
    why = (f"Driven most by <b>{E(worst['label'])}</b>: {E(worst['shown'])}." if worst and (worst.get("pts") or 0) >= 20
           else "Every signal is within its normal range." if d.get("signals") else
           "No source answered this time; the level will fill in on the next check.")
    chips = "".join(f"<a class='chip' href='{_href(t, pages)}' title='{E(w)}'>{E(t)}{_ret(ret.get(t))}</a>"
                    for t, w in related)
    hacked = ""
    if d.get("hacked"):
        rows = "".join(
            f"<li><a href='{E(h['url'])}' target='_blank' rel='noopener'>{E(h['name'])}</a>"
            + (f" <a class='chip' href='{_href(h['tkr'], pages)}'>{E(h['tkr'])}{_ret(ret.get(h['tkr']))}</a>" if h.get("tkr") else "")
            + f"<small>{E(h.get('date', ''))}</small></li>" for h in d["hacked"][:8])
        hacked = (f"<h4>Companies that reported a material cyber incident (30 days)</h4><ul class='rd-l'>{rows}</ul>"
                  "<p class='hint'>From their SEC 8-K filings (Item 1.05). The chip shows the stock's move over the "
                  "past month.</p>")
    events = _list(d.get("items"), now)
    news = _list(d.get("news"), now)
    return (f"<details class='rd-d' id='rd-{key}'{' open' if lv is not None and lv <= 3 else ''}>"
            f"<summary><div class='rd-dh'>{badge(lv)}<div><h3>{E(name)}</h3><p class='hint'>{why}</p></div>"
            f"<span class='rd-sc'>{'' if d.get('score') is None else f'{d['score']:.0f}/100'}</span></div></summary>"
            f"<p class='lead'>{E(about)}</p>{_history(hist, key)}"
            f"<div class='rd-sig'>{sig_rows}</div>"
            + (f"<h4>Latest events</h4>{events}" if events else "") + hacked
            + f"<div class='rd-notes'><div><h4>For investors</h4><p>{E(inv)}</p></div>"
              f"<div><h4>Be prepared</h4><p>{E(prep)}</p></div></div>"
              f"<h4>Stocks and funds tied to it</h4><div class='chips'>{chips}</div>"
              "<p class='hint'>Number is the move over the past month. These tend to react to this area; it's a "
              "starting point for research, not a buy list.</p>"
            + (f"<h4>In the news</h4>{news}" if news else "") + "</details>")


def render_live(snap, ctx=None, now=None):
    """The part of the page that the hourly job can replace."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    ctx = ctx or {}
    o = snap["overall"]
    lv = o.get("level")
    doms = snap["domains"]
    hist = snap.get("history") or []
    if lv is None:
        return ("<div class='hero'><div class='eyebrow'>Readiness levels</div><h1>Readiness levels</h1>"
                "<p>The first readings arrive with the next update.</p></div>")
    name, color, means = LEVELS[lv]
    top = sorted((d for d in doms if d.get("score") is not None), key=lambda d: -d["score"])
    up = [d for d in top if d["level"] <= 3]
    if up:
        lead = ("Highest right now: " + ", ".join(f"{DOM[d['key']][1]} (level {d['level']})" for d in up[:3]) + ".")
    else:
        lead = "Every area is at Normal or Watch."
    grid = "".join(
        f"<a class='rd-t' href='#rd-{d['key']}' style='--c:{LEVELS[d['level']][1] if d['level'] else 'var(--line)'}'>"
        f"<span>{E(DOM[d['key']][1])}</span>{badge(d['level'])}</a>" for d in doms)
    return (f"<div class='hero rd-hero' style='--c:{color}'><div class='eyebrow'>Readiness level · updated "
            f"{E(_ago_words(snap['generated'], now))}</div>"
            f"<div class='rd-top'>{badge(lv, big=True)}<div><h1>{E(name)}</h1><p>{E(means)} {E(lead)}</p></div></div>"
            f"{_history(hist)}<div class='rd-ts'>{grid}</div></div>"
            + "".join(domain_card(d, ctx, now, hist) for d in doms))


HOW = """<section class='panel' id='rd-how'><h2>How the levels work</h2>
<p>Numbered like DEFCON: <b>5 is calm, 1 is the most serious</b>. This is our own yardstick built from public data,
not an official government level (the real DEFCON is set by the military and isn't public).</p>
<div class='rd-lv'>{levels}</div>
<p>Each signal gets 0 to 100 points on a straight line between a <b>calm</b> mark and a <b>severe</b> mark, both
shown under it. An area's score is half its worst signal plus half the average of all its signals, so one
alarming number raises it but the top levels need several. The overall score is 60% the worst area plus 40% the
average. Scores under 20 are level 5, under 40 level 4, under 60 level 3, under 80 level 2, and 80+ level 1.</p>
<p>The marks are rules of thumb set from history (for example, the fear gauge has spent most of the last 30 years
under 20 and rarely above 40). Signals that count news coverage are compared with their own recent average, so a
busy news week doesn't look like a crisis by itself, and headlines are shown for context but never scored.</p>
<p class='hint'>Rechecked every hour; the stock moves and links refresh once a day. Sources: FRED (St. Louis Fed),
CISA, SEC EDGAR, GDELT, US State Department, NOAA (Space Weather Prediction Center, National Hurricane Center,
National Weather Service), USGS, FEMA, the New York Fed, CDC and WHO. Headlines from Google News. Research and
general preparedness information, not investment, safety or medical advice: in an emergency, follow local
officials.</p></section>"""


def remote_url():
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    return f"https://raw.githubusercontent.com/{repo}/news-feed/readiness.json" if repo else ""


JS = r"""<script>(function(){var box=document.getElementById('rd-live');if(!box)return;
var have=box.dataset.gen||'';function get(u){if(!u)return Promise.resolve(null);
return fetch(u+'?v='+Math.floor(Date.now()/300000),{cache:'no-store'}).then(function(r){return r.ok?r.json():null})
.catch(function(){return null})}
function load(){Promise.all([get(box.dataset.remote),get('readiness.json')]).then(function(r){
r.forEach(function(j){if(j&&j.html&&j.generated>have){have=j.generated;box.innerHTML=j.html}})})}
load();setInterval(load,20*60000)})()</script>"""


def page(con, built=()):
    snap = load(con)
    if not snap:
        return ("<div class='hero'><div class='eyebrow'>Readiness levels</div><h1>Readiness levels</h1>"
                "<p>A DEFCON-style read on markets, cyber attacks, geopolitics, the power grid, weather, supply "
                "chains and health. The first readings arrive with the next daily update.</p></div>"
                + HOW.format(levels=_levels_html()))
    ctx = context(con, built)
    live = render_live(snap, ctx)
    return (f"<div id='rd-live' data-gen='{E(snap['generated'])}' data-remote='{E(remote_url())}'>{live}</div>"
            + HOW.format(levels=_levels_html()) + JS)


def _levels_html():
    return "".join(f"<div>{badge(k)}<span>{E(v[2])}</span></div>" for k, v in LEVELS.items())


def site_json(con, built=()):
    """site/readiness.json: the daily snapshot plus the context the hourly job reuses."""
    snap = load(con)
    if not snap:
        return None
    ctx = context(con, built)
    return {**snap, "ctx": ctx, "html": render_live(snap, ctx)}


def _why(d):
    sigs = d.get("signals") or []
    worst = max(sigs, key=lambda s: s.get("pts") or 0, default=None)
    if not sigs:
        return "No source answered on the last check."
    if (worst.get("pts") or 0) < 20:
        return "Every signal is within its normal range."
    return f"{worst['label']}: {worst['shown']}."


def levels(con, today=None):
    """The levels in plain data, for the home page (and anything else):
    {"overall": {level, label, why}, "subs": [{key, name, level, label, why}], "href", "asof"}.
    Level 1 is the most serious, 5 is calm. None when nothing has been checked yet."""
    snap = load(con)
    if not snap or snap["overall"].get("level") is None:
        return None
    lab = lambda lv: LEVELS[lv][0].upper() if lv else "NO DATA"
    subs = [{"key": d["key"], "name": DOM[d["key"]][1], "level": d.get("level"), "label": lab(d.get("level")),
             "why": _why(d), "href": f"readiness.html#rd-{d['key']}"} for d in snap["domains"]]
    lv = snap["overall"]["level"]
    up = [s for s in sorted(subs, key=lambda s: s["level"] or 9) if s["level"] and s["level"] <= 3]
    why = (LEVELS[lv][2] + (" Highest: " + ", ".join(f"{s['name']} ({s['level']})" for s in up[:3]) + "." if up else ""))
    return {"overall": {"level": lv, "label": lab(lv), "why": why}, "subs": subs,
            "href": "readiness.html", "asof": snap["generated"][:10], "generated": snap["generated"]}


def home_teaser(con, today=None):
    snap = load(con)
    if not snap or snap["overall"].get("level") is None:
        return ""
    lv = snap["overall"]["level"]
    up = sorted((d for d in snap["domains"] if d.get("level") and d["level"] <= 4), key=lambda d: d["level"])
    chips = "".join(f"<a class='chip' href='readiness.html#rd-{d['key']}'>{E(DOM[d['key']][1])} "
                    f"<b style='color:{LEVELS[d['level']][1]}'>{d['level']}</b></a>" for d in up[:4])
    return (f"<p class='rd-tz'>{badge(lv)} Overall: <b>{E(LEVELS[lv][0])}</b></p>"
            + (f"<div class='chips'>{chips}</div>" if chips else "<p class='hint'>Every area is calm.</p>"))


CSS = """
.rd-b{display:inline-flex;align-items:center;gap:6px;border-radius:999px;padding:2px 10px 2px 3px;font-size:12.5px;
font-weight:700;white-space:nowrap;background:color-mix(in srgb,var(--c) 16%,transparent);color:var(--fg)}
.rd-b b{display:inline-grid;place-items:center;width:22px;height:22px;border-radius:50%;background:var(--c);color:#fff;font-size:13px}
.rd-b.na{padding:2px 10px;background:var(--chip);color:var(--mut)}
.rd-b.big{padding:6px 18px 6px 6px;font-size:16px;gap:10px}.rd-b.big b{width:58px;height:58px;font-size:32px}
.rd-hero{border-color:color-mix(in srgb,var(--c) 45%,var(--line))}
.rd-top{display:flex;gap:16px;align-items:center;margin-top:6px}.rd-top h1{margin:0}.rd-top .rd-b.big{padding:0;background:none}.rd-top .rd-b.big span{display:none}
.rd-top .rd-b.big b{box-shadow:0 0 0 6px color-mix(in srgb,var(--c) 22%,transparent)}
.rd-ts{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;margin-top:16px}
.rd-t{display:flex;flex-direction:column;align-items:flex-start;gap:8px;padding:10px 12px;border-radius:14px;
background:color-mix(in srgb,var(--bg) 55%,transparent);border:1px solid var(--line);border-left:4px solid var(--c);
color:var(--fg);text-decoration:none;font-weight:600;font-size:14px}
.rd-h{display:flex;gap:3px;margin:12px 0 4px}.rd-h i{flex:1;max-width:16px;height:14px;border-radius:3px;background:var(--c)}
.rd-h i.na{background:var(--chip)}
.rd-d{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:4px 18px;margin:12px 0;box-shadow:var(--shadow)}
.rd-d summary{list-style:none;cursor:pointer;padding:12px 0}.rd-d summary:before{display:none}
.rd-sc:after{content:' ▾';color:var(--acc)}.rd-d[open] .rd-sc:after{content:' ▴'}.rd-d summary::-webkit-details-marker{display:none}
.rd-dh{display:flex;gap:12px;align-items:center}.rd-dh h3{margin:0;font-size:18px}.rd-dh .hint{margin:2px 0 0}
.rd-dh>div{flex:1}.rd-sc{color:var(--mut);font-size:12px;white-space:nowrap}
.rd-d[open]{padding-bottom:16px}.rd-d h4{margin:18px 0 8px;font-size:14px}
.rd-sig{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:10px;margin-top:10px}
.rd-s{border:1px solid var(--line);border-radius:14px;padding:10px 12px;background:var(--card2)}
.rd-st{display:flex;justify-content:space-between;gap:10px;align-items:baseline;flex-wrap:wrap}.rd-st span{font-size:13px}
.rd-s .hint{margin:6px 0 0;font-size:12.5px}.rd-mk{display:block;margin-top:3px}
.rd-m{height:6px;border-radius:999px;background:var(--chip);margin-top:8px;overflow:hidden}
.rd-m i{display:block;height:100%;border-radius:999px;background:var(--c)}
.rd-l{list-style:none;margin:0;padding:0}.rd-l li{padding:7px 0;border-bottom:1px solid var(--line);font-size:14px}
.rd-l li:last-child{border-bottom:0}.rd-l a{color:var(--fg);text-decoration:none;font-weight:600}
.rd-l a:hover{text-decoration:underline}.rd-l small{display:block;color:var(--mut);font-size:12px}
.rd-l .chip{margin-left:6px;font-size:11.5px}
.rd-notes{display:grid;grid-template-columns:1fr 1fr;gap:12px}.rd-notes>div{background:var(--chip);border-radius:14px;padding:4px 14px}
.rd-notes p{margin:0 0 10px;font-size:14px}
.chip em{font-style:normal;font-weight:700}.chip em.up{color:var(--up)}.chip em.down{color:var(--down)}
.rd-lv{display:grid;gap:6px;margin:10px 0}.rd-lv div{display:flex;gap:10px;align-items:center;font-size:14px}
.rd-lv .rd-b{min-width:112px}.rd-tz{display:flex;gap:8px;align-items:center;margin:0 0 6px}
@media(max-width:640px){.rd-notes{grid-template-columns:1fr}.rd-ts{grid-template-columns:1fr 1fr}
.rd-t{font-size:13px}.rd-b.big b{width:46px;height:46px;font-size:26px}}
"""


# ------------------------------------------------------------------ hourly

def main(out_path, *old_paths, get=None, now=None):
    olds = []
    for p in old_paths:
        try:
            with open(p, encoding="utf-8") as fh:
                olds.append(json.load(fh))
        except (OSError, ValueError):
            pass
    snap = merge_history(collect(get, now), *olds)
    with_ctx = [o for o in olds if o.get("ctx")]
    ctx = max(with_ctx, key=lambda o: o.get("generated", ""))["ctx"] if with_ctx else {}
    snap["ctx"] = ctx
    snap["html"] = render_live(snap, ctx, now)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(snap, fh, separators=(",", ":"))
    bad = {k: v for k, v in snap["sources"].items() if v != "ok"}
    print(f"readiness: overall level {snap['overall']['level']} -> {out_path}"
          + (f" (not reached: {', '.join(f'{k} {v}' for k, v in bad.items())})" if bad else ""))


# ------------------------------------------------------------------ selftest

class _R:
    def __init__(self, body, code=200):
        self.content = body.encode() if isinstance(body, str) else body
        self.status_code = code


def fixture_web(now):
    """A fake internet for the selftest: url substring -> body."""
    d = lambda days: (now - datetime.timedelta(days=days)).date().isoformat()
    rfc = lambda days: (now - datetime.timedelta(days=days)).strftime("%a, %d %b %Y %H:%M:%S GMT")

    def fred(fid):
        base = {"VIXCLS": 31, "BAMLH0A0HYM2": 3.2, "STLFSI4": -.6, "T10Y3M": .4, "SAHMREALTIME": .1,
                "OVXCLS": 35, "USEPUINDXD": 120, "DCOILWTICO": 70, "DHHNGSP": 3, "GASREGW": 3.1, "GASDESW": 3.7}
        rows = ["observation_date," + fid]
        for i in range(60, -1, -1):
            v = base.get(fid, 5000)
            if fid == "SP500":
                v = 6000 if i > 20 else 5100      # a 15% drop
            if fid == "DCOILWTICO" and i < 5:
                v = 91                             # +30% oil spike
            rows.append(f"{d(i)},{v if i != 3 else '.'}")
        return "\n".join(rows)
    kev = {"vulnerabilities": [{"cveID": f"CVE-2026-{i:04d}", "vendorProject": "Acme", "product": "VPN",
                                "vulnerabilityName": "Acme VPN auth bypass", "dateAdded": d(i % 360),
                                "knownRansomwareCampaignUse": "Known" if i % 7 == 0 else "Unknown"}
                               for i in range(200)]
           + [{"cveID": f"CVE-2026-9{i:03d}", "vendorProject": "Big", "product": "Mail",
               "vulnerabilityName": "<script>x</script>", "dateAdded": d(1),
               "knownRansomwareCampaignUse": "Known"} for i in range(12)]}
    sec = {"hits": {"total": {"value": 3, "relation": "eq"}, "hits": [
        {"_id": "0001234567-26-000001:form8k.htm", "_source": {
            "ciks": ["0000012345"], "display_names": ["Acme Corp  (ACME)  (CIK 0000012345)"],
            "file_date": d(4), "form": "8-K", "items": ["1.05", "9.01"]}},
        {"_id": "0001234567-26-000002:x.htm", "_source": {
            "ciks": ["0000099999"], "display_names": ["Other Co  (CIK 0000099999)"], "file_date": d(9),
            "form": "8-K", "items": ["8.01"]}}]}}
    rss = lambda titles: ("<rss><channel>" + "".join(
        f"<item><title>{t}</title><link>https://example.com/{i}</link><pubDate>{rfc(a)}</pubDate></item>"
        for i, (t, a) in enumerate(titles)) + "</channel></rss>")
    nws = {"features": [
        {"properties": {"event": "Tornado Emergency", "severity": "Extreme", "geocode": {"UGC": ["OKC001"]}}},
        {"properties": {"event": "Winter Storm Warning", "severity": "Severe", "geocode": {"UGC": ["MNZ001", "WIZ002"]}}},
        {"properties": {"event": "Extreme Cold Warning", "severity": "Severe", "geocode": {"UGC": ["NDZ001", "MNZ003"]}}}]}
    quakes = {"features": [{"properties": {"mag": 7.1, "place": "Somewhere", "time": (now.timestamp() - 86400) * 1000,
                                           "alert": "orange", "url": "https://usgs.example/q", "title": "M 7.1 - Somewhere"}}]}
    storms = {"activeStorms": [{"name": "Zed", "classification": "HU", "intensity": "100", "lastUpdate": d(0)}]}
    fema = {"DisasterDeclarationsSummaries": [
        {"disasterNumber": 4900, "state": "FL", "declarationDate": d(3) + "T00:00:00.000Z",
         "incidentType": "Hurricane", "declarationTitle": "HURRICANE ZED"},
        {"disasterNumber": 4900, "state": "FL", "declarationDate": d(3) + "T00:00:00.000Z",
         "incidentType": "Hurricane", "declarationTitle": "HURRICANE ZED"}]}
    swpc = {"0": {"G": {"Scale": "1"}, "S": {"Scale": "0"}, "R": {"Scale": "0"}},
            "1": {"G": {"Scale": "3"}}, "2": {"G": {"Scale": None}}, "3": {"G": {"Scale": "0"}}}
    gdelt = {"timeline": [{"series": "Volume Intensity", "data": [
        {"date": f"2026{i:04d}", "value": 1.0 if i < 83 else 1.6} for i in range(90)]}]}
    who = {"value": [{"Title": "Avian influenza - Country X", "PublicationDateAndTime": d(5) + "T10:00:00Z",
                      "ItemDefaultUrl": "/2026-DON999"}]}
    # a minimal .xlsx with a date column and the GSCPI
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/sharedStrings.xml", "<sst xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'>"
                   "<si><t>Date</t></si><si><t>GSCPI</t></si><si><t>31-Oct-2026</t></si></sst>")
        z.writestr("xl/worksheets/sheet1.xml", "<worksheet xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'>"
                   "<sheetData><row><c t='s'><v>0</v></c><c t='s'><v>1</v></c></row>"
                   "<row><c><v>46265</v></c><c><v>0.12</v></c></row>"
                   "<row><c t='s'><v>2</v></c><c><v>1.45</v></c></row></sheetData></worksheet>")
    web = [("fredgraph", None), ("known_exploited", json.dumps(kev)), ("efts.sec.gov", json.dumps(sec)),
           ("cybersecurity-advisories.xml", rss([("AA26-001A: State hackers target telecoms", 3)])),
           ("ics-advisories.xml", rss([(f"ICSA-26-{i}", 2) for i in range(20)])),
           ("api.weather.gov", json.dumps(nws)), ("earthquake.usgs.gov", json.dumps(quakes)),
           ("nhc.noaa.gov", json.dumps(storms)), ("fema.gov", json.dumps(fema)),
           ("swpc.noaa.gov", json.dumps(swpc)), ("gdeltproject", json.dumps(gdelt)),
           ("travel.state.gov", rss([("Ruritania - Level 4: Do Not Travel", 5), ("Freedonia - Level 3: Reconsider Travel", 40),
                                     ("Elbonia - Level 4: Do Not Travel", 200), ("Utopia - Level 1: Exercise Normal Precautions", 2)])),
           ("newyorkfed.org", buf.getvalue()),
           ("wwwnc.cdc.gov", rss([("Level 2 - Practice Enhanced Precautions - Measles in X", 10),
                                  ("Level 1 - Practice Usual Precautions - Polio", 30)])),
           ("who.int", json.dumps(who)),
           ("news.google.com", rss([("Big hack hits retailer - Reuters", 1), ("Big hack hits retailer - AP", 1)]))]

    def get(url, headers=None, params=None):
        if "fredgraph" in url:
            return _R(fred(params["id"]))
        for k, body in web:
            if k in url:
                return _R(body)
        return _R("", 404)
    return get


def selftest():
    assert points(16, 16, 40) == 0 and points(40, 16, 40) == 100 and points(28, 16, 40) == 50
    assert points(-1, .5, -1) == 100 and points(1, .5, -1) == 0 and points(None, 1, 2) is None
    assert [level_for(x) for x in (0, 19.9, 20, 45, 65, 80, 100)] == [5, 5, 4, 3, 2, 1, 1]
    assert score([{"pts": 100}, {"pts": 0}, {"pts": 0}, {"pts": None}]) == round(50 + 50 / 3, 1)
    assert score([]) is None
    assert gdelt_ratio({"timeline": [{"data": [{"value": 1}] * 30}]}) == 1.0
    assert TICKER.search("Acme Corp  (ACME)  (CIK 1)").group(1) == "ACME"

    now = datetime.datetime(2026, 11, 27, 18, 0, tzinfo=datetime.timezone.utc)
    os.environ.setdefault("SEC_CONTACT_EMAIL", "test@example.com")
    snap = collect(fixture_web(now), now)
    D = {d["key"]: d for d in snap["domains"]}
    S = {d["key"]: {s["key"]: s for s in d["signals"]} for d in snap["domains"]}
    assert all(D[k]["score"] is not None for k in D), {k: v["score"] for k, v in D.items()}
    assert S["markets"]["vix"]["pts"] == points(31, 16, 40)
    assert -16 < S["markets"]["drawdown"]["v"] < -14, S["markets"]["drawdown"]
    assert S["cyber"]["kev"]["v"] > 2, S["cyber"]["kev"]          # 12+ this week vs ~4 usual
    assert S["cyber"]["ransom"]["v"] >= 12
    hacked = D["cyber"]["hacked"]
    assert [h["tkr"] for h in hacked] == ["ACME"] and hacked[0]["url"].endswith("/000123456726000001/form8k.htm")
    assert S["geopolitics"]["gdelt"]["v"] > 1.4 and S["geopolitics"]["travel"]["v"] == 1
    assert S["infrastructure"]["geomag"]["pts"] == 70 and S["infrastructure"]["ics"]["v"] == 20
    assert S["infrastructure"]["strain"]["v"] == 2                # ND and MN
    assert S["hazards"]["extreme"]["v"] == 1 and S["hazards"]["quake"]["pts"] == 75
    assert S["hazards"]["storm"]["pts"] == 90 and S["hazards"]["fema"]["v"] == 1  # one disaster, two counties
    assert abs(S["supply"]["gscpi"]["v"] - 1.45) < 1e-9, S["supply"]["gscpi"]
    assert S["supply"]["oil"]["v"] > 25
    assert S["health"]["cdc2"]["v"] == 1 and S["health"]["who"]["v"] == 1
    assert len(D["cyber"]["news"]) == 1, "duplicate headlines are merged"
    assert snap["overall"]["level"] in (1, 2, 3, 4, 5)

    # a dead internet: nothing scored, nothing crashes
    dead = collect(lambda url, headers=None, params=None: _R("", 503), now)
    assert dead["overall"]["level"] is None and all(d["score"] is None for d in dead["domains"])
    assert "first readings" in render_live(dead, {}, now)

    # history: one point per day, latest wins
    old = {"history": [{"d": "2026-11-25", "o": 5, "l": {}}, {"d": "2026-11-27", "o": 5, "l": {}}]}
    merge_history(snap, old)
    assert [p["d"] for p in snap["history"]] == ["2026-11-25", "2026-11-27"]
    assert snap["history"][-1]["o"] == snap["overall"]["level"]

    # rendering: escaped, linked, explained
    ctx = {"pages": ["CRWD", "ACME"], "ret": {"CRWD": .081, "ACME": -.12}}
    h = render_live(snap, ctx, now)
    assert "<script>x" not in h and "&lt;script&gt;" in h, "feed text is escaped"
    assert "href='s/CRWD.html'" in h and "+8.1%" in h and "-12.0%" in h
    assert "Companies that reported a material cyber incident" in h and "Acme Corp" in h
    assert "For investors" in h and "Be prepared" in h and "id='rd-hazards'" in h
    assert "look.html?s=PANW" in h

    # daily storage, page, home tile, hourly output
    import duckdb
    import tempfile
    con = duckdb.connect()
    assert "first readings arrive" in page(con) and home_teaser(con) == ""
    refresh(con, fixture_web(now), now)
    pg = page(con, {"CRWD"})
    assert "id='rd-live'" in pg and "How the levels work" in pg and "5 is calm" in pg
    assert "Overall" in home_teaser(con) and "readiness.html#rd-" in home_teaser(con)
    lv = levels(con)
    assert lv["overall"]["level"] == snap["overall"]["level"] and lv["overall"]["label"].isupper()
    assert [s["key"] for s in lv["subs"]] == [d[0] for d in DOMAINS] and lv["href"] == "readiness.html"
    assert all(s["why"] for s in lv["subs"]) and levels(duckdb.connect()) is None
    sj = site_json(con, {"CRWD"})
    assert sj["ctx"]["pages"] == ["CRWD"] and sj["html"]
    tmp = tempfile.mkdtemp()
    with open(f"{tmp}/old.json", "w") as fh:
        json.dump(sj, fh)
    main(f"{tmp}/feed/readiness.json", f"{tmp}/old.json", f"{tmp}/missing.json", get=fixture_web(now), now=now)
    hourly = json.load(open(f"{tmp}/feed/readiness.json"))
    assert hourly["ctx"]["pages"] == ["CRWD"] and "href='s/CRWD.html'" in hourly["html"]
    assert hourly["history"] and hourly["generated"]
    print("readiness selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    elif len(sys.argv) >= 2:
        main(sys.argv[1], *sys.argv[2:])
    else:
        print(__doc__)
