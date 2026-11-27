"""The Moves page: where money moved in the market, what analysts changed,
and unusual options bets. All free sources:

  heatmap.py       Nasdaq's stock screener + sector fund prices (Yahoo)
  analysts.py      Yahoo Finance rating changes and price targets
  optionsflow.py   Cboe's delayed options quotes

    refresh(con, pages)          daily: fetch all three (each optional)
    page(con, today, pages)      -> HTML body for moves.html
    stock_panel(con, tkr)        -> analyst + options panels for a stock page
"""

import datetime
import os
import sys
import traceback

import analysts
import heatmap
import optionsflow

CSS = heatmap.CSS + analysts.CSS + optionsflow.CSS


def refresh(con, pages=()):
    import prices
    today = datetime.date.today()
    steps = (("stock map", lambda: heatmap.refresh(con)),
             ("sector funds", lambda: prices.fetch(con, [t for t, _ in heatmap.SECTOR_ETFS] + [prices.BENCHMARK],
                                                   start=today - datetime.timedelta(days=740))),
             ("analyst ratings", lambda: analysts.refresh(con, heatmap.biggest(con, 150) + list(pages)[:150], minutes=5)),
             ("options", lambda: optionsflow.refresh(con, heatmap.biggest(con, 60) + list(pages)[:40], minutes=4)))
    for label, step in steps:
        try:
            step()
        except Exception as e:  # one source down shouldn't cost the others
            tb = traceback.extract_tb(e.__traceback__)
            where = f" at {os.path.basename(tb[-1].filename)}:{tb[-1].lineno}" if tb else ""
            print(f"{label} skipped ({type(e).__name__}: {e}{where})")


def page(con, today, pages):
    return ("<div class='sec-top'><h1>Market moves</h1><p class='hint'>Where money went in the last "
            "trading session, what Wall Street analysts changed this week, and the biggest unusual "
            "options bets. Use it to spot what's moving, then check the stock's page before acting.</p></div>"
            + heatmap.section(con, today, pages) + analysts.section(con, today, pages)
            + optionsflow.section(con, pages))


def home_teaser(con, today=None):
    """Two or three lines for the home page's Market moves tile."""
    import html
    today = today or datetime.date.today()
    out = []
    rets = heatmap.sector_returns(con, today)
    names = dict(heatmap.SECTOR_ETFS)
    day = sorted(((r.get("1d"), t) for t, r in rets.items() if t in names and r.get("1d") is not None))
    if day:
        (lo, lt), (hi, ht) = day[0], day[-1]
        out.append(f"<li>Best sector: <b>{html.escape(names[ht])}</b> {heatmap._pct(hi)}; "
                   f"worst: <b>{html.escape(names[lt])}</b> {heatmap._pct(lo)}</li>")
    calls = analysts.recent(con, today, 7)
    up = sum(r[3] == "Upgrade" for r in calls)
    down = sum(r[3] == "Downgrade" for r in calls)
    if up or down:
        out.append(f"<li><b>{up}</b> analyst upgrades and <b>{down}</b> downgrades this week</li>")
    _, bets = optionsflow.latest(con, 1)
    if bets:
        b = bets[0]
        out.append(f"<li>Biggest options bet: <b>{html.escape(b[1])}</b> ${b[5]:,.0f} {b[4]}s, "
                   f"{optionsflow._money(b[10])}</li>")
    return "<ul class='teaser'>" + "".join(out) + "</ul>" if out else ""


def stock_panel(con, tkr):
    return analysts.stock_panel(con, tkr) + optionsflow.stock_panel(con, tkr)


def selftest():
    import duckdb
    for m in (heatmap, analysts, optionsflow):
        m.selftest()
    con = duckdb.connect()
    pg = page(con, datetime.date(2026, 11, 27), set())
    assert "Market moves" in pg and "fills in on the next daily update" in pg and "<script" not in pg
    assert stock_panel(con, "AAA") == "" and home_teaser(con) == ""
    print("moves selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        import duckdb
        from schema import DB_PATH
        refresh(duckdb.connect(DB_PATH))
