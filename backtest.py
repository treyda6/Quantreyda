"""Did the signals ever beat just buying SPY? Check before risking money.

For every signal (see signals.py), pretend you bought at the close of the
first trading day *after* the filing date -- filings often post after the
market closes, so same-day would be cheating -- and held for 3, 6 and 12
months (63 / 126 / 252 trading days). Long windows on purpose: this is for
picking long-term holdings (e.g. in a Roth IRA), not for quick trades. Compare that to SPY over the exact same
days. "Excess" is signal return minus SPY return.

    py backtest.py                     # 2020-01-01 -> today, writes reports/backtest.md
    py backtest.py --start 2020-01-01
    py backtest.py --no-fetch          # use only prices already cached
    py backtest.py --selftest          # offline, synthetic prices

Read the result as "is there anything here?", not as a promise. No trading
costs, no taxes, survivorship bias from Yahoo dropping delisted tickers, and
a few hundred events is a small sample. A mean excess of +1% with a hit rate
near 50% is noise.
"""

import datetime
import os
import sys

import duckdb
import pandas as pd

import committees
import prices
import signals
from schema import DB_PATH, ensure_schema

HORIZONS = {"3m": 63, "6m": 126, "12m": 252}


def _price_series(con, tickers):
    df = con.execute(
        "SELECT tkr, d, close FROM prices WHERE tkr IN (SELECT unnest(?)) ORDER BY tkr, d",
        [list(tickers)]).df()
    return {t: g.set_index(pd.to_datetime(g.d))["close"] for t, g in df.groupby("tkr")}


def forward_returns(series, bench, event_date):
    """{horizon: (return, bench_return)} for one event; missing -> absent."""
    if series is None or bench is None:
        return {}
    ts = pd.Timestamp(event_date)
    after = series.index[series.index > ts]
    if not len(after):
        return {}
    i0 = series.index.get_loc(after[0])
    entry_day, entry = after[0], series.iloc[i0]
    b_after = bench.index[bench.index >= entry_day]
    if not len(b_after):
        return {}
    j0 = bench.index.get_loc(b_after[0])
    out = {}
    for name, h in HORIZONS.items():
        if i0 + h < len(series) and j0 + h < len(bench):
            out[name] = (series.iloc[i0 + h] / entry - 1,
                         bench.iloc[j0 + h] / bench.iloc[j0] - 1)
    return out


def evaluate(events, series_by_tkr, bench):
    """events: DataFrame with tkr, event_date. Adds ret_/xs_ columns."""
    rows = []
    for _, e in events.iterrows():
        fr = forward_returns(series_by_tkr.get(e.tkr), bench, e.event_date)
        r = e.to_dict()
        for name in HORIZONS:
            if name in fr:
                r[f"ret_{name}"] = fr[name][0]
                r[f"xs_{name}"] = fr[name][0] - fr[name][1]
        rows.append(r)
    return pd.DataFrame(rows)


def summarize(df, label):
    line = {"signal": label, "events": len(df)}
    for name in HORIZONS:
        col = f"xs_{name}"
        s = df[col].dropna() if col in df else pd.Series(dtype=float)
        line[f"n_{name}"] = len(s)
        line[f"avg_excess_{name}"] = s.mean() if len(s) else None
        line[f"median_excess_{name}"] = s.median() if len(s) else None
        line[f"beat_spy_{name}"] = (s > 0).mean() if len(s) else None
    return line


def _fmt_table(summary):
    hdr = ["Signal", "Events"] + [f"Held {h}: avg vs SPY / typical / % that beat SPY (n)"
                                  for h in HORIZONS]
    lines = ["| " + " | ".join(hdr) + " |", "|" + "---|" * len(hdr)]
    pct = lambda x: "—" if x is None or pd.isna(x) else f"{x * 100:+.1f}%"
    hit = lambda x: "—" if x is None or pd.isna(x) else f"{x * 100:.0f}%"
    for s in summary:
        cells = [s["signal"], str(s["events"])]
        for h in HORIZONS:
            cells.append(f"{pct(s[f'avg_excess_{h}'])} / {pct(s[f'median_excess_{h}'])}"
                         f" / {hit(s[f'beat_spy_{h}'])} ({s[f'n_{h}']})")
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


# ------------------------------------------------------------------ copycat
# "If you had copied them": buy every stock a group bought (the first close
# after the filing), hold one year, and compare to the S&P 500 over the same
# days, grouped by the year of the buy. Buys from the last year that haven't
# had a full year yet are counted up to the latest price and marked open.

