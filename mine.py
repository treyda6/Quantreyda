"""The "Mine" page: your own portfolios, watchlist and alerts, kept private.

The site is public and identical for everyone who opens the link, so nothing
personal is ever built into it. Instead the site publishes one public file,
mine.json (prices for popular funds and every recently traded stock, plus the
last few weeks of trades and company news), and the page's JavaScript
(app/mine.js, app/personal.js) combines it with what you've typed in, which is
saved only in your own browser. Back it up or move it to another device with
the Backup tab (optionally password-encrypted).

    py mine.py --selftest
"""

import bisect
import datetime
import json
import os
import re
import sys

import health
import news
import pictures
import signals
import stockpage

UNIVERSE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "universe.txt")
EVENT_DAYS = 45    # how far back trades and news go in mine.json (alerts look at 30)
QUOTE_DAYS = 380   # enough history for a 1-year return and 52-week range


def universe(path=UNIVERSE):
    """Tickers from universe.txt (anything after # ignored)."""
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return []
    return sorted({t.upper() for line in text.splitlines()
                   for t in line.split("#")[0].split() if stockpage.VALID.fullmatch(t.upper())})


def _ret(cs, i):
    return round(cs[-1] / cs[i] - 1, 4) if 0 <= i < len(cs) - 1 and cs[i] else None


def quotes(con, today):
    """{tkr: [close, 1d, 5d, 1m, 1y, 52w high, 52w low, date]} from the cached prices."""
    start = today - datetime.timedelta(days=QUOTE_DAYS)
    try:
        rows = con.execute("SELECT tkr, d, close FROM prices WHERE d >= ? AND close > 0 "
                           "ORDER BY tkr, d", [start]).fetchall()
    except Exception:  # no prices table yet
        return {}
    series = {}
    for t, d, c in rows:
        ds, cs = series.setdefault(t, ([], []))
        ds.append(str(d))
        cs.append(float(c))
    out = {}
    for t, (ds, cs) in series.items():
        n = len(cs)
        last = datetime.date.fromisoformat(ds[-1])
        year_ago = str(last - datetime.timedelta(days=365))
        i_y = bisect.bisect_left(ds, year_ago)
        tail = cs[bisect.bisect_left(ds, year_ago):]
        out[t] = [round(cs[-1], 4), _ret(cs, n - 2), _ret(cs, n - 6), _ret(cs, n - 22),
                  _ret(cs, i_y) if ds[0] <= year_ago else None,
                  round(max(tail), 4), round(min(tail), 4), ds[-1]]
    return out


POL = {"Senate", "House"}


def events(con, rows, today):
    """Recent trades and company news, flat and small, newest first."""
    start = str(today - datetime.timedelta(days=EVENT_DAYS))
    out = []
    for r in rows:
        if r["filed"] < start or r["side"] not in ("buy", "sell"):
            continue
        e = {"t": r["tkr"], "d": r["filed"], "k": "ins" if r["src"] == "Insider" else "pol",
             "s": r["side"], "w": r["who"], "a": r["amt"]}
        if e["k"] == "pol":
            e["g"] = "Congress" if r["src"] in POL else r["src"]
        out.append(e)
    try:
        for t, filed, form, what, url in con.execute(
                "SELECT tkr, filed, form, what, url FROM news WHERE filed >= ?", [start]).fetchall():
            out.append({"t": t, "d": str(filed), "k": "news", "x": what, "f": form, "u": url})
    except Exception:  # news table not created yet
        pass
    out.sort(key=lambda e: e["d"], reverse=True)
    return out


def names(rows):
    """{tkr: company name} from insider filings (issuer names are clean)."""
    out = {}
    for r in rows:
        if r["src"] == "Insider" and r.get("what") and r["tkr"] not in out:
            out[r["tkr"]] = r["what"][:60]
    return out


def data(con, rows, today, ideas=(), built=()):
    """Everything mine.json holds. Public data only: identical for every visitor."""
    q = quotes(con, today)
    ev = events(con, rows, today)
    tickers = set(q) | {e["t"] for e in ev}
    hl = {t: h.get("verdict") for t, h in health.lookup(con, tickers).items()
          if h.get("verdict") and h.get("verdict") != "Unknown"}
    return {"asof": str(today), "quotes": q, "events": ev,
            "ideas": {t: i + 1 for i, t in enumerate(ideas)},
            "health": hl, "names": names(rows),
            "logos": {t: src for t in sorted(tickers) if (src := pictures.logo_src(t))},
            "pages": {t: stockpage.slug(t) for t in sorted(built)}}


