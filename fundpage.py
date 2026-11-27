"""The Funds page and one page per fund (f/<fund>.html).

Everything here comes from the quarterly 13F reports that funds.py saves.

    overview(con, pages, root="")          -> HTML body for funds.html
    fund_page(con, fund, port, gone, pages) -> HTML body for one fund
    build(con, out, pages, page)           -> writes every f/<fund>.html, returns their count

`pages` is the set of tickers that have a stock page on the site: holdings
link there, and other tickers link to a quote page elsewhere.
"""

import datetime
import html
import json

import activists
import funds
import links
import pictures
import sections
import stockpage

E = html.escape
CHANGE = {"new": ("New", "up"), "added": ("Added", "up"), "trimmed": ("Trimmed", "down"),
          "held": ("", ""), "sold": ("Sold", "down")}
NOLOGO = "<span class='lg mono'>?</span>"


def _money(x):
    return f"${x / 1e12:.1f}T" if abs(x or 0) >= 1e12 else sections._money(x)


def stock_link(tkr, issuer, pages, root=""):
    """(href, external?) for a holding: our stock page, else the lookup page."""
    return (links.stock(tkr, pages, root) if tkr else ""), False


def _a(href, inner, ext=False, cls=""):
    if not href:
        return f"<span class='{cls}'>{inner}</span>" if cls else inner
    extra = " target='_blank' rel='noopener'" if ext else ""
    return f"<a href='{href}'{extra}{f' class={cls!r}' if cls else ''}>{inner}</a>"


def _face(f, root="", big=False):
    src = pictures.face_src(pictures.fund_key(f["cik"]), root)
    cls = "fface xl" if big else "fface"
    if src:
        return f"<span class='{cls}'><img src='{E(src)}' alt='' loading='lazy'></span>"
    return f"<span class='{cls} mono'>{E(pictures.initials(f['person'] or f['firm'] or f['label']))}</span>"


def _change_text(r):
    if r["change"] == "new":
        return "New buy", "up"
    if r["change"] in ("added", "trimmed") and r.get("prev_shares"):
        d = r["shares"] / r["prev_shares"] - 1
        return f"{'+' if d >= 0 else '−'}{abs(d) * 100:.0f}% shares", "up" if d >= 0 else "down"
    return "", ""


def _q(d):
    try:
        d = d if isinstance(d, datetime.date) else datetime.date.fromisoformat(str(d)[:10])
        return f"{d:%b %d, %Y}"
    except ValueError:
        return E(str(d))


def _quarter(d):
    try:
        d = d if isinstance(d, datetime.date) else datetime.date.fromisoformat(str(d)[:10])
        return f"Q{(d.month - 1) // 3 + 1} {d.year}"
    except ValueError:
        return E(str(d))


# ------------------------------------------------------------------ overview

def _ticker_rows(items, by_label, pages, verb, root=""):
    out = []
    for tkr, issuer, names in items:
        href, ext = stock_link(tkr, issuer, pages, root)
        who = ", ".join(_a(funds.href(n, root), E(n)) for n in names[:6])
        if len(names) > 6:
            who += f" +{len(names) - 6} more"
        logo = pictures.logo_html(tkr, root) if tkr else NOLOGO
        label = "<b>" + E(tkr or (issuer or "").title()) + "</b>"
        out.append(
            f"<li>{_a(href, logo, ext)}"
            f"<div class='t'>{_a(href, label, ext)}"
            f"<small>{E((issuer or '').title())}</small></div>"
            f"<div class='who'><b>{len(names)} funds</b> {verb}<small>{who}</small></div></li>")
    return "<ul class='fl'>" + "".join(out) + "</ul>"