COPY_DAYS = 252
RECORD_MIN, RECORD_WAIT = 5, 380  # finished trades needed; days before a result is known


def hold_return(series, bench, event_date, days=COPY_DAYS):
    """(return, S&P return, still_open) or None when there is no entry price."""
    if series is None or bench is None:
        return None
    ts = pd.Timestamp(event_date)
    after = series.index[series.index > ts]
    if not len(after):
        return None
    i0 = series.index.get_loc(after[0])
    b_after = bench.index[bench.index >= after[0]]
    if not len(b_after):
        return None
    j0 = bench.index.get_loc(b_after[0])
    left = min(len(series) - 1 - i0, len(bench) - 1 - j0)
    if left < 1:
        return None
    h = min(days, left)
    return (series.iloc[i0 + h] / series.iloc[i0] - 1,
            bench.iloc[j0 + h] / bench.iloc[j0] - 1, h < days)


def proven_picks(cb, series, bench):
    """Congress buys by members who, at the time of the buy, already had a
    winning record: RECORD_MIN+ earlier buys whose one-year result was known,
    averaging better than the S&P 500. Judged only on what was knowable then,
    so the result isn't flattered by hindsight."""
    if not len(cb):
        return cb
    cb = cb.copy()
    cb["d"] = pd.to_datetime(cb.event_date)
    xs = []
    for _, r in cb.iterrows():
        got = hold_return(series.get(r.tkr), bench, r.event_date)
        xs.append(got[0] - got[1] if got and not got[2] else None)
    cb["edge"] = xs
    keep = []
    for (_, last, first), g in cb.groupby(["chamber", "last_name", "first_name"], dropna=False):
        g = g.sort_values("d")
        for i, r in g.iterrows():
            done = g[(g.d <= r.d - pd.Timedelta(days=RECORD_WAIT)) & g.edge.notna()]
            if len(done) >= RECORD_MIN and done.edge.mean() > 0 and (done.edge > 0).mean() >= 0.5:
                keep.append(i)
    return cb.loc[sorted(keep)].drop(columns=["d", "edge"])


def wh_events(con, start, end):
    if not signals.has_table(con, "wh_trades"):
        return pd.DataFrame(columns=["tkr", "event_date"])
    return con.execute("""SELECT DISTINCT tkr, filed AS event_date, slug FROM wh_trades
                          WHERE kind = 'stock' AND tx_type = 'Purchase' AND tkr IS NOT NULL
                            AND filed BETWEEN ? AND ?""", [start, end]).df()


def copycat_groups(con, start, end, groups, series, bench):
    """[(label, about, events)] for the 'If you had copied them' section."""
    out = []
    cb = groups.get("Congress buys ≥ $15k")
    if cb is not None:
        out.append(("Every stock Congress bought", "Every stock purchase of $15,000 or more "
                    "that a member of Congress or their family disclosed.", cb))
        out.append(("Politicians with a winning record", "Only buys by members whose "
                    f"earlier picks (at least {RECORD_MIN} that had finished a full year by "
                    "then) had beaten the S&P 500 on average. Picked using only what you "
                    "could have known at the time.", proven_picks(cb, series, bench)))
    cl = groups.get("Insider cluster buys (3+ in 30d)")
    if cl is not None:
        out.append(("Insiders buying together", "Stocks where 3 or more executives or "
                    "directors bought their own company's shares within 30 days.", cl))
    wh = wh_events(con, start, end)
    if len(wh):
        out.append(("White House and cabinet", "Stocks the President, Vice President, "
                    "cabinet and other senior officials reported buying.", wh))
    return out


def copycat(label, about, events, series, bench):
    rows = []
    for _, e in events.iterrows():
        got = hold_return(series.get(e.tkr), bench, e.event_date)
        if got:
            rows.append((pd.Timestamp(e.event_date).year,) + got)
    years = []
    for y in sorted({r[0] for r in rows}):
        rs = [r for r in rows if r[0] == y]
        years.append({"year": y, "n": len(rs),
                      "ret": sum(r[1] for r in rs) / len(rs),
                      "spy": sum(r[2] for r in rs) / len(rs),
                      "open": sum(r[3] for r in rs)})
    total = None
    if rows:
        done = [r for r in rows if not r[3]]
        total = {"n": len(done), "ret": sum(r[1] for r in done) / len(done) if done else None,
                 "spy": sum(r[2] for r in done) / len(done) if done else None,
                 "beat": sum(r[1] > r[2] for r in done) / len(done) if done else None}
    return {"label": label, "about": about, "years": years, "total": total}


