"""Unusual options activity: big, fresh options bets in the last session,
from Cboe's free delayed quotes (one request per stock, no key).

An option is "unusual" here when, in one session:
  - at least MIN_VOLUME contracts traded,
  - more traded than were open the day before (volume > open interest),
    which means mostly new positions rather than old ones changing hands, and
  - the money spent (contracts x price x 100) is at least MIN_PREMIUM.

Free data has limits: Cboe's delayed file shows each contract's totals for
the day, not the individual trades, so we can't see who traded or tell a buyer
from a seller for sure. The "leans bought / sold" guess compares the last
trade with the bid and ask. Big bets are often hedges, not predictions.

    py optionsflow.py NVDA TSLA   # scan a few and print what stood out
    py optionsflow.py --selftest
"""

import datetime
import html
import re
import sys
import time

import requests

CBOE = "https://cdn.cboe.com/api/global/delayed_quotes/options/{sym}.json"
INDEXES = {"SPX", "NDX", "RUT", "VIX", "XSP"}  # Cboe files these under _SPX etc.
MIN_VOLUME = 500
MIN_PREMIUM = 150_000
KEEP_DAYS = 30
# Always scanned: the funds and stocks with the busiest options markets.
CORE = ["SPY", "QQQ", "IWM", "DIA", "XLF", "XLE", "TLT", "GLD", "SMH", "ARKK",
        "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD", "AVGO", "NFLX",
        "PLTR", "COIN", "MSTR", "INTC", "MU", "SMCI", "BA", "DIS", "JPM", "BAC",
        "C", "WFC", "XOM", "CVX", "UBER", "SHOP", "SOFI", "HOOD", "RIVN", "NIO",
        "BABA", "PYPL", "CRM", "ORCL", "ADBE", "LLY", "UNH", "PFE", "WMT", "COST"]
E = html.escape
OCC = re.compile(r"^(.*?)(\d{6})([CP])(\d{8})$")


def ensure_tables(con):
    con.execute("""CREATE TABLE IF NOT EXISTS options_unusual (
        snap DATE, tkr VARCHAR, contract VARCHAR, expiry DATE, kind VARCHAR, strike DOUBLE, spot DOUBLE,
        volume INTEGER, oi INTEGER, price DOUBLE, premium DOUBLE, iv DOUBLE, side VARCHAR,
        PRIMARY KEY (snap, contract))""")
    con.execute("""CREATE TABLE IF NOT EXISTS options_summary (
        snap DATE, tkr VARCHAR, spot DOUBLE, call_vol BIGINT, put_vol BIGINT, call_oi BIGINT, put_oi BIGINT,
        call_prem DOUBLE, put_prem DOUBLE, PRIMARY KEY (snap, tkr))""")


def cboe_symbol(tkr):
    t = tkr.upper().replace("-", ".").replace("/", ".")
    return f"_{t}" if t in INDEXES else t


def _f(v):
    try:
        f = float(v)
        return f if f == f else 0.0
    except (TypeError, ValueError):
        return 0.0


def side_guess(last, bid, ask):
    """Where the last trade printed between the bid and ask."""
    if not last or not bid or not ask or ask <= bid:
        return ""
    x = (last - bid) / (ask - bid)
    return "bought" if x >= .6 else "sold" if x <= .4 else ""


