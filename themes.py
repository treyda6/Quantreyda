"""Future themes: a study guide to where the economy might be heading.

Each theme is a big change that could run for a decade (AI, the power grid,
nuclear, robots, space…), with plain-English notes on why it matters, what
could go wrong and what to watch, the leaders, the smaller and riskier
players, and an ETF to study it through. On top of those notes, the page
shows live evidence from the tracker's own data:

  momentum      how the theme's stocks did over the past year vs the S&P 500
  attention     Wikipedia page views of the theme's articles, last 3 months
                vs the same months a year earlier (is the public catching on?)
  smart money   Congress, White House, insider and fund buys of its stocks
  Washington    bills moving in Congress and prediction-market questions that
                mention the theme

The theme lists are written by hand (THEMES below) and are a starting point
for research, not recommendations. Edit them freely.

    py themes.py               # refresh the Wikipedia attention numbers
    py themes.py --selftest
"""

import datetime
import html
import statistics
import sys
import urllib.parse

import pictures
import profiles

E = html.escape
WIKI_MONTHLY = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/"
                "all-access/user/{title}/monthly/{start:%Y%m}0100/{end:%Y%m}0100")

THEMES = [
    {"key": "ai", "name": "AI and data centers",
     "why": "Companies are spending hundreds of billions a year on chips, servers and buildings to "
            "train and run AI. Whoever sells the picks and shovels gets paid first.",
     "risk": "Spending can outrun what customers pay for AI. If the payoff disappoints, orders get "
             "cut fast and the most loved stocks fall the hardest.",
     "watch": "Big tech's capital spending plans each quarter; whether AI products bring in revenue; "
              "chip lead times.",
     "leaders": ["NVDA", "MSFT", "GOOGL", "AVGO", "TSM", "AMD"],
     "smaller": ["CRDO", "ALAB", "SOUN", "AI", "BBAI"], "etfs": ["SMH", "AIQ"],
     "wiki": ["Artificial intelligence", "Data center", "Large language model"],
     "words": ["artificial intelligence", " ai ", "data center", "semiconductor", "chip"]},
    {"key": "grid", "name": "Power demand and the grid",
     "why": "After 20 flat years, US electricity demand is rising again (data centers, factories, "
            "electric cars). Old transmission lines and transformers need replacing.",
     "risk": "Utilities are regulated, so profits are capped. Equipment makers are cyclical, and "
             "a lot of good news may already be in the price.",
     "watch": "Utility capital plans; transformer and turbine backlogs; data-center power deals.",
     "leaders": ["GEV", "ETN", "VRT", "PWR", "NEE"],
     "smaller": ["POWL", "AMSC", "BE", "PRIM"], "etfs": ["GRID"],
     "wiki": ["Electrical grid", "Electric power transmission"],
     "words": ["grid", "transmission", "electricity", "utility", "power plant"]},
    {"key": "nuclear", "name": "Nuclear power",
     "why": "Tech companies want carbon-free power that runs 24/7. Old plants are being restarted "
             "and small modular reactors (SMRs) promise cheaper, faster builds.",
     "risk": "No SMR is running commercially in the US yet. Delays and cost overruns are normal in "
             "nuclear, and many of the small names have no revenue.",
     "watch": "NRC licence approvals; signed power contracts; uranium prices; first SMR startups.",
     "leaders": ["CEG", "CCJ", "BWXT", "VST"],
     "smaller": ["OKLO", "SMR", "NNE", "LEU", "UEC"], "etfs": ["URA", "NLR"],
     "wiki": ["Small modular reactor", "Nuclear power"],
     "words": ["nuclear", "uranium", "reactor"]},
    {"key": "robots", "name": "Robots and automation",
     "why": "Fewer workers and higher wages push factories, warehouses and hospitals to automate. "
            "AI is making robots far more capable, including humanoid ones.",
     "risk": "Humanoid robots are years from real volume. Industrial automation rises and falls "
             "with factory spending.",
     "watch": "Warehouse automation orders; surgical robot procedures; humanoid pilot programs.",
     "leaders": ["ISRG", "ROK", "TER", "SYM", "CGNX"],
     "smaller": ["SERV", "RR", "PDYN"], "etfs": ["BOTZ", "ROBO"],
     "wiki": ["Humanoid robot", "Industrial robot"],
     "words": ["robot", "automation", "autonomous"]},
    {"key": "space", "name": "Space",
     "why": "Reusable rockets cut launch costs by about 90%. That opened satellite internet, "
            "Earth imaging and phone-to-satellite service as real businesses.",
     "risk": "Launches fail, schedules slip, and many space companies burn cash and sell new shares "
            "often.",
     "watch": "Launch cadence; government contracts (NASA, Space Force); paying subscribers.",
     "leaders": ["RKLB", "LHX", "NOC", "IRDM"],
     "smaller": ["ASTS", "LUNR", "PL", "RDW"], "etfs": ["UFO", "ARKX"],
     "wiki": ["Satellite internet constellation", "Reusable launch vehicle"],
     "words": ["space", "satellite", "nasa", "launch"]},
    {"key": "defense", "name": "Defense tech and drones",
     "why": "Wars in Ukraine and the Middle East showed cheap drones and software beating expensive "
            "hardware. Defense budgets are rising in the US and Europe.",
     "risk": "Budgets depend on politics. Contracts are lumpy and a lost bid can sink a small company.",
     "watch": "Defense budget bills; drone contract awards; Pentagon software deals.",
     "leaders": ["PLTR", "LMT", "RTX", "KTOS", "AVAV"],
     "smaller": ["RCAT", "ONDS"], "etfs": ["ITA", "SHLD"],
     "wiki": ["Unmanned combat aerial vehicle", "Loitering munition"],
     "words": ["defense", "drone", "military", "armed forces", "national security"]},
    {"key": "cyber", "name": "Cybersecurity",
     "why": "Every company is online and attacks keep getting worse. Security spending tends to "
            "hold up even when budgets get cut elsewhere.",
     "risk": "Crowded field with heavy price competition. Big platforms can squeeze small vendors.",
     "watch": "Major breaches; government security rules; customer growth at the leaders.",
     "leaders": ["CRWD", "PANW", "ZS", "FTNT", "NET"],
     "smaller": ["S", "RPD", "TENB", "VRNS"], "etfs": ["CIBR", "HACK"],
     "wiki": ["Computer security", "Ransomware"],
     "words": ["cyber", "ransomware", "hack"]},
    {"key": "quantum", "name": "Quantum computing",
     "why": "Quantum computers could crack problems normal computers can't (chemistry, materials, "
            "codes). Big tech and governments are spending heavily.",
     "risk": "Useful machines may still be many years away. The small pure plays have little "
             "revenue and very high valuations.",
     "watch": "Error-correction milestones; government funding; real paying customers.",
     "leaders": ["IBM", "GOOGL", "HON"],
     "smaller": ["IONQ", "RGTI", "QBTS", "QUBT"], "etfs": ["QTUM"],
     "wiki": ["Quantum computing"],
     "words": ["quantum"]},
    {"key": "obesity", "name": "Obesity drugs",
     "why": "GLP-1 drugs like Wegovy and Zepbound cause large weight loss and may help with heart, "
            "kidney and liver disease. It could become one of the biggest drug markets ever.",
     "risk": "Prices are under political pressure, side effects are still being studied, and most "
             "small challengers' drugs fail in trials.",
     "watch": "Trial results; Medicare coverage decisions; pill versions; supply.",
     "leaders": ["LLY", "NVO", "AMGN"],
     "smaller": ["VKTX", "ALT", "GPCR"], "etfs": ["XLV"],
     "wiki": ["Semaglutide", "Tirzepatide"],
     "words": ["obesity", "glp-1", "weight loss", "drug pric", "medicare"]},
    {"key": "genes", "name": "Gene editing and biotech tools",
     "why": "The first CRISPR cure (for sickle cell) was approved in 2023. Cheaper DNA reading and "
            "writing keeps opening new treatments.",
     "risk": "Treatments are very expensive and slow to sell. Small biotechs live trial to trial "
             "and sell shares often.",
     "watch": "FDA approvals; patients treated; cost per genome.",
     "leaders": ["VRTX", "CRSP", "ILMN"],
     "smaller": ["NTLA", "BEAM", "TWST", "PACB"], "etfs": ["ARKG", "XBI"],
     "wiki": ["CRISPR gene editing", "Gene therapy"],
     "words": ["gene", "biotech", "fda", "cure"]},
    {"key": "storage", "name": "Batteries and energy storage",
     "why": "Solar and wind need storage to run at night, and the grid needs batteries to smooth "
            "spikes in demand. Battery costs keep falling.",
     "risk": "Chinese competition is fierce, tax credits can change, and new battery chemistries "
             "often take longer than promised.",
     "watch": "Grid storage installs; tax-credit rules; solid-state battery production dates.",
     "leaders": ["TSLA", "FLNC", "ENPH"],
     "smaller": ["EOSE", "QS", "SLDP", "AMPX"], "etfs": ["LIT"],
     "wiki": ["Grid energy storage", "Solid-state battery"],
     "words": ["battery", "storage", "electric vehicle", "solar", "clean energy"]},
    {"key": "minerals", "name": "Critical minerals",
     "why": "Electric motors, chips and weapons need rare earths, lithium and copper, and China "
            "controls most processing. The US is paying to build its own supply.",
     "risk": "Mining is slow, costly and boom-or-bust. Prices swing wildly, and many juniors never "
             "reach production.",
     "watch": "Government stakes and price floors; China export rules; copper and lithium prices.",
     "leaders": ["MP", "ALB", "FCX"],
     "smaller": ["USAR", "LAC", "UAMY", "NB", "TMC"], "etfs": ["REMX", "COPX"],
     "wiki": ["Rare-earth element", "Lithium"],
     "words": ["mineral", "rare earth", "lithium", "mining", "copper"]},
    {"key": "chips", "name": "Chipmaking at home",
     "why": "The CHIPS Act and tariffs push chip factories back to the US. Every new factory needs "
            "billions in equipment.",
     "risk": "Chip demand is cyclical, export limits on China cut sales, and factories run late.",
     "watch": "New fab announcements; export rules; equipment orders.",
     "leaders": ["ASML", "AMAT", "LRCX", "MU", "INTC"],
     "smaller": ["AEHR", "ACMR", "COHU", "AXTI"], "etfs": ["SOXX"],
     "wiki": ["Semiconductor industry", "CHIPS and Science Act"],
     "words": ["semiconductor", "chips act", "chip", "export control", "tariff"]},
]


