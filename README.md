# Quantreyda

A personal market-intel app: one place to stay current with the market each
morning. The home page is a short briefing that pulls together markets and the
economy, the news, research ideas, prediction-market odds, and what insiders,
Congress and proven funds are buying. It's meant for finding long-term ideas
(for example for a Roth IRA), not for day trading.

Sections: **Home** (the briefing), **Markets** (stocks, rates, commodities,
crypto, economic numbers), **News**, **Ideas**, **Small caps** and **Future**
themes, **Funds**, **Odds**, and the who's-buying pages (**Congress**,
**Insiders**, **All trades**, **Leaderboard**, **Did it work?**). A section
that's still being built shows a "coming soon" page until it lands.

Under the hood, the who's-buying side does six things:

1. **Collects public filings** every day: stock trades that members of Congress
   must disclose (STOCK Act), and SEC Form 4 filings that company executives and
   directors file when they buy or sell their own company's stock.
2. **Writes a short digest** (`reports/digest-YYYY-MM-DD.md`) listing the buys
   worth a look, with a plain-English note on what each section means.
3. **Checks each company's health** from its own SEC filings: is it growing,
   profitable, not over-borrowed, not printing new shares? Each stock gets a
   plain verdict: Solid, Mixed, Shaky or Unknown.
4. **Watches your stocks**: put tickers in `watchlist.txt` and get an email
   when a member of Congress or an insider buys or sells one. A **Sunday email**
   sums up the week.
5. **Adds context**: big new government contracts, what about 70 well-known
   funds (Berkshire, Pershing Square, Baupost and others in `funds.txt`) own and
   bought last quarter, each with its own page listing every holding,
   a ⚑ when a member of Congress trades in an industry their committee
   oversees, and a "where signals agree" list of stocks with several kinds of
   support at once.
6. **Checks whether any of it has ever worked** (`reports/backtest.md`): if you'd
   bought every signal and held it for 3, 6 or 12 months, would you have beaten
   just owning SPY (an S&P 500 index fund)? The "If you had copied them" section
   on the site answers it year by year for every Congress buy, politicians who
   already had a winning record (judged only on what was knowable then),
   insiders buying together, and the White House and cabinet.

Everything comes free from government websites. No paid data.

> **Honest expectations.** Most studies find that copying Congress *after* the
> disclosure delay adds little or nothing over an index fund. Clustered insider
> buying has a better record, but it's not a sure thing either. Use the digest
> as a research list, keep the backtest honest, and keep the core of a Roth in
> index funds unless the numbers clearly say otherwise.

## The website (runs itself on GitHub)

A GitHub Actions workflow ([`tracker.yml`](.github/workflows/tracker.yml))
runs everything on GitHub's computers each morning and publishes a dashboard
at **https://treyda6.github.io/Quantreyda/**: the home briefing plus every
section listed above.
Nothing needs to run on your computer.

The site also hosts **The Advisor Track**, a self-study investing course, at
**https://treyda6.github.io/Quantreyda/course/**. Its files live in
[`course/`](course/), and the workflow copies them into the site on every run.
On a phone, open that link and use "Add to Home Screen" to get an app icon; it
works offline after the first visit.

One-time setup in the repo's **Settings**:

1. **Pages** → Build and deployment → Source: **GitHub Actions**.
2. **Secrets and variables → Actions → New repository secret**:
   - `SEC_CONTACT_EMAIL`: required (sec.gov wants a contact email)
   - `WATCHLIST`: your tickers, e.g. `VTI MSFT COST`. Kept private, never
     shown on the site.
   - `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_TO`: optional, for alert and Sunday
     emails (see the Email section below for the Gmail app password).
3. **Actions** tab → *tracker* → **Run workflow**, tick **backfill**. The first
   fill takes a few hours and may need two or three runs (each picks up where
   the last stopped). After that it runs daily by itself, and on Sundays it
   also runs the backtest and sends the weekly email.

The repo and site are public; they only contain code and public filings.

## Running it on your own computer instead

### Setup (once)

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

### Email (optional, for alerts and the Sunday digest)

Add these to `.env`. With Gmail you need an **App password** (Google Account →
Security → 2-Step Verification → App passwords), not your normal password:

```
SMTP_USER=you@gmail.com
SMTP_PASSWORD=abcd efgh ijkl mnop
EMAIL_TO=you@gmail.com
```

Without email set up, alerts and the weekly digest are saved in `reports/` instead.

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

That pulls new filings, matches tickers, grabs the last 7 days of Form 4s,
health-checks every company in the digest, writes today's digest, and emails
any new activity on your watchlist. To run it automatically on Windows:

```
schtasks /create /tn "Quantreyda Daily" /sc daily /st 09:00 ^
  /tr "cmd /c cd /d C:\path\to\Quantreyda && .venv\Scripts\python daily.py >> daily.log 2>&1" /f
```

On a Mac or Linux, a cron line does the same:
`0 9 * * * cd ~/Quantreyda && .venv/bin/python daily.py >> daily.log 2>&1`

## Every Sunday

```bash
python weekly.py
```

Emails you the week's digest with a health table for your watchlist on top.
Schedule it like `daily.py`, but weekly:

```
schtasks /create /tn "Quantreyda Weekly" /sc weekly /d SUN /st 08:00 ^
  /tr "cmd /c cd /d C:\path\to\Quantreyda && .venv\Scripts\python weekly.py >> weekly.log 2>&1" /f
```

Mac/Linux: `0 8 * * 0 cd ~/Quantreyda && .venv/bin/python weekly.py >> weekly.log 2>&1`

## Your watchlist

Edit `watchlist.txt`, one ticker per line:

```
VTI    # core index fund
MSFT
COST
```

You'll get an email when a member of Congress buys or sells one (any size), an
insider buys one, or an insider sells $50k+ of one. Each filing is only sent once.

## Company health check

```bash
python health.py MSFT INTC SGI
```

| Verdict | Means |
|---|---|
| **Solid** | No red flags |
| **Mixed** | One red flag. Read why before buying |
| **Shaky** | Two or more |
| **Unknown** | No SEC numbers: ETFs, funds, foreign companies |

Red flags: sales shrank more than 5% last year; lost money last year; debt more
than 2x equity (or negative equity); share count up more than 5% (your slice
gets diluted); price/earnings over 40. These are blunt rules. Banks, utilities
and REITs normally carry lots of debt, and young growth companies often lose
money, so a red flag means "look closer", not "never".

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

## On your phone

The site works like an app on a phone: a tab bar along the bottom (Ideas,
Congress, Insiders, Funds, Search). The Congress and Insiders pages show the
most active people, a map of the largest recent trades (tiles sized by
dollars, green for buys and red for sales; switch between single trades and
totals per stock, over 7, 30 or 90 days), the latest trades, and a "copy
them" portfolio from the weekly backtest charted against the S&P 500. The
Funds page shows what the managers in `funds.txt` bought last quarter and
the latest 13F filings from SEC's daily index. Add it to your home screen
from the browser's share menu.

## Hourly news

The **News** page (`news.html`) shows headlines about the stocks the site
tracks, the market and the economy, refreshed every hour. A second, small
workflow ([`news.yml`](.github/workflows/news.yml)) runs `headlines.py` at
17 minutes past each hour: it reads the list of tracked stocks from the live
site, pulls free RSS feeds (Google News searches for those companies, CNBC,
MarketWatch, Yahoo Finance, the Federal Reserve, the Bureau of Labor
Statistics) plus brand-new SEC 8-K filings from tracked companies, tags each
headline with the tickers it names, and pushes `news.json` to the
`news-feed` branch. The pages load it in the browser, so news stays fresh
without rebuilding the whole site. Each full stock page also gets a
"Latest headlines" box with the ones that name it.

No setup beyond what the daily run already needs; it starts working once the
daily run has published the site once. Your private `WATCHLIST` is never
searched for, only stocks already public on the site. To refresh by hand:
**Actions** tab → *news* → **Run workflow**.

## What bettors expect

`python markets.py` pulls the most-traded open questions on Polymarket and
Kalshi about the Fed, inflation, tariffs, shutdowns, elections, laws and big
companies (free public data, no keys), plus recent $10k+ bets. They're on the
"Odds" page, and a stock page shows any open question that names its company.
Sports and crypto are left out.

## Markets and the economy

`economy.html` on the site (built by [`economy.py`](economy.py), refreshed in
the daily run) has two halves, and every card opens its own page under `e/`:

- **Prices** for oil, natural gas, gasoline, diesel, gold, silver, platinum,
  palladium, copper, corn, wheat, soybeans, coffee, cocoa, cattle, bitcoin,
  ether and solana (Yahoo Finance, the same free source as the stock charts).
  Each page has a 1-year and 5-year chart, the trend (above or below its 50-
  and 200-day averages), what moves the price, the stocks and funds tied to
  it with how closely they actually moved together over the last year, and
  which parts of the market (sector funds, bonds, the dollar) move with it.
