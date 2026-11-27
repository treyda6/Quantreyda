"""Small-cap screener: little companies that people in the know are betting on.

Most of today's giants were small once. Nobody can tell which small company
will be the next big one, but a few things have a decent record of pointing
at the ones worth a closer look:

  * insiders buying their own small company's stock with their own money,
    especially several at once (the best-studied signal here: insider buying
    has worked better in small companies than in big ones)
  * Congress, the White House or a long-term fund buying it
  * an investor taking a 5%+ stake (13D) to push for changes
  * a government contract that is big next to the company's sales
  * sales growing fast, without printing lots of new shares
  * the public starting to read about it (Wikipedia page views)

Every stock that got at least one of those signals in the last 90 days is
scored by adding up points (the table is in POINTS and on the page), and
kept if the company is worth under $2 billion. Company size is shares
outstanding (SEC cover page, from the health check) times the last price.

    py screener.py             # print the top of the list from the database
    py screener.py --selftest

daily.py fetches prices and health checks for the best-scoring names each
day (tickers()), and dashboard.py writes site/screener.html (page()).
"""

import datetime
import html
import json
import sys

import links
import pictures
import profiles

E = html.escape
DAYS = 90
MAX_CAP = 2e9
TIERS = (("nano", "Nano", 0, 50e6), ("micro", "Micro", 50e6, 300e6), ("small", "Small", 300e6, MAX_CAP))
SAVINGS_BANKS = (6035, 6036)  # mutual-to-stock conversions look like insider clusters

POINTS = [  # (what, points) shown on the page so the score is never a black box
    ("Each insider buying on the open market (up to 3)", "+1 each"),
    ("An officer (CEO, CFO…) is one of the buyers", "+1"),
    ("Insiders put in $250k or more together", "+1"),
    ("Insider buys add up to 0.5%+ of the whole company", "+1"),
    ("Each member of Congress buying (up to 2)", "+2 each"),
    ("White House, cabinet or Fed official buying", "+2"),
    ("Investor took a 5%+ stake to push for change (13D)", "+2"),
    ("A followed long-term fund bought or added", "+2"),
    ("Won a big federal contract", "+2"),
    ("…worth 10%+ of a year's sales", "+2"),
    ("Sales up 25%+ last year (10%+: +1)", "+2"),
    ("Profitable", "+1"),
    ("Buying back its own shares", "+1"),
    ("Beat the S&P 500 over the last 6 months", "+1"),
    ("Wikipedia readers doubled this week", "+1"),
    ("Insiders sold far more than they bought", "−2"),
    ("Share count up 15%+ in a year (5%+: −1)", "−2"),
    ("Sales shrank 10%+", "−1"),
    ("Health check says Shaky", "−1"),
    ("Savings bank going public (directors all buy at the IPO)", "−3"),
]


def _money(x):
    a = abs(x or 0)
    return (f"${a / 1e9:.1f}B" if a >= 1e9 else f"${a / 1e6:.0f}M" if a >= 1e6
            else f"${a / 1e3:.0f}k" if a >= 1e3 else f"${a:.0f}")


def tier(cap):
    for key, _label, lo, hi in TIERS:
        if cap is not None and lo <= cap < hi:
            return key
    return None if cap is None else "big"


# ------------------------------------------------------------------ data

def _q(con, sql, params=()):
    return profiles._all(con, sql, list(params))


