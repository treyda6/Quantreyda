"""Build the dashboard: a few static pages in site/ that GitHub Pages serves.

    py dashboard.py            # writes site/index.html, trades.html, backtest.html
    py dashboard.py --selftest

Pages:
  index.html     today's digest (same text as the email)
  trades.html    every congress buy/sell and insider buy/sell from the last
                 180 days, with a search box and filters -- runs in the
                 browser, no server
  backtest.html  the latest "did it beat SPY?" report, if backtest.py has run
  leaders.html   politicians and insiders ranked by how their buys did
  moves.html     sector heatmap, analyst rating changes, unusual options bets
  s/<TKR>.html   one page per stock: chart with the buys, health, news,
                 and a plain-English explainer

Your watchlist is never written to the site: the page is public, your
holdings aren't.
"""

import datetime
import html
import json
import os
import shutil
import sys

import duckdb

import committees
import digest
import markets
import moves
import pictures
import sections
import leaders
import signals
import stockpage
import whitehouse
import today as front
from schema import DB_PATH, ensure_schema, load_report

OUT = "site"
DAYS = 180

CSS = """
:root,[data-theme=dark]{--bg:#0a0c10;--card:#12151b;--card2:#191d25;--fg:#f3f5f8;--mut:#8a93a3;
--line:#232934;--acc:#2fe0a0;--ins:#8b9cff;--acc-ink:#04140d;--up:#2fe0a0;--down:#ff5c63;--warn:#ffb648;--chip:#1d222b;
--shadow:0 1px 0 rgba(255,255,255,.03),0 10px 30px rgba(0,0,0,.35)}
[data-theme=light]{--bg:#f4f6f9;--card:#ffffff;--card2:#f1f3f7;--fg:#0c1117;--mut:#5f6b7a;
--line:#e3e7ee;--acc:#00a86b;--ins:#4f5fe0;--acc-ink:#ffffff;--up:#00995f;--down:#e0353c;--warn:#b26b00;--chip:#eef1f5;
--shadow:0 1px 2px rgba(16,24,40,.06),0 8px 24px rgba(16,24,40,.06)}
*{box-sizing:border-box}html{background:var(--bg)}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
-webkit-font-smoothing:antialiased;transition:background .2s,color .2s}
header{position:sticky;top:0;z-index:20;backdrop-filter:blur(12px);
background:color-mix(in srgb,var(--bg) 82%,transparent);border-bottom:1px solid var(--line)}
.bar{max-width:1180px;margin:0 auto;padding:12px 20px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.logo{font-weight:800;font-size:18px;letter-spacing:-.3px;margin-right:14px;display:flex;align-items:center;gap:8px}
.logo i{width:10px;height:10px;border-radius:50%;background:var(--acc);box-shadow:0 0 12px var(--acc)}
.bar a.nav{color:var(--mut);text-decoration:none;font-weight:600;font-size:14px;padding:7px 12px;border-radius:999px}
.bar a.nav:hover{color:var(--fg)}.bar a.nav.on{background:var(--chip);color:var(--fg)}
.bar .sp{flex:1}.upd{color:var(--mut);font-size:12px}
.tabs{display:none}
@media(max-width:700px){
.bar{flex-wrap:nowrap;overflow-x:auto;scrollbar-width:none;gap:4px;padding:10px 14px}.bar::-webkit-scrollbar{display:none}
.bar a.nav{white-space:nowrap}.upd{display:none}
.tabs{display:flex;position:fixed;left:0;right:0;bottom:0;z-index:30;justify-content:space-around;
padding:8px 4px calc(8px + env(safe-area-inset-bottom));border-top:1px solid var(--line);
background:color-mix(in srgb,var(--bg) 88%,transparent);backdrop-filter:blur(14px)}
.tabs a{flex:1;display:flex;flex-direction:column;align-items:center;gap:3px;color:var(--mut);
text-decoration:none;font-size:11px;font-weight:600}
.tabs svg{width:24px;height:24px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
.tabs a.on{color:var(--fg)}.tabs a.on svg{stroke:var(--acc)}
body{padding-bottom:74px}}
.toggle{border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:999px;
padding:6px 12px;font:600 13px inherit;cursor:pointer}
main{max-width:1180px;margin:0 auto;padding:24px 20px}
h1{font-size:30px;letter-spacing:-.6px;margin:.1em 0 .3em}h2{font-size:20px;letter-spacing:-.3px;margin:34px 0 10px}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--mut);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.5px;
position:sticky;top:57px;background:var(--bg)}
tbody tr:hover{background:var(--card)}
.wrap th{top:0}
.wrap{overflow-x:auto}a{color:var(--acc)}code{background:var(--chip);padding:1px 5px;border-radius:5px}
.filters{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0 16px}
.filters input,.filters select{font:inherit;padding:9px 12px;border:1px solid var(--line);border-radius:12px;
background:var(--card);color:var(--fg)}
.filters input{flex:1;min-width:240px}.mut{color:var(--mut);font-size:13px}
.fam{font-size:11px;font-weight:700;color:var(--warn);background:color-mix(in srgb,var(--warn) 15%,transparent);
border-radius:6px;padding:1px 6px;margin-left:4px}
.tt td{vertical-align:middle}
@media(max-width:640px){.tt th:nth-child(4),.tt td:nth-child(4){display:none}.tt td,.tt th{padding:8px 6px}
.tt .pic{gap:7px}.tt .pic .t small{max-width:110px}.tt .lg,.tt .face{width:28px;height:28px}}.tt .num{text-align:right;font-weight:700;font-variant-numeric:tabular-nums}
.buy{color:var(--up);font-weight:700}.sell{color:var(--down);font-weight:700}
footer{color:var(--mut);font-size:12px;padding:40px 20px;text-align:center}
"""

