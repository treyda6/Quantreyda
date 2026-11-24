"""One-time history fill, safe to run again: every step resumes.

    py backfill.py                       # House 2020+, Senate all, last 8 quarters of Form 4
    py backfill.py --since 2018 --quarters 12

The Senate step lists every filing since 2012 and is the slow one (hours on
the first run). If this gets cut off, run it again: finished filings are
skipped. The GitHub workflow runs it with a time limit and picks up where it
stopped on the next run.
"""

import datetime
import sys

import entities
import scrape_house
import scrape_insiders
import scrape_senate


def arg(flag, default=None):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


def main(since=2020, quarters=8):
    this_year = datetime.date.today().year
    print(f"--- House {since}-{this_year} ---")
    scrape_house.main(list(range(since, this_year + 1)))
    print("\n--- Resolve tickers ---")
    entities.main()

    print(f"\n--- Form 4: last {quarters} quarterly files ---")
    try:
        qs = sorted(scrape_insiders.list_quarters(scrape_insiders._session()))[-quarters:]
        for q in qs:
            scrape_insiders.main_bulk(q)
        scrape_insiders.main_live(days=100)  # the gap since the newest quarterly file
    except SystemExit as e:
        print(f"skipped: {e}")

    print("\n--- Senate (slow on first run) ---")
    scrape_senate.main()
    entities.main()


if __name__ == "__main__":
    main(int(arg("--since", 2020)), int(arg("--quarters", 8)))