def _signals(con, today, days=DAYS):
    """{tkr: raw signal dict} for every ticker with a smart-money signal."""
    start = today - datetime.timedelta(days=days)
    out = {}

    def get(t):
        return out.setdefault(t, {"tkr": t})
    for t, n, val, officer, issuer in _q(con, """
            SELECT upper(trim(ticker)), count(DISTINCT owner_cik), sum(shares * price_per_share),
                   bool_or(coalesce(owner_relationship, '') ILIKE '%officer%'
                           OR coalesce(owner_relationship, '') ILIKE '%ceo%'), min(issuer_name)
            FROM insider_trades
            WHERE trans_code = 'P' AND price_per_share > 0 AND shares > 0 AND ticker IS NOT NULL
              AND upper(trim(ticker)) NOT IN ('', 'NONE', 'N/A', 'NA')
              AND try_cast(filed_date AS DATE) BETWEEN ? AND ?
            GROUP BY 1""", [start, today]):
        if t:
            get(t).update(ins_n=n, ins_val=val or 0, ins_officer=bool(officer), issuer=issuer)
    for t, val in _q(con, """
            SELECT upper(trim(ticker)), sum(shares * price_per_share) FROM insider_trades
            WHERE trans_code = 'S' AND price_per_share > 0 AND ticker IS NOT NULL
              AND try_cast(filed_date AS DATE) BETWEEN ? AND ? GROUP BY 1""", [start, today]):
        if t in out:
            out[t]["ins_sold"] = val or 0
    for t, n, issuer in _q(con, """
            SELECT tkr, count(DISTINCT last_name), min(asset_name) FROM trades
            WHERE tx_type = 'Purchase' AND tkr IS NOT NULL AND amount_low >= 1001
              AND filed_date BETWEEN ? AND ? GROUP BY 1""", [start, today]):
        get(t).update(cong_n=n)
        out[t].setdefault("issuer", issuer)
    for t, n in _q(con, """SELECT tkr, count(DISTINCT name) FROM wh_trades
                           WHERE tx_type = 'Purchase' AND tkr IS NOT NULL AND kind = 'stock'
                             AND filed BETWEEN ? AND ? GROUP BY 1""", [start, today]):
        get(t)["wh_n"] = n
    for t, form, investor, company in _q(con, """
            SELECT tkr, form, investor, company FROM stakes
            WHERE tkr IS NOT NULL AND form = '13D' AND filed BETWEEN ? AND ?
            ORDER BY filed DESC""", [start, today]):
        r = get(t)
        r.setdefault("stake", investor)
        r.setdefault("issuer", company)
    for t, fund, change in _q(con, """SELECT tkr, fund, change FROM fund_changes
                                      WHERE tkr IS NOT NULL AND change IN ('new', 'added')""", []):
        get(t).setdefault("funds", []).append(f"{fund} ({change})")
    for t, amount, agency in _q(con, """
            SELECT tkr, sum(amount), min(agency) FROM big_contracts
            WHERE tkr IS NOT NULL AND start_date BETWEEN ? AND ? GROUP BY 1""", [start, today]):
        get(t).update(contract=amount or 0, agency=agency)
    return out


def _attach(con, rows, today):
    """Add health, size, industry, buzz and price moves to each raw row."""
    tickers = list(rows)
    if not tickers:
        return
    health = {}
    for r in _q(con, """SELECT tkr, company, verdict, reasons, revenue, revenue_growth, net_income,
                               share_growth, buybacks, shares FROM health
                        WHERE tkr IN (SELECT unnest(?))""", [tickers]):
        health[r[0]] = r[1:]
    for t, (co, verdict, reasons, rev, rg, ni, sg, bb, shares) in health.items():
        rows[t].update(company=co, verdict=verdict, reasons=reasons, revenue=rev,
                       growth=rg, profit=ni, dilution=sg, buybacks=bb, shares=shares)
    for t, sic, desc, name in _q(con, """SELECT tkr, sic, sic_desc, name FROM companies
                                       WHERE tkr IN (SELECT unnest(?))""", [tickers]):
        rows[t].update(sic=sic, industry=(desc or "").title() or None)
        rows[t].setdefault("company", name)
    for t, detail in _q(con, """SELECT tkr, detail FROM company_intel
                               WHERE source = 'buzz' AND tkr IN (SELECT unnest(?))""", [tickers]):
        try:
            rows[t]["buzz"] = float(str(detail).split("|")[1])
        except (IndexError, ValueError):
            pass
    since = today - datetime.timedelta(days=190)
    px = {}
    for t, d, c in _q(con, """SELECT tkr, d, close FROM prices WHERE tkr IN (SELECT unnest(?))
                              AND d BETWEEN ? AND ? ORDER BY tkr, d""",
                      [tickers + ["SPY"], since, today]):
        if c:
            px.setdefault(t, []).append(c)
    spy = px.get("SPY")
    spy_6m = spy[-1] / spy[0] - 1 if spy and len(spy) > 100 else None
    for t, r in rows.items():
        p = px.get(t)
        if not p:
            continue
        r["price"] = p[-1]
        if r.get("shares"):
            r["cap"] = r["shares"] * p[-1]
        if len(p) > 100:
            r["ret_6m"] = p[-1] / p[0] - 1
            if spy_6m is not None:
                r["vs_spy"] = r["ret_6m"] - spy_6m