THEME_HEAD = """<script>(function(){var t=localStorage.getItem('qt-theme')||
(matchMedia('(prefers-color-scheme: light)').matches?'light':'dark');
document.documentElement.setAttribute('data-theme',t)})()</script>"""

THEME_JS = """<script>(function(){var b=document.getElementById('theme');
function label(){b.textContent=document.documentElement.getAttribute('data-theme')==='dark'?'☀ Light':'☾ Dark'}
label();b.onclick=function(){var t=document.documentElement.getAttribute('data-theme')==='dark'?'light':'dark';
document.documentElement.setAttribute('data-theme',t);localStorage.setItem('qt-theme',t);label()}})()</script>"""


ICONS = {  # simple 24px line icons for the phone tab bar
    "digest": "<path d='M12 3l2.6 5.6 6.1.7-4.5 4.2 1.2 6L12 16.6 6.6 19.5l1.2-6L3.3 9.3l6.1-.7z'/>",
    "congress": "<path d='M3 10h18M5 10v8M9.5 10v8M14.5 10v8M19 10v8M3 20h18M12 3l9 5H3z'/>",
    "insiders": "<circle cx='12' cy='8' r='4'/><path d='M4 21c1-4.5 4.2-6.5 8-6.5s7 2 8 6.5'/>",
    "funds": "<path d='M4 20V10M10 20V4M16 20v-7M21 20H3'/>",
    "trades": "<circle cx='11' cy='11' r='6.5'/><path d='M16 16l5 5'/>",
}
NAV = (("digest", "index.html", "Ideas"), ("congress", "congress.html", "Congress"),
       ("insiders", "insiders.html", "Insiders"), ("funds", "funds.html", "Funds"),
       ("trades", "trades.html", "All trades"), ("moves", "moves.html", "Moves"),
       ("odds", "markets.html", "Odds"),
       ("leaders", "leaders.html", "Leaderboard"),
       ("backtest", "backtest.html", "Did it work?"))


APP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app")