def tickers():
    """Every theme ticker (and its ETFs), for daily.py to fetch prices and checks for."""
    out = []
    for t in THEMES:
        out += t["leaders"] + t["smaller"] + t["etfs"]
    return list(dict.fromkeys(out))


# ------------------------------------------------------------------ attention

def ensure_table(con):
    con.execute("""CREATE TABLE IF NOT EXISTS theme_views (
        article VARCHAR, month DATE, views BIGINT, PRIMARY KEY (article, month))""")


def refresh(con, today=None):
    """Monthly Wikipedia page views for every theme article, last 26 months."""
    import requests
    from companyintel import UA, _wiki_get
    ensure_table(con)
    today = today or datetime.date.today()
    end = today.replace(day=1)
    start = (end - datetime.timedelta(days=800)).replace(day=1)
    s = requests.Session()
    s.headers["User-Agent"] = UA
    n = 0
    for th in THEMES:
        for art in th["wiki"]:
            url = WIKI_MONTHLY.format(title=urllib.parse.quote(art.replace(" ", "_"), safe=""),
                                      start=start, end=end)
            try:
                r = _wiki_get(s, url)
            except Exception as e:  # one bad article shouldn't stop the rest
                print(f"themes: {art} skipped ({type(e).__name__})")
                continue
            if r.status_code != 200:
                print(f"themes: no views for {art} ({r.status_code})")
                continue
            for i in r.json().get("items", []):
                ts = i["timestamp"]
                con.execute("INSERT OR REPLACE INTO theme_views VALUES (?, ?, ?)",
                            [art, datetime.date(int(ts[:4]), int(ts[4:6]), 1), i["views"]])
            n += 1
    print(f"themes: page views for {n} articles")