def score(r):
    """(points, [(text, kind)]) for one row. kind: good, bad, info."""
    pts, why = 0, []

    def add(n, text, kind=None):
        nonlocal pts
        pts += n
        why.append((text, kind or ("good" if n > 0 else "bad" if n < 0 else "info")))
    n = r.get("ins_n") or 0
    if n:
        add(min(n, 3), f"{n} insider{'s' if n > 1 else ''} bought {_money(r.get('ins_val'))}"
            + (" (a cluster)" if n >= 3 else ""))
        if r.get("ins_officer"):
            add(1, "an officer is buying")
        if (r.get("ins_val") or 0) >= 250_000:
            add(1, "$250k+ of insiders' own money")
        if r.get("cap") and (r.get("ins_val") or 0) / r["cap"] >= 0.005:
            add(1, f"insiders bought {r['ins_val'] / r['cap']:.1%} of the company")
    sold = r.get("ins_sold") or 0
    if sold >= 500_000 and sold > 2 * (r.get("ins_val") or 0):
        add(-2, f"insiders sold {_money(sold)}")
    if r.get("cong_n"):
        add(2 * min(r["cong_n"], 2), f"{r['cong_n']} member{'s' if r['cong_n'] > 1 else ''} of Congress bought")
    if r.get("wh_n"):
        add(2, "White House / cabinet bought")
    if r.get("stake"):
        add(2, f"5%+ stake by {r['stake']}")
    if r.get("funds"):
        add(2, ", ".join(r["funds"][:2]))
    if r.get("contract"):
        add(2, f"{_money(r['contract'])} federal contract")
        if r.get("revenue") and r["contract"] / r["revenue"] >= 0.10:
            add(2, f"contract = {r['contract'] / r['revenue']:.0%} of a year's sales")
    g = r.get("growth")
    if g is not None:
        if g >= 0.25:
            add(2, f"sales up {g:.0%}")
        elif g >= 0.10:
            add(1, f"sales up {g:.0%}")
        elif g <= -0.10:
            add(-1, f"sales down {abs(g):.0%}")
    if r.get("profit") is not None and r["profit"] > 0:
        add(1, "profitable")
    if (r.get("buybacks") or 0) > 0:
        add(1, "buying back shares")
    if r.get("vs_spy") is not None and r["vs_spy"] > 0:
        add(1, f"beat the S&P by {r['vs_spy']:.0%} in 6 months")
    if (r.get("buzz") or 0) >= 2:
        add(1, f"Wikipedia views {r['buzz']:.1f}x normal")
    d = r.get("dilution")
    if d is not None and d > 0.15:
        add(-2, f"share count up {d:.0%} (dilution)")
    elif d is not None and d > 0.05:
        add(-1, f"share count up {d:.0%}")
    if r.get("verdict") == "Shaky":
        add(-1, "health: Shaky")
    if r.get("sic") in SAVINGS_BANKS and n >= 3:
        add(-3, "savings bank IPO? (directors all buy at listing)")
    return pts, why


def _too_big(r):
    if r.get("cap") is not None:
        return r["cap"] >= MAX_CAP
    # not sized yet: big sales almost always mean a big company
    return (r.get("revenue") or 0) >= 1.5e9


def _not_a_company(r):
    return r.get("verdict") == "Unknown" and "not in SEC" in (r.get("reasons") or "")