def write(con, rows, today, out, ideas=(), built=()):
    d = data(con, rows, today, ideas, built)
    with open(f"{out}/mine.json", "w", encoding="utf-8") as f:
        json.dump(d, f, separators=(",", ":"))
    return d


CSS = """
#mine-app{--r:16px}
.mi-priv{display:flex;gap:10px;align-items:flex-start;padding:12px 16px;border-radius:14px;margin:4px 0 18px;
background:color-mix(in srgb,var(--acc) 10%,var(--card));border:1px solid color-mix(in srgb,var(--acc) 30%,var(--line));font-size:14px}
.mi-priv svg{flex:none;width:20px;height:20px;stroke:var(--acc);fill:none;stroke-width:2;margin-top:1px}
.mi-tabs{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 18px;border-bottom:1px solid var(--line);padding-bottom:10px}
.mi-tabs button{font:600 14px inherit;padding:8px 14px;border-radius:999px;border:0;background:none;color:var(--mut);cursor:pointer}
.mi-tabs button.on{background:var(--chip);color:var(--fg)}
.mi-tabs .qt-badge{vertical-align:1px}
.mi-card{background:var(--card);border:1px solid var(--line);border-radius:var(--r);padding:16px 18px;margin:0 0 16px;box-shadow:var(--shadow)}
.mi-card h3{margin:0;font-size:18px;letter-spacing:-.2px}
.mi-head{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center}
.mi-head .sp{flex:1}
.mi-kind{font-size:12px;font-weight:700;padding:2px 9px;border-radius:999px;background:var(--chip);color:var(--mut)}
.mi-kind.roth,.mi-kind.custodial_roth{color:var(--acc)}.mi-kind.utma{color:var(--ins)}
.mi-stats{display:flex;flex-wrap:wrap;gap:10px 28px;margin:12px 0 4px}
.mi-stats>div{display:flex;flex-direction:column}.mi-stats>div>span{color:var(--mut);font-size:12px}
.mi-stats b{font-size:20px;letter-spacing:-.3px;font-variant-numeric:tabular-nums}
.mi-big b{font-size:28px}
.mi-note{color:var(--mut);font-size:13px;margin:6px 0 0}
.mi-room{height:8px;border-radius:99px;background:var(--chip);overflow:hidden;margin:6px 0 2px;max-width:360px}
.mi-room i{display:block;height:100%;background:var(--acc)}
.mi-t{width:100%;margin-top:10px}.mi-t td,.mi-t th{vertical-align:middle;padding:8px 8px}
.mi-t th{position:static}
.mi-t .num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.mi-t input{width:100%;min-width:60px;font:inherit;padding:6px 8px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg)}
.up{color:var(--up)}.down{color:var(--down)}
.mi-chip{display:inline-block;font-size:11.5px;font-weight:600;padding:2px 8px;border-radius:999px;margin:2px 4px 2px 0;
background:var(--chip);color:var(--fg);white-space:nowrap;text-decoration:none}
.mi-chip.buy{color:var(--up);background:color-mix(in srgb,var(--up) 14%,transparent)}
.mi-chip.sell{color:var(--down);background:color-mix(in srgb,var(--down) 14%,transparent)}
.mi-chip.news{color:var(--ins);background:color-mix(in srgb,var(--ins) 14%,transparent)}
.mi-chip.idea{color:var(--warn);background:color-mix(in srgb,var(--warn) 15%,transparent)}
.mi-form{display:flex;flex-wrap:wrap;gap:8px;align-items:flex-end;margin:10px 0 0}
.mi-form label{display:flex;flex-direction:column;gap:3px;font-size:12px;color:var(--mut);font-weight:600}
.mi-form input,.mi-form select,.mi-form textarea,.mi-set select{font:inherit;font-size:14px;padding:8px 10px;border:1px solid var(--line);
border-radius:10px;background:var(--bg);color:var(--fg)}
.mi-form textarea{width:100%;min-height:90px;font-family:ui-monospace,Menlo,monospace;font-size:13px}
.mi-btn{font:600 14px inherit;padding:8px 14px;border-radius:10px;border:1px solid var(--line);background:var(--card2);color:var(--fg);cursor:pointer}
.mi-btn.pri{background:var(--acc);color:var(--acc-ink);border-color:var(--acc)}
.mi-btn.dan{color:var(--down)}
.mi-btn.sm{padding:4px 10px;font-size:13px;border-radius:8px}
.mi-x{border:0;background:none;color:var(--mut);cursor:pointer;font-size:18px;line-height:1;padding:2px 6px}
.mi-x:hover{color:var(--down)}
.mi-empty{color:var(--mut);padding:18px;text-align:center;border:1px dashed var(--line);border-radius:14px}
.mi-al{display:flex;gap:12px;align-items:flex-start;padding:12px 4px;border-bottom:1px solid var(--line)}
.mi-al .dot{width:8px;height:8px;border-radius:50%;margin-top:7px;flex:none;background:transparent}
.mi-al.new .dot{background:var(--down)}
.mi-al .body{flex:1}.mi-al small{color:var(--mut)}
.mi-set label.row{display:flex;gap:10px;align-items:center;padding:7px 0;font-size:14.5px}
.mi-set input[type=checkbox]{width:18px;height:18px;accent-color:var(--acc)}
.mi-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:2px 16px}
.mi-msg{font-size:13px;margin-top:8px}
.mi-imp{border:1px solid var(--line);border-radius:14px;padding:12px 14px;margin:12px 0;background:var(--card2)}
.mi-imp select.need{border-color:var(--warn);box-shadow:0 0 0 2px color-mix(in srgb,var(--warn) 30%,transparent)}
#mi-import summary{cursor:pointer;font-size:15px}
.mi-form label{position:relative}
.mi-ac{min-width:min(360px,86vw)}.mi-ac em{font-style:normal;font-size:12px;color:var(--mut)}
.mi-unk{margin:10px 0 0;padding:10px 12px;border-radius:12px;font-size:13.5px;line-height:1.5;
background:color-mix(in srgb,var(--warn) 12%,var(--card));border:1px solid color-mix(in srgb,var(--warn) 35%,var(--line))}
.mi-unk .mi-btn{margin-left:4px}
.mi-stock{display:flex;align-items:center;gap:10px}.mi-stock small{display:block;color:var(--mut);font-size:12px;max-width:180px;
overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mi-chip.add{color:var(--mut);background:none;border:1px dashed var(--line)}
button.mi-chip{font-family:inherit;cursor:pointer;border:0}button.mi-chip.add{border:1px dashed var(--line)}
.mi-kind.due{color:var(--down);background:color-mix(in srgb,var(--down) 14%,transparent)}
.mi-j.due{border-color:color-mix(in srgb,var(--down) 45%,var(--line))}.mi-j.closed{opacity:.85}
.mi-j.flash{animation:mi-flash 1.6s ease-out}@keyframes mi-flash{from{box-shadow:0 0 0 3px var(--acc)}}
.mi-jgrid{display:grid;grid-template-columns:1fr 1fr;gap:6px 22px;margin:12px 0 2px}
.mi-jgrid span{color:var(--mut);font-size:12px;font-weight:600}
.mi-jgrid p{margin:3px 0 0;font-size:14.5px;white-space:pre-wrap;overflow-wrap:anywhere}
.mi-jhist{margin-top:10px}.mi-jhist summary,.mi-jclosed>summary{cursor:pointer;color:var(--mut);font-size:13px;font-weight:600}
.mi-jclosed>summary{margin:4px 0 12px;font-size:14px}
.mi-jhist .v-holds{color:var(--up)}.mi-jhist .v-broken{color:var(--down)}.mi-jhist .v-changed{color:var(--warn)}
.mi-jbtns{display:flex;gap:8px;margin:12px 0 0}
.mi-form label.wide{flex:1 1 100%}.mi-form label.wide textarea{font-family:inherit;font-size:14px;min-height:76px}
.mi-jv{display:flex;flex-wrap:wrap;gap:6px 16px;flex:1 1 100%}
.mi-jv label{flex-direction:row;align-items:center;gap:6px;font-size:14px;color:var(--fg)}
.mi-jv input{accent-color:var(--acc);width:16px;height:16px}
.mi-form label[hidden]{display:none}
.mi-jrev{border-top:1px solid var(--line);padding-top:12px;margin-top:12px}
@media(max-width:640px){.mi-jgrid{grid-template-columns:1fr}}
@media(max-width:640px){.mi-hide-sm{display:none}.mi-t td,.mi-t th{padding:7px 4px;font-size:13px}.mi-card{padding:14px}
.mi-stats b{font-size:17px}.mi-big b{font-size:24px}.mi-stock .lg{width:28px;height:28px}.mi-stock small{max-width:110px}}
"""