def attention(con, th):
    """(change vs a year ago, [monthly totals oldest first]) or (None, [])."""
    rows = profiles._all(con, """SELECT month, sum(views) FROM theme_views
                                 WHERE article IN (SELECT unnest(?)) GROUP BY 1 ORDER BY 1""",
                         [th["wiki"]])
    series = [v for _, v in rows]
    if len(series) < 15:
        return None, series[-24:]
    recent, year_ago = sum(series[-3:]), sum(series[-15:-12])
    return (recent / year_ago - 1 if year_ago else None), series[-24:]


# ------------------------------------------------------------------ evidence

def _returns(con, tkrs, today):
    """{tkr: (1-year return, price)} from cached prices."""
    since = today - datetime.timedelta(days=372)
    px = {}
    for t, d, c in profiles._all(con, """SELECT tkr, d, close FROM prices
                                         WHERE tkr IN (SELECT unnest(?)) AND d BETWEEN ? AND ?
                                         ORDER BY tkr, d""", [list(tkrs), since, today]):
        if c:
            px.setdefault(t, []).append((d, c))
    out = {}
    for t, s in px.items():
        first = s[0][0] if isinstance(s[0][0], datetime.date) else s[0][0].date()
        if len(s) > 200 and (today - first).days >= 330:
            out[t] = (s[-1][1] / s[0][1] - 1, s[-1][1])
    return out


