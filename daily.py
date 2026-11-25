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
    py daily.py            # senate + house + tickers + insiders (7 days) + contracts + 13Fs + committees + White House + activists + digest + alerts + news
    py daily.py --selftest # offline check of the year window, no network

Meant to run under Windows Task Scheduler once a day; stdout/stderr redirect
to a log file at the scheduler level (`cmd /c ... >> daily.log 2>&1`) rather
than a logging setup in here -- one redirect beats a logging config for a
script with three print-heavy steps already.
"""

import datetime
import sys

import activists
import alerts
import bills
import committees
import companyintel
import contracts
import fec
import digest
import funds
import health
import news
import prices
import stockpage
import whitehouse
import entities
import scrape_house
import scrape_insiders
import scrape_senate


def _con():
    import duckdb
    from schema import DB_PATH
    return duckdb.connect(DB_PATH)


def company_names(con, tickers):
    """{tkr: company name} from the health check, else SEC's company list."""
    names = {}
    for t in tickers:
        r = con.execute("""SELECT coalesce((SELECT company FROM health WHERE tkr = ?),
                                           (SELECT name FROM companies WHERE tkr = ?))""",
                        [t, t]).fetchone()
        if r and r[0]:
            names[t] = r[0]
    return names


def house_years(today=None):
    y = (today or datetime.date.today()).year
    return [y - 1, y]


def main():
    print(f"=== Quantreyda daily run: {datetime.datetime.now()} ===")

    print("\n--- Senate ---")
    scrape_senate.main(max_minutes=20)

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

    # Each extra source is optional: a failure there shouldn't cost you the digest.
    for label, step in (("Big government contracts", lambda: contracts.main(days=14)),
                        ("Top fund 13Fs", lambda: funds.fetch(_con())),
                        ("Committee rosters", lambda: committees.refresh_rosters(_con())),
                        ("White House and cabinet (OGE 278-T)", lambda: whitehouse.main()),
                        ("Activist 5%+ stakes (13D/13G)", lambda: activists.refresh(_con())),
                        ("Bills moving in Congress", lambda: bills.refresh(_con()))):
        print(f"\n--- {label} ---")
        try:
            step()
        except SystemExit as e:
            print(f"skipped: {e}")
        except Exception as e:
            print(f"skipped ({type(e).__name__}: {e})")

    print("\n--- Digest ---")
    digest.main(days=7)

    print("\n--- Watchlist alerts ---")
    alerts.main()

    # Stock pages: two years of prices, a health check and the latest SEC
    # filings ("news") for every stock that gets a page. All free; each step
    # caches, so after the first day this only fetches what's new.
    print("\n--- Stock pages: prices, health, news ---")
    con = _con()
    today = datetime.date.today()
    pages = stockpage.pick(con, today, digest.digest_tickers(con, today, 14))
    for label, step in (("prices", lambda: prices.fetch(con, set(pages) | {prices.BENCHMARK},
                                                         start=today - datetime.timedelta(days=740))),
                        ("industry codes", lambda: committees.refresh_companies(con, pages)),
                        ("health", lambda: health.refresh(con, pages)),
                        ("news", lambda: news.refresh(con, pages)),
                        ("lobbying, contracts, buzz",
                         lambda: companyintel.refresh(con, company_names(con, pages))),
                        ("election money (FEC)",
                         lambda: fec.refresh(con, company_names(con, pages)))):
        try:
            step()
        except SystemExit as e:
            print(f"{label} skipped: {e}")
        except Exception as e:
            print(f"{label} skipped ({type(e).__name__}: {e})")
    con.close()

def selftest():
    assert house_years(datetime.date(2026, 1, 15)) == [2025, 2026]
    assert house_years(datetime.date(2026, 8, 14)) == [2025, 2026]
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
