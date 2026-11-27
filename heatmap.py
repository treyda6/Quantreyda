"""Sector heatmap: where money moved in the last trading session.

  stocks    the ~200 biggest US-listed companies as tiles, grouped by sector,
            sized by company value (market cap) and colored by the day's move.
            From Nasdaq's free stock screener (one request, no key).
  sectors   the 11 S&P 500 sector funds (SPDR XLK, XLF, ...) over 1 day,
            1 week, 1 month, 3 months and the year so far, from the daily
            prices the tracker already downloads (Yahoo Finance).

The site rebuilds before the market opens, so "today" means the last full
trading session.

    py heatmap.py              # fetch and print the sector moves
    py heatmap.py --selftest
"""

import datetime
import html
import re
import sys

import requests

import sections

SCREENER = "https://api.nasdaq.com/api/screener/stocks"
# Nasdaq's API refuses requests that don't look like a browser.
BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
           "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
           "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}
TOP = 200  # tiles on the map
SECTOR_ETFS = [("XLK", "Technology"), ("XLF", "Financials"), ("XLV", "Health care"),
               ("XLY", "Consumer discretionary"), ("XLC", "Communication"), ("XLI", "Industrials"),
               ("XLP", "Consumer staples"), ("XLE", "Energy"), ("XLU", "Utilities"),
               ("XLRE", "Real estate"), ("XLB", "Materials")]
PERIODS = [("1d", "1D", 1), ("1w", "1W", 5), ("1m", "1M", 21), ("3m", "3M", 63), ("ytd", "YTD", None)]
E = html.escape


def ensure_tables(con):
    con.execute("""CREATE TABLE IF NOT EXISTS market_snap (
        tkr VARCHAR PRIMARY KEY, name VARCHAR, sector VARCHAR, industry VARCHAR, cap DOUBLE,
        last DOUBLE, pct DOUBLE, volume DOUBLE, checked TIMESTAMP)""")


def _num(s):
    """'$1,234.5' / '-0.53%' / '' -> float or None."""
    s = re.sub(r"[,$%\s]", "", str(s or ""))
    try:
        return float(s)
    except ValueError:
        return None


def _short_name(name):
    name = re.sub(r"\s+(Class [A-C] )?(Common|Ordinary|American Depositary|Capital) .*$", "", name or "")
    return re.sub(r",? (Inc|Corp|Corporation|Incorporated|Co|Ltd|plc|N\.V|S\.A|Holdings?)\.?$", "",
                  name, flags=re.I).strip()


def parse_screener(j, now):
    """Nasdaq screener JSON -> market_snap rows, biggest first, stocks only."""
    rows = ((j or {}).get("data") or {}).get("rows") or (((j or {}).get("data") or {}).get("table") or {}).get("rows") or []
    out = []
    for r in rows:
        tkr = (r.get("symbol") or "").strip().upper()
        cap, pct = _num(r.get("marketCap")), _num(r.get("pctchange"))
        if not tkr or not cap or pct is None or not re.fullmatch(r"[A-Z]{1,5}", tkr):
            continue  # preferreds, units and warrants have ^ / . in the symbol
        out.append((tkr, _short_name(r.get("name")), (r.get("sector") or "").strip() or "Other",
                    (r.get("industry") or "").strip(), cap, _num(r.get("lastsale")), pct / 100,
                    _num(r.get("volume")), now))
    out.sort(key=lambda x: -x[4])
    return out


def refresh(con):
    """Fetch the screener (one request). Keeps the last good snapshot on failure."""
    ensure_tables(con)
    now = datetime.datetime.utcnow().replace(microsecond=0)
    r = requests.get(SCREENER, params={"tableonly": "true", "limit": 25, "offset": 0, "download": "true"},
                     headers=BROWSER, timeout=60)
    r.raise_for_status()
    rows = parse_screener(r.json(), now)
    if len(rows) < 50:
        raise ValueError(f"screener returned only {len(rows)} stocks")
    con.execute("DELETE FROM market_snap")
    con.executemany("INSERT INTO market_snap VALUES (?,?,?,?,?,?,?,?,?)", rows)
    print(f"heatmap: {len(rows)} stocks from Nasdaq's screener")