def _buys(con, tkrs, today, days=90):
    """{tkr: [chip text]} smart-money buys of these tickers in the last `days`."""
    start = today - datetime.timedelta(days=days)
    out = {}
    for t, n in profiles._all(con, """SELECT tkr, count(DISTINCT last_name) FROM trades
            WHERE tx_type = 'Purchase' AND tkr IN (SELECT unnest(?)) AND filed_date BETWEEN ? AND ?
            GROUP BY 1""", [list(tkrs), start, today]):
        out.setdefault(t, []).append(f"Congress ×{n}")
    for t, n in profiles._all(con, """SELECT upper(trim(ticker)), count(DISTINCT owner_cik)
            FROM insider_trades WHERE trans_code = 'P' AND price_per_share > 0
              AND upper(trim(ticker)) IN (SELECT unnest(?))
              AND try_cast(filed_date AS DATE) BETWEEN ? AND ? GROUP BY 1""",
                              [list(tkrs), start, today]):
        out.setdefault(t, []).append(f"Insiders ×{n}")
    for t, n in profiles._all(con, """SELECT tkr, count(DISTINCT name) FROM wh_trades
            WHERE tx_type = 'Purchase' AND tkr IN (SELECT unnest(?)) AND filed BETWEEN ? AND ?
            GROUP BY 1""", [list(tkrs), start, today]):
        out.setdefault(t, []).append("White House")
    for t, fund, ch in profiles._all(con, """SELECT tkr, fund, change FROM fund_changes
            WHERE tkr IN (SELECT unnest(?)) AND change IN ('new', 'added')""", [list(tkrs)]):
        out.setdefault(t, []).append(f"{fund} {ch}")
    return out


def _mentions(con, th, today):
    """(bills, odds): recent bills and open prediction-market questions on the theme."""
    words = [w.lower() for w in th["words"]]
    since = today - datetime.timedelta(days=60)

    def hit(text):
        t = f" {(text or '').lower()} "
        return any(w in t for w in words)
    bills = [(title, url, action) for title, url, action, d in profiles._all(con,
             "SELECT title, url, action, action_date FROM bills WHERE action_date >= ? "
             "ORDER BY action_date DESC", [since]) if hit(title)][:4]
    odds = [(q, prob, url) for q, prob, url in profiles._all(con,
            "SELECT question, prob, url FROM pred_markets ORDER BY volume DESC NULLS LAST", [])
            if hit(q)][:3]
    return bills, odds


