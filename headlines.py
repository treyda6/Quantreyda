"""Hourly headlines: news about the stocks the site tracks, the market and the
economy, from free RSS feeds. No API keys.

The daily build writes site/tracked.json (the stocks that have a full research
page, ideas first). Every hour, .github/workflows/news.yml runs this script
against that list and pushes news.json to the `news-feed` branch; the News
page and each stock page read it from there in the browser, so headlines stay
fresh without rebuilding the whole site.

Sources (each one is optional; a feed that's down is just skipped):
  Google News      searches for the tracked companies, plus its Business section
  CNBC, MarketWatch, Yahoo Finance   market and economy headlines
  Federal Reserve  press releases (rate decisions, speeches)
  BLS              new economic data (jobs, inflation)
  SEC EDGAR        8-Ks filed in the last few hours by tracked companies
                   (needs SEC_CONTACT_EMAIL, like the other SEC scrapers)

Your private WATCHLIST is never used here: only stocks already public on the
site are searched for.

Usage:
    py headlines.py site/tracked.json site/news.json [old-news.json]
    py headlines.py --selftest   # offline check
"""

import datetime
import email.utils
import html
import json
import os
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET

import requests

KEEP_HOURS = 72          # how far back the feed goes
MAX_ITEMS = 400
SEARCH_TOP = 60          # companies searched for by name each hour (ideas first)
SEARCH_BATCH = 6         # companies per Google News query
UA = "Mozilla/5.0 (compatible; quantreyda-news/1.0)"