def screen(con, today, days=DAYS, include_unsized=True):
    """Scored candidates, best first: [row dict with score, why, tier]."""
    rows = _signals(con, today, days)
    _attach(con, rows, today)
    out = []
    for t, r in rows.items():
        if _too_big(r) or _not_a_company(r):
            continue
        r["tier"] = tier(r.get("cap")) or "unsized"
        if r["tier"] == "unsized" and not include_unsized:
            continue
        r["score"], r["why"] = score(r)
        r["company"] = r.get("company") or r.get("issuer") or ""
        out.append(r)
    out.sort(key=lambda r: (-r["score"], -(r.get("ins_val") or 0), r["tkr"]))
    return out


def tickers(con, today, n=150):
    """What daily.py should size and health-check: the best-scoring names,
    including ones whose size isn't known yet (that's how they get sized)."""
    return [r["tkr"] for r in screen(con, today)[:n]]


# ------------------------------------------------------------------ page

CSS = """
.scr-top .stats{grid-template-columns:repeat(4,1fr)}
@media(max-width:700px){.scr-top .stats{grid-template-columns:repeat(2,1fr)}}
.scr-tier{font-size:11px;font-weight:800;letter-spacing:.4px;text-transform:uppercase;border-radius:999px;
padding:3px 9px;background:var(--chip);color:var(--mut);white-space:nowrap}
.scr-tier.nano{color:var(--down)}.scr-tier.micro{color:var(--warn)}.scr-tier.small{color:var(--ins)}
.scr-why{list-style:none;margin:0;padding:0;display:flex;flex-wrap:wrap;gap:5px}
.scr-why li{font-size:12px;font-weight:600;border-radius:8px;padding:3px 8px;background:var(--chip)}
.scr-why li.good{color:var(--up)}.scr-why li.bad{color:var(--down)}
.scr-score{font-size:26px;font-weight:800;font-variant-numeric:tabular-nums;line-height:1}
.scr-score small{display:block;font-size:11px;color:var(--mut);font-weight:600;text-transform:uppercase;letter-spacing:.5px}
.scr-card .meter i{background:var(--acc)}
.scr-tbl td{vertical-align:middle}.scr-tbl tr[data-href]{cursor:pointer}
.scr-tbl .num{text-align:right;font-variant-numeric:tabular-nums;font-weight:700}
.scr-tbl .pic{display:flex;gap:10px;align-items:center}.scr-tbl .pic small{display:block;color:var(--mut);font-size:12px}
.scr-tbl .scr-why li{font-size:11px;padding:2px 6px}
@media(max-width:700px){.scr-tbl .c-why,.scr-tbl .c-g,.scr-tbl .c-h{display:none}}
.scr-guide{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px}
.scr-guide .card h3{margin:0;font-size:16px}.scr-guide .card p,.scr-guide .card li{font-size:14px;margin:0}
.scr-guide ol{margin:0;padding-left:18px}
.scr-pts td:last-child{text-align:right;font-weight:700;white-space:nowrap}
"""

