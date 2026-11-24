"""Daily run: pull new filings, match tickers, write the digest.

STOCK Act gives filers 30-45 days to disclose, so once-a-day is plenty. Each
step already does the incremental work itself (senate skips links already in
senate_trades, house skips doc_ids already in house_filings), so this file is
just the call order, not new scraping logic.

House only needs the current + previous year checked: the annual ZIP is keyed
by filing year, and a late Dec filing can land in next year's index in
January, so the prior year stays live for a few weeks after New Year's.
Older years never gain filings after publication.

Usage:
    py daily.py            # senate + house + tickers + insiders (7 days) + digest + alerts
    py daily.py --selftest # offline check of the year window, no network

Meant to run under Windows Task Scheduler once a day; stdout/stderr redirect
to a log file at the scheduler level (`cmd /c ... >> daily.log 2>&1`) rather
than a logging setup in here -- one redirect beats a logging config for a
script with three print-heavy steps already.
"""

import datetime
import sys

import alerts
import digest
import entities
import scrape_house
import scrape_insiders
import scrape_senate


def house_years(today=None):
    y = (today or datetime.date.today()).year
    return [y - 1, y]


def main():
    print(f"=== Quantreyda daily run: {datetime.datetime.now()} ===")

    print("\n--- Senate ---")
    scrape_senate.main()

    years = house_years()
    print(f"\n--- House {years} ---")
    scrape_house.main(years)

    print("\n--- Resolve entities ---")
    entities.main()

    # Form 4 needs SEC_CONTACT_EMAIL; skip (don't crash) without it so the
    # congress half still runs on a fresh checkout.
    print("\n--- Insider trades (Form 4, last 7 days) ---")
    try:
        scrape_insiders.main_live(days=7)
    except SystemExit as e:
        print(f"skipped: {e}")

    print("\n--- Digest ---")
    digest.main(days=7)

    print("\n--- Watchlist alerts ---")
    alerts.main()

def selftest():
    assert house_years(datetime.date(2026, 1, 15)) == [2025, 2026]
    assert house_years(datetime.date(2026, 8, 14)) == [2025, 2026]
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