GNEWS = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
FEEDS = [  # (category, source name, url)
    ("markets", "Google News", "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-US&gl=US&ceid=US:en"),
    ("markets", "CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"),
    ("markets", "CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=15839069"),
    ("markets", "MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("markets", "MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_marketpulse"),
    ("markets", "Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
    ("economy", "CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258"),
    ("economy", "Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("economy", "BLS", "https://www.bls.gov/feed/bls_latest.rss"),
    ("economy", "Google News", GNEWS.format(q=urllib.parse.quote(
        '"Federal Reserve" OR inflation OR "jobs report" OR "interest rates" OR recession when:1d'))),
]
SEC_8K = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K"
          "&company=&dateb=&owner=include&start=0&count=100&output=atom")
ATOM = "{http://www.w3.org/2005/Atom}"

# Words that are tickers but also ordinary headline words; never matched by symbol.
STOP = {"A", "AI", "ALL", "AN", "ARE", "AT", "BE", "BIG", "CAN", "CEO", "CFO", "CPI", "DO", "EV",
        "FOR", "GDP", "GO", "HAS", "IPO", "IT", "KEY", "LOW", "NEW", "NOW", "ON", "ONE", "OR",
        "OUT", "PM", "RUN", "SEC", "SO", "TV", "UK", "US", "USA", "WELL", "ETF", "FED", "IRS",
        "EU", "OK", "BEST", "GOOD", "REAL", "TRUE", "SAFE", "PLAY", "LIFE", "FAST", "HOPE"}
SUFFIX = re.compile(r"(?i)[,.]?\s+(inc|incorporated|corp|corporation|co|company|ltd|limited|plc|"
                    r"holdings?|group|n\.?v|s\.?a|ag|se|lp|llc|trust|class [a-c]|the|/[a-z]{2}/?)\.?$")


def clean_name(name):
    """'APPLE INC.' -> 'Apple', 'Alphabet Inc. Class A' -> 'Alphabet'."""
    n = re.sub(r"\s+", " ", (name or "").replace("&amp;", "&")).strip()
    for _ in range(4):
        n2 = SUFFIX.sub("", n).strip(" ,.")
        if n2 == n:
            break
        n = n2
    if n.isupper() and len(n) > 4:
        n = " ".join(w.capitalize() if len(w) > 3 or not w.isalpha() else w.capitalize()
                     for w in n.split())
    return n


def _when(text):
    """RSS/Atom date -> aware UTC datetime, or None."""
    if not text:
        return None
    text = text.strip()
    try:
        d = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            d = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=datetime.timezone.utc)
    return d.astimezone(datetime.timezone.utc)


def _text(s):
    s = re.sub(r"<[^>]+>", " ", html.unescape(s or ""))
    return re.sub(r"\s+", " ", s).strip()


def parse_feed(xml, cat, source):
    """Items from one RSS 2.0 or Atom feed: dicts with t(itle), u(rl), s(ource), d(ate), c(ategory)."""
    out = []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return out
    for it in root.iter("item"):
        title = _text(it.findtext("title"))
        src = _text(it.findtext("source")) or source
        # Google News titles end in " - Publisher"; show the publisher instead
        if src and src != source and title.endswith(" - " + src):
            title = title[: -len(src) - 3].strip()
        link = (it.findtext("link") or "").strip()
        d = _when(it.findtext("pubDate") or it.findtext("{http://purl.org/dc/elements/1.1/}date"))
        if title and link.startswith("http"):
            out.append({"t": title, "u": link, "s": src, "d": d, "c": cat})
    for e in root.iter(ATOM + "entry"):
        title = _text(e.findtext(ATOM + "title"))
        ln = e.find(ATOM + "link")
        link = ln.get("href", "") if ln is not None else ""
        d = _when(e.findtext(ATOM + "updated") or e.findtext(ATOM + "published"))
        if title and link.startswith("http"):
            out.append({"t": title, "u": link, "s": source, "d": d, "c": cat,
                        "x": _text(e.findtext(ATOM + "summary"))})
    return out


class Matcher:
    """Finds which tracked stocks a headline is about (by company name or $TICKER)."""

    def __init__(self, tracked):
        self.pats = []
        firsts = [clean_name(r.get("name")).split(" ")[0].lower() for r in tracked]
        for row in tracked:
            t = row["tkr"]
            alts = []
            name = clean_name(row.get("name"))
            if len(name) >= 4:
                alts.append(re.escape(name))
                first = name.split()[0]
                # "Nvidia Corp" -> also match "Nvidia"; skip generic first words
                if len(first) >= 5 and firsts.count(first.lower()) == 1 and first.lower() not in {"united", "american", "general",
                                                              "first", "national", "global",
                                                              "international", "southern",
                                                              "northern", "western", "eastern"}:
                    alts.append(re.escape(first))
            alts = [f"(?i:\\b{a}\\b)" for a in dict.fromkeys(alts)]
            sym = re.escape(t)
            alts.append(f"\\${sym}\\b")
            alts.append(f"\\({sym}\\)")
            alts.append(f"(?:NYSE|NASDAQ|Nasdaq|NYSEARCA|AMEX):\\s?{sym}\\b")
            if len(t) >= 3 and t not in STOP:
                alts.append(f"(?<![\\w$]){sym}(?![\\w])")
            self.pats.append((t, re.compile("|".join(alts))))

    def find(self, text):
        return [t for t, p in self.pats if p.search(text)]


def search_queries(tracked):
    """Google News searches for the top tracked companies, a few per query."""
    names = []
    for row in tracked[:SEARCH_TOP]:
        n = clean_name(row.get("name")) or row["tkr"]
        names.append(f'"{n}"' if " " in n else n)
    for i in range(0, len(names), SEARCH_BATCH):
        q = "(" + " OR ".join(names[i:i + SEARCH_BATCH]) + ") stock when:1d"
        yield GNEWS.format(q=urllib.parse.quote(q))


def sec_8ks(xml, tracked):
    """Fresh 8-Ks from tracked companies, labeled in plain English (news.py's labels)."""
    import news
    by_cik = {int(r["cik"]): r["tkr"] for r in tracked if r.get("cik")}
    out = []
    for it in parse_feed(xml, "sec", "SEC filing"):
        m = re.search(r"\((\d{6,10})\)", it["t"])
        if not m or int(m.group(1)) not in by_cik:
            continue
        tkr = by_cik[int(m.group(1))]
        items = ",".join(re.findall(r"Item (\d\.\d\d)", it.get("x", "")))
        what = news.label("8-K", items) or "Company news (8-K)"
        out.append({"t": f"{tkr}: {what}", "u": it["u"], "s": "SEC filing", "d": it["d"],
                    "c": "sec", "k": [tkr]})
    return out


def _key(title):
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()[:90]


def merge(items, old, now, matcher):
    """Tag, dedupe, drop stale, newest first. Old items carry over so the feed builds up."""
    cutoff = now - datetime.timedelta(hours=KEEP_HOURS)
    seen, out = {}, []
    for it in items + old:
        d = it["d"] if isinstance(it["d"], datetime.datetime) else _when(it.get("d"))
        if d is None:
            d = now  # undated: treat as just seen
        if d < cutoff or d > now + datetime.timedelta(hours=2):
            continue
        k = _key(it["t"])
        if not k:
            continue
        tk = it.get("k") or matcher.find(it["t"])
        if k in seen:  # same story from two feeds: keep the first, merge tags
            prev = seen[k]
            prev["k"] = list(dict.fromkeys(prev.get("k", []) + tk))
            continue
        row = {"t": it["t"][:220], "u": it["u"], "s": it["s"][:40], "d": d, "c": it["c"]}
        if tk:
            row["k"] = tk[:6]
        seen[k] = row
        out.append(row)
    out.sort(key=lambda r: r["d"], reverse=True)
    for r in out:
        r["d"] = r["d"].strftime("%Y-%m-%dT%H:%M:%SZ")
    return out[:MAX_ITEMS]


def fetch_all(tracked, get=None):
    """Every source; returns (items, {source url: status})."""
    s = requests.Session()
    s.headers["User-Agent"] = UA
    get = get or (lambda url, **kw: s.get(url, timeout=20, **kw))
    items, status = [], {}

    def pull(url, source, headers=None):
        try:
            r = get(url, headers=headers) if headers else get(url)
            if r.status_code != 200:
                print(f"  {source}: HTTP {r.status_code}")
                return ""
            return r.content
        except Exception as e:
            print(f"  {source}: {type(e).__name__}")
            return ""

    for cat, source, url in FEEDS:
        got = parse_feed(pull(url, source), cat, source)
        items += got
        status[f"{source} · {cat}"] = status.get(f"{source} · {cat}", 0) + len(got)
    for url in search_queries(tracked):
        got = parse_feed(pull(url, "Google News"), "stocks", "Google News")
        items += got
        status["Google News · your stocks"] = status.get("Google News · your stocks", 0) + len(got)
        time.sleep(1)  # be gentle
    email_ = os.environ.get("SEC_CONTACT_EMAIL", "").strip()
    if "@" in email_ and tracked:
        got = sec_8ks(pull(SEC_8K, "SEC", {"User-Agent": f"quantreyda/0.1 ({email_})"}) or b"<x/>",
                      tracked)
        items += got
        status["SEC EDGAR · 8-K"] = len(got)
    return items, status


def build(tracked, old=(), now=None, get=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    items, status = fetch_all(tracked, get)
    m = Matcher(tracked)
    rows = merge(items, list(old), now, m)
    return {"generated": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "sources": status, "items": rows}


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def main(tracked_path, out_path, old_path=None):
    tracked = _load(tracked_path, [])
    if isinstance(tracked, dict):
        tracked = tracked.get("stocks", [])
    old = _load(old_path, {}).get("items", []) if old_path else []
    data = build(tracked, old)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))
    tagged = sum(1 for r in data["items"] if r.get("k"))
    print(f"headlines: {len(data['items'])} items ({tagged} about tracked stocks) "
          f"from {len(tracked)} tracked stocks -> {out_path}")


