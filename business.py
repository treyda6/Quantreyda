"""What each company actually does, in plain English, for the stock pages and
the Ideas cards. All free, all cached in company_intel:

  wiki     Wikipedia's summary of the company (the first paragraph), plus
           Wikidata facts: founded, headquarters, CEO, employees, industry,
           main products, website. Companies are matched to Wikidata by their
           stock ticker on a US exchange, so "Apple" never turns into the fruit.
  tenk     The latest annual report (10-K, or 20-F for foreign companies) from
           SEC EDGAR: the opening of its Business section ("in its own words"),
           what it says about competition and big customers, its employee
           count, and its sales split by business segment, product and region
           (read from the report's own XBRL tags, so the numbers are exact).

Wikipedia is cheap (a few small requests per company); annual reports are big,
so only a couple of dozen are downloaded a day and each is only re-read when a
newer one is filed.

  refresh(con, tickers, names)  fetch the stalest few (tickers in priority order)
  snapshot(con, tkr)            dict for the page, or {}
  oneliner(b)                   one short sentence: what the company is
  money_line(b)                 'Most of its sales come from iPhone (51%) ...'
  panel(con, tkr, pr, root, known)   the "What it does" HTML panel

    py business.py AAPL          # fetch one and print
    py business.py --selftest
"""

import datetime
import html
import itertools
import json
import re
import sys
import time
import urllib.parse

import requests

import companyintel

E = html.escape
SPARQL = "https://query.wikidata.org/sparql"
WIKIDATA = "https://www.wikidata.org/w/api.php"
WIKI_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
FILING_DIR = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/"
WIKI_EVERY, TENK_EVERY = 60, 30          # days before re-checking
# NYSE, Nasdaq, NYSE American, NYSE Arca, Cboe BZX
US_EXCHANGES = ["Q13677", "Q82059", "Q846626", "Q2361291", "Q1048481"]


# ------------------------------------------------------------------ text bits

def sentences(text, n=None):
    """Split on sentence ends, but not after 'Inc.', 'U.S.' and the like."""
    text = re.sub(r"\s+", " ", text or "").strip()
    parts = re.split(r"(?<!\b[A-Z])(?<!\bInc)(?<!\bCorp)(?<!\bCo)(?<!\bLtd)(?<!\bNo)(?<!\bSt)"
                     r"(?<!\bvs)(?<!\bU\.S)(?<=[.!?])\s+(?=[A-Z\"'(])", text)
    parts = [p.strip() for p in parts if p.strip()]
    return parts[:n] if n else parts


