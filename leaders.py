"""Leaderboards: whose buys have actually beaten the market?

For every member of Congress and every company insider with enough buys on
record, look at what each stock did in the 6 months after the buy was made
public (the earliest you could have copied it) and compare it with SPY over
the same days. Same rules as the backtest: entry at the first close after the
filing date, no look-ahead.

  compute(con, start, end)    {"politicians": [...], "insiders": [...], "records": {key: ...}}
  key_congress(chamber, last, first) / key_insider(name)
                              the keys the conviction score uses to look people up

Writes reports/leaders.json; the dashboard reads it (no network on build).

Usage:
    py leaders.py            # last 5 years, fetches missing prices (weekly run)
    py leaders.py --selftest # offline check
"""

import datetime
import json
import os
import sys

import pandas as pd

import backtest
import signals

H = "6m"  # long enough to mean something for a Roth, short enough to have data
MIN_CONGRESS = 5
MIN_INSIDER = 3  # insiders buy rarely; 3 open-market buys is already a pattern
OUT = "reports/leaders.json"

INSIDER_EVENTS = """
SELECT upper(trim(ticker)) AS tkr, owner_cik, max(owner_name) AS owner_name,
       try_cast(filed_date AS DATE) AS event_date, sum(shares * price_per_share) AS value
FROM insider_trades
WHERE trans_code = 'P' AND acquired_disposed = 'A' AND price_per_share > 0 AND shares > 0
  AND ticker IS NOT NULL AND upper(trim(ticker)) NOT IN ('', 'NONE', 'N/A', 'NA')
  AND try_cast(filed_date AS DATE) BETWEEN ? AND ?
GROUP BY 1, 2, 4
HAVING sum(shares * price_per_share) >= 25000
"""


def key_congress(chamber, last, first):
    return f"c|{chamber}|{(last or '').strip().lower()}|{(first or '').strip().lower()}"


def key_official(slug):
    return f"w|{slug}"


def key_insider(name):
    return f"i|{' '.join((name or '').upper().split())}"


def _events(con, start, end):
    cb = pd.DataFrame()
    if signals.has_table(con, "senate_trades"):
        cb = signals.congress_buys(con, start, end)
        if len(cb):
            cb = cb.drop_duplicates(["tkr", "event_date", "last_name", "chamber"]).copy()
            cb["key"] = [key_congress(c, l, f) for c, l, f in
                         zip(cb.chamber, cb.last_name, cb.first_name)]
    ib = pd.DataFrame()
    if signals.has_table(con, "insider_trades"):
        ib = signals.clean(con.execute(INSIDER_EVENTS, [start, end]).df())
        if len(ib):
            ib["key"] = [key_insider(n) for n in ib.owner_name]
            counts = ib.key.value_counts()
            ib = ib[ib.key.isin(counts[counts >= MIN_INSIDER].index)].copy()
    return cb, ib


def _official_events(con, start, end):
    if not signals.has_table(con, "wh_trades"):
        return pd.DataFrame()
    wb = con.execute("""SELECT tkr, filed AS event_date, slug, max(name) AS name, max(title) AS title
                        FROM wh_trades WHERE kind = 'stock' AND tx_type = 'Purchase'
                          AND tkr IS NOT NULL AND filed BETWEEN ? AND ?
                        GROUP BY 1, 2, 3""", [start, end]).df()
    if len(wb):
        wb["key"] = [key_official(x) for x in wb.slug]
    return wb


def _board(res, min_n, name_of):
    col = f"xs_{H}"
    if not len(res) or col not in res:
        return []
    res = res.dropna(subset=[col])
    rows = []
    for key, g in res.groupby("key"):
        if len(g) < min_n:
            continue
        best = g.loc[g[col].idxmax()]
        rows.append({"key": key, **name_of(g.iloc[0]), "n": int(len(g)),
                     "avg": float(g[col].mean()), "median": float(g[col].median()),
                     "beat": float((g[col] > 0).mean()),
                     "best": best.tkr, "best_xs": float(best[col]),
                     "tickers": int(g.tkr.nunique())})
    return sorted(rows, key=lambda r: -r["avg"])