def overview(con, pages, root=""):
    cat = funds.catalog()
    ports = funds.portfolios(con)
    by_cik = {f["cik"]: f for f in cat}
    names = {f["cik"]: f["label"] for f in cat}
    owned, bought = funds.popular({c: p for c, p in ports.items() if c in by_cik}, names, top=12)
    have = [f for f in cat if f["cik"] in ports]
    latest_q = max((ports[f["cik"]]["period"] for f in have), default=None)
    total = sum(ports[f["cik"]]["total"] for f in have)

    groups = list(dict.fromkeys(f["group"] for f in cat))
    chips = ("<div class='fchips' role='group' aria-label='Filter funds'>"
             "<button type='button' class='on' data-g=''>All</button>"
             + "".join(f"<button type='button' data-g='{E(g)}'>{E(g)}</button>" for g in groups)
             + "</div>")
    cards = []
    for f in cat:
        p = ports.get(f["cik"])
        top = ""
        if p:
            top = "".join(f"<span class='ft'>{pictures.logo_html(r['tkr'], root) if r['tkr'] else ''}"
                          f"{E(r['tkr'] or (r['issuer'] or '')[:10].title())}</span>" for r in p["rows"][:3])
            nb = sum(1 for r in p["rows"] if r["change"] == "new")
            meta = (f"{_money(p['total'])} · {len(p['rows'])} stocks"
                    + (f" · <b class='up'>{nb} new</b>" if nb else ""))
        else:
            meta = "Not loaded yet"
        cards.append(
            f"<a class='fcard' href='{funds.href(f['label'], root)}' data-g='{E(f['group'])}' "
            f"data-q='{E((f['label'] + ' ' + f['firm'] + ' ' + (f['person'] or '')).lower())}'>"
            f"{_face(f, root)}<div class='t'><b>{E(f['person'] or f['firm'])}</b>"
            f"<small>{E(f['firm'])}</small><small class='m'>{meta}</small>"
            f"<div class='fts'>{top}</div></div></a>")

    filings = activists.recent_13f(con, 25)
    by_name = {}
    for f in cat:
        by_name[f["firm"].lower()] = f
    frows = []
    for d, name, u in filings:
        mine = next((f for k, f in by_name.items() if k and k.split()[0] in (name or "").lower()), None)
        link = (f" · <a href='{funds.href(mine['label'], root)}'>holdings</a>" if mine else "")
        frows.append(f"<tr><td><a href='{E(u)}' target='_blank' rel='noopener'>{E((name or '').title())}</a>"
                     f"{link}</td><td>{d:%b %d, %Y}</td></tr>")

    stats = (f"<div class='stats fst'><div><span>Funds tracked</span><b>{len(cat)}</b>"
             f"<small>{len(have)} loaded</small></div>"
             f"<div><span>Latest quarter</span><b>{_quarter(latest_q) if latest_q else '—'}</b>"
             f"<small>{'to ' + _q(latest_q) if latest_q else 'after the next run'}</small></div>"
             f"<div><span>Money tracked</span><b>{_money(total) if total else '—'}</b>"
             f"<small>in US stocks</small></div></div>")
    return (
        f"<style>{CSS}</style>"
        "<div class='sec-top'><h1>Funds</h1><p class='hint'>What " + str(len(cat)) + " well-known "
        "investors own, from the quarterly 13F reports every big fund files (up to 45 days after "
        "each quarter ends, so this is slow information: good for \"long-term pros like this "
        "company too\", not for timing). Tap any fund to see everything it owns, or any stock "
        "for its page.</p></div>" + stats
        + "<section><div class='sec-h'><h2>What several funds bought last quarter</h2></div>"
        + (_ticker_rows(bought, by_cik, pages, "started or added", root) if bought else
           "<p class='hint'>Shows up once two or more funds buy the same stock in the same quarter.</p>")
        + "<p class='hint'>Counts only real bets: positions worth 1% or more of the fund.</p></section>"
        + "<section><div class='sec-h'><h2>Most owned by these funds</h2></div>"
        + (_ticker_rows(owned, by_cik, pages, "own it", root) if owned else
           "<p class='hint'>Fills in after the next daily run.</p>") + "</section>"
        + "<section><div class='sec-h'><h2>Every fund</h2></div>"
        + "<div class='filters'><input id='fq' type='search' placeholder='Search funds or people…' "
          "aria-label='Search funds'></div>" + chips
        + "<div class='fgrid' id='fgrid'>" + "".join(cards) + "</div>"
        + "<p class='hint' id='fnone' hidden>No fund matches.</p>"
        + "<p class='hint'>Want another fund? Add its SEC number to <code>funds.txt</code>.</p></section>"
        + "<section><div class='sec-h'><h2>Recently reported 13F filings</h2></div>"
        + (f"<div class='wrap'><table><thead><tr><th>Fund</th><th>Filed</th></tr></thead><tbody>{''.join(frows)}"
           "</tbody></table></div>" if frows else
           "<p class='hint'>These fill in from the daily SEC index over the next week.</p>")
        + "</section>" + FILTER_JS)


