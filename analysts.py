"""Wall Street analyst ratings: upgrades, downgrades, new coverage and price
targets, from Yahoo Finance through `yfinance` (free, no key, unofficial).

  analyst_changes     one row per firm's rating or price-target change
  analyst_consensus   each stock's buy/hold/sell count and average target

Free data has limits: Yahoo only answers one stock at a time, so we check the
~150 biggest companies plus every stock with a page here, not the whole
market, and a change can show up a day late.

    py analysts.py AAPL MSFT   # fetch a few and print their latest changes
    py analysts.py --selftest
"""

import datetime
import html
import sys
import time

E = html.escape
ACTIONS = {"up": "Upgrade", "down": "Downgrade", "init": "New coverage", "main": "Kept",
           "reit": "Kept", "maintains": "Kept", "reiterated": "Kept"}
KEEP_DAYS = 400


def ensure_tables(con):
    con.execute("""CREATE TABLE IF NOT EXISTS analyst_changes (
        tkr VARCHAR, d DATE, firm VARCHAR, action VARCHAR, from_grade VARCHAR, to_grade VARCHAR,
        pt_action VARCHAR, pt DOUBLE, pt_prior DOUBLE, PRIMARY KEY (tkr, d, firm))""")
    con.execute("""CREATE TABLE IF NOT EXISTS analyst_consensus (
        tkr VARCHAR PRIMARY KEY, checked DATE, target_mean DOUBLE, target_low DOUBLE, target_high DOUBLE,
        strong_buy INTEGER, buy INTEGER, hold INTEGER, sell INTEGER, strong_sell INTEGER)""")


def _get(rec, *names):
    low = {str(k).lower(): v for k, v in rec.items()}
    for n in names:
        v = low.get(n.lower())
        if v is not None and v == v and v != "":  # skip None, NaN and blanks
            return v
    return None


def _float(v):
    try:
        f = float(v)
        return f if f == f and f > 0 else None
    except (TypeError, ValueError):
        return None


def _date(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):  # epoch seconds
        return datetime.datetime.utcfromtimestamp(v).date()
    if hasattr(v, "date"):
        return v.date() if isinstance(v, datetime.datetime) or hasattr(v, "to_pydatetime") else v
    try:
        return datetime.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def parse_changes(tkr, records, since):
    """yfinance upgrades_downgrades records -> analyst_changes rows since `since`.
    Column names have changed between yfinance versions, so look them up loosely."""
    out = {}
    for rec in records or []:
        d = _date(_get(rec, "GradeDate", "gradeDate", "epochGradeDate", "index", "Date"))
        firm = _get(rec, "Firm", "firm")
        if not d or not firm or d < since:
            continue
        act = str(_get(rec, "Action", "action") or "").lower()
        out[(tkr, d, firm)] = (tkr, d, str(firm), ACTIONS.get(act, act.title() or "Kept"),
                               str(_get(rec, "FromGrade", "fromGrade") or ""),
                               str(_get(rec, "ToGrade", "toGrade") or ""),
                               str(_get(rec, "priceTargetAction", "PriceTargetAction") or ""),
                               _float(_get(rec, "currentPriceTarget", "CurrentPriceTarget")),
                               _float(_get(rec, "priorPriceTarget", "PriorPriceTarget")))
    return list(out.values())


def parse_consensus(tkr, targets, trend, today):
    """analyst_price_targets dict + recommendations records -> one consensus row."""
    t = targets or {}
    now = next((r for r in trend or [] if str(_get(r, "period") or "") == "0m"), (trend or [None])[0]) or {}

    def n(k):
        v = _get(now, k)
        return int(v) if v is not None else None
    row = (tkr, today, _float(t.get("mean")), _float(t.get("low")), _float(t.get("high")),
           n("strongBuy"), n("buy"), n("hold"), n("sell"), n("strongSell"))
    return row if any(x is not None for x in row[2:]) else None


def _records(df):
    if df is None:
        return []
    if isinstance(df, list):
        return df
    try:
        return df.reset_index().to_dict("records")
    except Exception:
        return []


def refresh(con, tickers, minutes=6):
    """Fetch rating changes and consensus for `tickers` (biggest first). Stops after
    `minutes`; the rest wait for the next run."""
    import yfinance as yf  # lazy: selftests don't need it
    import prices
    ensure_tables(con)
    today = datetime.date.today()
    since = today - datetime.timedelta(days=KEEP_DAYS)
    stop = time.monotonic() + minutes * 60
    done = n_changes = fails = 0
    for tkr in dict.fromkeys(tickers):
        if time.monotonic() > stop:
            break
        try:
            tk = yf.Ticker(prices.yahoo_symbol(tkr))
            rows = parse_changes(tkr, _records(tk.upgrades_downgrades), since)
            cons = parse_consensus(tkr, getattr(tk, "analyst_price_targets", None),
                                   _records(getattr(tk, "recommendations", None)), today)
        except Exception:
            fails += 1
            if fails >= 15 and not done:  # Yahoo is refusing us today
                break
            continue
        if rows:
            con.executemany("INSERT OR REPLACE INTO analyst_changes VALUES (?,?,?,?,?,?,?,?,?)", rows)
        if cons:
            con.execute("INSERT OR REPLACE INTO analyst_consensus VALUES (?,?,?,?,?,?,?,?,?,?)", cons)
        done += 1
        n_changes += len(rows)
        time.sleep(0.2)
    con.execute("DELETE FROM analyst_changes WHERE d < ?", [since])
    print(f"analysts: {done} of {len(set(tickers))} stocks checked, {n_changes} rating changes on file"
          + (f", {fails} failed" if fails else ""))