def app_head(root=""):
    """Tags that let phones add the site to the home screen as an app."""
    return (f"<link rel='manifest' href='{root}manifest.webmanifest'>"
            f"<link rel='apple-touch-icon' href='{root}icon-180.png'>"
            f"<link rel='icon' type='image/png' href='{root}icon-192.png'>"
            "<meta name='theme-color' content='#0a0c10'>"
            "<meta name='mobile-web-app-capable' content='yes'>"
            "<meta name='apple-mobile-web-app-capable' content='yes'>"
            "<meta name='apple-mobile-web-app-title' content='Quantreyda'>"
            "<meta name='apple-mobile-web-app-status-bar-style' content='black'>"
            "<script>if('serviceWorker' in navigator)addEventListener('load',()=>"
            f"navigator.serviceWorker.register('{root}sw.js').catch(()=>{{}}))</script>")


def page(title, active, body, updated, root=""):
    nav = "".join(f'<a href="{root}{href}" class="nav{" on" if key == active else ""}">{label}</a>'
                  for key, href, label in NAV)
    tabs = "".join(
        f"<a href='{root}{href}' class='{'on' if key == active else ''}'><svg viewBox='0 0 24 24' "
        f"aria-hidden='true'>{ICONS[key]}</svg><span>{'Search' if key == 'trades' else label}</span></a>"
        for key, href, label in NAV if key in ICONS)
    return (f"<!DOCTYPE html><html lang='en' data-theme='dark'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)} · Quantreyda</title>{THEME_HEAD}{app_head(root)}"
            f"<style>{CSS}{front.CSS}{stockpage.CSS}{leaders.LB_CSS}{pictures.CSS}{sections.CSS}{markets.CSS}{moves.CSS}</style></head><body>"
            f"<header><div class='bar'><span class='logo'><i></i>Quantreyda</span>{nav}"
            f"<span class='sp'></span><span class='upd'>updated {updated}</span>"
            f"<button class='toggle' id='theme' type='button'>☀ Light</button></div></header>"
            f"<main>{body}</main><footer>Public filings only (STOCK Act disclosures, SEC Form 4 and "
            f"13F, USAspending). Research, not investment advice. Member portraits: public domain "
            f"(@unitedstates); other photos: Wikimedia Commons.</footer>"
            f"<nav class='tabs'>{tabs}</nav>{THEME_JS}</body></html>")


def md(text):
    import markdown
    return f"<div class='wrap'>{markdown.markdown(text, extensions=['tables'])}</div>"


def trade_rows(con, today, days=DAYS):
    """Flat list of dicts for the trades page, newest first."""
    start = today - datetime.timedelta(days=days)
    rows = []
    if signals.has_table(con, "senate_trades"):
        for r in con.execute("""
                SELECT filed_date, txn_date, tkr, chamber, first_name, last_name, owner,
                       tx_type, amount_low, amount_high, asset_name
                FROM trades WHERE filed_date BETWEEN ? AND ? AND tkr IS NOT NULL
                ORDER BY filed_date DESC""", [start, today]).fetchall():
            filed, txn, tkr, ch, fn, ln, owner, tx, lo, hi, asset = r
            side = "buy" if tx == "Purchase" else "sell" if "Sale" in (tx or "") else "other"
            who = f"{fn or ''} {(ln or '').strip(' ,.')}".strip()
            o = str(owner or "").lower()
            own = "family" if o and o not in ("self", "none", "nan", "joint") else "self"
            if own == "family":
                who += f" ({front.OWNER.get(o, o)})"
            rows.append({"filed": str(filed), "traded": str(txn or ""), "tkr": tkr,
                         "src": "Senate" if ch == "S" else "House", "who": who, "side": side,
                         "amt": digest._bracket(lo, hi), "lo": lo or 0, "hi": hi, "what": asset or "",
                         "own": own, "last": ln, "first": fn})
    if signals.has_table(con, "insider_trades"):
        for r in con.execute("""
                SELECT try_cast(filed_date AS DATE) fd, min(trans_date), upper(trim(ticker)),
                       owner_name, owner_relationship, trans_code, sum(shares * price_per_share),
                       min(issuer_name)
                FROM insider_trades
                WHERE trans_code IN ('P','S') AND price_per_share > 0 AND ticker IS NOT NULL
                  AND try_cast(filed_date AS DATE) BETWEEN ? AND ?
                GROUP BY fd, upper(trim(ticker)), owner_name, owner_relationship, trans_code
                ORDER BY fd DESC""", [start, today]).fetchall():
            fd, td, tkr, owner, rel, code, val, issuer = r
            rows.append({"filed": str(fd), "traded": str(td or ""), "tkr": tkr,
                         "src": "Insider", "who": f"{owner} ({(rel or 'insider').lower()})",
                         "side": "buy" if code == "P" else "sell",
                         "amt": digest._money(val or 0), "lo": val or 0, "what": issuer or "",
                         "own": "self"})
    if signals.has_table(con, "wh_trades"):
        for r in con.execute("""
                SELECT filed, tx_date, tkr, name, title, tx_type, amount_low, amount_high,
                       description, kind, slug
                FROM wh_trades WHERE filed BETWEEN ? AND ? AND tkr IS NOT NULL
                ORDER BY filed DESC""", [start, today]).fetchall():
            filed, txn, tkr, name, title, tx, lo, hi, desc, kind, slug = r
            rows.append({"filed": str(filed), "traded": str(txn or ""), "tkr": tkr,
                         "src": whitehouse.group(title), "who": f"{name} ({title})",
                         "side": "buy" if tx == "Purchase" else "sell" if tx == "Sale" else "other",
                         "amt": digest._bracket(lo, hi), "lo": lo or 0, "hi": hi,
                         "what": (kind + ": " if kind != "stock" else "") + (desc or ""),
                         "own": "self", "slug": slug})
    rows.sort(key=lambda x: x["filed"], reverse=True)
    decorate(con, rows)
    return rows