def parse_chain(tkr, j, today):
    """Cboe delayed-quotes JSON -> (snap date, summary row, [unusual rows])."""
    data = (j or {}).get("data") or {}
    spot = _f(data.get("current_price")) or _f(data.get("close"))
    try:
        snap = datetime.date.fromisoformat(str(j.get("timestamp") or "")[:10])
    except ValueError:
        snap = today
    cv = pv = coi = poi = 0
    cp = pp = 0.0
    hits = []
    for o in data.get("options") or []:
        m = OCC.match(str(o.get("option") or ""))
        if not m:
            continue
        try:
            exp = datetime.datetime.strptime(m.group(2), "%y%m%d").date()
        except ValueError:
            continue
        kind, strike = m.group(3), int(m.group(4)) / 1000
        vol, oi = int(_f(o.get("volume"))), int(_f(o.get("open_interest")))
        bid, ask, last = _f(o.get("bid")), _f(o.get("ask")), _f(o.get("last_trade_price"))
        price = last or ((bid + ask) / 2 if ask else 0)
        prem = vol * price * 100
        if kind == "C":
            cv, coi, cp = cv + vol, coi + oi, cp + prem
        else:
            pv, poi, pp = pv + vol, poi + oi, pp + prem
        if vol >= MIN_VOLUME and vol > oi and prem >= MIN_PREMIUM and exp >= snap:
            hits.append((snap, tkr, m.group(0), exp, "call" if kind == "C" else "put", strike, spot, vol, oi,
                         price, prem, _f(o.get("iv")) or None, side_guess(last, bid, ask)))
    return snap, (snap, tkr, spot, cv, pv, coi, poi, cp, pp), hits


def refresh(con, tickers, minutes=5):
    """Scan CORE plus `tickers`, one Cboe request each, until `minutes` run out."""
    import companyintel
    ensure_tables(con)
    today = datetime.date.today()
    stop = time.monotonic() + minutes * 60
    s = requests.Session()
    s.headers["User-Agent"] = companyintel.UA
    done = n_hits = fails = 0
    todo = list(dict.fromkeys(CORE + [t for t in tickers if re.fullmatch(r"[A-Z]{1,5}([.\-/][A-Z])?", t or "")]))
    for tkr in todo:
        if time.monotonic() > stop:
            break
        try:
            r = s.get(CBOE.format(sym=cboe_symbol(tkr)), timeout=30)
            if r.status_code in (403, 404):  # no listed options
                continue
            r.raise_for_status()
            snap, summ, hits = parse_chain(tkr, r.json(), today)
        except (requests.RequestException, ValueError):
            fails += 1
            if fails >= 10 and not done:
                break
            continue
        con.execute("DELETE FROM options_unusual WHERE snap = ? AND tkr = ?", [snap, tkr])
        if hits:
            con.executemany("INSERT OR REPLACE INTO options_unusual VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", hits)
        if summ[3] or summ[4]:
            con.execute("INSERT OR REPLACE INTO options_summary VALUES (?,?,?,?,?,?,?,?,?)", summ)
        done += 1
        n_hits += len(hits)
        time.sleep(0.15)
    for t in ("options_unusual", "options_summary"):
        con.execute(f"DELETE FROM {t} WHERE snap < ?", [today - datetime.timedelta(days=KEEP_DAYS)])
    print(f"options: {done} of {len(todo)} stocks scanned, {n_hits} unusual contracts"
          + (f", {fails} failed" if fails else ""))


# ------------------------------------------------------------------- read

def latest(con, n=40, tkr=None):
    try:
        snap = con.execute("SELECT max(snap) FROM options_unusual" + (" WHERE tkr = ?" if tkr else ""),
                           [tkr] if tkr else []).fetchone()[0]
        if not snap:
            return None, []
        rows = con.execute("SELECT * FROM options_unusual WHERE snap = ?" + (" AND tkr = ?" if tkr else "")
                           + " ORDER BY premium DESC LIMIT ?", [snap] + ([tkr] if tkr else []) + [n]).fetchall()
        return snap, rows
    except Exception:
        return None, []


def summary(con, tkr):
    try:
        return con.execute("SELECT * FROM options_summary WHERE tkr = ? ORDER BY snap DESC LIMIT 1", [tkr]).fetchone()
    except Exception:
        return None


