"""Build the dashboard: a few static pages in site/ that GitHub Pages serves.

    py dashboard.py            # writes site/index.html, trades.html, backtest.html
    py dashboard.py --selftest

Pages:
  index.html     today's digest (same text as the email)
  trades.html    every congress buy/sell and insider buy/sell from the last
                 180 days, with a search box and filters -- runs in the
                 browser, no server
  backtest.html  the latest "did it beat SPY?" report, if backtest.py has run

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
import signals
from schema import DB_PATH, ensure_schema

OUT = "site"
DAYS = 180

CSS = """
:root{--bg:#fff;--fg:#1d1d1f;--mut:#6e6e73;--line:#e5e5ea;--acc:#0a66c2;--chip:#f2f2f7}
@media (prefers-color-scheme:dark){:root{--bg:#111;--fg:#eee;--mut:#9a9aa0;--line:#2c2c2e;--acc:#5aa9ff;--chip:#1c1c1e}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
header{border-bottom:1px solid var(--line);padding:14px 20px;display:flex;gap:20px;align-items:baseline;flex-wrap:wrap}
header b{font-size:17px}header a{color:var(--mut);text-decoration:none}header a.on{color:var(--fg);font-weight:600}
main{max-width:1100px;margin:0 auto;padding:20px}
h1{font-size:24px;margin:.2em 0 .6em}h2{font-size:18px;margin:1.6em 0 .4em;border-top:1px solid var(--line);padding-top:1em}
table{border-collapse:collapse;width:100%;font-size:13.5px}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--mut);font-weight:600;position:sticky;top:0;background:var(--bg)}
.wrap{overflow-x:auto}a{color:var(--acc)}code{background:var(--chip);padding:1px 4px;border-radius:4px}
.filters{display:flex;gap:10px;flex-wrap:wrap;margin:10px 0 14px}
.filters input,.filters select{font:inherit;padding:6px 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg)}
.filters input{flex:1;min-width:220px}.mut{color:var(--mut);font-size:13px}
.buy{color:#1a7f37;font-weight:600}.sell{color:#c62828;font-weight:600}
footer{color:var(--mut);font-size:12px;padding:30px 20px;text-align:center}
"""


def page(title, active, body, updated):
    nav = "".join(f'<a href="{href}" class="{"on" if key == active else ""}">{label}</a>'
                  for key, href, label in (("digest", "index.html", "Today"),
                                           ("trades", "trades.html", "All trades"),
                                           ("backtest", "backtest.html", "Did it work?")))
    return (f"<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)} · Quantreyda</title><style>{CSS}</style></head><body>"
            f"<header><b>Quantreyda</b>{nav}<span class='mut'>updated {updated}</span></header>"
            f"<main>{body}</main><footer>Public filings only (STOCK Act disclosures, SEC Form 4 and "
            f"13F, USAspending). Research, not investment advice.</footer></body></html>")


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
    try:
        body = md(digest.build(con, today, days=7))
    except Exception as e:  # one bad row shouldn't take the whole site down
        import traceback
        traceback.print_exc()
        body = (f"<h1>Today</h1><p>The digest couldn't be built this run "
                f"({html.escape(type(e).__name__)}). The other pages are current.</p>")
    with open(f"{out}/index.html", "w", encoding="utf-8") as f:
        f.write(page("Today", "digest", body, updated))
    with open(f"{out}/trades.html", "w", encoding="utf-8") as f:
        f.write(trades_page(trade_rows(con, today), updated))
    bt = "reports/backtest.md"
    body = md(open(bt, encoding="utf-8").read()) if os.path.exists(bt) else \
        "<h1>Did it work?</h1><p>The backtest hasn't run yet. It runs every Sunday.</p>"
    with open(f"{out}/backtest.html", "w", encoding="utf-8") as f:
        f.write(page("Did it work?", "backtest", body, updated))
    open(f"{out}/.nojekyll", "w").close()
    return out


def selftest():
    import tempfile
    con = duckdb.connect()
    signals.make_fixture(con)
    out = build(con, datetime.date(2025, 3, 15), tempfile.mkdtemp())
    idx = open(f"{out}/index.html", encoding="utf-8").read()
    assert "<table>" in idx and "AAA" in idx and "Where signals agree" in idx
    tr = open(f"{out}/trades.html", encoding="utf-8").read()
    rows = trade_rows(con, datetime.date(2025, 3, 31))
    assert {r["tkr"] for r in rows} >= {"AAA", "BBB", "XYZ", "SEL"}, rows
    assert next(r for r in rows if r["tkr"] == "BBB")["side"] == "sell"
    assert all(r["src"] != "Insider" or r["side"] in ("buy", "sell") for r in rows)
    con.execute("UPDATE senate_trades SET asset_name = '</script><b>x' WHERE ticker = 'AAA'")
    tr = trades_page(trade_rows(con, datetime.date(2025, 3, 31)), "now")
    assert "</script><b>x" not in tr, "data can't break out of the script tag"
    assert "watchlist" not in idx.lower() or "Your watchlist" not in idx
    assert "did it work" in open(f"{out}/backtest.html", encoding="utf-8").read().lower()
    print("dashboard selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        c = duckdb.connect(DB_PATH)
        ensure_schema(c)
        print(f"built {build(c, datetime.date.today())}/")