TABLE_JS = """
const tb = document.querySelector('#scr tbody'), q = document.getElementById('scr-q'),
      sz = document.getElementById('scr-size'), mn = document.getElementById('scr-min'),
      hl = document.getElementById('scr-health'), ct = document.getElementById('scr-count');
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const pct = x => x === null || x === undefined ? '<span class="mut">—</span>'
  : `<span class="${x >= 0 ? 'up' : 'down'}">${x >= 0 ? '+' : ''}${(x * 100).toFixed(0)}%</span>`;
function draw() {
  const t = q.value.trim().toLowerCase(), min = +mn.value;
  const hit = SCR.filter(r => (!t || (r.tkr + ' ' + r.co + ' ' + r.ind).toLowerCase().includes(t))
    && (sz.value === 'all' || (sz.value === 'sized' ? r.tier !== 'unsized' : r.tier === sz.value))
    && r.score >= min && (!hl.value || r.verdict === hl.value));
  ct.textContent = hit.length + ' of ' + SCR.length;
  tb.innerHTML = hit.slice(0, 400).map(r => {
    const lg = r.logo ? `<span class="lg"><img src="${r.logo}" alt="" loading="lazy"></span>`
                      : `<span class="lg mono">${esc(r.tkr[0])}</span>`;
    const why = r.why.slice(0, 4).map(w => `<li class="${w[1]}">${esc(w[0])}</li>`).join('');
    return `<tr data-href="${r.href}"><td><div class="pic">${lg}<div><a href="${r.href}"><b>${esc(r.tkr)}</b></a>
      <small>${esc(r.co).slice(0, 34)}</small></div></div></td>
      <td><span class="scr-tier ${r.tier}">${esc(r.tierLabel)}</span><small class="mut" style="display:block">${esc(r.cap)}</small></td>
      <td class="num">${r.score}</td><td class="c-why"><ul class="scr-why">${why}</ul></td>
      <td class="num c-g">${pct(r.growth)}</td><td class="c-h"><span class="badge b-${r.verdict || 'none'}">${esc(r.verdict || 'not checked')}</span></td>
      <td class="num">${pct(r.vs)}</td></tr>`}).join('') || '<tr><td colspan="7" class="mut">Nothing matches.</td></tr>';
}
[q, sz, mn, hl].forEach(el => el.addEventListener('input', draw));
tb.addEventListener('click', e => { const tr = e.target.closest('tr[data-href]');
  if (tr && !e.target.closest('a')) location.href = tr.dataset.href; });
draw();
"""


def _href(tkr, pages, root=""):
    return links.stock(tkr, pages, root)


def _tier_label(r):
    return {"nano": "Nano", "micro": "Micro", "small": "Small", "unsized": "Size ?"}.get(r["tier"], "")


def _card(r, pages, top):
    why = "".join(f"<li class='{k}'>{E(t)}</li>" for t, k in r["why"][:6])
    g = r.get("growth")
    stats = (f"<div class='stats4'><div><span>Size</span><b>{_money(r['cap']) if r.get('cap') else '—'}</b></div>"
             f"<div><span>Sales</span><b>{_money(r['revenue']) if r.get('revenue') else '—'}</b></div>"
             f"<div><span>Growth</span><b>{'—' if g is None else f'{g:+.0%}'}</b></div>"
             f"<div><span>6m vs S&amp;P</span><b>{'—' if r.get('vs_spy') is None else format(r['vs_spy'], '+.0%')}</b></div></div>")
    href = _href(r["tkr"], pages)
    width = max(6, min(100, round(100 * r["score"] / max(top, 1))))
    return (f"<div class='card scr-card'><div class='top'><div class='pic' style='display:flex;gap:10px;align-items:center'>"
            f"{pictures.logo_html(r['tkr'])}<div><a class='tkr' href='{href}'>{E(r['tkr'])}</a>"
            f"<div class='co'>{E(r['company'][:40])}</div></div></div>"
            f"<div class='scr-score' style='text-align:right'>{r['score']}<small>points</small></div></div>"
            f"<div class='meter'><i style='width:{width}%'></i></div>"
            f"<ul class='scr-why'>{why}</ul>{stats}"
            f"<div class='foot'><span class='scr-tier {r['tier']}'>{_tier_label(r)} cap</span>"
            f"<span class='badge b-{E(r.get('verdict') or 'none')}'>{E(r.get('verdict') or 'not checked')}</span>"
            f"{'<span class=chip>' + E(r['industry'][:30]) + '</span>' if r.get('industry') else ''}"
            f"<a href='{href}' style='margin-left:auto;font-weight:700;font-size:13px'>Research →</a></div></div>")