def _members(con):
    """{chamber: roster rows} for matching filers to bioguide IDs and parties."""
    try:
        rows = con.execute("SELECT bioguide, first, nickname, last, party, chamber FROM members").fetchall()
    except Exception:
        return {}
    out = {}
    for r in rows:
        out.setdefault(r[5], []).append(r)
    return out


def _closes(con, tickers):
    """{tkr: ([dates], [closes])} oldest first, from the cached prices."""
    import prices
    try:
        rows = con.execute("SELECT tkr, d, close FROM prices WHERE tkr IN (SELECT unnest(?)) "
                           "ORDER BY tkr, d", [sorted(set(tickers) | {prices.BENCHMARK})]).fetchall()
    except Exception:
        return {}
    out = {}
    for t, d, c in rows:
        ds, cs = out.setdefault(t, ([], []))
        ds.append(str(d))
        cs.append(c)
    return out


def excess_since(px, tkr, day, bench="SPY"):
    """The stock's return since `day` minus the S&P 500's (SPY), or None."""
    import bisect
    if tkr not in px or bench not in px or not day:
        return None

    def ret(t):
        ds, cs = px[t]
        i = bisect.bisect_left(ds, day[:10])
        if i >= len(ds) or (len(ds) - 1 - i) < 1:
            return None
        return cs[-1] / cs[i] - 1
    a, b = ret(tkr), ret(bench)
    return None if a is None or b is None else round(a - b, 4)


def decorate(con, rows):
    """Add pictures, party and the since-the-trade excess return to each row."""
    roster, px, seen = _members(con), _closes(con, {r["tkr"] for r in rows}), {}
    for r in rows:
        r["xs"] = excess_since(px, r["tkr"], r["traded"] or r["filed"])
        r["logo"] = pictures.logo_src(r["tkr"])
        r["party"], r["face"] = "", ""
        if r["src"] in ("Senate", "House"):
            key = (r["src"], r.get("last"), r.get("first"))
            if key not in seen:
                hit = committees.match_people(roster.get(r["src"][0], []), r.get("last"), r.get("first"))
                seen[key] = hit[0] if hit else None
            m = seen[key]
            if m:
                r["party"] = (m[4] or "")[:1]
                r["face"] = pictures.face_src(pictures.member_key(m[0]))
        elif r.get("slug"):
            r["face"] = pictures.face_src(pictures.official_key(r["slug"]))
        r["ini"] = pictures.initials(r["who"].split(" (")[0])
        r.pop("last", None), r.pop("first", None), r.pop("slug", None)