def page():
    """The page body. Static and the same for everyone; mine.js fills it in."""
    return (f"<style>{CSS}</style><div id='mine-app'>"
            "<h1>Mine</h1>"
            "<div class='mi-priv'><svg viewBox='0 0 24 24' aria-hidden='true'><rect x='5' y='11' width='14' "
            "height='10' rx='2'/><path d='M8 11V7a4 4 0 0 1 8 0v4'/></svg><div><b>Private to this device.</b> "
            "Your accounts, watchlist, journal and settings are saved only in this browser. Anyone else who opens "
            "this link sees an empty page, and nothing you type is ever sent anywhere. Use "
            "<a href='#backup'>Backup</a> to move it to your phone or keep a copy.</div></div>"
            "<div class='mi-tabs' role='tablist'>"
            "<button data-tab='accounts'>Accounts</button><button data-tab='watch'>Watchlist</button>"
            "<button data-tab='journal'>Journal</button><button data-tab='alerts'>Alerts</button><button data-tab='settings'>Customize</button>"
            "<button data-tab='backup'>Backup</button></div>"
            "<div id='mi-view'><p class='mut'>Loading…</p></div>"
            "<noscript><p>This page needs JavaScript, because it runs entirely in your browser.</p></noscript>"
            "</div><script src='brokerimport.js'></script><script src='mine.js'></script>")


