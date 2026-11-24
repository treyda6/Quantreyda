# Quantreyda

A personal tracker for what members of Congress and company insiders are buying.
It's meant for finding long-term ideas (for example for a Roth IRA), not for
day trading.

It does three things:

1. **Collects public filings** every day: stock trades that members of Congress
   must disclose (STOCK Act), and SEC Form 4 filings that company executives and
   directors file when they buy or sell their own company's stock.
2. **Writes a short digest** (`reports/digest-YYYY-MM-DD.md`) listing the buys
   worth a look, with a plain-English note on what each section means.
3. **Checks whether any of it has ever worked** (`reports/backtest.md`): if you'd
   bought every signal and held it for 3, 6 or 12 months, would you have beaten
   just owning SPY (an S&P 500 index fund)?

Everything comes free from government websites. No paid data.

> **Honest expectations.** Most studies find that copying Congress *after* the
> disclosure delay adds little or nothing over an index fund. Clustered insider
> buying has a better record, but it's not a sure thing either. Use the digest
> as a research list, keep the backtest honest, and keep the core of a Roth in
> index funds unless the numbers clearly say otherwise.

## Setup (once)

You need Python 3.10 or newer.

```bash
git clone https://github.com/treyda6/Quantreyda
cd Quantreyda
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip ...
cp .env.example .env                             # then put your email in it
```

The SEC refuses requests without a contact email in them, so `.env` needs
`SEC_CONTACT_EMAIL=you@example.com`. It stays on your computer (`.env` is never
committed). Without it, the congress half still works and insider trades are
skipped.

## First fill (one-time, slow)

```bash
python scrape_senate.py            # Senate since 2012, takes a few hours
python scrape_house.py --year 2025 # House, one year at a time (2012 → now if no --year)
python scrape_house.py --year 2026
python entities.py                 # pull tickers out of the asset descriptions
python scrape_insiders.py --quarter 2025q4   # one quarter of Form 4 from SEC's bulk files
python scrape_insiders.py --live --days 30   # the days since the last bulk file
```

Every script can be stopped with Ctrl-C and re-run; it picks up where it left off.

## Every day

```bash
python daily.py
```

That pulls new filings, matches tickers, grabs the last 7 days of Form 4s and
writes today's digest. To run it automatically on Windows:

```
schtasks /create /tn "Quantreyda Daily" /sc daily /st 09:00 ^
  /tr "cmd /c cd /d C:\path\to\Quantreyda && .venv\Scripts\python daily.py >> daily.log 2>&1" /f
```

On a Mac or Linux, a cron line does the same:
`0 9 * * * cd ~/Quantreyda && .venv/bin/python daily.py >> daily.log 2>&1`

## Did it ever work?

```bash
python backtest.py                 # filings since 2020, writes reports/backtest.md
```

This downloads daily prices from Yahoo Finance (free, cached in the database)
and prints a table like:

| Signal | Held 6m: avg vs SPY / typical / % that beat SPY (n) |
|---|---|
| Congress buys ≥ $15k | … |
| Insider cluster buys (3+ in 30d) | … |

The report explains each column. In short: a signal is only interesting if it
beats SPY on average *and* typically *and* more than half the time, at 6 and 12
months, over a decent number of examples.

## What the signals are

- **Congress buys**: a member, spouse or dependent disclosed buying a stock or
  ETF worth at least $15,001 (the smallest $1k–$15k bracket is skipped as noise).
  Dated by when it was **filed**, because that's when you could have known.
  Buys filed more than 90 days after the trade are left out of the digest.
- **Bought by 2+ members**: the same ticker bought by more than one member in
  the last 30 days.
- **Insider cluster buys**: 3 or more different insiders at one company buying
  on the open market within 30 days, at least $50k combined. Sales, stock
  grants and option exercises are ignored; they're mostly pay, not opinion.
- **Big single insider buys**: one insider buying $100k or more.

Exact definitions are in [`signals.py`](signals.py).

## Asking your own questions

The data lives in one file, `congress_trades.duckdb`. `q.py` runs SQL on it:

```bash
python q.py "SELECT tkr, count(*) n FROM trades WHERE tx_type='Purchase'
             AND filed_date > current_date - 90 GROUP BY 1 ORDER BY n DESC LIMIT 10"
```

Query the `trades` view (both chambers, real dates, recovered tickers) and the
`insider_trades` table.

## Known gaps

- About 1 in 8 House filings are scanned paper images. They're skipped and
  listed by `python scrape_house.py --ocr-queue`.
- Around 1 in 5 congress trades have no ticker (bonds, private funds, or a name
  we can't match). They're kept, but signals ignore them.
- Yahoo Finance has no prices for companies that later went bust or were
  bought out, which makes backtest numbers look a bit better than reality.
- Small banks converting to public companies show up as big insider clusters
  (their directors all buy at the IPO). Worth skipping when you see one.

## Tests

`python selftest.py` runs every module's offline checks (no internet, no real
data). GitHub runs it on every push.

## Credits

The congress, Form 4 and ticker-matching scrapers come from
[Quantgress](https://github.com/DMulajkar/Quantgress) by Dhruv Mulajkar, MIT
licensed (see [LICENSE](LICENSE)). This repo keeps those, drops the rest
(public API, sign-ups, extra datasets), and adds the digest and backtest.