# ---------------------------------------------------------------- site

def tracked_list(con, pages):
    """[{tkr, name, cik}] for the stocks with pages, in page order (ideas first)."""
    out = []
    for t in pages:
        name = cik = None
        for sql in ("SELECT company, NULL FROM health WHERE tkr = ?",
                    "SELECT name, cik FROM companies WHERE tkr = ?"):
            try:
                r = con.execute(sql, [t]).fetchone()
            except Exception:  # table not there yet
                r = None
            if r:
                name = name or r[0]
                cik = cik or r[1]
        out.append({"tkr": t, "name": name or "", "cik": int(cik) if cik else None})
    return out


def remote_url():
    """Where the hourly workflow publishes news.json (empty when built off GitHub)."""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    return f"https://raw.githubusercontent.com/{repo}/news-feed/news.json" if repo else ""


CSS = """
.hl-tabs{display:flex;gap:6px;flex-wrap:wrap;margin:6px 0 12px}
.hl-tabs button{border:1px solid var(--line);background:var(--card);color:var(--mut);border-radius:999px;
padding:6px 12px;font:600 13px inherit;cursor:pointer}.hl-tabs button.on{background:var(--chip);color:var(--fg)}
.hl-tabs input{flex:1;min-width:180px;border:1px solid var(--line);background:var(--card);color:var(--fg);
border-radius:999px;padding:6px 14px;font:14px inherit}
.hl-list{list-style:none;margin:0;padding:0}
.hl-list li{padding:11px 2px;border-bottom:1px solid var(--line)}
.hl-list li:last-child{border-bottom:0}
.hl-list a.t{color:var(--fg);text-decoration:none;font-weight:600;line-height:1.35}
.hl-list a.t:hover{text-decoration:underline}
.hl-m{color:var(--mut);font-size:12.5px;margin-top:3px;display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.hl-m .k{font-size:11.5px;font-weight:700;background:var(--chip);color:var(--fg);border-radius:6px;
padding:1px 7px;text-decoration:none}
.hl-m .cat{font-size:11px;text-transform:uppercase;letter-spacing:.4px}
.hl-m .cat.sec{color:var(--warn)}.hl-m .cat.economy{color:var(--ins)}.hl-m .cat.stocks{color:var(--acc)}
.hl-empty{color:var(--mut);padding:10px 2px}
"""