def page(con, today, pages=()):
    rows = screen(con, today)
    sized = [r for r in rows if r["tier"] != "unsized"]
    top = [r for r in sized if r["score"] > 0][:6] or [r for r in rows if r["score"] > 0][:6]
    best = top[0]["score"] if top else 1
    clusters = sum(1 for r in rows if (r.get("ins_n") or 0) >= 3)
    data = [{"tkr": r["tkr"], "co": r["company"], "ind": r.get("industry") or "",
             "tier": r["tier"], "tierLabel": _tier_label(r),
             "cap": _money(r["cap"]) if r.get("cap") else "not sized yet",
             "score": r["score"], "why": r["why"], "growth": r.get("growth"),
             "verdict": r.get("verdict"), "vs": r.get("vs_spy"),
             "logo": pictures.logo_src(r["tkr"]), "href": _href(r["tkr"], pages)} for r in rows]
    js = json.dumps(data).replace("</", "<\\/")
    stats = (f"<div class='stats'><div class='stat'><b>{len(rows)}</b><span>small companies with a signal</span></div>"
             f"<div class='stat'><b>{sum(r['tier'] == 'micro' for r in rows)}</b><span>micro caps ($50M–$300M)</span></div>"
             f"<div class='stat'><b>{sum(r['tier'] == 'small' for r in rows)}</b><span>small caps ($300M–$2B)</span></div>"
             f"<div class='stat'><b>{clusters}</b><span>with 3+ insiders buying</span></div></div>")
    hero = ("<div class='hero scr-top'><div class='eyebrow'>Small-cap screener · last 90 days</div>"
            "<h1>Small companies people in the know are buying</h1>"
            "<p>Every company worth under $2 billion where insiders, Congress, the White House, a "
            "long-term fund or an activist investor put money in lately, scored on how many signals "
            "line up and whether the business is actually growing. A research list for finding the "
            "next big one early, not a buy list.</p>" + stats + "</div>")
    cards = ("<h2>Best-scoring right now</h2><div class='cards'>"
             + "".join(_card(r, pages, best) for r in top) + "</div>") if top else (
             "<h2>Best-scoring right now</h2><p class='mut'>No small companies have a signal yet. "
             "The list fills in as filings and health checks arrive.</p>")
    table = ("<h2>Every candidate</h2>"
             "<div class='filters'><input id='scr-q' placeholder='Search ticker, company or industry…'>"
             "<select id='scr-size'><option value='all'>Every size</option>"
             "<option value='sized'>Sized only</option><option value='nano'>Nano (under $50M)</option>"
             "<option value='micro'>Micro ($50M–$300M)</option><option value='small'>Small ($300M–$2B)</option>"
             "<option value='unsized'>Not sized yet</option></select>"
             "<select id='scr-min'><option value='-99'>Any score</option><option value='3'>3+ points</option>"
             "<option value='5' selected>5+ points</option><option value='8'>8+ points</option></select>"
             "<select id='scr-health'><option value=''>Any health</option><option>Solid</option>"
             "<option>Mixed</option><option>Shaky</option></select>"
             "<span id='scr-count' class='mut' style='align-self:center'></span></div>"
             "<div class='wrap'><table class='scr-tbl' id='scr'><thead><tr><th>Stock</th><th>Size</th>"
             "<th style='text-align:right'>Score</th><th class='c-why'>Why</th>"
             "<th class='c-g' style='text-align:right'>Sales growth</th><th class='c-h'>Health</th>"
             "<th style='text-align:right' title='Return over 6 months minus the S&amp;P 500'>6m vs S&amp;P</th>"
             "</tr></thead><tbody></tbody></table></div>"
             "<p class='mut'>Tap a row for the stock's page: chart with the buys, health check, "
             "filings and news. “Not sized yet” companies get their size from the next daily run.</p>")
    guide = ("<h2>Before you buy a small company</h2><div class='scr-guide'>"
             "<div class='card'><h3>Why small companies</h3><p>A $200M company can grow 10x and still be "
             "small. A $2 trillion one can't. Fewer analysts follow small companies, so insiders' buying "
             "tells you more there, and studies of insider buying find most of its edge in small caps.</p></div>"
             "<div class='card'><h3>Why be careful</h3><p>Most small companies never become big ones, and "
             "many shrink or fail. Prices swing hard, some trade so little that buying moves the price, and "
             "nano caps (under $50M) attract promoters. Keep each one a small slice of your money.</p></div>"
             "<div class='card'><h3>Five things to check</h3><ol><li>Does it make money, or how many years "
             "of cash does it have left?</li><li>Is the share count climbing? That's your slice shrinking.</li>"
             "<li>Is sales growth speeding up?</li><li>Did insiders buy at market prices, not in an offering?</li>"
             "<li>Would you still want it if you'd never seen this list?</li></ol></div></div>")
    pts = "".join(f"<tr><td>{E(a)}</td><td>{E(b)}</td></tr>" for a, b in POINTS)
    how = ("<details style='margin-top:24px'><summary><b>How the score works</b></summary>"
           "<p class='mut'>Points add up from what's on file for the last 90 days. Size is shares "
           "outstanding from the company's SEC cover page times the last price. Over $2B is left out. "
           "Health and growth come from the company's last annual report.</p>"
           f"<div class='wrap'><table class='scr-pts'>{pts}</table></div></details>")
    return (f"<style>{CSS}</style>{hero}{cards}{table}{guide}{how}"
            f"<script>const SCR = {js};{TABLE_JS}</script>")