def evidence(con, today):
    """One dict per theme with its live numbers, plus a 0-5 'heat' score."""
    all_t = tickers() + ["SPY"]
    rets, buys = _returns(con, all_t, today), _buys(con, all_t, today)
    spy = rets.get("SPY", (None,))[0]
    out = []
    for th in THEMES:
        stocks = th["leaders"] + th["smaller"]
        r = [rets[t][0] for t in stocks if t in rets]
        med = statistics.median(r) if r else None
        vs = med - spy if med is not None and spy is not None else None
        att, series = attention(con, th)
        n_buys = sum(len(buys.get(t, [])) for t in stocks)
        bills, odds = _mentions(con, th, today)
        heat = ((vs is not None and vs > 0) + (vs is not None and vs > 0.2)
                + (att is not None and att > 0.15) + (n_buys >= 3) + bool(bills))
        out.append({**th, "median_1y": med, "vs_spy": vs, "attention": att, "series": series,
                    "buys": buys, "n_buys": n_buys, "rets": rets, "bills": bills, "odds": odds,
                    "heat": int(heat)})
    return out


# ------------------------------------------------------------------ page

CSS = """
.th-heat{display:inline-flex;gap:3px}.th-heat i{width:9px;height:9px;border-radius:50%;background:var(--chip)}
.th-heat i.on{background:var(--acc);box-shadow:0 0 6px color-mix(in srgb,var(--acc) 60%,transparent)}
.th-tbl td{vertical-align:middle}.th-tbl .num{text-align:right;font-variant-numeric:tabular-nums;font-weight:700}
.th-tbl a{color:var(--fg);font-weight:700;text-decoration:none}.th-tbl a:hover{color:var(--acc)}
@media(max-width:700px){.th-tbl .c-b,.th-tbl .c-a{display:none}}
.thm{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:20px;margin:18px 0;
box-shadow:var(--shadow);scroll-margin-top:70px}
.th-h{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap}
.th-h h2{margin:0}.thm>summary{list-style:none;cursor:pointer;font-weight:inherit;font-size:inherit}.thm>summary:before{display:none}.thm>summary::-webkit-details-marker{display:none}
.th-h h2:after{content:' ▾';color:var(--mut);font-size:15px}.thm:not([open]) .th-h h2:after{content:' ▸'}.th-nums{display:flex;gap:8px;flex-wrap:wrap}
.th-nums div{background:var(--card2);border-radius:12px;padding:6px 12px;font-size:12px;color:var(--mut)}
.th-nums b{display:block;font-size:16px;color:var(--fg);font-variant-numeric:tabular-nums}
.th-notes{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:14px 0}
@media(max-width:800px){.th-notes{grid-template-columns:1fr}}
.th-notes div{font-size:14px}.th-notes b{display:block;font-size:12px;text-transform:uppercase;
letter-spacing:.5px;color:var(--mut);margin-bottom:2px}
.th-co{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:8px;margin:6px 0 4px}
.th-co a{display:flex;gap:10px;align-items:center;background:var(--card2);border-radius:12px;padding:8px 10px;
color:var(--fg);text-decoration:none;border:1px solid transparent}
.th-co a:hover{border-color:color-mix(in srgb,var(--acc) 45%,var(--line))}
.th-co .t{flex:1;min-width:0}.th-co .t small{display:block;color:var(--mut);font-size:11px;white-space:nowrap;
overflow:hidden;text-overflow:ellipsis}.th-co .r{font-size:13px;font-weight:700;font-variant-numeric:tabular-nums}
.th-sub{font-size:12px;text-transform:uppercase;letter-spacing:.5px;color:var(--mut);margin:12px 0 4px;font-weight:700}
.th-bars{display:flex;align-items:flex-end;gap:2px;height:38px;width:180px}
.th-bars i{flex:1;background:color-mix(in srgb,var(--ins) 70%,transparent);border-radius:2px 2px 0 0;min-height:2px}
.th-list{margin:4px 0 0;padding-left:18px;font-size:14px}.th-list li{margin:2px 0}
.th-guide ol{margin:6px 0 0;padding-left:20px}.th-guide li{margin:6px 0}
"""