JS = r"""<script>(function(){
document.querySelectorAll('.hl-feed').forEach(function(box){
  var d=box.dataset, list=box.querySelector('.hl-list'), info=box.querySelector('.hl-upd');
  var pages={}; try{pages=JSON.parse(d.pages||'{}')}catch(e){}
  var tab='all', q='', feed=null, limit=+d.limit||0;
  function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
  function ago(iso){var m=Math.max(0,(Date.now()-Date.parse(iso))/60000);
    return m<60?Math.round(m)+' min ago':m<1440?Math.round(m/60)+' h ago':Math.round(m/1440)+' d ago'}
  function get(u){if(!u)return Promise.resolve(null);
    return fetch(u+(u.indexOf('?')<0?'?':'&')+'v='+Math.floor(Date.now()/300000),{cache:'no-store'})
      .then(function(r){return r.ok?r.json():null}).catch(function(){return null})}
  var NAMES={stocks:'Tracked stock',markets:'Markets',economy:'Economy',sec:'SEC filing'};
  function show(){
    if(!feed){list.innerHTML="<li class='hl-empty'>Headlines couldn't load. Try again in a few minutes.</li>";return}
    var rows=feed.items.filter(function(r){
      var k=r.k||[];
      if(d.tkr&&k.indexOf(d.tkr)<0)return false;
      if(tab==='stocks'&&!k.length)return false;
      if(tab!=='all'&&tab!=='stocks'&&r.c!==tab)return false;
      if(q){var hay=(r.t+' '+r.s+' '+k.join(' ')).toLowerCase();if(hay.indexOf(q)<0)return false}
      return true});
    if(limit)rows=rows.slice(0,limit);
    if(d.tkr&&!rows.length){box.style.display='none';return}
    box.style.display='';
    list.innerHTML=rows.length?rows.map(function(r){
      var chips=(r.k||[]).map(function(t){return pages[t]
        ?"<a class='k' href='"+esc((d.root||'')+'s/'+pages[t]+'.html')+"'>"+esc(t)+"</a>"
        :"<span class='k'>"+esc(t)+"</span>"}).join('');
      return "<li><a class='t' href='"+esc(r.u)+"' target='_blank' rel='noopener'>"+esc(r.t)+"</a>"
        +"<div class='hl-m'><span class='cat "+esc(r.c)+"'>"+esc(NAMES[r.c]||r.c)+"</span><span>"+esc(r.s)
        +"</span><span>· "+ago(r.d)+"</span>"+chips+"</div></li>"}).join('')
      :"<li class='hl-empty'>Nothing matches.</li>";
    if(info)info.textContent='Updated '+ago(feed.generated)+' · '+feed.items.length+' headlines from the last 3 days';
  }
  function load(){Promise.all([get(d.remote),get(d.local)]).then(function(r){
    var a=r[0],b=r[1];feed=(a&&b)?(a.generated>=b.generated?a:b):(a||b);show()})}
  box.querySelectorAll('.hl-tabs button').forEach(function(b){b.onclick=function(){
    tab=b.dataset.tab;box.querySelectorAll('.hl-tabs button').forEach(function(x){x.classList.toggle('on',x===b)});show()}});
  var inp=box.querySelector('.hl-tabs input');if(inp)inp.oninput=function(){q=inp.value.trim().toLowerCase();show()};
  load();setInterval(load,15*60000);
});})()</script>"""