FILTER_JS = """<script>(function(){var q=document.getElementById('fq'),g='',
cards=[].slice.call(document.querySelectorAll('#fgrid .fcard')),none=document.getElementById('fnone');
function run(){var s=(q.value||'').toLowerCase().trim(),n=0;cards.forEach(function(c){
var ok=(!g||c.dataset.g===g)&&(!s||c.dataset.q.indexOf(s)>=0);c.hidden=!ok;n+=ok});none.hidden=n>0}
q.addEventListener('input',run);[].forEach.call(document.querySelectorAll('.fchips button'),function(b){
b.addEventListener('click',function(){g=b.dataset.g;[].forEach.call(document.querySelectorAll('.fchips button'),
function(x){x.classList.toggle('on',x===b)});run()})})})()</script>"""


# ------------------------------------------------------------------ one fund

def _treemap(rows, pages, root):
    rows = [r for r in rows if r["value"] > 0][:40]
    if not rows:
        return ""
    rects = sections.squarify([r["value"] ** 0.8 for r in rows])
    tiles = []
    for r, (x, y, w, h) in zip(rows, rects):
        area = w * h
        big = ("xl" if area > 900 else "l" if area > 300 else "m" if area > 90
               else "s" if area > 25 and min(w, h) > 4 else "xs")
        mix = {"new": 100, "added": 80, "trimmed": 20}.get(r["change"], 50)
        href, ext = stock_link(r["tkr"], r["issuer"], pages, root)
        label = r["tkr"] or (r["issuer"] or "?").split()[0].title()
        tip = f"{label}: {r['weight'] * 100:.1f}% of the fund, {_money(r['value'])}"
        word = CHANGE.get(r["change"], ("", ""))[0]
        tag = "a" if href else "div"
        extra = (f" href='{href}'" + (" target='_blank' rel='noopener'" if ext else "")) if href else ""
        tiles.append(f"<{tag} class='tile {big}'{extra} title='{E(tip)}' style='left:{x:.3f}%;top:{y:.3f}%;"
                     f"width:{w:.3f}%;height:{h:.3f}%;--mix:{mix}%'><b>{E(label)}</b>"
                     f"<span>{r['weight'] * 100:.1f}%{' · ' + word if word else ''}</span></{tag}>")
    return ("<section><div class='sec-h'><h2>The portfolio at a glance</h2></div>"
            "<div class='tm ftm'>" + "".join(tiles) + "</div>"
            "<div class='tm-key'><span>Trimmed</span><span>Held</span><span>Added or new</span></div>"
            "<p class='hint'>Bigger tile = bigger share of the fund. Its 40 largest positions.</p></section>")


