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


def stock_panel(con, tkr):
    return analysts.stock_panel(con, tkr) + optionsflow.stock_panel(con, tkr)


def selftest():
    import duckdb
    for m in (heatmap, analysts, optionsflow):
        m.selftest()
    con = duckdb.connect()
    pg = page(con, datetime.date(2026, 11, 27), set())
    assert "Market moves" in pg and "fills in on the next daily update" in pg and "<script" not in pg
    assert stock_panel(con, "AAA") == ""
    print("moves selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        import duckdb
        from schema import DB_PATH
        refresh(duckdb.connect(DB_PATH))