def _box(root, tkr="", limit=0, pages=None, extra=""):
    attrs = (f" data-remote='{html.escape(remote_url())}' data-local='{root}news.json'"
             f" data-root='{root}'")
    if tkr:
        attrs += f" data-tkr='{html.escape(tkr)}'"
    if limit:
        attrs += f" data-limit='{limit}'"
    if pages:
        attrs += f" data-pages='{html.escape(json.dumps(pages))}'"
    return (f"<style>{CSS}</style><div class='hl-feed'{attrs}>{extra}"
            f"<ul class='hl-list'><li class='hl-empty'>Loading headlines…</li></ul></div>{JS}")


def page_body(pages=None):
    """The News page. `pages` is {tkr: slug} so ticker chips link to stock pages."""
    tabs = ("<div class='hl-tabs'>"
            "<button data-tab='all' class='on' type='button'>All</button>"
            "<button data-tab='stocks' type='button'>Tracked stocks</button>"
            "<button data-tab='markets' type='button'>Markets</button>"
            "<button data-tab='economy' type='button'>Economy &amp; Fed</button>"
            "<button data-tab='sec' type='button'>SEC filings</button>"
            "<input type='search' placeholder='Search headlines or a ticker…'></div>"
            "<p class='hint hl-upd'></p>")
    return ("<h1>News</h1><p class='mut'>Headlines about the stocks this site tracks, the market "
            "and the economy, refreshed every hour from free sources (Google News, CNBC, "
            "MarketWatch, Yahoo Finance, the Federal Reserve, the Bureau of Labor Statistics) "
            "plus brand-new SEC 8-K filings from tracked companies. A ticker chip means the "
            "headline names that company; tap it for the stock's page. Headlines are a "
            "starting point, not a reason to trade.</p>"
            f"<section class='panel'>{_box('', pages=pages, extra=tabs)}</section>")


def stock_panel(tkr, pages=None):
    """Headlines naming one stock, for its page. Hidden when there are none."""
    return ("<section class='panel'><h2 style='margin-top:0'>Latest headlines</h2>"
            "<p class='hint'>From the last 3 days, updated hourly. "
            "<a href='../news.html'>All news</a></p>"
            f"{_box('../', tkr=tkr, limit=8, pages=pages)}</section>")


def home_teaser(con=None, today=None):
    """Top 5 headlines for the home page's News box (home.py adds the heading)."""
    return _box("", limit=5)