def compute(con, start, end, fetch=True):
    cb, ib = _events(con, start, end)
    wb = _official_events(con, start, end)
    tickers = (set(cb.tkr if len(cb) else []) | set(ib.tkr if len(ib) else [])
               | set(wb.tkr if len(wb) else []))
    if fetch and tickers:
        import prices
        prices.fetch(con, tickers | {prices.BENCHMARK}, start=start - datetime.timedelta(days=10))
    series = backtest._price_series(con, tickers | {"SPY"})
    bench = series.get("SPY")
    if bench is None:
        return {"politicians": [], "insiders": [], "officials": [], "records": {}}
    pol = _board(backtest.evaluate(cb, series, bench) if len(cb) else cb, MIN_CONGRESS,
                 lambda r: {"name": f"{r.first_name or ''} {r.last_name}".strip(),
                            "last": r.last_name, "first": r.first_name, "chamber": r.chamber})
    ins = _board(backtest.evaluate(ib, series, bench) if len(ib) else ib, MIN_INSIDER,
                 lambda r: {"name": r.owner_name, "cik": r.owner_cik})
    off = _board(backtest.evaluate(wb, series, bench) if len(wb) else wb, MIN_INSIDER,
                 lambda r: {"name": r["name"], "slug": r["slug"], "title": r["title"]})
    records = {r["key"]: {"n": r["n"], "avg": r["avg"], "beat": r["beat"]}
               for r in pol + ins + off}
    return {"horizon": H, "start": str(start), "end": str(end),
            "politicians": pol, "insiders": ins, "officials": off, "records": records}


def load(con, path=OUT):
    from schema import load_report
    try:
        return json.loads(load_report(con, path) or "null")
    except ValueError:
        return None


def record_points(rec):
    """Conviction points for one buyer's record: (points, plain label) or (0, None)."""
    if not rec:
        return 0, None
    pct = f"{rec['avg'] * 100:+.0f}% vs SPY over {rec['n']} buys"
    if rec["avg"] >= 0.05 and rec["beat"] >= 0.5:
        return 10, f"strong past record ({pct})"
    if rec["avg"] > 0:
        return 5, f"decent past record ({pct})"
    return 0, f"weak past record ({pct})"


LB_CSS = """
.lb{width:100%;border-collapse:collapse;font-size:14px}
.lb th{text-align:left;color:var(--mut);font-weight:600;font-size:12px;text-transform:uppercase;
letter-spacing:.5px;padding:8px 10px;border-bottom:1px solid var(--line);position:static;background:none}
.lb td{padding:10px;border-bottom:1px solid var(--line);vertical-align:middle}
.lb td.n,.lb th.n{text-align:right;font-variant-numeric:tabular-nums}
.lb .rk{color:var(--mut);width:28px}
.lb tr:last-child td{border-bottom:0}
.barx{display:inline-block;height:6px;border-radius:6px;vertical-align:middle;margin-left:8px}
@media (max-width:640px){.lb .hide{display:none}.lb td,.lb th{padding:8px 6px}}
"""


def _pct(x):
    return f"{x * 100:+.1f}%"


def _table(rows, who, best_link, kind):
    if not rows:
        return "<p class='empty'>Not enough history yet. This fills in as the weekly check runs.</p>"
    top = max(abs(r["avg"]) for r in rows) or 1
    body = []
    for k, r in enumerate(rows, 1):
        w = int(60 * min(1, abs(r["avg"]) / top))
        col = "var(--up)" if r["avg"] >= 0 else "var(--down)"
        body.append(
            f"<tr><td class='rk'>{k}</td><td>{who(r)}</td><td class='n'>{r['n']}</td>"
            f"<td class='n'><b class='{'up' if r['avg'] >= 0 else 'down'}'>{_pct(r['avg'])}</b>"
            f"<i class='barx' style='width:{w}px;background:{col}'></i></td>"
            f"<td class='n hide'>{r['beat'] * 100:.0f}%</td>"
            f"<td class='hide'>{best_link(r['best'])} <span class='hint'>{_pct(r['best_xs'])}</span></td></tr>")
    return (f"<div class='panel'><table class='lb'><thead><tr><th></th><th>{kind}</th>"
            "<th class='n'>Buys</th><th class='n'>Avg vs S&amp;P 500</th><th class='n hide'>Beat it</th>"
            "<th class='hide'>Best pick</th></tr></thead><tbody>" + "".join(body) + "</tbody></table></div>")