# ---------------------------------------------------------------- strategies
# A "copy them" portfolio you could actually have run: every day, hold an
# equal slice of every stock the group bought in the last year (bought the day
# after it was disclosed, sold a year later); stay in cash when there's none.

STRATEGIES = {"Every stock Congress bought": "Copy Congress",
              "Politicians with a winning record": "Copy the winners",
              "Insiders buying together": "Copy insider clusters",
              "White House and cabinet": "Copy the White House"}


def strategy_curve(events, series, bench, hold=COPY_DAYS):
    """(dates, portfolio value, S&P 500 value) both starting at 1, or None."""
    import numpy as np
    days = bench.index
    if not len(events) or len(days) < 30:
        return None
    count = {}
    for _, e in events.iterrows():
        s = series.get(e.tkr)
        if s is None:
            continue
        after = s.index[s.index > pd.Timestamp(e.event_date)]
        if not len(after):
            continue
        i0 = days.searchsorted(after[0])          # entry close (no return that day)
        i1 = min(i0 + hold, len(days) - 1)
        if i0 >= len(days) - 1:
            continue
        arr = count.setdefault(e.tkr, np.zeros(len(days) + 1))
        arr[i0 + 1] += 1                           # earns from the next day...
        arr[i1 + 1] -= 1                           # ...through the exit day
    if not count:
        return None
    tk = sorted(count)
    w = np.array([np.cumsum(count[t])[:-1] for t in tk])          # positions held, per day
    px = pd.DataFrame({t: series[t] for t in tk}).reindex(days).ffill()
    rets = px.pct_change().fillna(0).to_numpy().T                  # tickers x days
    held = w.sum(axis=0)
    port = np.where(held > 0, (w * rets).sum(axis=0) / np.maximum(held, 1), 0.0)
    if not held.any():
        return None
    first = max(0, int(np.argmax(held > 0)) - 1)  # start at the first entry close, value 1
    nav = np.cumprod(1 + port[first:])
    spy = (bench / bench.iloc[first]).to_numpy()[first:]
    return list(days[first:]), nav, spy


def curve_stats(dates, nav, spy):
    import numpy as np
    years = max((dates[-1] - dates[0]).days / 365.25, 1e-9)
    daily = np.diff(nav) / nav[:-1]
    sharpe = float(daily.mean() / daily.std() * np.sqrt(252)) if len(daily) > 20 and daily.std() > 0 else None
    k = max(0, len(nav) - 253)
    return {"total": float(nav[-1] - 1), "spy_total": float(spy[-1] - 1),
            "cagr": float(nav[-1] ** (1 / years) - 1) if years >= 1 else None,
            "one_year": float(nav[-1] / nav[k] - 1) if len(nav) > 253 else None,
            "spy_one_year": float(spy[-1] / spy[k] - 1) if len(spy) > 253 else None,
            "sharpe": sharpe, "start": str(dates[0].date()), "years": round(years, 1)}