def fund_page(con, f, port, gone, pages, root="../"):
    head = (f"<style>{CSS}</style><a class='back' href='{root}funds.html'>← All funds</a>"
            f"<div class='fhead'>{_face(f, root, big=True)}<div class='t'><h1>{E(f['person'] or f['firm'])}</h1>"
            f"<div class='co'>{E(f['firm'])} · {E(f['group'])}</div></div>")
    sec = (f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={f['cik']}"
           "&type=13F-HR&dateb=&owner=include&count=40")
    if not port:
        return (head + "</div><p>This fund's 13F reports haven't been loaded yet. They're fetched on "
                f"the daily run. Meanwhile, see its <a href='{E(sec)}' target='_blank' rel='noopener'>"
                "filings at the SEC</a>.</p>")
    rows = port["rows"]
    head += (f"<div class='big'>{_money(port['total'])}<span class='hint'>{_quarter(port['period'])} · "
             f"filed {_q(port['filed'])}</span></div></div>")
    n = {k: sum(1 for r in rows if r["change"] == k) for k in ("new", "added", "trimmed")}
    top10 = sum(r["weight"] for r in rows[:10])
    stats = ("<div class='kv fkv'>"
             + "".join(f"<div><span>{k}</span><b>{v}</b></div>" for k, v in (
                 ("Stocks", f"{len(rows):,}"), ("Top 10 holdings", f"{top10 * 100:.0f}% of the fund"),
                 ("New buys", n["new"]), ("Added to", n["added"]), ("Trimmed", n["trimmed"]),
                 ("Sold out", len(gone))))
             + "</div>")

    trs = []
    for i, r in enumerate(rows, 1):
        href, ext = stock_link(r["tkr"], r["issuer"], pages, root)
        ch, cls = _change_text(r)
        name = E(r["tkr"] or (r["issuer"] or "").title())
        logo = pictures.logo_html(r["tkr"], root) if r["tkr"] else NOLOGO
        label = f"<b>{name}</b>" + (" ↗" if ext else "")
        stock = (f"<div class='pic'>{logo}"
                 f"<div class='t'>{_a(href, label, ext)}"
                 f"<small>{E((r['issuer'] or '').title())}</small></div></div>")
        trs.append(f"<tr data-q='{E(((r['tkr'] or '') + ' ' + (r['issuer'] or '')).lower())}'>"
                   f"<td class='num'>{i}</td><td>{stock}</td>"
                   f"<td class='num'>{r['weight'] * 100:.2f}%<i class='wbar' style='width:{min(100, r['weight'] * 400):.1f}%'></i></td>"
                   f"<td class='num'>{_money(r['value'])}</td>"
                   f"<td class='num {cls}'>{ch}</td></tr>")
    table = ("<section><div class='sec-h'><h2>Everything it owns</h2></div>"
             "<div class='filters'><input id='hq' type='search' placeholder='Search holdings…' "
             "aria-label='Search holdings'><span id='hn' class='mut' style='align-self:center'></span></div>"
             "<div class='wrap'><table class='tt ht'><thead><tr><th class='num'>#</th><th>Stock</th>"
             "<th class='num'>Share of fund</th><th class='num'>Value</th><th class='num'>Vs last quarter</th>"
             f"</tr></thead><tbody id='hb'>{''.join(trs)}</tbody></table></div>"
             "<p class='hint'>Values are as of the quarter end, as reported. Stocks only: options "
             "and bonds are left out. ↗ opens a quote on another site (no page here yet).</p></section>")
    sold_html = ""
    if gone:
        items = []
        for t, issuer, w in gone[:40]:
            href, ext = stock_link(t, issuer, pages, root)
            label = "<b>" + E(t or (issuer or "").title()) + "</b>"
            items.append(f"<li>{_a(href, label, ext)} "
                         f"<span class='hint'>· {E((issuer or '').title())}"
                         f"{f' · was {w * 100:.1f}% of the fund' if w else ''}</span></li>")
        sold_html = ("<section class='panel'><h2>Sold everything of</h2><ul class='list'>"
                     + "".join(items) + "</ul></section>")
    js = """<script>(function(){var q=document.getElementById('hq'),rs=[].slice.call(document.querySelectorAll('#hb tr')),
c=document.getElementById('hn');function run(){var s=(q.value||'').toLowerCase().trim(),n=0;
rs.forEach(function(r){var ok=!s||r.dataset.q.indexOf(s)>=0;r.hidden=!ok;n+=ok});
c.textContent=s?n+' of '+rs.length:''}q.addEventListener('input',run)})()</script>"""
    return (head + stats + _treemap(rows, pages, root) + table + sold_html
            + f"<p class='hint'>Source: <a href='{E(sec)}' target='_blank' rel='noopener'>its 13F filings "
              "at the SEC</a>.</p>" + js)


def build(con, out, pages, page, updated):
    """Write f/<fund>.html for every fund in funds.txt. `page` wraps a body
    in the site layout: page(title, active, body, updated, root=...)."""
    import os
    os.makedirs(f"{out}/f", exist_ok=True)
    ports = funds.portfolios(con)
    gone = funds.sold(con)
    n = 0
    for f in funds.catalog():
        body = fund_page(con, f, ports.get(f["cik"]), gone.get(f["cik"], []), pages)
        with open(f"{out}/{funds.href(f['label'])}", "w", encoding="utf-8") as fh:
            fh.write(page(f["person"] or f["firm"], "funds", body, updated, root="../"))
        n += 1
    return n