def biggest(con, n=150):
    """Tickers of the n biggest companies (for analyst and options scans)."""
    try:
        return [t for (t,) in con.execute("SELECT tkr FROM market_snap ORDER BY cap DESC LIMIT ?", [n]).fetchall()]
    except Exception:
        return []


def snapshot(con, n=TOP):
    try:
        return con.execute("""SELECT tkr, name, sector, industry, cap, last, pct, volume, checked
                              FROM market_snap ORDER BY cap DESC LIMIT ?""", [n]).fetchall()
    except Exception:
        return []


def sector_returns(con, today):
    """{etf: {period: return}} from the cached prices (needs SECTOR_ETFS fetched)."""
    out = {}
    tk = [t for t, _ in SECTOR_ETFS] + ["SPY"]
    try:
        rows = con.execute("SELECT tkr, d, close FROM prices WHERE tkr IN (SELECT unnest(?)) AND d >= ? "
                           "ORDER BY tkr, d", [tk, today - datetime.timedelta(days=150)]).fetchall()
    except Exception:
        return out
    series = {}
    for t, d, c in rows:
        series.setdefault(t, []).append((d, c))
    for t, s in series.items():
        if len(s) < 2:
            continue
        last_d, last = s[-1]
        r = {}
        for key, _, n in PERIODS:
            if n is None:  # year to date: from the last close of the previous year
                prev = [c for d, c in s if d.year < last_d.year]
                base = prev[-1] if prev else None
            else:
                base = s[-1 - n][1] if len(s) > n else None
            r[key] = last / base - 1 if base else None
        r["asof"] = last_d
        out[t] = r
    return out


# ------------------------------------------------------------------- render

def color(p, scale=.03):
    """Day move -> tile color: red (down) through slate to green (up)."""
    if p is None:
        return "#4a505c"
    x = max(-1.0, min(1.0, p / scale))
    a, b = ((0x3d, 0x43, 0x50), (0x1f, 0xa8, 0x66)) if x >= 0 else ((0x3d, 0x43, 0x50), (0xd2, 0x3a, 0x41))
    x = abs(x) ** .8
    return "#" + "".join(f"{round(a[i] + (b[i] - a[i]) * x):02x}" for i in range(3))


def _pct(p):
    return "—" if p is None else f"{p * 100:+.1f}%"


def stock_map(rows, pages, root=""):
    """Sectors laid out first (by total value), then each sector's stocks inside."""
    if not rows:
        return ""
    by = {}
    for r in rows:
        by.setdefault(r[2], []).append(r)
    secs = sorted(by.items(), key=lambda kv: -sum(x[4] for x in kv[1]))
    out = []
    # area grows a little slower than value so mid-size companies stay readable
    for (name, rs), (x, y, w, h) in zip(secs, sections.squarify([sum(r[4] ** .7 for r in v) for _, v in secs])):
        rs = sorted(rs, key=lambda r: -r[4])
        wt = sum(r[4] * r[6] for r in rs) / sum(r[4] for r in rs)
        out.append(f"<div class='hm-sec' style='left:{x:.3f}%;top:{y:.3f}%;width:{w:.3f}%;height:{h:.3f}%'>"
                   f"<span>{E(name)} {_pct(wt)}</span></div>")
        for r, (tx, ty, tw, th) in zip(rs, sections.squarify([r[4] ** .7 for r in rs], x, y, w, h)):
            # font sizes follow the tile's real size (container units), so labels fit on any screen
            fb = f"min({tw / (len(r[0]) * .74 + .3):.3f}cqw,{th / 2.4:.3f}cqh,34px)"
            fs = f"min({tw / 4.6:.3f}cqw,{th / 4.2:.3f}cqh,13px)"
            tip = f"{r[0]} {r[1]}: {_pct(r[6])}, worth {sections._money(r[4])} ({r[3] or r[2]})"
            out.append(f"<a class='tile' href='{sections._stock_href(r[0], pages, root)}' title='{E(tip)}' "
                       f"style='left:{tx:.3f}%;top:{ty:.3f}%;width:{tw:.3f}%;height:{th:.3f}%;"
                       f"background:{color(r[6])}'><b style='font-size:{fb}'>{E(r[0])}</b>"
                       f"<span style='font-size:{fs}'>{_pct(r[6])}</span></a>")
    return "<div class='tm hm'>" + "".join(out) + "</div>"


