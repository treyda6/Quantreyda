"""The Sunday email: this week's digest plus a health check on your watchlist.

    py weekly.py              # build and email it (or save to reports/ without email set up)
    py weekly.py --selftest

Schedule it for Sunday morning (see README). It doesn't scrape anything
itself; daily.py keeps the data fresh during the week.
"""

import datetime
import os
import sys

import duckdb

import alerts
import digest
import health
import mailer
from schema import DB_PATH, ensure_schema


def watchlist_section(hl, tickers):
    if not tickers:
        return ["## Your watchlist", "",
                "_Empty. Add tickers you own or are watching to `watchlist.txt`._", ""]
    out = ["## Your watchlist", "", "| Ticker | Company | Health |", "|---|---|---|"]
    for t in tickers:
        h = hl.get(t)
        out.append(f"| **{t}** | {(h or {}).get('company') or ''} | "
                   f"{h['verdict'] + ': ' + h['reasons'] if h else 'not checked yet'} |")
    return out + [""]


def build(con, today, tickers):
    _, rest = digest.build(con, today, days=7).split("\n", 1)  # drop its title
    watch = "\n".join(watchlist_section(health.lookup(con, tickers), tickers))
    return f"# Your weekly digest, week ending {today}\n\n{watch}{rest}"


def main():
    today = datetime.date.today()
    con = duckdb.connect(DB_PATH)
    ensure_schema(con)
    tickers = alerts.read_watchlist()
    try:
        health.refresh(con, set(tickers) | digest.digest_tickers(con, today, 7))
    except SystemExit as e:
        print(f"health check skipped: {e}")
    text = build(con, today, tickers)
    if mailer.send(f"Weekly digest, {today:%b %d}", text):
        print("weekly digest emailed")
    else:
        os.makedirs("reports", exist_ok=True)
        path = f"reports/weekly-{today}.md"
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print(text)
        print(f"\nemail not set up; saved to {path}")


def selftest():
    import signals
    con = duckdb.connect()
    signals.make_fixture(con)
    health.ensure_table(con)
    con.execute("INSERT INTO health (tkr, checked_on, company, verdict, reasons) VALUES "
                "('MSFT', '2025-03-30', 'Microsoft', 'Solid', 'sales up 15%, profitable')")
    text = build(con, datetime.date(2025, 3, 15), ["MSFT", "ZZZ"])
    assert text.startswith("# Your weekly digest, week ending 2025-03-15"), text[:80]
    assert "| **MSFT** | Microsoft | Solid: sales up 15%, profitable |" in text
    assert "| **ZZZ** |  | not checked yet |" in text
    assert "**AAA**" in text, "the week's congress buys follow the watchlist"
    assert text.index("Your watchlist") < text.index("Congress buys")
    assert "Empty." in build(con, datetime.date(2025, 3, 15), [])
    print("weekly selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