- **The economy**: about 30 numbers from FRED, the St. Louis Fed's free
  database (no key): growth, jobs, inflation, interest rates and the yield
  curve, market stress, housing and household finances. Each one gets a
  rough Good / Watch / Weak reading in plain English, a long chart, and what
  it's related to. The top of the page sums it up and shows six "recession
  warning lights".

Stock pages also show a "Commodities it's tied to" panel when a stock is on
one of those lists (Exxon and oil, Newmont and gold, Coinbase and bitcoin...).

## Extra context (runs inside daily.py)

- `python contracts.py --days 30`: federal contracts of $50M+ that just started,
  matched to the winning company's ticker (from USAspending.gov).
- `python funds.py`: the latest two quarterly 13F filings for each fund in
  `funds.txt`, and what changed (new, added, sold). Edit `funds.txt` to follow
  other investors; the script prints each fund's official SEC name so you can
  check the number.
- `python committees.py`: loads current committee rosters and shows which
  recent filers it could match. Committee → industry links are in
  `COMMITTEE_SECTORS` in [`committees.py`](committees.py). Rosters are *current*
  members, so an old trade is judged by today's committee seats.

- `python whitehouse.py`: stock and bond trades by the President, Vice
  President and cabinet-level officials (OGE Form 278-T), read from
  ProPublica's free public archive of the PDFs. Who's followed is in
  `whitehouse_officials.txt`. Most of what they report is bonds; only stock
  buys feed the ideas.
- `python activists.py`: new 13D filings (an investor took 5%+ of a company
  and may push for changes), from EDGAR's daily index.

- `python companyintel.py TKR "Company name"`: lobbying spend (Senate LDA),
  federal contract dollars (USAspending) and Wikipedia page-view "buzz" for one
  company. The daily run refreshes a few dozen companies of each per day.
  Buybacks and dividends come with the SEC health check.

- `python bills.py`: bills that moved in Congress in the last 30 days (from
  Congress.gov; needs the free `CONGRESS_API_KEY` secret), matched to
  industries. A Congress buy gets ⚖ when the buyer sponsored a moving bill in
  that stock's industry.
- `python fec.py company "Lockheed Martin Corp"`: election money from the FEC.
  Stock pages show what the company's PAC gave to federal candidates this
  cycle (and the Republican / Democrat split); hover cards show how much each
  trading member of Congress has raised. Reuses `CONGRESS_API_KEY` (an
  api.data.gov key works for the FEC too), or set `FEC_API_KEY`.
- `python pictures.py TKR`: company logos and official portraits for the site
  (logos from free public logo sets, member portraits from the public-domain
  @unitedstates collection, officials from Wikipedia). Downloaded once into
  the database and served from the site itself. No picture means a lettered
  circle instead. The all-trades page also shows each stock's return since the
  trade, compared with the S&P 500.
- `python secextras.py short AAPL`: shares sold short and "days to cover"
  (FINRA, twice a month, no key). `python secextras.py risks AAPL`: compares
  the risk-factor section of the last two annual reports and lists the new
  warnings. CEO pay (from the proxy's pay table) and the last stock split come
  with the health check.
- Federal Reserve governors are followed through `whitehouse_officials.txt`
  too (same Form 278-T). Regional Fed bank presidents only publish annual
  reports, not trades, so they aren't included.

`backtest.py` also tests "Congress buys in their committee's industry" as its
own signal, so you can see whether that ⚑ means anything.

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
- The health check uses each company's last annual report, so it can be up to
  a year behind. P/E needs a current price from Yahoo; if
  that download fails, the check runs without it.
- Small banks converting to public companies show up as big insider clusters
  (their directors all buy at the IPO). Worth skipping when you see one.

## Tests

`python selftest.py` runs every module's offline checks (no internet, no real
data). GitHub runs it on every push.

## Credits

The White House 278-T reader adapts Quantgress's `scrape_trump.py`. The
congress, Form 4 and ticker-matching scrapers come from
[Quantgress](https://github.com/DMulajkar/Quantgress) by Dhruv Mulajkar, MIT
licensed (see [LICENSE](LICENSE)). This repo keeps those, drops the rest
(public API, sign-ups, extra datasets), and adds the digest and backtest.

## Use it as a phone app

The site can be added to your phone's home screen. It then opens full-screen
like an app, with its own icon, and shows the pages you've already viewed
even with no connection.

- **iPhone:** open the site in Safari, tap Share, then **Add to Home Screen**.
- **Android:** open it in Chrome, tap ⋮, then **Install app** (or **Add to Home screen**).

The files are in `app/` (manifest, icons, `sw.js`); `dashboard.py` copies them
to the site root on every build. The service worker is network-first, so
you always get today's data when you're online.