def selftest():
    import tempfile
    import duckdb
    con = duckdb.connect()
    signals.make_fixture(con)
    con.execute("CREATE TABLE IF NOT EXISTS prices (tkr VARCHAR, d DATE, close DOUBLE)")
    base = datetime.date(2025, 3, 14)
    for i in range(300):
        d = base - datetime.timedelta(days=299 - i)
        con.execute("INSERT INTO prices VALUES ('VTI', ?, ?)", [d, 100 + i * 0.1])
    con.execute("INSERT INTO prices VALUES ('ONE', ?, 5)", [base])
    q = quotes(con, base)
    v = q["VTI"]
    assert v[0] == 129.9 and v[7] == "2025-03-14", v
    assert abs(v[1] - (129.9 / 129.8 - 1)) < 1e-4 and v[4] is None, v  # under a year of history
    assert v[5] == 129.9 and v[6] == 100.0
    assert q["ONE"][1] is None, "one price: no daily change"
    ns = news
    ns.ensure_table(con)
    con.execute("INSERT INTO news VALUES ('AAA', '2025-03-10', '8-K', 'CEO change', 'https://sec.gov/x')")
    import dashboard
    rows = dashboard.trade_rows(con, datetime.date(2025, 3, 31))
    d = data(con, rows, datetime.date(2025, 3, 31), ideas=["AAA"], built=["AAA"])
    ks = {e["k"] for e in d["events"]}
    assert {"pol", "ins", "news"} <= ks, ks
    assert all(e["s"] in ("buy", "sell") for e in d["events"] if e["k"] != "news")
    assert d["ideas"] == {"AAA": 1} and d["pages"] == {"AAA": "AAA"}
    assert [e["d"] for e in d["events"]] == sorted((e["d"] for e in d["events"]), reverse=True)
    out = tempfile.mkdtemp()
    write(con, rows, datetime.date(2025, 3, 31), out)
    json.load(open(f"{out}/mine.json"))
    u = universe()
    assert "VTI" in u and "BRK.B" in u and "#" not in "".join(u), u[:5]
    body = page()
    assert "Private to this device" in body and "mine.js" in body
    assert "data-tab='journal'" in body, "the decision journal has its own tab"
    # nothing personal is ever written to the site
    assert "watchlist.txt" not in body and not re.search(r"WATCHLIST", body)
    assert "brokerimport.js" in body
    # the broker-file importer is JavaScript; check it with node when available (CI has it)
    import shutil
    import subprocess
    node = shutil.which("node")
    if node:
        test = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests", "brokerimport_test.js")
        subprocess.run([node, test], check=True)
    else:
        print("node not found: skipped the broker import test")
    print("mine selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