def leaning(con, n=8):
    """Stocks where the day's options money leaned hardest to calls or puts."""
    try:
        snap = con.execute("SELECT max(snap) FROM options_summary").fetchone()[0]
        return con.execute("""SELECT tkr, call_prem, put_prem, call_vol, put_vol FROM options_summary
                              WHERE snap = ? AND call_prem + put_prem >= 5e6
                              ORDER BY call_prem / (call_prem + put_prem) DESC""", [snap]).fetchall()
    except Exception:
        return []


# ------------------------------------------------------------------- render

def _money(x):
    return f"${x / 1e6:.1f}M" if x >= 1e6 else f"${x / 1e3:.0f}k"


def mood(kind, side):
    if side == "bought":
        return "up" if kind == "call" else "down"
    if side == "sold":
        return "down" if kind == "call" else "up"
    return ""


def bet_line(r, link=None):
    snap, tkr, contract, exp, kind, strike, spot, vol, oi, price, prem, iv, side = r
    days = (exp - snap).days
    gap = (strike / spot - 1) if spot else None
    where = ""
    if gap is not None:
        where = (f"{abs(gap) * 100:.0f}% {'above' if gap > 0 else 'below'} today's price"
                 if abs(gap) >= .005 else "at today's price")
    md = mood(kind, side)
    guess = {"up": "a bet it rises", "down": "a bet it falls"}.get(md, "direction unclear")
    stock = f"<a href='{E(link)}'><b>{E(tkr)}</b></a>" if link else f"<b>{E(tkr)}</b>"
    before = f"{vol:,} contracts vs {oi:,} open before" if oi else f"{vol:,} contracts, none open before"
    tag = f"{vol / oi:.0f}x open interest" if oi else "all new"
    cls = "buy" if md == "up" else "sell" if md == "down" else ""
    return (f"<li><div class='t'>{stock} <span class='chip'>${strike:,.2f} {kind}</span>"
            f"<small>Expires {exp:%b %d, %Y} ({days} days) · strike {where}</small>"
            f"<small>{before}" + (f" · leans {side}" if side else "") + f" · {guess}</small></div>"
            f"<div class='amt {cls}'><b>{_money(prem)}</b><small>{tag}</small></div></li>")


def section(con, pages, root=""):
    import sections
    snap, rows = latest(con, 30)
    head = "<h2>Unusual options activity</h2>"
    if not rows:
        return head + "<p class='hint'>The options scan fills in on the next daily update.</p>"
    out = [head, f"<p class='hint'>Big options bets from the session on {snap:%b %d} where more contracts "
                 "traded than were open before, so they're mostly new positions. A call pays off if the "
                 "stock rises past the strike price before it expires; a put pays off if it falls. "
                 "Big bets sometimes come before news, but many are hedges that protect other holdings, "
                 "so treat these as a reason to look, not a signal to copy.</p>",
           "<ul class='rt bets ob'>" + "".join(bet_line(r, sections._stock_href(r[1], pages, root)) for r in rows)
           + "</ul>"]
    lean = leaning(con)
    if len(lean) >= 6:
        def li(t, c, p, cv, pv):
            share = c / (c + p)
            return (f"<li><div class='t'><a href='{sections._stock_href(t, pages, root)}'><b>{E(t)}</b></a>"
                    f"<small>{cv:,} calls · {pv:,} puts</small></div>"
                    f"<div class='amt {'buy' if share >= .5 else 'sell'}'><b>{share * 100:.0f}% calls</b>"
                    f"<small>{_money(c + p)} spent</small></div></li>")
        out.append("<div class='mv2'><div><h3>Money leaning to calls</h3><ul class='rt mv'>"
                   + "".join(li(*r) for r in lean[:5]) + "</ul></div><div><h3>Money leaning to puts</h3>"
                   "<ul class='rt mv'>" + "".join(li(*r) for r in lean[::-1][:5]) + "</ul></div></div>"
                   "<p class='hint'>Share of the day's options dollars spent on calls (bets on a rise) "
                   "versus puts (bets on a fall or protection), for stocks with $5M+ traded.</p>")
    return "".join(out)