def sector_strip(rets, key="sx"):
    """The 11 sector funds as tiles, with a CSS-only 1D / 1W / 1M / 3M / YTD switch."""
    if not rets:
        return ""
    radios = "".join(f"<input type='radio' name='{key}' id='{key}{k}' class='r-{k}'{' checked' if k == '1d' else ''}>"
                     for k, _, _ in PERIODS)
    per = "<div class='per'>" + "".join(f"<label for='{key}{k}'>{lab}</label>" for k, lab, _ in PERIODS) + "</div>"
    views = []
    for k, _, _ in PERIODS:
        scale = {"1d": .02, "1w": .04, "1m": .06, "3m": .1, "ytd": .15}[k]
        etfs = sorted((r for r in SECTOR_ETFS if r[0] in rets), key=lambda r: -(rets[r[0]].get(k) or -9))
        spy = rets.get("SPY", {}).get(k)
        cells = "".join(f"<div class='sx' style='background:{color(rets[t].get(k), scale)}'><b>{E(n)}</b>"
                        f"<span>{_pct(rets[t].get(k))}</span><small>{t}</small></div>" for t, n in etfs)
        views.append(f"<div class='sxv v-{k}'><div class='sxs'>{cells}</div>"
                     + (f"<p class='hint'>S&amp;P 500 (SPY) over the same time: {_pct(spy)}.</p>" if spy is not None else "")
                     + "</div>")
    asof = max((r["asof"] for r in rets.values()), default=None)
    return (f"<section class='sxb'>{radios}<div class='sec-h'><h2>Sectors</h2>{per}</div>{''.join(views)}"
            f"<p class='hint'>Each tile is a fund that holds one S&amp;P 500 sector, best first"
            + (f", through the close on {asof:%b %d}" if asof else "") + ".</p></section>")


def movers(rows, n=6):
    if len(rows) < 2 * n:
        return ""
    s = sorted(rows, key=lambda r: r[6])

    def li(r):
        return (f"<li><div class='t'><b>{E(r[0])}</b><small>{E(r[1])}</small></div>"
                f"<div class='amt {'buy' if r[6] >= 0 else 'sell'}'><b>{_pct(r[6])}</b>"
                f"<small>{sections._money(r[4])} company</small></div></li>")
    return ("<div class='mv2'><div><h3>Biggest gains</h3><ul class='rt mv'>" + "".join(li(r) for r in s[::-1][:n])
            + "</ul></div><div><h3>Biggest drops</h3><ul class='rt mv'>" + "".join(li(r) for r in s[:n])
            + "</ul></div></div>")


def section(con, today, pages, root=""):
    rows = snapshot(con)
    rets = sector_returns(con, today)
    when = f"{rows[0][8]:%b %d}" if rows and rows[0][8] else None
    body = ["<h2>Where money moved</h2>"]
    if rows:
        body.append("<p class='hint'>The biggest US companies, grouped by sector. Bigger tile = bigger "
                    "company; green rose and red fell in the last trading session"
                    + (f" (checked {E(when)})" if when else "") + ". Tap a tile for the stock.</p>")
        body.append(stock_map(rows, pages, root))
        body.append("<div class='tm-key hm-key'><span>−3%</span><span>0</span><span>+3%</span></div>")
        body.append(movers(rows))
    else:
        body.append("<p class='hint'>The stock map fills in on the next daily update.</p>")
    body.append(sector_strip(rets))
    return "".join(body)