def _pct(x):
    return "—" if x is None else f"{x * 100:+.0f}%"


def _cls(x):
    return "" if x is None else ("up" if x >= 0 else "down")


def _heat(n):
    return ("<span class='th-heat' title='Heat: " + str(n) + " of 5'>"
            + "".join(f"<i class='{'on' if i < n else ''}'></i>" for i in range(5)) + "</span>")


def _href(tkr, pages):
    import stockpage
    return stockpage.href(tkr) if tkr in pages else f"trades.html?q={E(tkr)}"


def _company_row(con, t, ev, pages):
    name = (profiles._all(con, "SELECT coalesce((SELECT company FROM health WHERE tkr = ?), "
                               "(SELECT name FROM companies WHERE tkr = ?))", [t, t]) or [[None]])[0][0]
    ret = ev["rets"].get(t, (None,))[0]
    chips = ev["buys"].get(t, [])
    sub = " · ".join(chips[:2]) if chips else (name or "")
    return (f"<a href='{_href(t, pages)}'>{pictures.logo_html(t)}<div class='t'><b>{E(t)}</b>"
            f"<small{' style=color:var(--up)' if chips else ''}>{E(sub[:40])}</small></div>"
            f"<span class='r {_cls(ret)}' title='Past year'>{_pct(ret)}</span></a>")


def _bars(series):
    if not series:
        return ""
    top = max(series) or 1
    return ("<div class='th-bars' title='Wikipedia readers per month, last 2 years'>"
            + "".join(f"<i style='height:{max(4, round(100 * v / top))}%'></i>" for v in series) + "</div>")


def _theme(con, ev, pages, is_open=False):
    nums = (f"<div class='th-nums'><div>Heat<b>{_heat(ev['heat'])}</b></div>"
            f"<div>Past year (median)<b class='{_cls(ev['median_1y'])}'>{_pct(ev['median_1y'])}</b></div>"
            f"<div>vs S&amp;P 500<b class='{_cls(ev['vs_spy'])}'>{_pct(ev['vs_spy'])}</b></div>"
            f"<div>Attention vs last year<b class='{_cls(ev['attention'])}'>{_pct(ev['attention'])}</b></div>"
            f"<div>Smart-money buys (90d)<b>{ev['n_buys']}</b></div></div>")
    notes = (f"<div class='th-notes'><div><b>Why it could matter</b>{E(ev['why'])}</div>"
             f"<div><b>What could go wrong</b>{E(ev['risk'])}</div>"
             f"<div><b>What to watch</b>{E(ev['watch'])}</div></div>")
    leaders = "".join(_company_row(con, t, ev, pages) for t in ev["leaders"])
    smaller = "".join(_company_row(con, t, ev, pages) for t in ev["smaller"])
    etfs = "".join(_company_row(con, t, ev, pages) for t in ev["etfs"])
    extra = ""
    if ev["series"]:
        extra += (f"<div class='th-sub'>Public attention</div><div style='display:flex;gap:14px;"
                  f"align-items:flex-end;flex-wrap:wrap'>{_bars(ev['series'])}<span class='mut'>"
                  f"Wikipedia readers of {E(', '.join(ev['wiki']))}, monthly</span></div>")
    if ev["bills"]:
        extra += ("<div class='th-sub'>Moving in Congress</div><ul class='th-list'>"
                  + "".join(f"<li><a href='{E(u or '#')}' target='_blank' rel='noopener'>{E(t[:110])}</a>"
                            f" <span class='mut'>{E((a or '')[:60])}</span></li>" for t, u, a in ev["bills"])
                  + "</ul>")
    if ev["odds"]:
        extra += ("<div class='th-sub'>What bettors expect</div><ul class='th-list'>"
                  + "".join(f"<li><a href='{E(u or 'markets.html')}' target='_blank' rel='noopener'>"
                            f"{E(q[:110])}</a> <b>{'' if p is None else f'{p:.0%}'}</b></li>"
                            for q, p, u in ev["odds"]) + "</ul>")
    return (f"<details class='thm' id='{ev['key']}'{' open' if is_open else ''}><summary class='th-h'>"
            f"<h2>{E(ev['name'])}</h2>{nums}</summary>"
            f"{notes}<div class='th-sub'>Leaders</div><div class='th-co'>{leaders}</div>"
            f"<div class='th-sub'>Smaller and riskier</div><div class='th-co'>{smaller}</div>"
            f"<div class='th-sub'>Study it through a fund</div><div class='th-co'>{etfs}</div>"
            f"{extra}</details>")