TRADES_JS = """
const rows = DATA, tb = document.querySelector('tbody'), q = document.getElementById('q'),
      src = document.getElementById('src'), side = document.getElementById('side'),
      size = document.getElementById('size'), own = document.getElementById('own'), count = document.getElementById('count');
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function draw() {
  const t = q.value.trim().toLowerCase(), min = +size.value;
  const hit = rows.filter(r => (!t || (r.tkr + ' ' + r.who + ' ' + r.what).toLowerCase().includes(t))
    && (!src.value || r.src === src.value || (src.value === 'Congress' && (r.src === 'Senate' || r.src === 'House')))
    && (!own.value || r.own === own.value)
    && (!side.value || r.side === side.value) && r.lo >= min);
  count.textContent = hit.length + ' of ' + rows.length;
  tb.innerHTML = hit.slice(0, 1000).map(r => {
    const lg = r.logo ? `<span class="lg"><img src="${r.logo}" alt="" loading="lazy"></span>`
                      : `<span class="lg mono">${esc(r.tkr[0] || '?')}</span>`;
    const tint = r.party === 'R' ? ' r' : r.party === 'D' ? ' d' : '';
    const face = r.face ? `<span class="face${tint}"><img src="${r.face}" alt="" loading="lazy"></span>`
                        : `<span class="face mono${tint}">${esc(r.ini)}</span>`;
    const tag = r.src + (r.party ? ' / ' + r.party : '');
    const xs = r.xs === null || r.xs === undefined ? '<span class="mut">—</span>'
             : `<span class="${r.xs >= 0 ? 'up' : 'down'}">${r.xs >= 0 ? '+' : ''}${(r.xs * 100).toFixed(1)}%</span>`;
    const stock = PAGES[r.tkr] ? `<a href="s/${PAGES[r.tkr]}.html"><b>${esc(r.tkr)}</b></a>` : `<b>${esc(r.tkr)}</b>`;
    return `<tr><td><div class="pic">${lg}<div class="t">${stock}<small>${esc(r.what).slice(0, 40)}</small></div></div></td>
    <td class="${r.side}">${r.side}<small class="mut" style="display:block">${esc(r.amt)}</small></td>
    <td><div class="pic">${face}<div class="t">${esc(r.who)}${r.own === 'family' ? ' <span class="fam">family</span>' : ''}<small>${esc(tag)}</small></div></div></td>
    <td>${r.filed}</td><td>${r.traded}</td><td class="num">${xs}</td></tr>`}).join('');
}
[q, src, side, size, own].forEach(el => el.addEventListener('input', draw));
const p = new URLSearchParams(location.search); if (p.get('q')) q.value = p.get('q');
if (p.get('src')) src.value = p.get('src');
draw();
"""