# ---------------------------------------------------------------- selftest

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>
<item><title>Nvidia beats estimates as AI demand soars - Reuters</title>
<link>https://example.com/nv</link><pubDate>Fri, 27 Nov 2026 14:00:00 GMT</pubDate>
<source url="https://reuters.com">Reuters</source></item>
<item><title>Stocks slip; $COST and Apple lead losers</title><link>https://example.com/mk</link>
<pubDate>Fri, 27 Nov 2026 13:30:00 +0000</pubDate></item>
<item><title>IT spending outlook is NEW and ALL good</title><link>https://example.com/it</link>
<pubDate>Fri, 27 Nov 2026 12:00:00 GMT</pubDate></item>
<item><title>Ancient story</title><link>https://example.com/old</link>
<pubDate>Mon, 02 Nov 2026 12:00:00 GMT</pubDate></item>
<item><title>Bad link</title><link>javascript:alert(1)</link></item>
</channel></rss>"""

SEC = """<?xml version="1.0" encoding="ISO-8859-1"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>8-K - NVIDIA CORP (0001045810) (Filer)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/1045810/x-index.htm"/>
<summary type="html"> &lt;b&gt;Filed:&lt;/b&gt; 2026-11-27 &lt;b&gt;AccNo:&lt;/b&gt; 1 &lt;br&gt;Item 2.02: Results
&lt;br&gt;Item 9.01: Exhibits</summary><updated>2026-11-27T10:05:00-05:00</updated></entry>
<entry><title>8-K - SOMEONE ELSE INC (0000000001) (Filer)</title>
<link href="https://www.sec.gov/y"/><updated>2026-11-27T10:00:00-05:00</updated></entry>
</feed>"""


def selftest():
    assert clean_name("APPLE INC") == "Apple", clean_name("APPLE INC")
    assert clean_name("Alphabet Inc. Class A") == "Alphabet"
    assert clean_name("NVIDIA CORP /DE/") == "Nvidia", clean_name("NVIDIA CORP /DE/")
    assert clean_name("Costco Wholesale Corporation") == "Costco Wholesale"
    tracked = [{"tkr": "NVDA", "name": "NVIDIA CORP", "cik": 1045810},
               {"tkr": "AAPL", "name": "Apple Inc.", "cik": 320193},
               {"tkr": "COST", "name": "Costco Wholesale Corp"},
               {"tkr": "IT", "name": "Gartner Inc"}, {"tkr": "NEW", "name": "X"}]
    rows = parse_feed(RSS, "markets", "Test")
    assert rows[0]["t"] == "Nvidia beats estimates as AI demand soars" and rows[0]["s"] == "Reuters"
    assert len(rows) == 4, "the javascript: link is dropped"
    m = Matcher(tracked)
    assert m.find("Nvidia beats estimates") == ["NVDA"]
    assert set(m.find("Stocks slip; $COST and Apple lead losers")) == {"AAPL", "COST"}
    assert m.find("IT spending outlook is NEW and ALL good") == [], "stop-word tickers ignored"
    assert m.find("Pineapple prices") == [], "whole words only"
    sec = sec_8ks(SEC, tracked)
    assert len(sec) == 1 and sec[0]["t"] == "NVDA: Reported quarterly results", sec
    now = datetime.datetime(2026, 11, 27, 16, tzinfo=datetime.timezone.utc)
    old = [{"t": "Nvidia beats estimates as AI demand soars", "u": "https://dup", "s": "AP",
            "d": "2026-11-27T13:00:00Z", "c": "stocks"},
           {"t": "Yesterday's Fed note", "u": "https://fed", "s": "Federal Reserve",
            "d": "2026-11-26T18:00:00Z", "c": "economy"}]
    out = merge(rows + sec, old, now, m)
    titles = [r["t"] for r in out]
    assert "Ancient story" not in titles and titles.count("Nvidia beats estimates as AI demand soars") == 1
    assert titles[0] == "NVDA: Reported quarterly results", titles  # 15:05 UTC, newest
    assert "Yesterday's Fed note" in titles, "old items carry over"
    assert next(r for r in out if r["t"].startswith("Nvidia"))["k"] == ["NVDA"]

    class R:
        def __init__(self, body, code=200):
            self.content, self.status_code = body.encode(), code

    def fake_get(url, headers=None):
        if "sec.gov" in url:
            return R(SEC)
        if "federalreserve" in url:
            raise requests.ConnectionError("down")
        return R(RSS) if "cnbc" in url else R("", 503)

    os.environ.setdefault("SEC_CONTACT_EMAIL", "test@example.com")
    data = build(tracked, [], now, fake_get)
    assert data["items"] and data["sources"]["SEC EDGAR · 8-K"] == 1
    assert data["sources"]["Federal Reserve · economy"] == 0, "a dead feed is skipped, not fatal"
    json.dumps(data)
    import duckdb
    con = duckdb.connect()
    con.execute("CREATE TABLE companies (tkr VARCHAR, cik BIGINT, name VARCHAR)")
    con.execute("INSERT INTO companies VALUES ('NVDA', 1045810, 'NVIDIA CORP')")
    assert tracked_list(con, ["NVDA", "ZZZ"]) == [{"tkr": "NVDA", "name": "NVIDIA CORP", "cik": 1045810},
                                                  {"tkr": "ZZZ", "name": "", "cik": None}]
    body = page_body({"NVDA": "NVDA"})
    assert "hl-feed" in body and "data-local='news.json'" in body and "Tracked stocks" in body
    assert "data-tkr='NVDA'" in stock_panel("NVDA") and "../news.json" in stock_panel("NVDA")
    assert "data-limit='5'" in home_teaser(None, None)
    print("headlines selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    elif len(sys.argv) >= 3:
        main(*sys.argv[1:4])
    else:
        print(__doc__)