GUIDE = """<div class='card th-guide'><h3 style='margin:0'>How to study a future theme</h3><ol>
<li><b>Name the change, not the stock.</b> “Electricity demand is rising for the first time in 20 years”
is a theme. “NVDA goes up” is not.</li>
<li><b>Find who gets paid first.</b> In a gold rush, sellers of picks and shovels (chips, power gear,
equipment) earn money before the miners do.</li>
<li><b>Check the evidence is real.</b> Rising sales at the companies involved, government money, signed
contracts. Hype shows up in Wikipedia views and prices before it shows up in sales.</li>
<li><b>Watch what insiders and Washington do.</b> The chips on each company show Congress, insider,
White House and fund buys from the last 90 days.</li>
<li><b>Ask what's already priced in.</b> A theme everyone loves (heat 5 of 5, stocks up a lot) can still
be a poor buy. Some of the best entries come when a real theme is out of favour.</li>
<li><b>Start with a fund, add single stocks slowly.</b> An ETF spreads the bet. Small, risky names
belong in a small slice of your money, if at all.</li></ol></div>"""


def page(con, today, pages=()):
    evs = evidence(con, today)
    order = sorted(evs, key=lambda e: (-e["heat"], -(e["vs_spy"] if e["vs_spy"] is not None else -9)))
    rows = "".join(
        f"<tr><td><a href='#{e['key']}' onclick=\"document.getElementById('{e['key']}').open=true\">{E(e['name'])}</a></td><td>{_heat(e['heat'])}</td>"
        f"<td class='num {_cls(e['vs_spy'])}'>{_pct(e['vs_spy'])}</td>"
        f"<td class='num c-a {_cls(e['attention'])}'>{_pct(e['attention'])}</td>"
        f"<td class='num c-b'>{e['n_buys']}</td><td class='num c-b'>{len(e['bills'])}</td></tr>"
        for e in order)
    table = ("<div class='wrap'><table class='th-tbl'><thead><tr><th>Theme</th><th>Heat</th>"
             "<th style='text-align:right' title='Median past-year return of its stocks minus the S&amp;P 500'>"
             "Past year vs S&amp;P</th><th class='c-a' style='text-align:right'>Attention</th>"
             "<th class='c-b' style='text-align:right'>Smart-money buys</th>"
             "<th class='c-b' style='text-align:right'>Bills</th></tr></thead>"
             f"<tbody>{rows}</tbody></table></div>"
             "<p class='mut'>Heat adds one point each for: its stocks beat the S&amp;P 500 over the past "
             "year, beat it by 20%+, Wikipedia readers up 15%+ on a year ago, 3+ smart-money buys in 90 "
             "days, and a bill on it moving in Congress. Tap a theme to jump to it; tap a theme's title to open or close it.</p>")
    hero = ("<div class='hero'><div class='eyebrow'>Future themes</div>"
            f"<h1>What could the next decade be built on?</h1><p>{len(THEMES)} big changes worth "
            "studying, with the companies involved and live evidence from the tracker: how the stocks "
            "are doing, whether the public is paying attention, and whether insiders, Congress and funds "
            "are buying in. A place to learn and look for ideas, not a list of things to buy.</p></div>")
    body = "".join(_theme(con, e, pages, i < 3) for i, e in enumerate(order))
    return (f"<style>{CSS}</style>{hero}<h2>Which themes are heating up</h2>{table}{GUIDE}{body}"
            "<p class='mut'>The themes and company lists are hand-picked starting points in "
            "<code>themes.py</code> and can be edited. Prices from Yahoo Finance, attention from "
            "Wikipedia page views, buys from public filings.</p>")