def trades_page(rows, updated, built=()):
    # "</" is escaped so no data string can close the script tag early
    data = json.dumps(rows).replace("</", "<\\/")
    pages = json.dumps({t: stockpage.slug(t) for t in sorted(built)}).replace("</", "<\\/")
    body = (f"<h1>All trades, last {DAYS} days</h1>"
            "<p class='mut'>Every disclosed congressional, White House and cabinet trade and "
            "insider open-market buy or sale with a ticker. <span class='fam'>family</span> marks "
            "a trade in a spouse's or child's account. Search a ticker, a name or a company. Dates are filing dates "
            "(when the public found out).</p>"
            "<div class='filters'><input id='q' placeholder='Search ticker, person or company…'>"
            "<select id='src'><option value=''>Everyone</option><option>Congress</option>"
            "<option>Senate</option><option>House</option><option>White House</option><option>Fed</option>"
            "<option>Insider</option></select>"
            "<select id='own'><option value=''>Anyone's money</option><option value='self'>"
            "Their own</option><option value='family'>Spouse or child</option></select>"
            "<select id='side'><option value=''>Buys and sells</option><option value='buy'>Buys</option>"
            "<option value='sell'>Sells</option></select>"
            "<select id='size'><option value='0'>Any size</option><option value='15001'>$15k+</option>"
            "<option value='50001'>$50k+</option><option value='100000'>$100k+</option>"
            "<option value='250001'>$250k+</option><option value='1000000'>$1M+</option></select>"
            "<span id='count' class='mut' style='align-self:center'></span></div>"
            "<p class='mut' style='margin:-6px 0 10px'>“Since” is how the stock has done since the "
            "trade date compared with the S&amp;P 500 (green = beat it). Blank when we don't have "
            "its prices.</p>"
            "<div class='wrap'><table class='tt'><thead><tr><th>Stock</th><th>Trade</th>"
            "<th>Who</th><th>Filed</th><th>Traded</th><th title='Stock return since the trade minus "
            "the S&amp;P 500'>Since</th></tr></thead>"
            "<tbody></tbody></table></div>"
            f"<script>const DATA = {data}, PAGES = {pages};{TRADES_JS}</script>")
    return page("All trades", "trades", body, updated)


def build(con, today, out=OUT):
    os.makedirs(out, exist_ok=True)
    for name in os.listdir(APP_DIR):  # manifest, icons, service worker: the site root
        shutil.copy(os.path.join(APP_DIR, name), out)
    pictures.write(con, out)
    updated = datetime.datetime.now().strftime("%b %d, %Y %H:%M UTC") \
        if os.environ.get("GITHUB_ACTIONS") else f"{today:%b %d, %Y}"
    board = leaders.load(con)
    records = (board or {}).get("records") or {}
    idea_tickers = []
    try:
        # two weeks: enough to fill the page, still fresh (Congress files up to 45 days late anyway)
        data = front.collect(con, today, 14, records=records)
        idea_tickers = [i["tkr"] for i in data["ideas"]]
        body = front.render(data, today, 14)
    except Exception as e:  # one bad row shouldn't take the whole site down
        import traceback
        traceback.print_exc()
        body = (f"<h1>Today</h1><p>This week's summary couldn't be built this run "
                f"({html.escape(type(e).__name__)}). The other pages are current.</p>")
    with open(f"{out}/index.html", "w", encoding="utf-8") as f:
        f.write(page("Today", "digest", body, updated))
    bt_json = load_report(con, "reports/backtest.json")
    bt_md = load_report(con, "reports/backtest.md")
    if bt_json:
        body = front.render_backtest(json.loads(bt_json))
        if bt_md:
            body += ("<details><summary>Full numbers</summary>" + md(bt_md) + "</details>")
    else:
        body = ("<div class='hero'><h1>Did it work?</h1><p>The first check runs on Sunday. "
                "It will show, in plain English, whether each kind of signal has beaten "
                "an S&P 500 index fund.</p></div>")
    with open(f"{out}/backtest.html", "w", encoding="utf-8") as f:
        f.write(page("Did it work?", "backtest", body, updated))
    rows = trade_rows(con, today)
    # Every ticker on the all-trades page gets a page, so every one is
    # clickable. The picked ones (ideas, most bought) get the full research;
    # the rest show what's on file and fill in as data arrives.
    pages = stockpage.pick(con, today, idea_tickers)
    picked = set(pages)
    pages += [t for t in dict.fromkeys(r.get("tkr") for r in rows)
              if t and t not in picked and stockpage.VALID.fullmatch(t)]
    os.makedirs(f"{out}/s", exist_ok=True)
    built = []
    for t in pages:
        try:
            body = stockpage.render(con, t, today, records)
        except Exception as e:  # one odd ticker shouldn't stop the rest
            print(f"stock page {t} skipped ({type(e).__name__}: {e})")
            continue
        with open(f"{out}/{stockpage.href(t)}", "w", encoding="utf-8") as f:
            f.write(page(t, "", body, updated, root="../"))
        built.append(t)
    with open(f"{out}/trades.html", "w", encoding="utf-8") as f:
        f.write(trades_page(rows, updated, built))
    bt = json.loads(bt_json) if bt_json else {}
    for name, title, make in (("congress", "Congress", lambda: sections.page_congress(con, rows, today, bt, set(built))),
                              ("insiders", "Insiders", lambda: sections.page_insiders(con, rows, today, bt, set(built))),
                              ("funds", "Funds", lambda: sections.page_funds(con, today, set(built))),
                              ("markets", "Odds", lambda: markets.page(con)),
                              ("moves", "Market moves", lambda: moves.page(con, today, set(built)))):
        try:
            body = make()
        except Exception as e:  # one section failing shouldn't take the site down
            print(f"{name} page skipped ({type(e).__name__}: {e})")
            body = f"<h1>{title}</h1><p>This page couldn't be built this run.</p>"
        with open(f"{out}/{name}.html", "w", encoding="utf-8") as f:
            f.write(page(title, "odds" if name == "markets" else name, body, updated))
    with open(f"{out}/leaders.html", "w", encoding="utf-8") as f:
        f.write(page("Leaderboard", "leaders", leaders.render(con, board, built), updated))
    open(f"{out}/.nojekyll", "w").close()
    return out