def render(con, board, pages=()):
    """Leaderboard page body. pages: tickers that have a stock page (to link)."""
    import html
    import today as front
    import stockpage
    pages = set(pages)

    def best_link(t):
        e = html.escape(t)
        return f"<a href='{stockpage.href(t)}'>{e}</a>" if t in pages else e

    def pol(r):
        row = type("R", (), {"last_name": r["last"], "first_name": r["first"], "chamber": r["chamber"],
                             "owner": None})
        return front.member_html(con, row, front.friendly_who(row), face=True)

    def ins(r):
        return front.insider_html(con, r["name"], r.get("cik"), face=True)

    board = board or {"politicians": [], "insiders": []}
    p = [r for r in board.get("politicians") or [] if r["n"] >= MIN_CONGRESS]
    i = [r for r in board.get("insiders") or [] if r["n"] >= MIN_INSIDER]
    when = (f" Buys made public from {board['start']} to {board['end']}." if board.get("start") else "")
    out = ("<div class='hero'><div class='eyebrow'>Track records</div><h1>Whose buys beat the market?</h1>"
           "<p>For each person, every stock they bought, measured six months after the buy became public "
           "(the earliest you could have copied it), minus what an S&amp;P 500 index fund did over the "
           f"same months.{when} Hover a name for who they are.</p></div>")
    out += ("<h2>Members of Congress</h2><p class='hint'>At least "
            f"{MIN_CONGRESS} buys of $15k+ with six months of prices after them.</p>"
            + _table(p[:25], pol, best_link, "Member"))
    o = [r for r in board.get("officials") or [] if r["n"] >= MIN_INSIDER]
    if o:
        out += ("<h2>White House and cabinet</h2><p class='hint'>Stock buys only (most of what they "
                f"report is bonds), at least {MIN_INSIDER} of them.</p>"
                + _table(o[:25], lambda r: front.official_html(con, r["slug"], r["name"], face=True),
                         best_link, "Official"))
    out += ("<h2>Company insiders</h2><p class='hint'>Executives, directors and big owners with at least "
            f"{MIN_INSIDER} open-market buys of $25k+ in their own company.</p>"
            + _table(i[:25], ins, best_link, "Insider"))
    if len(p) > 25 or len(i) > 25:
        worst = sorted(p[25:], key=lambda r: r["avg"])[:10]
        out += ("<details><summary>Weakest Congress records <span>· copying these lost money</span></summary>"
                + _table(worst, pol, best_link, "Member") + "</details>")
    out += ("<p class='hint'>A good record over a handful of trades can be luck. Treat a high rank as a "
            "reason to look closer at their next buy, not as a reason to copy it.</p>")
    return out


def main():
    import duckdb
    from schema import DB_PATH, load_dotenv
    load_dotenv()
    end = datetime.date.today()
    con = duckdb.connect(DB_PATH)
    out = compute(con, end - datetime.timedelta(days=5 * 365), end)
    os.makedirs("reports", exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, default=str)
    from schema import save_report
    save_report(con, OUT)
    print(f"leaders: {len(out['politicians'])} politicians, {len(out['insiders'])} insiders "
          f"with enough history -> {OUT}")


def selftest():
    import duckdb
    con = duckdb.connect()
    signals.make_fixture(con)
    con.execute("CREATE TABLE IF NOT EXISTS prices (tkr VARCHAR, d DATE, close DOUBLE)")
    days = pd.bdate_range("2025-01-01", "2026-06-30")
    rows = []
    for k, d in enumerate(days):
        rows += [("SPY", d.date(), 100.0), ("AAA", d.date(), 100 + k * 0.5),
                 ("BBB", d.date(), 100 - k * 0.05), ("CCC", d.date(), 100.0)]
    con.executemany("INSERT INTO prices VALUES (?,?,?)", rows)
    global MIN_CONGRESS, MIN_INSIDER
    MIN_CONGRESS, MIN_INSIDER = 1, 1
    try:
        out = compute(con, datetime.date(2025, 1, 1), datetime.date(2025, 12, 31), fetch=False)
        page = render(con, out, pages=["AAA"])
    finally:
        MIN_CONGRESS, MIN_INSIDER = 5, 3
    assert out["politicians"], out
    top = out["politicians"][0]
    assert top["avg"] > 0 and top["best"] == "AAA", top
    assert out["records"][top["key"]]["n"] == top["n"]
    assert top["key"] == key_congress(top["chamber"], top["last"], top["first"])
    assert record_points({"avg": 0.08, "beat": 0.6, "n": 9})[0] == 10
    assert record_points({"avg": -0.02, "beat": 0.4, "n": 9})[0] == 0
    assert record_points(None) == (0, None)
    assert "Whose buys beat the market?" in page and "s/AAA.html" in page and "<span class='bio'" in page
    assert "Not enough history" in render(con, None)
    print("leaders selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