def home_teaser(con, today, n=3, root=""):
    """Top few small caps as a small block for the home page."""
    rows = [r for r in screen(con, today, include_unsized=False) if r["score"] > 0][:n]
    if not rows:
        return ""
    items = "".join(
        f"<li><a href='{root}screener.html'>{pictures.logo_html(r['tkr'], root)}</a>"
        f"<div><b>{E(r['tkr'])}</b> <span class='scr-tier {r['tier']}'>{_tier_label(r)}</span>"
        f"<small style='display:block;color:var(--mut)'>{E(r['why'][0][0]) if r['why'] else ''}</small></div></li>"
        for r in rows)
    return (f"<style>{CSS}.scr-tz{{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:10px}}"
            ".scr-tz li{display:flex;gap:10px;align-items:center}</style>"
            f"<div class='scr-teaser'><ul class='scr-tz'>{items}</ul>"
            f"<a href='{root}screener.html'>All small-cap ideas →</a></div>")


# ------------------------------------------------------------------ selftest

def make_fixture(con, today):
    """Small companies with known answers, on top of signals.make_fixture."""
    import health
    import signals
    signals.make_fixture(con)
    d = today - datetime.timedelta(days=10)
    con.executemany("""INSERT INTO insider_trades (accession_number, trans_seq, filed_date, ticker,
        issuer_name, owner_cik, owner_name, owner_relationship, trans_code, acquired_disposed, shares,
        price_per_share) VALUES (?,0,?,?,?,?,?,?,?,?,?,?)""", [
        ("s1", str(d), "TINY", "Tiny Robotics Inc", "91", "Ann", "OFFICER (CEO)", "P", "A", 50000, 4),
        ("s2", str(d), "TINY", "Tiny Robotics Inc", "92", "Bo", "DIRECTOR", "P", "A", 20000, 4),
        ("s3", str(d), "TINY", "Tiny Robotics Inc", "93", "Cy", "DIRECTOR", "P", "A", 20000, 4),
        ("g1", str(d), "GIANT", "Giant Corp", "94", "Di", "DIRECTOR", "P", "A", 1000, 300),
        ("b1", str(d), "SAVB", "Sav Bancorp", "95", "Ed", "DIRECTOR", "P", "A", 9000, 10),
        ("b2", str(d), "SAVB", "Sav Bancorp", "96", "Fi", "DIRECTOR", "P", "A", 9000, 10),
        ("b3", str(d), "SAVB", "Sav Bancorp", "97", "Gu", "DIRECTOR", "P", "A", 9000, 10),
        ("x1", str(d), "DUMP", "Dump Co", "98", "Ha", "DIRECTOR", "P", "A", 100, 10),
        ("x2", str(d), "DUMP", "Dump Co", "98", "Ha", "DIRECTOR", "S", "D", 100000, 10),
    ])
    health.ensure_table(con)
    con.executemany("""INSERT INTO health (tkr, checked_on, company, revenue, revenue_growth,
        net_income, share_growth, verdict, reasons, buybacks, shares) VALUES (?,?,?,?,?,?,?,?,?,?,?)""", [
        ("TINY", today, "Tiny Robotics Inc", 40e6, 0.6, 2e6, 0.01, "Solid", "ok", 1e5, 15e6),
        ("GIANT", today, "Giant Corp", 50e9, 0.05, 5e9, 0.0, "Solid", "ok", None, 2e9),
        ("SAVB", today, "Sav Bancorp", 20e6, 0.0, 1e6, 0.0, "Solid", "ok", None, 5e6),
        ("DUMP", today, "Dump Co", 10e6, -0.3, -5e6, 0.4, "Shaky", "bad", None, 3e6)])
    con.execute("CREATE TABLE IF NOT EXISTS prices (tkr VARCHAR, d DATE, close DOUBLE)")
    for t, a, b in (("TINY", 2.0, 4.0), ("GIANT", 300, 300), ("SAVB", 10, 10), ("DUMP", 12, 10),
                    ("SPY", 500, 520)):
        con.executemany("INSERT INTO prices VALUES (?, ?, ?)",
                        [(t, today - datetime.timedelta(days=180 - i), a + (b - a) * i / 180)
                         for i in range(181)])
    con.execute("""CREATE TABLE IF NOT EXISTS companies (tkr VARCHAR PRIMARY KEY, cik BIGINT,
        name VARCHAR, sic INTEGER, sic_desc VARCHAR, fetched_on DATE)""")
    con.execute("INSERT INTO companies VALUES ('SAVB', 1, 'Sav Bancorp', 6036, 'SAVINGS INSTITUTIONS', ?), "
                "('TINY', 2, 'Tiny Robotics', 3569, 'GENERAL INDUSTRIAL MACHINERY', ?)", [today, today])