def home_teaser(con, today, n=3, root=""):
    """The hottest few themes as a small block for the home page."""
    evs = sorted(evidence(con, today), key=lambda e: -e["heat"])[:n]
    items = "".join(f"<li><a href='{root}themes.html#{e['key']}'>{E(e['name'])}</a> {_heat(e['heat'])}"
                    f"<small style='display:block;color:var(--mut)'>past year vs S&amp;P "
                    f"{_pct(e['vs_spy'])}</small></li>" for e in evs)
    heat_css = CSS[CSS.index(".th-heat"):CSS.index("\n", CSS.index(".th-heat i.on"))]
    return (f"<style>{heat_css}.th-tz{{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;"
            f"gap:8px}}</style><div class='th-teaser'><ul class='th-tz'>{items}</ul>"
            f"<a href='{root}themes.html'>All future themes →</a></div>")


# ------------------------------------------------------------------ selftest

def selftest():
    import duckdb
    today = datetime.date(2025, 3, 31)
    keys = [t["key"] for t in THEMES]
    assert len(keys) == len(set(keys)), "theme keys are unique"
    for t in THEMES:
        assert t["leaders"] and t["smaller"] and t["why"] and t["risk"] and t["watch"], t["key"]
    assert "NVDA" in tickers() and "OKLO" in tickers() and len(tickers()) == len(set(tickers()))
    con = duckdb.connect()
    import signals
    signals.make_fixture(con)
    con.execute("CREATE TABLE IF NOT EXISTS prices (tkr VARCHAR, d DATE, close DOUBLE)")
    for t, a, b in (("OKLO", 10, 40), ("CEG", 100, 150), ("SPY", 500, 550), ("NVDA", 100, 90)):
        con.executemany("INSERT INTO prices VALUES (?, ?, ?)",
                        [(t, today - datetime.timedelta(days=365 - i), a + (b - a) * i / 365)
                         for i in range(366)])
    con.execute("""INSERT INTO senate_trades (first_name, last_name, filed, tx_date, ticker,
        asset_name, asset_type, tx_type, amount_low, amount_high) VALUES
        ('A','Alpha','03/20/2025','03/01/2025','OKLO','Oklo Inc','Stock','Purchase',15001,50000)""")
    ensure_table(con)
    for art, base in (("Small modular reactor", 1000), ("Nuclear power", 5000)):
        for i in range(24):
            m = datetime.date(2023 + (i + 3) // 12, (i + 3) % 12 + 1, 1)
            con.execute("INSERT INTO theme_views VALUES (?, ?, ?)", [art, m, base * (2 if i >= 21 else 1)])
    nuc = next(e for e in evidence(con, today) if e["key"] == "nuclear")
    assert abs(nuc["attention"] - 1.0) < 1e-9, nuc["attention"]
    assert nuc["rets"]["OKLO"][0] > 2.9 and nuc["vs_spy"] > 0, nuc["vs_spy"]
    assert nuc["buys"]["OKLO"] == ["Congress ×1"] and nuc["heat"] >= 3
    ai = next(e for e in evidence(con, today) if e["key"] == "ai")
    assert ai["vs_spy"] < 0 and ai["attention"] is None
    html_ = page(con, today, {"OKLO"})
    assert "What could the next decade be built on?" in html_ and "id='nuclear'" in html_
    assert "s/OKLO.html" in html_ and "trades.html?q=CCJ" in html_
    assert html_.index("id='nuclear'") < html_.index("id='ai'"), "hottest theme first"
    assert "themes.html#nuclear" in home_teaser(con, today)
    assert "How to study a future theme" in page(duckdb.connect(), today), "empty DB still renders"
    print("themes selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        import duckdb
        from schema import DB_PATH
        refresh(duckdb.connect(DB_PATH))