# ------------------------------------------------------------------- read

def recent(con, today, days=7, n=200):
    try:
        return con.execute("""SELECT tkr, d, firm, action, from_grade, to_grade, pt_action, pt, pt_prior
                              FROM analyst_changes WHERE d >= ? ORDER BY d DESC, tkr LIMIT ?""",
                           [today - datetime.timedelta(days=days), n]).fetchall()
    except Exception:
        return []


def for_ticker(con, tkr, n=8):
    try:
        ch = con.execute("""SELECT tkr, d, firm, action, from_grade, to_grade, pt_action, pt, pt_prior
                            FROM analyst_changes WHERE tkr = ? ORDER BY d DESC LIMIT ?""", [tkr, n]).fetchall()
        cs = con.execute("SELECT * FROM analyst_consensus WHERE tkr = ?", [tkr]).fetchone()
    except Exception:
        return [], None
    return ch, cs


def last_close(con, tkr):
    try:
        r = con.execute("SELECT close FROM prices WHERE tkr = ? ORDER BY d DESC LIMIT 1", [tkr]).fetchone()
        return r[0] if r else None
    except Exception:
        return None


# ------------------------------------------------------------------- render

def _target(pt, prior):
    if not pt:
        return ""
    if prior and abs(pt / prior - 1) >= .005:
        return f"target ${prior:,.0f} → <b>${pt:,.0f}</b> ({(pt / prior - 1) * 100:+.0f}%)"
    return f"target <b>${pt:,.0f}</b>"


def change_line(r, link=None):
    tkr, d, firm, action, frm, to, pta, pt, prior = r
    if action == "Upgrade" or action == "Downgrade":
        what = f"{action.lower()}d to <b>{E(to)}</b>" + (f" from {E(frm)}" if frm else "")
    elif action == "New coverage":
        what = f"started at <b>{E(to)}</b>"
    else:
        what = f"kept <b>{E(to)}</b>" if to else "updated"
    tg = _target(pt, prior)
    cls = ("buy" if action == "Upgrade" or (pt and prior and pt > prior) else
           "sell" if action == "Downgrade" or (pt and prior and pt < prior) else "")
    stock = f"<a href='{E(link)}'><b>{E(tkr)}</b></a>" if link else f"<b>{E(tkr)}</b>"
    return (f"<li class='ac {cls}'><span class='d'>{d:%b %d}</span><span>{stock} · {E(firm)} {what}"
            + (f" · {tg}" if tg else "") + "</span></li>")


def section(con, today, pages, root=""):
    import sections
    rows = recent(con, today, 7)
    head = "<h2>Analyst calls this week</h2>"
    if not rows:
        return head + "<p class='hint'>Rating changes fill in on the next daily update.</p>"
    link = lambda t: sections._stock_href(t, pages, root)
    groups = [("Upgrades", [r for r in rows if r[3] == "Upgrade"]),
              ("Downgrades", [r for r in rows if r[3] == "Downgrade"]),
              ("New coverage", [r for r in rows if r[3] == "New coverage"]),
              ("Biggest price-target moves", sorted(
                  (r for r in rows if r[3] not in ("Upgrade", "Downgrade", "New coverage") and r[7] and r[8]),
                  key=lambda r: -abs(r[7] / r[8] - 1))[:15])]
    out = [head, "<p class='hint'>When a bank or research firm changes its rating or its price "
                 "target (where it thinks the stock will be in about a year). One call rarely matters; "
                 "several firms moving the same way can. Analysts are often late and lean "
                 "optimistic: \"Hold\" usually means they don't like it.</p><div class='acg'>"]
    for title, rs in groups:
        if rs:
            out.append(f"<div class='panel'><h3>{title} <span class='hint'>({len(rs)})</span></h3><ul class='list'>"
                       + "".join(change_line(r, link(r[0])) for r in rs[:15]) + "</ul></div>")
    out.append("</div>")
    return "".join(out)


