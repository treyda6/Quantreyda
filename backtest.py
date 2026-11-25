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
    return summary, (pd.concat(detail) if detail else pd.DataFrame())


def write_report(summary, start, end, path="reports/backtest.md"):
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
              "these lists as ideas to research, not to copy.\n")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    import json  # the dashboard reads this to write plain-English verdicts
    with open(path.replace(".md", ".json"), "w", encoding="utf-8") as f:
        json.dump({"start": str(start), "end": str(end), "summary": [
            {k: (None if isinstance(v, float) and v != v else v) for k, v in s.items()}
            for s in summary]}, f, default=float)
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
    print(write_report(summ, start, end))
    os.makedirs("reports", exist_ok=True)
    det.to_csv("reports/backtest_events.csv", index=False)
    print("per-event detail: reports/backtest_events.csv")