def selftest():
    import tempfile
    con = duckdb.connect()
    signals.make_fixture(con)
    out = build(con, datetime.date(2025, 3, 15), tempfile.mkdtemp())
    idx = open(f"{out}/index.html", encoding="utf-8").read()
    assert "30-second version" in idx and "AAA" in idx
    assert "href='s/AAA.html'" in idx, "idea cards link to their stock page"
    st = open(f"{out}/s/AAA.html", encoding="utf-8").read()
    assert "About this stock" in st and "href=\"../leaders.html\"" in st
    # home-screen app: files at the site root, every page points at them
    assert all(os.path.exists(f"{out}/{f}") for f in ("manifest.webmanifest", "sw.js", "icon-180.png"))
    assert json.load(open(f"{out}/manifest.webmanifest"))["start_url"] == "./index.html"
    assert "href='manifest.webmanifest'" in idx and "register('sw.js')" in idx
    assert "href='../manifest.webmanifest'" in st and "register('../sw.js')" in st
    assert "Whose buys beat the market?" in open(f"{out}/leaders.html", encoding="utf-8").read()
    tr = open(f"{out}/trades.html", encoding="utf-8").read()
    rows = trade_rows(con, datetime.date(2025, 3, 31))
    assert {r["tkr"] for r in rows} >= {"AAA", "BBB", "XYZ", "SEL"}, rows
    assert next(r for r in rows if r["tkr"] == "BBB")["side"] == "sell"
    assert all(r["src"] != "Insider" or r["side"] in ("buy", "sell") for r in rows)
    con.execute("UPDATE senate_trades SET asset_name = '</script><b>x' WHERE ticker = 'AAA'")
    tr = trades_page(trade_rows(con, datetime.date(2025, 3, 31)), "now")
    assert "</script><b>x" not in tr, "data can't break out of the script tag"
    assert "watchlist" not in idx.lower() or "Your watchlist" not in idx
    assert "first check runs on sunday" in open(f"{out}/backtest.html", encoding="utf-8").read().lower()
    cg = open(f"{out}/congress.html", encoding="utf-8").read()
    assert "Congress trading" in cg and "class='tabs'" in cg and "href='congress.html' class='on'" in cg
    assert "Insider trading" in open(f"{out}/insiders.html", encoding="utf-8").read()
    assert "Funds" in open(f"{out}/funds.html", encoding="utf-8").read()
    assert "What bettors expect" in open(f"{out}/markets.html", encoding="utf-8").read()
    mv = open(f"{out}/moves.html", encoding="utf-8").read()
    assert "Market moves" in mv and "href='moves.html' class='on'" not in mv and 'class="nav on">Moves' in mv
    print("dashboard selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        c = duckdb.connect(DB_PATH)
        ensure_schema(c)
        print(f"built {build(c, datetime.date.today())}/")