def strategies(groups, series, bench):
    out = []
    for label, about, ev in groups:
        got = strategy_curve(ev, series, bench)
        if not got:
            continue
        dates, nav, spy = got
        step = max(1, len(dates) // 160)  # ~160 points is plenty for a small chart
        idx = list(range(0, len(dates), step)) + ([len(dates) - 1] if (len(dates) - 1) % step else [])
        out.append({"label": STRATEGIES.get(label, label), "group": label, "about": about,
                    "dates": [str(dates[i].date()) for i in idx],
                    "nav": [round(float(nav[i]), 4) for i in idx],
                    "spy": [round(float(spy[i]), 4) for i in idx], **curve_stats(dates, nav, spy)})
    return out


def build_events(con, start, end):
    groups = {}
    if signals.has_table(con, "senate_trades"):
        cb = signals.congress_buys(con, start, end)
        cb = cb.drop_duplicates(["tkr", "event_date", "last_name"])
        groups["Congress buys ≥ $15k"] = cb
        groups["Congress buys ≥ $50k"] = cb[cb.amount_low >= 50001]
        groups["Congress buys ≥ $250k"] = cb[cb.amount_low >= 250001]
        if signals.has_table(con, "committee_members") and len(cb):
            flagged = cb.apply(lambda r: bool(committees.committee_flag(
                con, r.tkr, r.last_name, r.first_name, r.chamber)), axis=1)
            groups["Congress buys in their committee's industry"] = cb[flagged]
    if signals.has_table(con, "insider_trades"):
        cl = signals.insider_clusters(con, start, end)
        groups["Insider cluster buys (3+ in 30d)"] = cl
        groups["…with an officer buying"] = cl[cl.any_officer]
    return groups


def run(con, start, end, fetch=True):
    if fetch and signals.has_table(con, "senate_trades"):
        committees.ensure_flags_ready(con, set(signals.congress_buys(con, start, end).tkr))
    groups = build_events(con, start, end)
    tickers = set().union(*(set(g.tkr) for g in groups.values())) if groups else set()
    if fetch:
        prices.fetch(con, tickers | {prices.BENCHMARK},
                     start=start - datetime.timedelta(days=10))
    prices.ensure_tables(con)
    series = _price_series(con, tickers | {prices.BENCHMARK})
    bench = series.get(prices.BENCHMARK)
    if bench is None:
        sys.exit("no SPY prices cached; run without --no-fetch first")
    summary, detail = [], []
    for label, ev in groups.items():
        res = evaluate(ev, series, bench)
        summary.append(summarize(res, label))
        detail.append(res.assign(signal=label))
    cg = copycat_groups(con, start, end, groups, series, bench)
    run.copycat = [copycat(label, about, ev, series, bench) for label, about, ev in cg]
    run.strategies = strategies(cg, series, bench)
    return summary, (pd.concat(detail) if detail else pd.DataFrame())


def copycat_md(cc):
    pct = lambda x: "—" if x is None else f"{x * 100:+.1f}%"
    out = ["## If you had copied them (bought every one, held a year)\n"]
    for g in cc:
        out += [f"### {g['label']}\n", g["about"] + "\n",
                "| Year bought | Buys | Their picks | S&P 500 | Result |", "|---|---|---|---|---|"]
        for y in g["years"]:
            gap = y["ret"] - y["spy"]
            note = f" ({y['open']} still open)" if y["open"] else ""
            out.append(f"| {y['year']} | {y['n']} | {pct(y['ret'])} | {pct(y['spy'])} | "
                       f"{'Beat' if gap >= 0 else 'Lagged'} by {abs(gap) * 100:.1f}%{note} |")
        out.append("")
    return "\n".join(out)


def write_report(summary, start, end, path="reports/backtest.md", cc=None, strat=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    text = (f"# Backtest: signals vs SPY\n\nFilings from {start} to {end}. Entry at the close "
            "of the first trading day after the filing date. \"vs SPY\" is the signal's "
            "return minus SPY's over the same days. No costs or taxes.\n\n"
            + _fmt_table(summary)
            + "\n\n## How to read this\n\n"
              "- **avg vs SPY**: on average, how much better (+) or worse (-) the stock did "
              "than just owning SPY (an S&P 500 index fund) over the same months.\n"
              "- **typical**: the median, i.e. the middle result. If the average is high but "
              "the typical result is near zero, a few lucky winners are doing all the work.\n"
              "- **% that beat SPY**: how often it won. Around 50% is a coin flip.\n"
              "- **n**: how many examples. Under ~50, don't trust it.\n\n"
              "A signal worth using shows a positive average **and** typical result **and** "
              "beats SPY clearly more than half the time, at 6 and 12 months. If none do, "
              "the honest answer is to keep most of the Roth in an index fund and treat "
              "these lists as ideas to research, not to copy.\n"
            + ("\n" + copycat_md(cc) if cc else ""))
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    import json  # the dashboard reads this to write plain-English verdicts
    with open(path.replace(".md", ".json"), "w", encoding="utf-8") as f:
        json.dump({"start": str(start), "end": str(end), "summary": [
            {k: (None if isinstance(v, float) and v != v else v) for k, v in s.items()}
            for s in summary], "copycat": cc or [], "strategies": strat or []}, f, default=float)
    return text


def selftest():
    con = duckdb.connect()
    signals.make_fixture(con)
    committees.load_fixture(con)
    con.execute("INSERT INTO committee_members VALUES ('A1','S','A',NULL,'Alpha','SSAS',"
                "'Senate Committee on Armed Services','2025-01-01')")
    prices.ensure_tables(con)
    days = pd.bdate_range("2025-01-01", "2025-12-31")
    # SPY flat; AAA rises 1%/day; XYZ falls 0.5%/day
    for tkr, step in (("SPY", 0.0), ("AAA", 0.01), ("XYZ", -0.005), ("CCC", 0.0)):
        px = [(tkr, d.date(), 100 * (1 + step) ** i) for i, d in enumerate(days)]
        con.executemany("INSERT INTO prices VALUES (?,?,?)", px)
    D = datetime.date
    summary, detail = run(con, D(2025, 1, 1), D(2025, 6, 30), fetch=False)
    assert "xs_12m" not in detail or detail["xs_12m"].isna().all(), "12m runs past the fixture"
    by = {s["signal"]: s for s in summary}
    aaa = by["Congress buys ≥ $15k"]
    assert aaa["events"] == 2, aaa  # AAA + CCC
    row = detail[(detail.tkr == "AAA")].iloc[0]
    assert abs(row["xs_3m"] - (1.01 ** 63 - 1)) < 1e-9, row
    xyz = detail[detail.tkr == "XYZ"].iloc[0]
    assert xyz["xs_6m"] < 0
    # entry is strictly after the filing date: filed Mon 2025-03-10 -> entry Tue
    fr = forward_returns(pd.Series([1.0, 2.0, 4.0], index=pd.to_datetime(
        ["2025-03-10", "2025-03-11", "2025-03-12"])), pd.Series([1.0, 1.0, 1.0], index=pd.to_datetime(
        ["2025-03-10", "2025-03-11", "2025-03-12"])), D(2025, 3, 10))
    assert fr == {}, "too few days for any horizon"
    assert "CCC" in set(detail.tkr)
    assert by["Congress buys in their committee's industry"]["events"] == 1, \
        "Alpha (Armed Services) buying AAA (missiles) is the one committee-linked buy"
    cc = {g["label"]: g for g in run.copycat}
    every = cc["Every stock Congress bought"]
    assert [y["year"] for y in every["years"]] == [2025] and every["years"][0]["open"] == 2, every
    assert every["years"][0]["ret"] > 0 and every["years"][0]["spy"] == 0, every
    assert hold_return(series_fixture := pd.Series([1.0, 2.0, 3.0], index=pd.to_datetime(
        ["2025-01-01", "2025-01-02", "2025-01-03"])), series_fixture, D(2025, 1, 1)) == (0.5, 0.5, True)
    # winning record: 6 finished winners by one member, then a later buy counts
    days2 = pd.bdate_range("2020-01-01", "2024-12-31")
    up = pd.Series([1.01 ** i for i in range(len(days2))], index=days2)
    flat = pd.Series(1.0, index=days2)
    cb = pd.DataFrame({"tkr": ["AAA"] * 7 + ["AAA"], "chamber": ["S"] * 8,
                       "last_name": ["Alpha"] * 7 + ["Beta"], "first_name": ["A"] * 8,
                       "event_date": [D(2020, 1, 10 + i) for i in range(6)] + [D(2023, 1, 10),
                                                                               D(2023, 1, 10)]})
    picked = proven_picks(cb, {"AAA": up}, flat)
    assert list(picked.event_date) == [D(2023, 1, 10)] and picked.last_name.iloc[0] == "Alpha", picked
    assert "If you had copied them" in copycat_md(run.copycat)
    st = {x["group"]: x for x in run.strategies}["Every stock Congress bought"]
    assert st["label"] == "Copy Congress" and st["nav"][0] == 1.0 and st["nav"][-1] > 1, st["nav"][-3:]
    assert st["spy"][-1] == 1.0 and (st["sharpe"] is None or st["sharpe"] > 0), st
    # one stock up 1%/day held 3 days of a flat index: 1.01^3
    d3 = pd.bdate_range("2025-01-01", periods=40)
    up3 = pd.Series([1.01 ** i for i in range(40)], index=d3)
    ev = pd.DataFrame({"tkr": ["U"], "event_date": [D(2025, 1, 1)]})
    dts, nav, spy = strategy_curve(ev, {"U": up3}, pd.Series(1.0, index=d3), hold=3)
    assert abs(nav[-1] - 1.01 ** 3) < 1e-9 and nav[0] == 1 and abs(nav[2] - 1.01 ** 2) < 1e-9, nav[:5]
    print(_fmt_table(summary))
    print("backtest selftest ok")


def arg(flag, default=None):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
        sys.exit()
    start = datetime.date.fromisoformat(arg("--start", "2020-01-01"))
    end = datetime.date.fromisoformat(arg("--end", datetime.date.today().isoformat()))
    c = duckdb.connect(DB_PATH)
    ensure_schema(c)
    summ, det = run(c, start, end, fetch="--no-fetch" not in sys.argv)
    print(write_report(summ, start, end, cc=getattr(run, "copycat", None),
                       strat=getattr(run, "strategies", None)))
    from schema import save_report
    for p in ("reports/backtest.md", "reports/backtest.json"):
        save_report(c, p)
    os.makedirs("reports", exist_ok=True)
    det.to_csv("reports/backtest_events.csv", index=False)
    print("per-event detail: reports/backtest_events.csv")