CSS = """
.hm{aspect-ratio:3/4}@media(min-width:700px){.hm{aspect-ratio:16/9;max-height:640px}}
.hm{container-type:size}.hm .tile{border-width:1px;padding:1px}.hm .tile span{max-width:none}
.hm-sec{position:absolute;box-sizing:border-box;border:2px solid var(--bg);pointer-events:none;z-index:2}
.hm-sec span{position:absolute;left:0;top:0;font-size:10px;font-weight:700;color:#fff;background:rgba(0,0,0,.45);
padding:1px 5px;border-bottom-right-radius:6px;white-space:nowrap;max-width:100%;overflow:hidden;text-overflow:ellipsis}
.hm-key{background:linear-gradient(90deg,#d23a41,#3d4350 50%,#1fa866)}
.mv2{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:14px}.mv2 h3{margin:0 0 8px;font-size:15px}
.rt.mv li{grid-template-columns:1fr auto}.rt.mv .t small{max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
@media(max-width:640px){.mv2{grid-template-columns:1fr}}
.sxb>input{position:absolute;opacity:0;pointer-events:none}.sxv{display:none}
.sxb .r-1d:checked~.v-1d,.sxb .r-1w:checked~.v-1w,.sxb .r-1m:checked~.v-1m,.sxb .r-3m:checked~.v-3m,
.sxb .r-ytd:checked~.v-ytd{display:block}
.sxb .r-1d:checked~.sec-h label[for$='1d'],.sxb .r-1w:checked~.sec-h label[for$='1w'],
.sxb .r-1m:checked~.sec-h label[for$='1m'],.sxb .r-3m:checked~.sec-h label[for$='3m'],
.sxb .r-ytd:checked~.sec-h label[for$=ytd]{background:var(--card);color:var(--fg);box-shadow:var(--shadow)}
.sxs{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}
.sx{border-radius:14px;padding:12px;color:#fff;display:flex;flex-direction:column;gap:2px}
.sx b{font-size:13px}.sx span{font-size:22px;font-weight:800;font-variant-numeric:tabular-nums}.sx small{opacity:.8;font-size:11px}
"""


def selftest():
    import duckdb
    now = datetime.datetime(2026, 11, 27, 13)
    j = {"data": {"rows": [
        {"symbol": "AAPL", "name": "Apple Inc. Common Stock", "lastsale": "$230.10", "pctchange": "1.25%",
         "marketCap": "3,500,000,000,000", "sector": "Technology", "industry": "Computer Manufacturing", "volume": "50,000,000"},
        {"symbol": "XOM", "name": "Exxon Mobil Corporation Common Stock", "lastsale": "$110", "pctchange": "-2.5%",
         "marketCap": "480,000,000,000", "sector": "Energy", "industry": "Oil", "volume": "1"},
        {"symbol": "BAC^K", "name": "pref", "pctchange": "0.1%", "marketCap": "1,000", "sector": "Finance"},
        {"symbol": "ZZZ", "name": "No cap", "pctchange": "0.1%", "marketCap": "", "sector": "Finance"}]}}
    rows = parse_screener(j, now)
    assert [r[0] for r in rows] == ["AAPL", "XOM"], rows
    assert rows[0][1] == "Apple" and abs(rows[0][6] - .0125) < 1e-9 and rows[1][1] == "Exxon Mobil", rows
    assert color(0) == "#3d4350" and color(.05) == "#1fa866" and color(-.05) == "#d23a41"
    con = duckdb.connect()
    ensure_tables(con)
    extra = [(f"T{i:02d}"[:5], f"Co {i}", "Finance" if i % 2 else "Health Care", "", 1e9 * (50 - i), 10, (i - 25) / 1000,
              1, now) for i in range(30)]
    con.executemany("INSERT INTO market_snap VALUES (?,?,?,?,?,?,?,?,?)", rows + extra)
    con.execute("CREATE TABLE prices (tkr VARCHAR, d DATE, close DOUBLE)")
    d0 = datetime.date(2025, 12, 1)
    for t, g in (("XLK", .001), ("XLE", -.001), ("SPY", .0005)):
        con.executemany("INSERT INTO prices VALUES (?,?,?)",
                        [(t, d0 + datetime.timedelta(days=i), 100 * (1 + g) ** i) for i in range(120)])
    rets = sector_returns(con, datetime.date(2026, 3, 31))
    assert rets["XLK"]["1d"] > 0 > rets["XLE"]["1d"] and rets["XLK"]["ytd"] > 0, rets
    html_ = section(con, datetime.date(2026, 3, 31), {"AAPL"})
    assert "Where money moved" in html_ and "href='s/AAPL.html'" in html_ and "Technology" in html_
    assert "Biggest gains" in html_ and "Sectors" in html_ and "<script" not in html_
    assert biggest(con, 2) == ["AAPL", "XOM"]
    print("heatmap selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        import duckdb
        from schema import DB_PATH
        c = duckdb.connect(DB_PATH)
        refresh(c)
        for r in snapshot(c, 20):
            print(f"{r[0]:6} {r[6] * 100:+6.2f}%  {r[2]}")