def clip(text, limit):
    """Whole sentences up to about `limit` characters (at least one sentence)."""
    out = ""
    for s in sentences(text):
        if out and len(out) + len(s) + 1 > limit:
            break
        out = (out + " " + s).strip()
    if len(out) > limit * 1.5:
        out = out[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return out


# ------------------------------------------------------------------ Wikipedia

def sparql_query(tickers):
    syms = sorted({v for t in tickers for v in (t, t.replace("-", "."), t.replace(".", "-"))})
    vals = " ".join(json.dumps(t) for t in syms)
    ex = " ".join(f"wd:{q}" for q in US_EXCHANGES)
    return ("SELECT ?tkr ?item ?article WHERE { VALUES ?tkr { " + vals + " } "
            "VALUES ?ex { " + ex + " } ?item p:P414 ?st . ?st ps:P414 ?ex ; pq:P249 ?tkr . "
            "OPTIONAL { ?article schema:about ?item ; "
            "schema:isPartOf <https://en.wikipedia.org/> . } }")


def parse_sparql(j, tickers):
    """{tkr: (QID, enwiki title or None)} from SPARQL JSON results."""
    want = {t.replace("-", ".").upper(): t for t in tickers}
    out = {}
    for b in (j.get("results") or {}).get("bindings", []):
        sym = b.get("tkr", {}).get("value", "").replace("-", ".").upper()
        t = want.get(sym)
        item = b.get("item", {}).get("value", "").rsplit("/", 1)[-1]
        if not t or not item.startswith("Q"):
            continue
        art = b.get("article", {}).get("value")
        title = urllib.parse.unquote(art.rsplit("/wiki/", 1)[-1]).replace("_", " ") if art else None
        if t not in out or (title and not out[t][1]):
            out[t] = (item, title)
    return out


def _claims(ent, prop):
    """Usable statements for a property: preferred ones if any, never deprecated."""
    cs = [c for c in (ent.get("claims") or {}).get(prop, [])
          if c.get("rank") != "deprecated" and (c.get("mainsnak") or {}).get("snaktype") == "value"]
    pref = [c for c in cs if c.get("rank") == "preferred"]
    return pref or cs


def _val(c):
    return c["mainsnak"]["datavalue"]["value"]


def _qual_time(c, prop):
    for q in (c.get("qualifiers") or {}).get(prop, []):
        try:
            return q["datavalue"]["value"]["time"]
        except (KeyError, TypeError):
            pass
    return None


def parse_entity(ent):
    """Facts from one Wikidata entity; item values are QIDs (labels come later)."""
    f = {"desc": ((ent.get("descriptions") or {}).get("en") or {}).get("value")}

    def items(prop, n):
        out = []
        for c in _claims(ent, prop):
            v = _val(c)
            if isinstance(v, dict) and v.get("id") and v["id"] not in out:
                out.append(v["id"])
        return out[:n]
    f["industry"] = items("P452", 3)
    f["products"] = items("P1056", 8)
    f["founders"] = items("P112", 3)
    f["hq"] = items("P159", 1)
    ceo = [c for c in _claims(ent, "P169") if not _qual_time(c, "P582")]  # no end date: current
    f["ceo"] = [_val(ceo[-1])["id"]] if ceo and isinstance(_val(ceo[-1]), dict) else []
    inc = _claims(ent, "P571")
    if inc:
        m = re.match(r"[+-]?(\d{3,4})", _val(inc[0]).get("time", ""))
        f["founded"] = int(m.group(1)) if m else None
    emp = sorted(_claims(ent, "P1128"), key=lambda c: _qual_time(c, "P585") or "")
    if emp:
        try:
            f["employees"] = int(float(_val(emp[-1])["amount"]))
            f["employees_year"] = (_qual_time(emp[-1], "P585") or "")[1:5] or None
        except (KeyError, ValueError, TypeError):
            pass
    web = _claims(ent, "P856")
    if web and isinstance(_val(web[0]), str) and _val(web[0]).startswith("http"):
        f["website"] = _val(web[0])
    return f


def _labels(s, qids):
    out = {}
    qids = list(dict.fromkeys(qids))
    for i in range(0, len(qids), 50):
        r = companyintel._wiki_get(s, WIKIDATA, params={
            "action": "wbgetentities", "ids": "|".join(qids[i:i + 50]), "props": "labels",
            "languages": "en", "format": "json"})
        r.raise_for_status()
        for q, e in (r.json().get("entities") or {}).items():
            lab = ((e.get("labels") or {}).get("en") or {}).get("value")
            if lab:
                out[q] = lab
    return out


def fetch_wiki(s, qid, title):
    """{desc, extract, wiki, founded, hq, ceo, employees, industry, products, ...}."""
    out = {}
    if qid:
        r = companyintel._wiki_get(s, WIKIDATA, params={
            "action": "wbgetentities", "ids": qid, "props": "claims|descriptions|sitelinks",
            "languages": "en", "sitefilter": "enwiki", "format": "json"})
        r.raise_for_status()
        ent = (r.json().get("entities") or {}).get(qid) or {}
        out = parse_entity(ent)
        title = title or ((ent.get("sitelinks") or {}).get("enwiki") or {}).get("title")
        refs = [q for k in ("industry", "products", "founders", "hq", "ceo") for q in out.get(k, [])]
        lab = _labels(s, refs) if refs else {}
        for k in ("industry", "products", "founders", "hq", "ceo"):
            out[k] = [lab[q] for q in out.get(k, []) if q in lab and not re.fullmatch(r"Q\d+", lab[q])]
    if title:
        r = companyintel._wiki_get(s, WIKI_SUMMARY.format(
            title=urllib.parse.quote(title.replace(" ", "_"), safe="")))
        if r.status_code == 200:
            j = r.json()
            if j.get("type") != "disambiguation":
                out["extract"] = j.get("extract")
                out["wiki"] = j.get("title") or title
                out["desc"] = out.get("desc") or j.get("description")
    return out if (out.get("extract") or out.get("desc")) else None


BUSINESSY = re.compile(r"\b(compan(y|ies)|corporation|conglomerate|manufacturer|maker|retailer|bank|"
                       r"insurer|firm|trust|airline|producer|provider|operator|developer|holding|"
                       r"chain|brand|supplier|utility|REIT|fund|business|biotech|pharmaceutical)", re.I)


def looks_like_company(w):
    """For pages found by name search (no ticker match): is it about a business?"""
    return bool(w) and bool(BUSINESSY.search(f"{w.get('desc') or ''} {sentences(w.get('extract'), 1)[:1]}"))


# ------------------------------------------------------------- annual report

BIZ_RE = {"10-K": re.compile(r"item\s*1\s*\.?[\s\-–—:.]*business\b", re.I),
          "20-F": re.compile(r"item\s*4\s*\.?[\s\-–—:.]*information\s+on\s+the\s+company", re.I)}
BIZ_END = {"10-K": re.compile(r"item\s*1a\s*\.?[\s\-–—:.]*risk\s+factors", re.I),
           "20-F": re.compile(r"item\s*(4a|5)\s*\.?[\s\-–—:.]*(unresolved|operating)", re.I)}
BOILER = re.compile(r"(?i)forward[- ]looking|this (annual )?report|unless the context|"
                    r"references (in this|to)|as used in|incorporated by reference|table of contents|"
                    r"^\(?(in|dollars in) (millions|thousands)|available (free of charge|on our website)|"
                    r"we were incorporated in|investor relations")


def business_section(text, form="10-K"):
    head, end = BIZ_RE.get(form, BIZ_RE["10-K"]), BIZ_END.get(form, BIZ_END["10-K"])
    best = ""
    for m in head.finditer(text):
        stop = end.search(text, m.end())
        part = text[m.end(): stop.start() if stop else m.end() + 300_000]
        if len(part) > len(best):
            best = part
    return best


def _paras(section):
    return [p for p in section.split("\n") if len(p) >= 160 and not BOILER.search(p[:200])
            and sum(c.isalpha() for c in p) > len(p) * 0.6]


def overview(section, limit=650):
    """The first real paragraphs of the Business section."""
    out = ""
    for p in _paras(section)[:3]:
        if out and len(out) + len(p) > limit:
            break
        out = (out + " " + p).strip()
    return clip(out, limit) if out else None


def after_heading(section, word, limit=420):
    """The paragraph under a short heading such as 'Competition' or 'Customers'."""
    lines = section.split("\n")
    for k, line in enumerate(lines):
        if len(line) <= 60 and re.fullmatch(rf"(?i)(our )?{word}[\w ,&]*\.?", line.strip()):
            for p in lines[k + 1: k + 6]:
                if len(p) >= 120:
                    return clip(p, limit)
    return None


CUSTOMER_RE = re.compile(r"[^.]*\b(customer|client|distributor)s?\b[^.]*\baccounted for (approximately |about )?"
                         r"\d+(\.\d+)?\s?%[^.]*\.", re.I)
EMPLOYEE_RE = re.compile(r"(?:approximately|about|had|employed)\s+([\d,]{3,9})\s+(?:full[- ]time\s+)?(?:equivalent\s+)?"
                         r"(?:employees|people|team members|associates)", re.I)


def big_customers(text):
    for m in CUSTOMER_RE.finditer(text):
        s = m.group(0).strip()
        if 40 <= len(s) <= 320 and not BOILER.search(s):
            return s
    return None


def employees(text):
    for m in EMPLOYEE_RE.finditer(text):
        n = int(m.group(1).replace(",", ""))
        if n >= 10:
            return n
    return None


# ------------------------------------------------- sales split (inline XBRL)

AXES = {"segments": "us-gaap:StatementBusinessSegmentsAxis",
        "products": "srt:ProductOrServiceAxis",
        "regions": "srt:StatementGeographicalAxis"}
REVENUE = ["us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax", "us-gaap:Revenues",
           "us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax", "us-gaap:SalesRevenueNet",
           "us-gaap:SalesRevenueGoodsNet", "us-gaap:RevenuesNetOfInterestExpense"]
CTX_RE = re.compile(r"<(?:xbrli:)?context\b[^>]*?\bid=\"([^\"]+)\"[^>]*>(.*?)</(?:xbrli:)?context>", re.S | re.I)
MEM_RE = re.compile(r"<(?:xbrldi:)?explicitMember\b[^>]*?dimension=\"([^\"]+)\"[^>]*>\s*([^<\s]+)\s*<", re.I)
FACT_RE = re.compile(r"<ix:nonFraction\b([^>]*)>(.*?)</ix:nonFraction>", re.S | re.I)
ATTR_RE = re.compile(r"([\w:\-]+)=\"([^\"]*)\"")
COUNTRY = {"US": "United States", "CN": "China", "JP": "Japan", "GB": "United Kingdom", "DE": "Germany",
           "CA": "Canada", "FR": "France", "IN": "India", "KR": "South Korea", "TW": "Taiwan",
           "MX": "Mexico", "BR": "Brazil", "IE": "Ireland", "NL": "Netherlands", "CH": "Switzerland",
           "AU": "Australia", "SG": "Singapore", "IL": "Israel", "IT": "Italy", "ES": "Spain",
           "HK": "Hong Kong", "SE": "Sweden", "BE": "Belgium", "DK": "Denmark"}
NICE = {"NonUs": "Outside the US", "US": "United States", "Service": "Services", "Product": "Products",
        "EMEA": "Europe, Middle East & Africa", "AsiaPacific": "Asia-Pacific", "Americas": "Americas"}


def contexts(doc):
    """{id: (end date, days, {axis: member}, other_dims)} for duration contexts."""
    out = {}
    for cid, body in CTX_RE.findall(doc):
        st = re.search(r"<(?:xbrli:)?startDate>\s*([\d-]{10})", body)
        en = re.search(r"<(?:xbrli:)?endDate>\s*([\d-]{10})", body)
        if not (st and en):
            continue
        try:
            d0, d1 = datetime.date.fromisoformat(st.group(1)), datetime.date.fromisoformat(en.group(1))
        except ValueError:
            continue
        dims = dict(MEM_RE.findall(body))
        typed = bool(re.search(r"typedMember", body, re.I))
        out[cid] = (d1, (d1 - d0).days, dims, typed)
    return out


def _number(attrs, inner):
    txt = re.sub(r"<[^>]+>", "", inner).strip()
    if "zero" in attrs.get("format", "") or txt in ("-", "—", "–"):
        v = 0.0
    else:
        txt = re.sub(r"[^\d.,]", "", txt)
        if "comma" in attrs.get("format", "").replace("-", ""):  # 1.234,5 style
            txt = txt.replace(".", "").replace(",", ".")
        else:
            txt = txt.replace(",", "")
        try:
            v = float(txt)
        except ValueError:
            return None
    try:
        v *= 10 ** int(attrs.get("scale") or 0)
    except ValueError:
        pass
    return -v if attrs.get("sign") == "-" else v


def facts(doc, names):
    """[(concept, contextRef, value)] for the given concepts."""
    out = []
    for a, inner in FACT_RE.findall(doc):
        attrs = dict(ATTR_RE.findall(a))
        if attrs.get("name") in names:
            v = _number(attrs, inner)
            if v is not None:
                out.append((attrs["name"], attrs.get("contextRef"), v))
    return out


def _drop_parents(items, total):
    """Remove a member that's just the sum of other members (e.g. 'Products'
    alongside iPhone, Mac and iPad) when the parts add up to more than the total."""
    items = dict(items)
    for _ in range(4):
        if not total or sum(items.values()) <= total * 1.03:
            break
        for big in sorted(items, key=items.get, reverse=True):
            rest = [k for k in items if k != big and items[k] < items[big]][:14]
            hit = None
            for n in range(2, len(rest) + 1):
                for combo in itertools.combinations(rest, n):
                    if abs(sum(items[k] for k in combo) - items[big]) <= items[big] * 0.006:
                        hit = combo
                        break
                if hit:
                    break
            if hit:
                del items[big]
                break
        else:
            break
    return items


def sales_split(doc):
    """{'year_end', 'total', 'segments': [(member, value)], 'products': [...], 'regions': [...]}"""
    ctx = contexts(doc)
    fs = [f for f in facts(doc, set(REVENUE)) if f[1] in ctx and 330 <= ctx[f[1]][1] <= 380 and f[2] > 0]
    if not fs:
        return {}
    end = max(ctx[c][0] for _, c, _ in fs)
    fs = [f for f in fs if ctx[f[1]][0] == end]
    out = {"year_end": str(end)}
    totals = {n: v for n, c, v in fs if not ctx[c][2] and not ctx[c][3]}
    for key, axis in AXES.items():
        best = {}
        for concept in REVENUE:
            got = {}
            for n, c, v in fs:
                dims = ctx[c][2]
                if n == concept and len(dims) == 1 and axis in dims and not ctx[c][3]:
                    got[dims[axis]] = max(v, got.get(dims[axis], 0))
            if len(got) > len(best):
                best, total = got, totals.get(concept)
        if len(best) >= 2:
            total = total or max(totals.values(), default=None)
            best = _drop_parents(best, total)
            out[key] = sorted(best.items(), key=lambda kv: -kv[1])
            out.setdefault("total", total)
    return out if len(out) > 1 else {}


def humanize(member, labels=None):
    """'aapl:IPhoneMember' -> label from the filing ('iPhone'), else 'I Phone'."""
    if labels and member in labels:
        lab = labels[member]
    else:
        prefix, _, local = member.partition(":")
        if prefix == "country" and local in COUNTRY:
            return COUNTRY[local]
        local = re.sub(r"(Segment)?Member$", "", local)
        if local in NICE:
            return NICE[local]
        lab = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", local)
    lab = re.sub(r"\s*\[(Member|Domain|Axis)\]\s*$", "", lab).strip()
    lab = re.sub(r"(?i)\s+(reportable |operating |business )?segments?$", "", lab).strip()
    return lab or member


def parse_labels(xml):
    """{'aapl:IPhoneMember': 'iPhone'} from a filing's label linkbase (_lab.xml)."""
    locs = {}
    for a in re.findall(r"<(?:link:)?loc\b([^>]*)/?>", xml, re.I):
        at = dict(ATTR_RE.findall(a))
        frag = (at.get("xlink:href") or "").rsplit("#", 1)[-1]
        if frag and at.get("xlink:label"):
            locs[at["xlink:label"]] = frag.replace("_", ":", 1)
    labs = {}
    for a, text in re.findall(r"<(?:link:)?label\b([^>]*)>(.*?)</(?:link:)?label>", xml, re.S | re.I):
        at = dict(ATTR_RE.findall(a))
        role = at.get("xlink:role", "")
        if role.endswith("/label") or role.endswith("/terseLabel"):
            labs.setdefault(at.get("xlink:label"), {})[role.rsplit("/", 1)[-1]] = html.unescape(text.strip())
    out = {}
    for a in re.findall(r"<(?:link:)?labelArc\b([^>]*)/?>", xml, re.I):
        at = dict(ATTR_RE.findall(a))
        concept, lab = locs.get(at.get("xlink:from")), labs.get(at.get("xlink:to"))
        if concept and lab and concept.endswith("Member"):
            out[concept] = re.sub(r"\s*\[(Member|Domain)\]\s*$", "", lab.get("terseLabel") or lab.get("label"))
    return out


def latest_annual(s, cik):
    j = s.get(SUBMISSIONS.format(cik=cik), timeout=60).json()
    rec = j.get("filings", {}).get("recent", {})
    for k, form in enumerate(rec.get("form", [])):
        if form in ("10-K", "20-F", "40-F") and rec["primaryDocument"][k]:
            return {"form": form, "filed": rec["filingDate"][k], "acc": rec["accessionNumber"][k],
                    "doc": rec["primaryDocument"][k]}
    return None


def fetch_tenk(s, cik, filing):
    import secextras
    base = FILING_DIR.format(cik=cik, acc=filing["acc"].replace("-", ""))
    time.sleep(0.2)
    doc = s.get(base + filing["doc"], timeout=120).text
    text = secextras.html_text(doc)
    sec = business_section(text, filing["form"])
    out = {"form": filing["form"], "filed": filing["filed"], "acc": filing["acc"],
           "url": base + filing["doc"], "overview": overview(sec) if sec else None,
           "competition": after_heading(sec, "competition") if sec else None,
           "customers": big_customers(sec or text), "employees": employees(sec or text)}
    split = sales_split(doc)
    if split:
        members = {m for k in AXES for m, _ in split.get(k, [])}
        labels = {}
        if any(m.split(":")[0] not in ("us-gaap", "srt", "country") for m in members):
            try:
                time.sleep(0.2)
                idx = s.get(base + "index.json", timeout=60).json()
                lab = next((i["name"] for i in idx["directory"]["item"] if i["name"].endswith("_lab.xml")), None)
                if lab:
                    time.sleep(0.2)
                    labels = parse_labels(s.get(base + lab, timeout=120).text)
            except (requests.RequestException, ValueError, KeyError):
                pass
        for k in AXES:
            if split.get(k):
                split[k] = [(humanize(m, labels), v) for m, v in split[k]]
        out["split"] = split
    return out


# ------------------------------------------------------------------- driver

def _due(con, source, tickers, every, today):
    have = dict(con.execute("SELECT tkr, checked_on FROM company_intel WHERE source = ?",
                            [source]).fetchall())
    new = [t for t in tickers if t not in have]               # in priority order (ideas first)
    old = sorted((t for t in tickers if t in have and (today - have[t]).days >= every),
                 key=lambda t: have[t])
    return list(dict.fromkeys(new + old))


def refresh_wiki(con, tickers, limit=120, today=None):
    companyintel.ensure_tables(con)
    today = today or datetime.date.today()
    due = _due(con, "wiki", tickers, WIKI_EVERY, today)[:limit]
    if not due:
        return
    s = requests.Session()
    s.headers["User-Agent"] = companyintel.UA
    try:
        time.sleep(0.5)
        r = s.get(SPARQL, params={"query": sparql_query(due), "format": "json"},
                  headers={"Accept": "application/sparql-results+json"}, timeout=90)
        r.raise_for_status()
        hits = parse_sparql(r.json(), due)
    except (requests.RequestException, ValueError) as e:
        print(f"  wikidata ticker lookup failed ({type(e).__name__}); using name matches only")
        hits = {}
    buzz = dict(con.execute("SELECT tkr, split_part(detail, '|', 1) FROM company_intel "
                            "WHERE source = 'buzz' AND detail IS NOT NULL").fetchall())
    found = fails = 0
    for t in due:
        if fails >= 8:
            print("  wiki: stopping after 8 errors in a row")
            break
        qid, title = hits.get(t, (None, None))
        by_name = not qid
        title = title or buzz.get(t) or None
        try:
            got = fetch_wiki(s, qid, title) if (qid or title) else None
            fails = 0
        except (requests.RequestException, ValueError) as e:
            fails += 1
            print(f"  wiki {t}: {type(e).__name__}")
            continue
        if got and by_name and not looks_like_company(got):
            got = None  # the name search found something else
        companyintel._save(con, t, "wiki", 1 if got else None, json.dumps(got) if got else None)
        found += bool(got)
    print(f"what they do (Wikipedia): {found} of {len(due)} companies "
          f"({sum(t in hits for t in due)} matched by ticker)")


def refresh_tenk(con, tickers, limit=20, today=None):
    import health
    companyintel.ensure_tables(con)
    today = today or datetime.date.today()
    due = _due(con, "tenk", tickers, TENK_EVERY, today)[:limit * 3]
    if not due:
        return
    s = health._session()  # SEC wants a contact in the user agent
    ciks = health.ticker_ciks(s)
    old = {t: json.loads(d) for t, d in con.execute(
        "SELECT tkr, detail FROM company_intel WHERE source = 'tenk' AND detail IS NOT NULL").fetchall()}
    read = same = 0
    for t in due:
        if read >= limit:
            break
        hit = ciks.get(t) or ciks.get(t.replace(".", "-"))
        got = None
        if hit:
            try:
                time.sleep(0.15)
                filing = latest_annual(s, hit[0])
                if filing and old.get(t, {}).get("acc") == filing["acc"]:
                    companyintel._save(con, t, "tenk", 1, json.dumps(old[t]))  # nothing new: just mark checked
                    same += 1
                    continue
                if filing:
                    got = fetch_tenk(s, hit[0], filing)
                    read += 1
            except (requests.RequestException, ValueError) as e:
                print(f"  annual report {t}: {type(e).__name__}")
                continue
        companyintel._save(con, t, "tenk", 1 if got else None, json.dumps(got) if got else None)
    print(f"what they do (annual reports): read {read}, unchanged {same}")


def refresh(con, tickers, wiki=120, tenk=20):
    tickers = list(dict.fromkeys(tickers))
    refresh_wiki(con, tickers, wiki)
    refresh_tenk(con, tickers, tenk)


# ------------------------------------------------------------------- output

def snapshot(con, tkr):
    out = {}
    try:
        rows = con.execute("SELECT source, detail FROM company_intel WHERE tkr = ? AND "
                           "source IN ('wiki', 'tenk') AND detail IS NOT NULL", [tkr]).fetchall()
    except Exception:  # table not created yet
        return out
    for source, detail in rows:
        try:
            out[source] = json.loads(detail)
        except ValueError:
            pass
    return out


def _a(word):
    return "An" if word[:1].lower() in "aeiou" else "A"


def oneliner(b, limit=170):
    """One short sentence on what the company is, or None."""
    w, k = b.get("wiki") or {}, b.get("tenk") or {}
    first = (sentences(w.get("extract"), 1) or [""])[0]
    m = re.search(r"\b(?:is|was) (an?|the) (.+?)\.?$", first)
    if m and len(m.group(2)) > 8:
        rest = m.group(2)
        if len(rest) > limit:
            rest = re.split(r",| that | which | headquartered | based ", rest)[0]
        if len(rest) > limit * 1.3:
            return f"{_a(rest)} {rest[:limit].rsplit(' ', 1)[0]}…"
        return f"{_a(rest) if m.group(1) != 'the' else 'The'} {rest}."
    if w.get("desc") and len(w["desc"]) > 12:
        return w["desc"][0].upper() + w["desc"][1:].rstrip(".") + "."
    if k.get("overview"):
        s = sentences(k["overview"], 1)[0]
        return s if len(s) <= limit * 1.4 else clip(s, limit)
    return None


def _shares(items, total):
    base = total or sum(v for _, v in items) or 1
    return [(n, v, v / base) for n, v in items]


GEO = re.compile(r"(?i)\b(americas?|europe|asia|pacific|china|japan|greater china|emea|apac|latin|"
                 r"international|domestic|united states|u\.s\.|north|south|middle east|africa|canada|"
                 r"mexico|india|outside the us|rest of (the )?world|countries)\b")


def views(sp):
    """(what it sells, where it sells) from a sales split: segments named after
    places (Apple's 'Americas', 'Europe') count as regions."""
    seg, prod, reg = (sp.get(k) or [] for k in ("segments", "products", "regions"))
    seg = seg if len(seg) >= 2 else []
    geo_seg = bool(seg) and sum(bool(GEO.search(n)) for n, _ in seg) >= len(seg) - 1
    what = prod if len(prod) >= 2 else ([] if geo_seg else seg)
    where = reg if len(reg) >= 2 else (seg if geo_seg else [])
    biz = seg if seg and not geo_seg and what is not seg else []
    return what, where, biz


def _what_title(sp, what):
    return "Sales by business" if what is (sp.get("segments") or None) else "Sales by product"


def money_line(b):
    """'Most of its sales come from iPhone (51%), then Services (25%).' or None."""
    sp = (b.get("tenk") or {}).get("split") or {}
    what, where, _ = views(sp)
    out = []
    if what:
        sh = _shares(what, sp.get("total"))
        (n0, _, p0), rest = sh[0], sh[1:3]
        lead = "Most of its sales come from" if p0 >= .5 else "Its biggest business is"
        s = f"{lead} {n0} ({p0:.0%})"
        if rest:
            s += ", then " + " and ".join(f"{n} ({p:.0%})" for n, _, p in rest)
        out.append(s + ".")
    if where:
        (n0, _, p0) = _shares(where, sp.get("total"))[0]
        out.append(f"{'Most' if p0 >= .5 else 'The biggest share'} of its sales ({p0:.0%}) "
                   f"{'are' if p0 >= .5 else 'is'} in {'the ' if n0 in ('Americas', 'United States') else ''}{n0}.")
    return " ".join(out) or None


def _bars(items, total, n=6):
    sh = _shares(items, total)
    top = sh[:n]
    if len(sh) > n:
        other = sum(v for _, v, _ in sh[n:])
        top.append(("Everything else", other, other / (total or sum(v for _, v in items) or 1)))
    overlap = sum(p for _, _, p in sh) > 1.05
    rows = "".join(
        f"<div class='bar'><span class='n'>{E(name)}</span><span class='track'>"
        f"<i style='width:{max(2, min(100, p * 100)):.0f}%'></i></span>"
        f"<b>{p:.0%}</b><span class='v'>{E(_money(v))}</span></div>" for name, v, p in top)
    return rows + ("<p class='hint'>Some of these overlap (one is part of another).</p>" if overlap else "")


def _money(v):
    import profiles
    return profiles.money(v) or "—"


def _fact(label, value):
    return f"<div><span>{E(label)}</span><b>{E(str(value))}</b></div>" if value else ""


def peers(con, tkr, n=6):
    """Other tracked companies with the same SEC industry code, biggest first."""
    try:
        return [r[0] for r in con.execute("""
            SELECT c.tkr FROM companies c JOIN companies me ON me.tkr = ? AND me.sic = c.sic
            LEFT JOIN health h ON h.tkr = c.tkr
            WHERE c.tkr <> ? AND me.sic IS NOT NULL
            ORDER BY h.revenue DESC NULLS LAST, c.tkr LIMIT ?""", [tkr, tkr, n]).fetchall()]
    except Exception:  # health table missing on a fresh database
        try:
            return [r[0] for r in con.execute("""SELECT c.tkr FROM companies c JOIN companies me
                ON me.tkr = ? AND me.sic = c.sic WHERE c.tkr <> ? ORDER BY c.tkr LIMIT ?""",
                [tkr, tkr, n]).fetchall()]
        except Exception:
            return []


def panel(con, tkr, pr=None, root="../", known=None):
    """The 'What it does' panel at the top of a stock page ('' if nothing on file)."""
    import stockpage
    pr = pr or {}
    b = snapshot(con, tkr)
    w, k = b.get("wiki") or {}, b.get("tenk") or {}
    name = pr.get("name") or tkr
    one = oneliner(b)
    if not (one or k.get("overview") or w.get("extract")):
        return ""
    out = ["<section class='panel biz'><h2>What it does</h2>"]
    if one:
        out.append(f"<p class='lede'>{E(one)}</p>")
    summary = clip(" ".join(sentences(w.get("extract"))[1:]), 520) if w.get("extract") else ""
    if summary:
        out.append(f"<p>{E(summary)}</p>")
    rev, ni = pr.get("revenue"), pr.get("net_income")
    money = []
    ml = money_line(b)
    if ml:
        money.append(E(ml))
    if rev and rev > 0 and ni is not None:
        c = ni / rev * 100
        money.append(f"It keeps about <b>{c:.0f}¢ of profit</b> from every $1 of sales." if c >= 1 else
                     "It keeps less than 1¢ of profit from each $1 of sales." if c >= 0 else
                     f"It <b>loses about {-c:.0f}¢</b> on every $1 of sales right now.")
    if money:
        out.append(f"<p>{' '.join(money)}</p>")
    emp = k.get("employees") or w.get("employees")
    facts_ = [("Founded", w.get("founded")), ("Headquarters", ", ".join(w.get("hq") or [])),
              ("CEO", ", ".join(w.get("ceo") or [])),
              ("Employees", f"{emp:,}" if emp else None),
              ("Sales per employee", _money(rev / emp) if rev and emp and emp >= 10 else None),
              ("Industry", ", ".join(w.get("industry") or [])[:60] or pr.get("sector")),
              ("Founders", ", ".join(w.get("founders") or []))]
    fx = "".join(_fact(a, v) for a, v in facts_)
    if fx:
        out.append(f"<div class='kv facts'>{fx}</div>")
    sp = k.get("split") or {}
    what, where, biz = views(sp)
    cols = [(t, items) for t, items in (("Sales by business", biz), (_what_title(sp, what), what),
                                         ("Sales by region", where)) if items]
    if cols:
        yr = (sp.get("year_end") or "")[:4]
        out.append("<div class='cols'>" + "".join(
            f"<div><h3>{E(t)}{f' ({yr})' if yr else ''}</h3>{_bars(items, sp.get('total'))}</div>"
            for t, items in cols) + "</div>")
    if w.get("products"):
        out.append("<h3>Known for</h3><div class='chips'>"
                   + "".join(f"<span class='chip'>{E(p)}</span>" for p in w["products"][:8]) + "</div>")
    if k.get("overview"):
        out.append(f"<h3>In its own words</h3><p class='quote'>{E(k['overview'])}</p>")
    if k.get("competition"):
        out.append(f"<h3>Who it competes with</h3><p>{E(k['competition'])}</p>")
    pe = peers(con, tkr)
    if pe:
        links = ", ".join(
            f"<a href='{E(stockpage.href(p, root))}'>{E(p)}</a>" if known is None or p in known else E(p)
            for p in pe)
        out.append(f"<p class='hint'>Same industry on this site: {links}</p>")
    if k.get("customers"):
        out.append(f"<h3>Big customers</h3><p>{E(k['customers'])}</p>")
    src = []
    if w.get("wiki"):
        src.append(f"<a href='https://en.wikipedia.org/wiki/{E(urllib.parse.quote(w['wiki'].replace(' ', '_')))}'>"
                   "Wikipedia</a>")
    if k.get("url"):
        src.append(f"<a href='{E(k['url'])}'>annual report ({E(k.get('form') or '10-K')}, filed "
                   f"{E(k.get('filed') or '')})</a>")
    if w.get("website"):
        src.append(f"<a href='{E(w['website'])}'>company website</a>")
    out.append("<p class='hint'>Sources: " + (", ".join(src) or "public filings") +
               ". Sales splits come straight from the annual report's tagged numbers.</p></section>")
    return "".join(out)


CSS = """
.biz .lede{font-size:18px;font-weight:650;margin:0 0 8px;line-height:1.4}
.biz h3{font-size:15px;margin:16px 0 6px}
.biz .facts{grid-template-columns:repeat(4,1fr)}
.biz .quote{border-left:3px solid var(--line);padding-left:12px;color:var(--mut);margin:0}
.biz .cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:4px 18px}
.biz .chips{display:flex;flex-wrap:wrap;gap:6px}
.biz .bar{display:grid;grid-template-columns:minmax(110px,1.7fr) 1.6fr 44px 60px;gap:8px;align-items:center;font-size:14px;padding:3px 0}
.biz .bar .n{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.biz .bar .track{background:var(--card2);border-radius:6px;height:10px;overflow:hidden}
.biz .bar .track i{display:block;height:100%;background:var(--acc);border-radius:6px}
.biz .bar b{text-align:right;font-variant-numeric:tabular-nums}.biz .bar .v{color:var(--mut);font-size:12px;text-align:right}
.what{font-size:14px;margin:8px 0 2px;line-height:1.4}.what+.hint{margin:0 0 6px;font-size:13px}
@media (max-width:640px){.biz .facts{grid-template-columns:repeat(2,1fr)}
.biz .bar{grid-template-columns:minmax(80px,1fr) 1.3fr 40px}.biz .bar .v{display:none}}
"""


# ------------------------------------------------------------------ selftest

FIXTURE_DOC = """<html><body><div style='display:none'><ix:header><ix:resources>
<xbrli:context id="c-1"><xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier></xbrli:entity>
<xbrli:period><xbrli:startDate>2024-10-01</xbrli:startDate><xbrli:endDate>2025-09-27</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:context id="c-old"><xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier></xbrli:entity>
<xbrli:period><xbrli:startDate>2023-10-01</xbrli:startDate><xbrli:endDate>2024-09-28</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:context id="c-p"><xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier><xbrli:segment>
<xbrldi:explicitMember dimension="srt:ProductOrServiceAxis">us-gaap:ProductMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity>
<xbrli:period><xbrli:startDate>2024-10-01</xbrli:startDate><xbrli:endDate>2025-09-27</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:context id="c-ph"><xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier><xbrli:segment>
<xbrldi:explicitMember dimension="srt:ProductOrServiceAxis">aapl:IPhoneMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity>
<xbrli:period><xbrli:startDate>2024-10-01</xbrli:startDate><xbrli:endDate>2025-09-27</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:context id="c-mac"><xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier><xbrli:segment>
<xbrldi:explicitMember dimension="srt:ProductOrServiceAxis">aapl:MacMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity>
<xbrli:period><xbrli:startDate>2024-10-01</xbrli:startDate><xbrli:endDate>2025-09-27</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:context id="c-svc"><xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier><xbrli:segment>
<xbrldi:explicitMember dimension="srt:ProductOrServiceAxis">us-gaap:ServiceMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity>
<xbrli:period><xbrli:startDate>2024-10-01</xbrli:startDate><xbrli:endDate>2025-09-27</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:context id="c-am"><xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier><xbrli:segment>
<xbrldi:explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis">aapl:AmericasSegmentMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity>
<xbrli:period><xbrli:startDate>2024-10-01</xbrli:startDate><xbrli:endDate>2025-09-27</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:context id="c-eu"><xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier><xbrli:segment>
<xbrldi:explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis">aapl:EuropeSegmentMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity>
<xbrli:period><xbrli:startDate>2024-10-01</xbrli:startDate><xbrli:endDate>2025-09-27</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:context id="c-x"><xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier><xbrli:segment>
<xbrldi:explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis">aapl:EuropeSegmentMember</xbrldi:explicitMember>
<xbrldi:explicitMember dimension="srt:ProductOrServiceAxis">aapl:MacMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity>
<xbrli:period><xbrli:startDate>2024-10-01</xbrli:startDate><xbrli:endDate>2025-09-27</xbrli:endDate></xbrli:period></xbrli:context>
</ix:resources></ix:header></div>
<p>Table of Contents</p><p>Item 1. Business</p><p>3</p><p>Item 1A. Risk Factors</p><p>9</p>
<p><b>Item 1. Business</b></p>
<p>This Annual Report on Form 10-K contains forward-looking statements that involve risks and uncertainties, and this sentence is long enough to count as a paragraph if it weren't boilerplate.</p>
<p>The Company designs, manufactures and markets smartphones, personal computers, tablets, wearables and accessories, and sells a variety of related services. The Company's fiscal year is the 52- or 53-week period that ends on the last Saturday of September.</p>
<p>Competition</p>
<p>The markets for the Company's products and services are highly competitive, and are characterized by aggressive price competition and rapid technological change. Its competitors include Samsung and Google.</p>
<p>As of September 27, 2025, the Company had approximately 166,000 full-time equivalent employees.</p>
<p>No single customer accounted for more than 10% of net sales, but one distributor accounted for 12% of net sales in 2025.</p>
<p><b>Item 1A. Risk Factors</b></p><p>Lots of risk text here that goes on.</p>
<table><tr><td>Net sales</td><td><ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="c-1" unitRef="usd" decimals="-6" scale="6" format="ixt:num-dot-decimal">400,000</ix:nonFraction></td>
<td><ix:nonFraction contextRef="c-old" name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" scale="6">390,000</ix:nonFraction></td></tr>
<tr><td><ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="c-p" scale="6">300,000</ix:nonFraction></td>
<td><ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="c-ph" scale="6">200,000</ix:nonFraction></td>
<td><ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="c-mac" scale="6">100,000</ix:nonFraction></td>
<td><ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="c-svc" scale="6">100,000</ix:nonFraction></td>
<td><ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="c-am" scale="6">250,000</ix:nonFraction></td>
<td><ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="c-eu" scale="6">150,000</ix:nonFraction></td>
<td><ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="c-x" scale="6">40,000</ix:nonFraction></td></tr></table>
</body></html>"""

FIXTURE_LAB = """<link:linkbase><link:labelLink>
<link:loc xlink:type="locator" xlink:href="aapl-20250927.xsd#aapl_IPhoneMember" xlink:label="loc_iphone"/>
<link:label xlink:type="resource" xlink:label="lab_iphone" xlink:role="http://www.xbrl.org/2003/role/label" xml:lang="en-US">iPhone [Member]</link:label>
<link:labelArc xlink:type="arc" xlink:from="loc_iphone" xlink:to="lab_iphone"/>
</link:labelLink></link:linkbase>"""


def selftest():
    import duckdb
    import secextras
    assert sentences("Apple Inc. is a U.S. company. It makes phones.") == \
        ["Apple Inc. is a U.S. company.", "It makes phones."], sentences("Apple Inc. is a U.S. company. It makes phones.")
    q = sparql_query(["BRK.B", "AAPL"])
    assert '"BRK-B"' in q and '"BRK.B"' in q and "wd:Q13677" in q
    sp = {"results": {"bindings": [
        {"tkr": {"value": "AAPL"}, "item": {"value": "http://www.wikidata.org/entity/Q312"},
         "article": {"value": "https://en.wikipedia.org/wiki/Apple_Inc."}},
        {"tkr": {"value": "BRK-B"}, "item": {"value": "http://www.wikidata.org/entity/Q217583"}}]}}
    assert parse_sparql(sp, ["AAPL", "BRK.B"]) == {"AAPL": ("Q312", "Apple Inc."), "BRK.B": ("Q217583", None)}
    ent = {"descriptions": {"en": {"value": "American technology company"}}, "claims": {
        "P452": [{"rank": "normal", "mainsnak": {"snaktype": "value", "datavalue": {"value": {"id": "Q11661"}}}}],
        "P571": [{"rank": "normal", "mainsnak": {"snaktype": "value", "datavalue": {"value": {"time": "+1976-04-01T00:00:00Z"}}}}],
        "P169": [{"rank": "normal", "mainsnak": {"snaktype": "value", "datavalue": {"value": {"id": "Q19837"}}},
                  "qualifiers": {"P582": [{"datavalue": {"value": {"time": "+2011-08-24T00:00:00Z"}}}]}},
                 {"rank": "normal", "mainsnak": {"snaktype": "value", "datavalue": {"value": {"id": "Q265852"}}}}],
        "P1128": [{"rank": "normal", "mainsnak": {"snaktype": "value", "datavalue": {"value": {"amount": "+100000"}}},
                   "qualifiers": {"P585": [{"datavalue": {"value": {"time": "+2020-01-01T00:00:00Z"}}}]}},
                  {"rank": "normal", "mainsnak": {"snaktype": "value", "datavalue": {"value": {"amount": "+164000"}}},
                   "qualifiers": {"P585": [{"datavalue": {"value": {"time": "+2024-09-28T00:00:00Z"}}}]}},
                  {"rank": "deprecated", "mainsnak": {"snaktype": "value", "datavalue": {"value": {"amount": "+1"}}}}],
        "P856": [{"rank": "normal", "mainsnak": {"snaktype": "value", "datavalue": {"value": "https://www.apple.com/"}}}]}}
    f = parse_entity(ent)
    assert f["founded"] == 1976 and f["ceo"] == ["Q265852"] and f["employees"] == 164000, f
    assert f["employees_year"] == "2024" and f["website"] == "https://www.apple.com/" and f["industry"] == ["Q11661"]

    text = secextras.html_text(FIXTURE_DOC)
    sec = business_section(text)
    ov = overview(sec)
    assert ov.startswith("The Company designs, manufactures") and "forward-looking" not in ov, ov
    assert "Samsung and Google" in after_heading(sec, "competition")
    assert employees(sec) == 166000
    assert "distributor accounted for 12%" in big_customers(sec)
    split = sales_split(FIXTURE_DOC)
    assert split["year_end"] == "2025-09-27" and split["total"] == 400e9, split
    prods = dict(split["products"])
    assert "us-gaap:ProductMember" not in prods and prods["aapl:IPhoneMember"] == 200e9, prods  # parent dropped
    assert dict(split["segments"]) == {"aapl:AmericasSegmentMember": 250e9, "aapl:EuropeSegmentMember": 150e9}
    labels = parse_labels(FIXTURE_LAB)
    assert labels == {"aapl:IPhoneMember": "iPhone"}, labels
    assert humanize("aapl:IPhoneMember", labels) == "iPhone"
    assert humanize("aapl:AmericasSegmentMember") == "Americas" and humanize("country:CN") == "China"
    assert humanize("us-gaap:NonUsMember") == "Outside the US"
    assert humanize("abc:CloudAndAIServicesMember") == "Cloud And AI Services"
    for kk in AXES:
        if split.get(kk):
            split[kk] = [(humanize(m, labels), v) for m, v in split[kk]]

    con = duckdb.connect()
    companyintel.ensure_tables(con)
    wiki = {"desc": "American technology company",
            "extract": "Apple Inc. is an American multinational technology company headquartered in Cupertino, "
                       "California. It is best known for the iPhone. Apple was founded in 1976.",
            "wiki": "Apple Inc.", "founded": 1976, "hq": ["Cupertino"], "ceo": ["Tim Cook"],
            "employees": 164000, "industry": ["consumer electronics"], "products": ["iPhone", "Mac"],
            "founders": [], "website": "https://www.apple.com/"}
    tenk = {"form": "10-K", "filed": "2025-10-31", "acc": "1", "url": "https://sec/x", "overview": ov,
            "competition": "Its competitors include Samsung.", "customers": None, "employees": 166000,
            "split": split}
    companyintel._save(con, "AAPL", "wiki", 1, json.dumps(wiki))
    companyintel._save(con, "AAPL", "tenk", 1, json.dumps(tenk))
    b = snapshot(con, "AAPL")
    assert oneliner(b) == "An American multinational technology company headquartered in Cupertino, California.", oneliner(b)
    assert money_line(b) == ("Most of its sales come from iPhone (50%), then Mac (25%) and Services (25%). "
                             "Most of its sales (62%) are in the Americas."), money_line(b)
    assert oneliner({"wiki": {"desc": "maker of widgets and gadgets"}}) == "Maker of widgets and gadgets."
    assert oneliner({"tenk": {"overview": "We make widgets. More text."}}) == "We make widgets."
    assert oneliner({}) is None
    con.execute("CREATE TABLE companies (tkr VARCHAR PRIMARY KEY, cik BIGINT, name VARCHAR, sic INTEGER, "
                "sic_desc VARCHAR, fetched_on DATE)")
    con.execute("INSERT INTO companies VALUES ('AAPL',1,'Apple',3571,'computers',NULL), "
                "('DELL',2,'Dell',3571,'computers',NULL), ('XOM',3,'Exxon',2911,'oil',NULL)")
    assert peers(con, "AAPL") == ["DELL"]
    h = panel(con, "AAPL", {"name": "Apple", "revenue": 400e9, "net_income": 100e9}, known={"DELL"})
    assert "What it does" in h and "headquartered in Cupertino" in h and "best known for the iPhone" in h
    assert "25¢ of profit" in h and "Sales by product (2025)" in h and "Sales by region" in h
    assert "iPhone" in h and "50%" in h and "Tim Cook" in h and "166,000" in h and "$2.4M" in h, h
    assert "In its own words" in h and "Samsung" in h and "href='../s/DELL.html'" in h
    assert "Same industry on this site" in h and "Wikipedia</a>" in h and "<script" not in h
    assert panel(con, "ZZZ") == ""
    assert looks_like_company({"desc": "American bank holding company"})
    assert not looks_like_company({"desc": "species of fruit", "extract": "The apple is a fruit."})
    print("business selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    elif len(sys.argv) > 1:
        import duckdb
        from schema import DB_PATH
        c = duckdb.connect(DB_PATH)
        t = sys.argv[1].upper()
        refresh(c, [t])
        print(json.dumps(snapshot(c, t), indent=1))
    else:
        print(__doc__)
