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
  s/<TKR>.html   one page per stock: chart with the buys, health, news,
                 and a plain-English explainer

Your watchlist is never written to the site: the page is public, your
holdings aren't.
"""

import datetime
import html
import json
import os
import sys

import duckdb

import digest
import leaders
import signals
import stockpage
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
.toggle{border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:999px;
padding:6px 12px;font:600 13px inherit;cursor:pointer}
main{max-width:1180px;margin:0 auto;padding:24px 20px}
h1{font-size:30px;letter-spacing:-.6px;margin:.1em 0 .3em}h2{font-size:20px;letter-spacing:-.3px;margin:34px 0 10px}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--mut);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.5px;
position:sticky;top:57px;background:var(--bg)}
tbody tr:hover{background:var(--card)}
.wrap{overflow-x:auto}a{color:var(--acc)}code{background:var(--chip);padding:1px 5px;border-radius:5px}
.filters{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0 16px}
.filters input,.filters select{font:inherit;padding:9px 12px;border:1px solid var(--line);border-radius:12px;
background:var(--card);color:var(--fg)}
.filters input{flex:1;min-width:240px}.mut{color:var(--mut);font-size:13px}
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


def page(title, active, body, updated, root=""):
    nav = "".join(f'<a href="{root}{href}" class="nav{" on" if key == active else ""}">{label}</a>'
                  for key, href, label in (("digest", "index.html", "Ideas"),
                                           ("leaders", "leaders.html", "Leaderboard"),
                                           ("trades", "trades.html", "All trades"),
                                           ("backtest", "backtest.html", "Did it work?")))
    return (f"<!DOCTYPE html><html lang='en' data-theme='dark'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)} · Quantreyda</title>{THEME_HEAD}"
            f"<style>{CSS}{front.CSS}{stockpage.CSS}{leaders.LB_CSS}</style></head><body>"
            f"<header><div class='bar'><span class='logo'><i></i>Quantreyda</span>{nav}"
            f"<span class='sp'></span><span class='upd'>updated {updated}</span>"
            f"<button class='toggle' id='theme' type='button'>☀ Light</button></div></header>"
            f"<main>{body}</main><footer>Public filings only (STOCK Act disclosures, SEC Form 4 and "
            f"13F, USAspending). Research, not investment advice.</footer>{THEME_JS}</body></html>")


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
            if owner and str(owner).lower() not in ("self", "none"):
                who += f" ({str(owner).lower()})"
            rows.append({"filed": str(filed), "traded": str(txn or ""), "tkr": tkr,
                         "src": "Senate" if ch == "S" else "House", "who": who, "side": side,
                         "amt": digest._bracket(lo, hi), "lo": lo or 0, "what": asset or ""})
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
                         "amt": digest._money(val or 0), "lo": val or 0, "what": issuer or ""})
    rows.sort(key=lambda x: x["filed"], reverse=True)
    return rows


TRADES_JS = """
const rows = DATA, tb = document.querySelector('tbody'), q = document.getElementById('q'),
      src = document.getElementById('src'), side = document.getElementById('side'),
      size = document.getElementById('size'), count = document.getElementById('count');
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function draw() {
  const t = q.value.trim().toLowerCase(), min = +size.value;
  const hit = rows.filter(r => (!t || (r.tkr + ' ' + r.who + ' ' + r.what).toLowerCase().includes(t))
    && (!src.value || r.src === src.value || (src.value === 'Congress' && r.src !== 'Insider'))
    && (!side.value || r.side === side.value) && r.lo >= min);
  count.textContent = hit.length + ' of ' + rows.length;
  tb.innerHTML = hit.slice(0, 1000).map(r => `<tr><td>${r.filed}</td><td><b>${esc(r.tkr)}</b></td>
    <td class="${r.side}">${r.side}</td><td>${esc(r.who)}</td><td>${r.src}</td><td>${esc(r.amt)}</td>
    <td>${r.traded}</td><td class="mut">${esc(r.what).slice(0, 60)}</td></tr>`).join('');
}
[q, src, side, size].forEach(el => el.addEventListener('input', draw));
const p = new URLSearchParams(location.search); if (p.get('q')) q.value = p.get('q');
draw();
"""


def trades_page(rows, updated):
    # "</" is escaped so no data string can close the script tag early
    data = json.dumps(rows).replace("</", "<\\/")
    body = (f"<h1>All trades, last {DAYS} days</h1>"
            "<p class='mut'>Every disclosed congressional trade and insider open-market buy or "
            "sale with a ticker. Search a ticker, a name or a company. Dates are filing dates "
            "(when the public found out).</p>"
            "<div class='filters'><input id='q' placeholder='Search ticker, person or company…'>"
            "<select id='src'><option value=''>Everyone</option><option>Congress</option>"
            "<option>Senate</option><option>House</option><option>Insider</option></select>"
            "<select id='side'><option value=''>Buys and sells</option><option value='buy'>Buys</option>"
            "<option value='sell'>Sells</option></select>"
            "<select id='size'><option value='0'>Any size</option><option value='15001'>$15k+</option>"
            "<option value='50001'>$50k+</option><option value='100000'>$100k+</option>"
            "<option value='250001'>$250k+</option><option value='1000000'>$1M+</option></select>"
            "<span id='count' class='mut' style='align-self:center'></span></div>"
            "<div class='wrap'><table><thead><tr><th>Filed</th><th>Ticker</th><th>Side</th>"
            "<th>Who</th><th>Source</th><th>Amount</th><th>Traded</th><th>Asset</th></tr></thead>"
            "<tbody></tbody></table></div>"
            f"<script>const DATA = {data};{TRADES_JS}</script>")
    return page("All trades", "trades", body, updated)


def build(con, today, out=OUT):
    os.makedirs(out, exist_ok=True)
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
    with open(f"{out}/trades.html", "w", encoding="utf-8") as f:
        f.write(trades_page(trade_rows(con, today), updated))
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
    pages = stockpage.pick(con, today, idea_tickers)
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
    print("dashboard selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        c = duckdb.connect(DB_PATH)
        ensure_schema(c)
        print(f"built {build(c, datetime.date.today())}/")