def stock_panel(con, tkr):
    snap, rows = latest(con, 5, tkr)
    sm = summary(con, tkr)
    if not rows and not sm:
        return ""
    body = []
    if sm and (sm[3] or sm[4]):
        c, p = sm[7] or 0, sm[8] or 0
        body.append(f"<p>On {sm[0]:%b %d}, {sm[3]:,} call and {sm[4]:,} put contracts traded"
                    + (f"; {c / (c + p) * 100:.0f}% of the {_money(c + p)} spent went to calls (bets on a rise)"
                       if c + p else "") + ".</p>")
    if rows:
        body.append("<p>Unusual bets that day:</p><ul class='rt bets ob'>" + "".join(bet_line(r) for r in rows) + "</ul>")
    return ("<section class='panel'><h2>Options activity</h2>" + "".join(body)
            + "<p class='hint'>From Cboe's free delayed quotes. A big bet can be a hedge, not a "
              "prediction.</p></section>")


CSS = """
.ob li{grid-template-columns:1fr auto}.ob .chip{font-size:11.5px;font-weight:700;margin-left:4px}
"""


def selftest():
    import duckdb
    today = datetime.date(2026, 11, 27)
    j = {"timestamp": "2026-11-26 16:15:00", "data": {"current_price": 100.0, "options": [
        # 2,000 new calls at $3 bought at the ask: unusual, bullish
        {"option": "AAA261218C00110000", "volume": 2000, "open_interest": 300, "bid": 2.8, "ask": 3.0,
         "last_trade_price": 3.0, "iv": 0.45},
        # big volume but below open interest: not unusual
        {"option": "AAA261218P00090000", "volume": 5000, "open_interest": 9000, "bid": 1, "ask": 1.1,
         "last_trade_price": 1.05},
        # new but too small in dollars
        {"option": "AAA261218P00080000", "volume": 600, "open_interest": 0, "bid": .1, "ask": .12,
         "last_trade_price": .1},
        # new puts, all new, sold at the bid: bullish-ish
        {"option": "AAA270115P00095000", "volume": 1500, "open_interest": 0, "bid": 2.0, "ask": 2.4,
         "last_trade_price": 2.0},
        {"option": "junk"}]}}
    snap, summ, hits = parse_chain("AAA", j, today)
    assert snap == datetime.date(2026, 11, 26) and summ[3] == 2000 and summ[4] == 7100, summ
    assert [h[2] for h in hits] == ["AAA261218C00110000", "AAA270115P00095000"], hits
    assert hits[0][4] == "call" and hits[0][5] == 110 and hits[0][10] == 600_000 and hits[0][12] == "bought"
    assert hits[1][12] == "sold" and mood("put", "sold") == "up"
    assert cboe_symbol("SPX") == "_SPX" and cboe_symbol("BRK-B") == "BRK.B"
    con = duckdb.connect()
    ensure_tables(con)
    con.executemany("INSERT INTO options_unusual VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", hits)
    con.execute("INSERT INTO options_summary VALUES (?,?,?,?,?,?,?,?,?)", summ)
    sec = section(con, {"AAA"})
    assert "Unusual options activity" in sec and "$600k" in sec and "a bet it rises" in sec, sec
    assert "10% above today's price" in sec and "none open before" in sec and "href='s/AAA.html'" in sec
    pn = stock_panel(con, "AAA")
    assert "Options activity" in pn and "2,000 call" in pn, pn
    assert stock_panel(con, "ZZZ") == ""
    print("optionsflow selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        import duckdb
        from schema import DB_PATH
        c = duckdb.connect(DB_PATH)
        refresh(c, [a.upper() for a in sys.argv[1:]], minutes=2)
        for r in latest(c, 20)[1]:
            print(r[1], r[2], f"{r[7]:,} vol", f"{r[8]:,} oi", _money(r[10]), r[12])