def consensus_html(cs, price):
    if not cs:
        return ""
    _, checked, mean, lo, hi, sb, b, h, s, ss = cs
    counts = [("Strong buy", sb), ("Buy", b), ("Hold", h), ("Sell", s), ("Strong sell", ss)]
    total = sum(c or 0 for _, c in counts)
    out = []
    if total:
        bar = "".join(f"<i class='c{i}' style='width:{(c or 0) / total * 100:.1f}%' title='{lab}: {c or 0}'></i>"
                      for i, (lab, c) in enumerate(counts) if c)
        bulls = (sb or 0) + (b or 0)
        out.append(f"<p><b>{bulls} of {total}</b> analysts say buy, {h or 0} hold, {(s or 0) + (ss or 0)} sell.</p>"
                   f"<div class='acbar'>{bar}</div>")
    if mean:
        up = f" That's {(mean / price - 1) * 100:+.0f}% from the last close." if price else ""
        rng = f" (range ${lo:,.0f} to ${hi:,.0f})" if lo and hi else ""
        out.append(f"<p>Average price target <b>${mean:,.0f}</b>{rng}.{up}</p>")
    return "".join(out)


def stock_panel(con, tkr):
    ch, cs = for_ticker(con, tkr)
    if not ch and not cs:
        return ""
    return ("<section class='panel'><h2>What analysts say</h2>" + consensus_html(cs, last_close(con, tkr))
            + ("<ul class='list'>" + "".join(change_line(r) for r in ch) + "</ul>" if ch else "")
            + "<p class='hint'>From Yahoo Finance. Analysts lean optimistic, and price targets "
              "follow the price as often as they lead it.</p></section>")


CSS = """
.acg{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:12px}.acg .panel{margin:0}
.acg h3{margin:0 0 8px;font-size:16px}
li.ac{border-left:3px solid var(--line);padding-left:8px}li.ac.buy{border-color:var(--up)}li.ac.sell{border-color:var(--down)}
.acbar{display:flex;height:10px;border-radius:999px;overflow:hidden;background:var(--chip);margin:6px 0 10px}
.acbar i{display:block;height:100%}.acbar .c0{background:#13a35f}.acbar .c1{background:#55c48a}
.acbar .c2{background:#8a93a3}.acbar .c3{background:#ef7a73}.acbar .c4{background:#d23a41}
@media(max-width:640px){.acg{grid-template-columns:1fr}}
"""


def selftest():
    import duckdb
    today = datetime.date(2026, 11, 27)
    recs = [{"GradeDate": datetime.datetime(2026, 11, 25, 9), "Firm": "Morgan Stanley", "ToGrade": "Overweight",
             "FromGrade": "Equal-Weight", "Action": "up", "priceTargetAction": "Raises",
             "currentPriceTarget": 250.0, "priorPriceTarget": 200.0},
            {"GradeDate": "2026-11-24", "Firm": "Citi", "ToGrade": "Buy", "FromGrade": "", "Action": "main",
             "currentPriceTarget": 180, "priorPriceTarget": 220},
            {"GradeDate": "2026-11-26", "Firm": "Jefferies", "ToGrade": "Buy", "Action": "init",
             "currentPriceTarget": float("nan")},
            {"GradeDate": "2020-01-01", "Firm": "Old", "ToGrade": "Buy", "Action": "up"}]
    rows = parse_changes("AAA", recs, today - datetime.timedelta(days=KEEP_DAYS))
    assert len(rows) == 3 and rows[0][3] == "Upgrade" and rows[0][7] == 250 and rows[2][7] is None, rows
    cons = parse_consensus("AAA", {"current": 210, "mean": 240.5, "low": 150, "high": 300},
                           [{"period": "0m", "strongBuy": 10, "buy": 20, "hold": 8, "sell": 1, "strongSell": 1},
                            {"period": "-1m", "strongBuy": 1, "buy": 1, "hold": 1, "sell": 1, "strongSell": 1}], today)
    assert cons[2] == 240.5 and cons[5] == 10 and cons[9] == 1, cons
    assert parse_consensus("AAA", {}, [], today) is None
    con = duckdb.connect()
    ensure_tables(con)
    con.executemany("INSERT INTO analyst_changes VALUES (?,?,?,?,?,?,?,?,?)", rows)
    con.execute("INSERT INTO analyst_consensus VALUES (?,?,?,?,?,?,?,?,?,?)", cons)
    sec = section(con, today, {"AAA"})
    assert "Upgrades" in sec and "Morgan Stanley" in sec and "$200 → <b>$250</b>" in sec, sec
    assert "New coverage" in sec and "Biggest price-target moves" in sec and "href='s/AAA.html'" in sec
    pn = stock_panel(con, "AAA")
    assert "30 of 40" in pn and "$240" in pn and "What analysts say" in pn, pn
    assert stock_panel(con, "ZZZ") == ""
    print("analysts selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        import duckdb
        from schema import DB_PATH
        c = duckdb.connect(DB_PATH)
        refresh(c, [a.upper() for a in sys.argv[1:]] or ["AAPL"], minutes=2)
        for r in recent(c, datetime.date.today(), 30, 20):
            print(r)