CSS = """
.fst{max-width:620px}
.fl{list-style:none;margin:0;padding:0;background:var(--card);border:1px solid var(--line);border-radius:16px;overflow:hidden}
.fl li{display:grid;grid-template-columns:auto minmax(0,1fr) minmax(0,1.6fr);gap:12px;align-items:center;padding:10px 14px;border-bottom:1px solid var(--line)}
.fl li:last-child{border-bottom:0}.fl a{color:var(--fg);text-decoration:none}
.fl .t small,.fl .who small{display:block;color:var(--mut);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fl .who{font-size:13.5px}.fl .who b{color:var(--up)}.fl .who small a{color:var(--mut)}.fl .who small a:hover{color:var(--acc)}
.fchips{display:flex;gap:6px;flex-wrap:wrap;margin:-4px 0 14px}
.fchips button{font:600 13px inherit;border:1px solid var(--line);background:var(--card);color:var(--mut);border-radius:999px;padding:6px 12px;cursor:pointer}
.fchips button.on{background:var(--fg);color:var(--bg);border-color:var(--fg)}
.fgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px}
.fcard{display:flex;gap:12px;align-items:flex-start;padding:14px;background:var(--card);border:1px solid var(--line);
border-radius:16px;color:var(--fg);text-decoration:none;transition:border-color .15s,transform .15s}
.fcard:hover{border-color:var(--acc);transform:translateY(-1px)}.fcard[hidden]{display:none}
.fcard .t{min-width:0}.fcard b{display:block;line-height:1.25}
.fcard small{display:block;color:var(--mut);font-size:12.5px;line-height:1.35}.fcard small.m{margin-top:3px}
.fts{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.ft{display:inline-flex;align-items:center;gap:4px;font-size:12px;font-weight:700;background:var(--chip);border-radius:8px;padding:2px 7px 2px 2px}
.ft .lg{width:20px;height:20px;border-radius:6px;font-size:10px}
.fface{display:inline-flex;align-items:center;justify-content:center;flex:none;width:56px;height:56px;border-radius:50%;
overflow:hidden;background:var(--chip);color:var(--mut);font-weight:800;font-size:18px}
.fface img{width:100%;height:100%;object-fit:cover;object-position:50% 20%}
.fface.xl{width:84px;height:84px;font-size:28px}
.fhead{display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin:10px 0 6px}
.fhead h1{margin:0}.fhead .co{color:var(--mut)}.fhead .t{flex:1;min-width:200px}
.fhead .big{font-size:30px;font-weight:800;text-align:right;font-variant-numeric:tabular-nums}
.fhead .big span{display:block;font-size:13px;font-weight:500}
.fkv{grid-template-columns:repeat(6,1fr)}
.ftm{aspect-ratio:16/9}
.ht td{vertical-align:middle}.ht td.num{white-space:nowrap;position:relative}
.ht .wbar{display:block;height:3px;border-radius:2px;background:var(--acc);margin:4px 0 0 auto;opacity:.7}
.ht td.up{color:var(--up);font-weight:700}.ht td.down{color:var(--down);font-weight:700}
.ht th.num{text-align:right}.ht a{color:var(--fg);text-decoration:none}.ht a:hover{color:var(--acc)}
b.up{color:var(--up)}
@media(max-width:700px){.fkv{grid-template-columns:repeat(3,1fr)}.ftm{aspect-ratio:3/4}
.fl li{grid-template-columns:auto minmax(0,1fr);}.fl .who{grid-column:1/-1}
.fhead .big{text-align:left;width:100%}
.ht th:nth-child(4),.ht td:nth-child(4){display:none}.ht td,.ht th{padding:8px 6px}}
"""


def selftest():
    import duckdb
    import tempfile
    con = duckdb.connect()
    funds.load_fixture(con)
    cat = [dict(cik=1, label="Berkshire", firm="Berkshire Hathaway", person="Warren Buffett", group="Value"),
           dict(cik=2, label="Solo", firm="Solo Capital", person=None, group="Value"),
           dict(cik=3, label="Empty", firm="Empty Partners", person=None, group="Activists")]
    real = funds.catalog
    funds.catalog = lambda path="funds.txt": cat
    try:
        body = overview(con, {"AAA"})
        assert "href='f/berkshire.html'" in body and "href='s/AAA.html'" in body
        assert "Not loaded yet" in body and "data-g='Activists'" in body
        ports = funds.portfolios(con)
        assert [r["tkr"] for r in ports[1]["rows"]][:1] and len(ports[1]["rows"]) == 4
        page = fund_page(con, cat[0], ports[1], funds.sold(con).get(1, []), {"AAA"})
        assert "href='../s/AAA.html'" in page, "holdings with a stock page link to it"
        assert "look.html?s=BBB" in page, "others open the lookup page"
        assert "DDD" in page and "Sold everything of" in page
        assert "+50% shares" in page and "New buy" in page
        assert "haven't been loaded yet" in fund_page(con, cat[2], None, [], set())
        out = tempfile.mkdtemp()
        n = build(con, out, {"AAA"}, lambda t, a, b, u, root="": f"<title>{t}</title>{b}", "now")
        assert n == 3 and "Warren Buffett" in open(f"{out}/f/berkshire.html", encoding="utf-8").read()
    finally:
        funds.catalog = real
    print("fundpage selftest ok")


if __name__ == "__main__":
    selftest()