def selftest():
    import duckdb
    today = datetime.date(2025, 3, 31)
    con = duckdb.connect()
    make_fixture(con, today)
    rows = {r["tkr"]: r for r in screen(con, today)}
    assert "GIANT" not in rows, "a $600B company is not small"
    t = rows["TINY"]
    assert t["tier"] == "micro" and abs(t["cap"] - 60e6) < 1, t.get("cap")
    whys = " | ".join(w for w, _ in t["why"])
    assert "3 insiders bought" in whys and "an officer is buying" in whys and "sales up 60%" in whys, whys
    assert "insiders bought" in whys and "of the company" in whys, "insider $ vs company size"
    assert "beat the S&P" in whys and "profitable" in whys
    assert rows["SAVB"]["score"] < t["score"] and any("savings bank" in w for w, _ in rows["SAVB"]["why"])
    assert rows["DUMP"]["score"] < 0 and any("dilution" in w for w, _ in rows["DUMP"]["why"])
    assert rows["TINY"]["tier"] == "micro" and rows["DUMP"]["tier"] == "nano"
    assert screen(con, today)[0]["tkr"] == "TINY", "best first"
    assert "XYZ" in rows and rows["XYZ"]["tier"] == "unsized", "unsized kept so it gets sized"
    assert "XYZ" not in {r["tkr"] for r in screen(con, today, include_unsized=False)}
    assert "TINY" in tickers(con, today)
    assert tier(10e6) == "nano" and tier(5e9) == "big" and tier(None) is None
    html_ = page(con, today, {"TINY"})
    assert "Small companies people in the know are buying" in html_
    assert "s/TINY.html" in html_ and "look.html?s=DUMP" in html_
    con.execute("UPDATE health SET company = '</script><b>x' WHERE tkr = 'TINY'")
    assert "</script><b>x" not in page(con, today), "data can't break out of the script tag"
    tz = home_teaser(con, today)
    assert "TINY" in tz and "screener.html" in tz
    # an empty database still renders
    empty = duckdb.connect()
    assert "No small companies have a signal yet" in page(empty, today)
    assert home_teaser(empty, today) == ""
    print("screener selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        import duckdb
        from schema import DB_PATH
        c = duckdb.connect(DB_PATH, read_only=True)
        for r in screen(c, datetime.date.today(), include_unsized=False)[:30]:
            print(f"{r['score']:>3}  {r['tkr']:<6} {_money(r.get('cap')):>7}  "
                  + "; ".join(w for w, _ in r["why"][:4]))
