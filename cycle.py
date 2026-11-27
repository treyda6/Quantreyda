"""Where are we in the cycle? Two gauges for the Markets & economy page, in
the spirit of Howard Marks ("we may never know where we're going, but we'd
better have a good idea where we are"):

  market cycle   is the market cheap and scared, or expensive and euphoric?
                 valuation (Shiller CAPE, the Buffett indicator, stock
                 earnings yield vs real bond yield), credit (corporate and
                 junk-bond spreads) and sentiment (fear gauge, consumer mood,
                 how stretched the S&P 500 is above its 200-day average).
                 Each reading is ranked against its own history.
  liquidity      is money flooding in or draining out (Dalio, Druckenmiller)?
                 the Fed's net liquidity (balance sheet minus the Treasury's
                 cash account and reverse repo), the Fed's balance sheet,
                 real M2, the dollar's trend, real rates, bank lending
                 standards and Chicago Fed financial conditions.
  debt           the slow long-term debt cycle: federal debt vs GDP,
                 interest as a share of federal revenue, household debt
                 payments vs income. Shown for context, not scored.

All free: FRED (St. Louis Fed, no key) and multpl.com's monthly Shiller CAPE
table. Stored in the same `econ` table as economy.py; the detail pages reuse
its charts.

    py cycle.py              # fetch the series into the DB
    py cycle.py --selftest
"""

import bisect
import datetime
import html
import math
import re
import sys
import time

import economy as ec

E = html.escape
MULTPL_CAPE = "https://www.multpl.com/shiller-pe/table/by-month"
CAPE_ID = "MULTPL_CAPE"  # our id for the CAPE series in the econ table

# Fetched with their full history, so readings can be ranked against decades.
# Some are economy.py's too; this refetch (after economy's) just adds history.
FRED_IDS = ["SP500", "BAA10Y", "BAMLH0A0HYM2", "VIXCLS", "UMCSENT",
            "WALCL", "WTREGEN", "RRPONTSYD", "M2REAL", "DTWEXBGS", "DFII10", "DRTSCILM", "NFCI",
            "NCBEILQ027S", "GDP", "GFDEGDQ188S", "A091RC1Q027SBEA", "FGRECPT", "TDSP"]
FRED_START = "1945-01-01"


# ------------------------------------------------------------------- fetch

def parse_multpl(text):
    """multpl.com's 'by month' table -> [(date, value)], oldest first."""
    out = {}
    for d, cell in re.findall(r"<td[^>]*>\s*([A-Z][a-z]{2} \d{1,2}, \d{4})\s*</td>\s*<td[^>]*>(.*?)</td>",
                              text or "", re.S):
        num = re.search(r"-?\d+(?:\.\d+)?", re.sub(r"<[^>]+>|&[#\w]+;", " ", cell))
        if not num:
            continue
        try:
            out[datetime.datetime.strptime(d, "%b %d, %Y").date()] = float(num.group())
        except ValueError:
            continue
    return sorted(out.items())


def refresh(con, get=None):
    """Fetch every series. Any one source failing leaves its old data in place."""
    ec.ensure_tables(con)
    if get is None:
        import requests
        s = requests.Session()
        s.headers["User-Agent"] = ec.UA
        get = lambda url, params=None: s.get(url, params=params, timeout=60)
    today = datetime.date.today()
    ok, bad = 0, []

    def store(fid, rows):
        con.execute("DELETE FROM econ WHERE id = ?", [fid])
        ec._bulk(con, "econ", [(fid, d, v) for d, v in rows], ("id", "d", "v"))
        con.execute("INSERT OR REPLACE INTO econ_fetches VALUES (?,?,?)", [fid, today, len(rows)])

    for fid in FRED_IDS:
        try:
            r = get(ec.FRED_CSV, params={"id": fid, "cosd": FRED_START})
            rows = ec.parse_fred(r.text) if r.status_code == 200 else []
            if rows:
                store(fid, rows)
                ok += 1
            else:
                bad.append(f"{fid} HTTP {r.status_code}")
        except Exception as e:  # one series down is fine
            bad.append(f"{fid} {type(e).__name__}")
        time.sleep(0.3)
    try:
        r = get(MULTPL_CAPE)
        rows = parse_multpl(r.text) if r.status_code == 200 else []
        if len(rows) > 100:  # a partial page would wipe the long history
            store(CAPE_ID, rows)
            ok += 1
        else:
            bad.append(f"CAPE ({len(rows)} rows, HTTP {r.status_code})")
    except Exception as e:
        bad.append(f"CAPE {type(e).__name__}")
    print(f"cycle: {ok}/{len(FRED_IDS) + 1} series" + (f" (failed: {', '.join(bad)})" if bad else ""))


# ------------------------------------------------------------------- series

def raw(con, fid):
    return ec._series(con, "SELECT d, v FROM econ WHERE id = ? ORDER BY d", [fid])


def at(series, default=None):
    """f(date) -> the last value on or before that date."""
    dates = [d for d, _ in series]

    def f(d):
        k = bisect.bisect_right(dates, d) - 1
        return series[k][1] if k >= 0 else default
    return f


def net_liquidity(walcl, tga, rrp):
    """Fed balance sheet minus the Treasury's cash at the Fed minus reverse repo, in $ trillions.
    WALCL and WTREGEN are $ millions (weekly), RRPONTSYD $ billions (daily; zero before it existed)."""
    t, r = at(tga), at(rrp, 0.0)
    out = []
    for d, w in walcl:
        g = t(d)
        if g is not None:
            out.append((d, (w - g - (r(d) or 0.0) * 1000) / 1e6))
    return out


def _qend(d):
    """FRED dates a quarter by its first day; Z.1 levels are as of the quarter's end."""
    m = d.month + 3
    return datetime.date(d.year + (m > 12), (m - 1) % 12 + 1, 1) - datetime.timedelta(days=1)


def buffett(equities, gdp, sp500=()):
    """US company stock market value (Fed Z.1, $ millions) as % of GDP ($ billions), per quarter,
    plus today's estimate: the last quarter moved by the S&P 500 since then."""
    g = at(gdp)
    out = []
    for d, v in equities:
        y = g(d)
        if y:
            out.append((_qend(d), v / 1000 / y * 100))
    if out and sp500 and sp500[-1][0] > out[-1][0]:
        then = at(sp500)(out[-1][0])
        if then:
            out.append((sp500[-1][0], out[-1][1] * sp500[-1][1] / then))
    return out


def ey_gap(cape, real10):
    """Earnings yield (100 / CAPE) minus the 10-year inflation-protected Treasury yield, in points."""
    r = at(real10)
    first = real10[0][0] if real10 else None
    return [(d, 100 / c - r(d)) for d, c in cape if c and first and d >= first and r(d) is not None]


def vs_avg(px, n=200):
    """% above (or below) the trailing n-observation average."""
    out, run = [], 0.0
    for i, (d, v) in enumerate(px):
        run += v
        if i >= n:
            run -= px[i - n][1]
        if i >= n - 1:
            out.append((d, (v / (run / n) - 1) * 100))
    return out


def ratio(a, b):
    """a / b * 100 on a's dates."""
    f = at(b)
    return [(d, v / f(d) * 100) for d, v in a if f(d)]


def change(series, days, pct=True):
    """Latest value vs the one `days` earlier (% or plain difference), or None."""
    if not series:
        return None
    then = at(series)(series[-1][0] - datetime.timedelta(days=days))
    if then is None:
        return None
    return (series[-1][1] / then - 1) * 100 if pct else series[-1][1] - then


def pctile(series, v):
    """Share of the history at or below v, 0-100."""
    vals = sorted(x for _, x in series)
    if not vals or v is None:
        return None
    return bisect.bisect_right(vals, v) / len(vals) * 100


def lin(x, at0, at100):
    """0 at `at0`, 100 at `at100`, straight line between, clamped."""
    if x is None:
        return None
    return max(0.0, min(100.0, (x - at0) / (at100 - at0) * 100))


# ---------------------------------------------------------------- components
# Heat is 0-100. Market cycle: 0 = cheap and fearful, 100 = expensive and euphoric.
# Liquidity: 0 = money draining out, 100 = money flooding in.

def _rank(invert=False):
    def h(s):
        p = pctile(s, s[-1][1]) if s else None
        return None if p is None else (100 - p if invert else p)
    return h


def _rank_words(s, noun="readings"):
    if not s:
        return ""
    p = pctile(s, s[-1][1])
    if p >= 50:
        return f"Higher than {min(99, round(p))}% of {noun} since {s[0][0].year}"
    return f"Lower than {min(99, round(100 - p))}% of {noun} since {s[0][0].year}"


def _chg_words(days, unit="%", money=False, label=None):
    def w(s):
        c = change(s, days, pct=not money)
        if c is None:
            return ""
        lab = label or (f"{days // 7} weeks" if days < 200 else "a year")
        if money:  # $ trillions -> billions
            return f"{'Up' if c >= 0 else 'Down'} ${abs(c) * 1000:,.0f}B over {lab}"
        return f"{'Up' if c >= 0 else 'Down'} {abs(c):.1f}{unit} over {lab}"
    return w


def _src(fid):
    return (f"FRED, series {fid}", f"https://fred.stlouisfed.org/series/{fid}")


def S(fid):
    return lambda con: raw(con, fid)


def _yoy(fid):
    return lambda con: ec.transform_series(raw(con, fid), "yoy")


COMPONENTS = [
    # ---- market cycle: valuation
    {"slug": "cape", "gauge": "cycle", "group": "Valuation", "name": "Shiller CAPE ratio", "unit": "x",
     "series": S(CAPE_ID), "heat": _rank(), "detail": lambda s: _rank_words(s, "months"),
     "words": ("Cheap by history", "Below average", "Around its long-run middle", "Expensive", "Very expensive by history"),
     "what": "The S&P 500's price divided by its average earnings over the last 10 years, after inflation. "
             "Averaging a decade of profits smooths out booms and busts.",
     "why": "Its long-run average is about 17. CAPE says almost nothing about next year, but a lot about the next "
            "ten: from readings above 30, the S&P 500's following 10-year returns after inflation have mostly "
            "been low single digits, and from readings under 15 they have mostly been strong.",
     "who": "Robert Shiller (Nobel prize, 2013) built it from Benjamin Graham's advice to average several years of "
            "earnings. Value investors from Templeton to Marks use it as a sanity check on how much optimism is priced in.",
     "how": "Monthly. Price is the month's average S&P 500 level (the current month uses the latest price); "
            "earnings are the trailing 10-year average of S&P 500 earnings, adjusted for inflation.",
     "src": [("multpl.com Shiller PE by month", MULTPL_CAPE), ("Robert Shiller's data", "http://www.econ.yale.edu/~shiller/data.htm")],
     "related": [("Buffett indicator", "buffett"), ("Earnings yield gap", "eygap"), ("S&P 500 fund", "SPY")]},
    {"slug": "buffett", "gauge": "cycle", "group": "Valuation", "name": "Buffett indicator (stocks vs GDP)", "unit": "%",
     "series": lambda con: buffett(raw(con, "NCBEILQ027S"), raw(con, "GDP"), raw(con, "SP500")),
     "heat": _rank(), "detail": lambda s: _rank_words(s, "quarters"),
     "words": ("Cheap by history", "Below average", "Around its long-run middle", "Expensive", "Very expensive by history"),
     "what": "The market value of all US companies' stock as a share of the size of the US economy (GDP).",
     "why": "Profits can't outgrow the economy forever, so when stock values race far ahead of GDP, future returns "
            "have tended to be lower. It was at records in 2000 and 2021, and at lows in 1982 and 2009.",
     "who": "Warren Buffett called it \"probably the best single measure of where valuations stand at any given "
            "moment\" (Fortune, 2001).",
     "how": "The Federal Reserve's quarterly count of what US nonfinancial companies' shares are worth (Financial "
            "Accounts, Z.1) divided by GDP. The Fed publishes it about 10 weeks after each quarter, so the latest "
            "point is an estimate: the last quarter's value moved by how much the S&P 500 has moved since.",
     "src": [_src("NCBEILQ027S"), _src("GDP"), _src("SP500")],
     "related": [("Shiller CAPE", "cape"), ("Economic growth (GDP)", "gdp"), ("S&P 500 fund", "SPY")]},
    {"slug": "eygap", "gauge": "cycle", "group": "Valuation", "name": "Stocks' edge over safe bonds", "unit": "pts",
     "series": lambda con: ey_gap(raw(con, CAPE_ID), raw(con, "DFII10")),
     "heat": _rank(invert=True), "detail": lambda s: _rank_words(s, "months"),
     "words": ("Stocks pay well over safe bonds", "Stocks pay a bit more than usual", "A normal edge",
               "A thin edge for taking stock risk", "Almost no reward for stock risk"),
     "what": "What stocks earn per dollar invested (1 ÷ CAPE) minus what a 10-year inflation-protected Treasury "
             "pays. Both are already after inflation, so it's the extra return you're offered for owning stocks.",
     "why": "When the gap is wide, stocks are cheap next to a risk-free bond. When it shrinks toward zero, a "
            "Treasury that can't lose to inflation pays about as much as the stock market's earnings, so investors "
            "aren't being paid much to take stock risk. That's the question Buffett asks when he compares stocks "
            "with bonds.",
     "who": "A version of the 'equity risk premium' that Buffett, Klarman and most valuation-minded investors watch.",
     "how": "Monthly: 100 ÷ CAPE (the cyclically adjusted earnings yield) minus the 10-year TIPS yield on that date. "
            "TIPS data starts in 2003.",
     "src": [("multpl.com Shiller PE", MULTPL_CAPE), _src("DFII10")],
     "related": [("Shiller CAPE", "cape"), ("Real interest rate", "realrate"), ("10-year Treasury", "t10")]},
    # ---- market cycle: credit
    {"slug": "baa", "gauge": "cycle", "group": "Credit", "name": "Corporate bond spread", "unit": "pts",
     "series": S("BAA10Y"), "heat": _rank(invert=True), "detail": lambda s: _rank_words(s, "days"),
     "words": ("Lenders are scared", "Lenders are cautious", "Normal lending", "Lenders are relaxed",
               "Lenders are complacent"),
     "what": "Extra yield that solid but not top-rated companies (Moody's Baa) pay over the 10-year Treasury.",
     "why": "Credit is where the cycle shows first. Tight spreads mean money is easy to get, which feels good but is "
            "when the worst loans get made. Wide spreads mean fear, and have often been good times for patient "
            "buyers of stocks and bonds alike.",
     "who": "Howard Marks built Oaktree on reading the credit cycle: \"the riskiest thing in the world is the belief "
            "that there's no risk.\"",
     "how": "Daily: Moody's seasoned Baa corporate bond yield minus the 10-year Treasury yield. History back to 1986.",
     "src": [_src("BAA10Y")],
     "related": [("Junk-bond risk premium", "hyspread"), ("Financial stress", "stress"), ("Corporate bond fund", "LQD")]},
    {"slug": "hy", "page": "hyspread", "gauge": "cycle", "group": "Credit", "name": "Junk-bond risk premium", "unit": "pts",
     "series": S("BAMLH0A0HYM2"), "heat": lambda s: lin(s[-1][1], 8, 2.5) if s else None,
     "detail": lambda s: "2.5 or under has been a record low; 8 or more is crisis level" if s else "",
     "words": ("Lenders are scared", "Lenders are cautious", "Normal lending", "Lenders are relaxed",
               "Lenders are complacent")},
    # ---- market cycle: sentiment
    {"slug": "vix", "page": "vix", "gauge": "cycle", "group": "Sentiment", "name": "Fear gauge (VIX)", "unit": "",
     "series": S("VIXCLS"), "heat": _rank(invert=True), "detail": lambda s: _rank_words(s, "days"),
     "words": ("Fear", "Nervous", "Normal", "Calm", "Very calm: complacent")},
    {"slug": "mood", "page": "sentiment", "gauge": "cycle", "group": "Sentiment", "name": "Consumer mood", "unit": "",
     "series": S("UMCSENT"), "heat": _rank(), "detail": lambda s: _rank_words(s, "months"),
     "words": ("Gloomy: often near bottoms", "Uneasy", "Normal", "Upbeat", "Euphoric")},
    {"slug": "sp200", "gauge": "cycle", "group": "Sentiment", "name": "S&P 500 vs its 200-day average", "unit": "%",
     "series": lambda con: vs_avg(raw(con, "SP500")), "heat": _rank(), "detail": lambda s: _rank_words(s, "days"),
     "words": ("Washed out", "Below trend", "Near trend", "Running above trend", "Stretched far above trend"),
     "what": "How far the S&P 500 is above or below its average of the last 200 trading days (about 10 months).",
     "why": "A market far above its trend has had a lot of good news priced in quickly; one far below has had a lot "
            "of fear priced in. Neither tells you the next move, but extremes in either direction tend to fade.",
     "who": "John Templeton: \"Bull markets are born on pessimism, grow on skepticism, mature on optimism, and die "
            "on euphoria.\"",
     "how": "Daily S&P 500 close divided by its trailing 200-day average, minus one. FRED keeps 10 years of the index.",
     "src": [_src("SP500")],
     "related": [("Fear gauge (VIX)", "vix"), ("S&P 500 fund", "SPY")]},
    # ---- liquidity
    {"slug": "netliq", "gauge": "liq", "group": "Liquidity", "name": "Fed net liquidity", "unit": "$T",
     "series": lambda con: net_liquidity(raw(con, "WALCL"), raw(con, "WTREGEN"), raw(con, "RRPONTSYD")),
     "heat": lambda s: lin(change(s, 91), -8, 8), "detail": _chg_words(91, money=True),
     "words": ("Draining fast", "Draining", "Steady", "Rising", "Pouring in"),
     "what": "The money the Fed has actually put into the financial system: its balance sheet, minus cash the "
             "Treasury keeps parked at the Fed and money funds park in its reverse-repo facility.",
     "why": "When this rises, banks and investors have more cash to put to work and stocks and crypto have tended to "
            "rise with it; when it drains, as during the Fed's bond selling in 2022, they have struggled. Its "
            "13-week change drives this reading.",
     "who": "Stanley Druckenmiller: \"Earnings don't move the overall market; it's the Federal Reserve Board... "
            "focus on the central banks, and focus on the movement of liquidity.\"",
     "how": "Weekly: Fed total assets (WALCL) minus the Treasury General Account (WTREGEN) minus overnight reverse "
            "repos (RRPONTSYD) on the same date.",
     "src": [_src("WALCL"), _src("WTREGEN"), _src("RRPONTSYD")],
     "related": [("Fed balance sheet", "fedbs"), ("Bitcoin", "bitcoin"), ("S&P 500 fund", "SPY")]},
    {"slug": "fedbs", "gauge": "liq", "group": "Liquidity", "name": "Fed balance sheet", "unit": "$T",
     "series": lambda con: [(d, v / 1e6) for d, v in raw(con, "WALCL")],
     "heat": lambda s: lin(change(s, 365), -10, 10), "detail": _chg_words(365),
     "words": ("Shrinking fast (QT)", "Shrinking", "Flat", "Growing", "Growing fast (QE)"),
     "what": "Everything the Federal Reserve owns, mostly Treasury bonds and mortgage bonds it bought with newly "
             "created money.",
     "why": "Buying bonds (QE) pushes money into markets and pulls rates down; letting them run off (QT) pulls money "
            "back out. It went from under $1 trillion in 2007 to about $9 trillion in 2022.",
     "who": "Ray Dalio calls central-bank money printing the key lever of the late debt cycle.",
     "how": "Weekly total assets of the Federal Reserve, every Wednesday. The reading uses the change over a year.",
     "src": [_src("WALCL")],
     "related": [("Fed net liquidity", "netliq"), ("The Fed's rate", "fedfunds"), ("Long-term bonds", "TLT")]},
    {"slug": "realm2", "gauge": "liq", "group": "Liquidity", "name": "Money supply after inflation (real M2)", "unit": "%",
     "series": _yoy("M2REAL"), "heat": lambda s: lin(s[-1][1], -4, 6) if s else None,
     "detail": lambda s: "Change versus a year earlier" if s else "",
     "words": ("Shrinking: tight", "Barely growing", "Normal growth", "Growing fast", "Booming"),
     "what": "Cash, checking, savings and money-market funds, after inflation, versus a year earlier.",
     "why": "When the money supply grows faster than prices, the extra cash tends to find its way into assets. Real "
            "M2 shrank in 2022-23 for the first time in decades, which squeezed stocks and bonds.",
     "who": "Milton Friedman's monetarists, and macro investors like Druckenmiller who track money growth.",
     "how": "M2 divided by consumer prices (FRED's M2REAL), shown as the change from a year earlier.",
     "src": [_src("M2REAL")],
     "related": [("Money supply (M2)", "m2"), ("Inflation (CPI)", "cpi"), ("Gold", "gold")]},
    {"slug": "dollar", "page": "dollar", "gauge": "liq", "group": "Liquidity", "name": "US dollar trend", "unit": "",
     "series": S("DTWEXBGS"), "heat": lambda s: lin(change(s, 365), 10, -10), "detail": _chg_words(365),
     "words": ("Surging: squeezes the world", "Strengthening", "Steady", "Weakening", "Falling fast: easy money abroad")},
    {"slug": "realrate", "gauge": "liq", "group": "Liquidity", "name": "Real interest rate (10-year)", "unit": "%",
     "series": S("DFII10"), "heat": lambda s: lin(s[-1][1], 3, -1) if s else None,
     "detail": _chg_words(182, unit=" pts", label="6 months"),
     "words": ("Very tight", "Tight", "Neutral", "Easy", "Very easy"),
     "what": "What a 10-year Treasury pays after inflation (the yield on inflation-protected bonds, TIPS).",
     "why": "The true price of money. High real rates make cash and bonds compete hard with stocks, gold and "
            "property; negative real rates (2020-21) push money into anything that might beat inflation.",
     "who": "Dalio's 'all weather' thinking starts here; gold investors watch it most closely.",
     "how": "Daily 10-year TIPS yield.",
     "src": [_src("DFII10")],
     "related": [("Gold", "gold"), ("10-year Treasury", "t10"), ("Inflation-protected bonds", "TIP")]},
    {"slug": "lending", "gauge": "liq", "group": "Liquidity", "name": "Banks' lending standards", "unit": "%",
     "series": S("DRTSCILM"), "heat": lambda s: lin(s[-1][1], 50, -20) if s else None,
     "detail": lambda s: "Net share of banks tightening (positive) or easing (negative)" if s else "",
     "words": ("Banks slamming the door", "Banks tightening", "Normal", "Banks easing", "Banks lending freely"),
     "what": "The Fed's quarterly survey of loan officers: the share of banks making business loans harder to get, "
             "minus the share making them easier.",
     "why": "Credit is the oxygen of the economy. Every recession since 1990 came with a big jump in banks "
            "tightening; easing usually comes before a recovery.",
     "who": "A staple of Dalio's debt-cycle work, and one of the Fed's own favourite early warnings.",
     "how": "Senior Loan Officer Opinion Survey: net percentage of domestic banks tightening standards for "
            "commercial and industrial loans to large and middle-market firms. Quarterly.",
     "src": [_src("DRTSCILM")],
     "related": [("Banks", "XLF"), ("Corporate bond spread", "baa")]},
    {"slug": "nfci", "gauge": "liq", "group": "Liquidity", "name": "Financial conditions (Chicago Fed)", "unit": "",
     "series": S("NFCI"), "heat": lambda s: lin(s[-1][1], 1, -.7) if s else None,
     "detail": lambda s: _rank_words(s, "weeks") if s else "",
     "words": ("Very tight", "Tighter than normal", "Normal", "Looser than normal", "Very loose"),
     "what": "A weekly blend of 105 measures of how easy it is to borrow and raise money: rates, spreads, stock "
             "swings, bank lending and more. Zero is average; below zero is looser than average.",
     "why": "The broadest single read on whether money is easy or hard to get. Loose conditions feed booms; a fast "
            "tightening has led every major stress episode.",
     "who": "The Fed itself watches it to judge whether its rate moves are biting.",
     "how": "Chicago Fed National Financial Conditions Index, weekly since 1971.",
     "src": [_src("NFCI")],
     "related": [("Financial stress", "stress"), ("Junk-bond risk premium", "hyspread")]},
    # ---- debt (context, not scored)
    {"slug": "fedebt", "gauge": "debt", "group": "Debt", "name": "Federal debt vs GDP", "unit": "%",
     "series": S("GFDEGDQ188S"), "tone": lambda v: "good" if v < 60 else "ok" if v < 100 else "bad",
     "detail": lambda s: _rank_words(s, "quarters"),
     "words": {"good": "Moderate", "ok": "High", "bad": "Very high: the late stage of a debt cycle"},
     "what": "Everything the US government owes as a share of a year's economic output.",
     "why": "Debt itself isn't a crisis; the cost of carrying it is. Above 100%, each rise in interest rates eats "
            "more of the budget, which tends to end in more money printing and a weaker currency.",
     "who": "Ray Dalio's long-term debt cycle: big debts end in either defaults or printing, and printing is the "
            "usual path for a country that borrows in its own currency.",
     "how": "Total public debt as a percent of GDP, quarterly.",
     "src": [_src("GFDEGDQ188S")],
     "related": [("Interest as a share of revenue", "interest"), ("Gold", "gold"), ("Long-term bonds", "TLT")]},
    {"slug": "interest", "gauge": "debt", "group": "Debt", "name": "Interest as a share of federal revenue", "unit": "%",
     "series": lambda con: ratio(raw(con, "A091RC1Q027SBEA"), raw(con, "FGRECPT")),
     "tone": lambda v: "good" if v < 10 else "ok" if v < 15 else "bad",
     "detail": lambda s: _rank_words(s, "quarters"),
     "words": {"good": "Manageable", "ok": "Getting heavy", "bad": "A heavy burden"},
     "what": "How much of every tax dollar the federal government spends on interest.",
     "why": "This is where a debt load starts to bite. It peaked near 19% in the early 1990s; rising rates pushed it "
            "back toward that level after 2023.",
     "who": "Dalio and Druckenmiller both point to it as the number that forces a government's hand.",
     "how": "Federal interest payments (A091RC1Q027SBEA) divided by federal current receipts (FGRECPT), quarterly, "
            "both at yearly rates.",
     "src": [_src("A091RC1Q027SBEA"), _src("FGRECPT")],
     "related": [("Federal debt vs GDP", "fedebt"), ("10-year Treasury", "t10")]},
    {"slug": "dsr", "gauge": "debt", "group": "Debt", "name": "Household debt payments vs income", "unit": "%",
     "series": S("TDSP"), "tone": lambda v: "good" if v < 10.5 else "ok" if v < 12.5 else "bad",
     "detail": lambda s: _rank_words(s, "quarters"),
     "words": {"good": "Households are not stretched", "ok": "Households are fairly stretched",
               "bad": "Households are stretched"},
     "what": "Mortgage and consumer-debt payments as a share of after-tax income.",
     "why": "Households carry the economy. This peaked near 16% before the 2008 crisis; low readings mean consumers "
            "can keep spending even if rates stay high.",
     "who": "Dalio's short-term debt cycle: spending rises with borrowing until payments outgrow income.",
     "how": "Federal Reserve household debt service ratio, quarterly.",
     "src": [_src("TDSP")],
     "related": [("Late credit card payments", "cardlate"), ("Savings rate", "savings")]},
]
C = {c["slug"]: c for c in COMPONENTS}
GROUPS = [("cycle", "Valuation"), ("cycle", "Credit"), ("cycle", "Sentiment"), ("liq", "Liquidity"), ("debt", "Debt")]

GAUGES = {  # name, low end, high end, labels by fifth, colors run low -> high
    "cycle": ("Market cycle", "Fear", "Greed",
              ("Fearful: bargains are likelier", "Cautious", "Middle of the road", "Optimistic: be selective",
               "Euphoric: be careful"), "good-bad"),
    "liq": ("Liquidity", "Draining", "Flooding",
            ("Money is draining fast", "Tightening", "Neutral", "Easing", "Money is flooding in"), "bad-good"),
}


def _fifth(h):
    return min(4, int(h // 20))


# ------------------------------------------------------------------ readings

def view(con, c):
    try:
        s = c["series"](con)
    except Exception:
        s = []
    v = s[-1][1] if s else None
    out = {"c": c, "s": s, "v": v, "asof": s[-1][0] if s else None, "heat": None, "tone": "info",
           "words": "No data yet", "detail": ""}
    if v is None:
        return out
    if "heat" in c:
        h = c["heat"](s)
        out["heat"] = h
        if h is not None:
            out["words"] = c["words"][_fifth(h)]
    else:
        out["tone"] = c["tone"](v)
        out["words"] = c["words"][out["tone"]]
    try:
        out["detail"] = c["detail"](s)
    except Exception:
        out["detail"] = ""
    return out


def views(con):
    return {c["slug"]: view(con, c) for c in COMPONENTS}


def gauge(vs, key):
    """(score 0-100, {group: score}) for one gauge: the average of its groups' averages."""
    groups = {}
    for v in vs.values():
        if v["c"]["gauge"] == key and v["heat"] is not None:
            groups.setdefault(v["c"]["group"], []).append(v["heat"])
    g = {k: sum(x) / len(x) for k, x in groups.items()}
    return (sum(g.values()) / len(g) if g else None), g


def phase(cyc, liq):
    """One plain sentence on what the two gauges say together."""
    if cyc is None or liq is None:
        return ""
    hot, cold, flush, dry = cyc >= 60, cyc < 40, liq >= 60, liq < 40  # the gauges' own bands
    if hot and dry:
        return ("Expensive while liquidity drains: historically the riskiest mix. Marks would say this is the time to "
                "turn more careful: favour quality, demand a margin of safety, keep some dry powder.")
    if hot and flush:
        return ("Expensive, but money is still flowing in. Rallies like this can run longer than seems sensible; "
                "stay invested in what you'd own anyway, and don't stretch on price.")
    if hot:
        return ("Prices are rich and liquidity is neutral. Expect lower returns from here than the last few years; "
                "be choosy about what you pay.")
    if cold and flush:
        return ("Cheap and fearful while money returns: historically the best time to buy. Templeton called it "
                "buying at the point of maximum pessimism.")
    if cold and dry:
        return ("Cheap, but money is still tight. Good prices, but the selling may not be done; buy in steps rather "
                "than all at once.")
    if cold:
        return "Prices are reasonable and fear is high. Patient buyers have usually been rewarded from here."
    if dry:
        return "Middle of the cycle with liquidity tightening. No extremes to act on; keep to the plan and watch credit."
    if flush:
        return "Middle of the cycle with liquidity easing: a tailwind. No extremes to act on; keep to the plan."
    return "No extremes either way. This is the time to stick to the plan, not to make big bets."


# ------------------------------------------------------------------ render

def fmt(v, unit):
    if v is None:
        return "—"
    if unit == "$T":
        return f"${v:,.2f}T"
    if unit == "x":
        return f"{v:.1f}x"
    if unit == "%":
        return f"{v:.1f}%"
    if unit == "pts":
        return f"{v:.2f}"
    return f"{v:,.2f}" if abs(v) < 10 else f"{v:,.1f}"


def _href(c, root=""):
    return f"{root}{ec.DIR}/{c.get('page', c['slug'])}.html"


def _related_href(ref, root, built):
    if ref in C:
        return _href(C[ref], root)
    return ec.link_for(ref, root, built)


def bar(h, colors, big=False):
    """A horizontal 0-100 bar with a marker."""
    if h is None:
        return ""
    return (f"<div class='gzb {colors}{' big' if big else ''}' role='img' aria-label='{h:.0f} out of 100'>"
            f"<i style='left:{h:.1f}%'></i></div>")


def heat_chip(v):
    c = v["c"]
    if v["heat"] is None:
        return ec.tone_chip(v["tone"]) if v["tone"] != "info" else ""
    f = _fifth(v["heat"])
    cls = (("good", "good", "", "ok", "bad") if c["gauge"] == "cycle" else ("bad", "ok", "", "good", "good"))[f]
    return f"<span class='tone {cls}'>{v['heat']:.0f}/100</span>"


def card(v, root=""):
    c = v["c"]
    s = v["s"]
    tail = [p for p in s if p[0] >= s[-1][0] - datetime.timedelta(days=365 * 5)] if s else []
    colors = GAUGES[c["gauge"]][4] if c["gauge"] in GAUGES else ""
    return (f"<a class='ec cyc' href='{_href(c, root)}'><div class='ec-h'><b>{E(c['name'])}</b>{heat_chip(v)}</div>"
            f"<div class='ec-v'><span class='big'>{fmt(v['v'], c['unit'])}</span>"
            f"<small class='hint'>{E(ec._asof(v['asof'], ec._is_monthly(s)))}</small></div>{ec.spark(tail)}"
            f"{bar(v['heat'], colors)}<div class='ec-r'><b>{E(v['words'])}</b>"
            + (f"<br>{E(v['detail'])}" if v["detail"] else "") + "</div></a>")


def _gauge_panel(vs, key):
    name, lo, hi, labels, colors = GAUGES[key]
    score, groups = gauge(vs, key)
    if score is None:
        return (f"<div class='gz'><div class='eyebrow'>{E(name)}</div><b class='gz-n'>—</b>"
                f"<p class='hint'>Arrives with the next daily update.</p></div>")
    rows = "".join(
        f"<a class='gzr' href='#cy-{E(g.lower())}'><span>{E(g)}</span>{bar(h, colors)}<b>{h:.0f}</b></a>"
        for g, h in groups.items() if len(groups) > 1)
    if key == "liq":  # one group: show its parts instead
        rows = "".join(
            f"<a class='gzr' href='{_href(v['c'])}'><span>{E(v['c']['name'])}</span>{bar(v['heat'], colors)}"
            f"<b>{v['heat']:.0f}</b></a>" for v in vs.values() if v["c"]["gauge"] == key and v["heat"] is not None)
    return (f"<div class='gz'><div class='eyebrow'>{E(name)}</div>"
            f"<div class='gz-t'><b class='gz-n'>{score:.0f}</b><span>/100 · {E(labels[_fifth(score)])}</span></div>"
            f"{bar(score, colors, big=True)}<div class='gz-l'><span>{E(lo)}</span><span>{E(hi)}</span></div>"
            f"<div class='gzrs'>{rows}</div></div>")


def section(con, root=""):
    """The 'Where are we in the cycle?' block on the Markets & economy page."""
    vs = views(con)
    cyc, _ = gauge(vs, "cycle")
    liq, _ = gauge(vs, "liq")
    out = ["<h2 id='cycle'>Where are we in the cycle?</h2>"
           "<p class='hint'>\"We may never know where we're going, but we'd better have a good idea where we are.\" "
           "(Howard Marks). Two gauges built from free Fed data: how cheap or expensive and how fearful or greedy "
           "the market is, ranked against decades of history, and whether money is flowing into or out of the "
           "financial system. Neither times the market; both say how much risk is priced in.</p>",
           f"<section class='panel cyc-top'><div class='gzs'>{_gauge_panel(vs, 'cycle')}{_gauge_panel(vs, 'liq')}</div>"
           + (f"<p class='cyc-ph'><b>Together:</b> {E(phase(cyc, liq))}</p>" if phase(cyc, liq) else "")
           + "</section>"]
    blurbs = {"Valuation": "How much you pay for a dollar of earnings or of the economy, against history.",
              "Credit": "How freely lenders hand out money. The cycle usually turns here first.",
              "Sentiment": "How scared or excited investors and consumers are. Extremes tend to reverse.",
              "Liquidity": "Whether the Fed, banks and the dollar are adding money to the system or taking it away.",
              "Debt": "The slow, long-term debt cycle. Context for the decade, not a timing signal; not scored."}
    for key, g in GROUPS:
        cards = "".join(card(v, root) for v in vs.values() if v["c"]["group"] == g)
        out.append(f"<h3 class='eg' id='cy-{g.lower()}'>{E(g)}</h3><p class='hint'>{E(blurbs[g])}</p>"
                   f"<div class='ecs'>{cards}</div>")
    return "".join(out)


def detail_page(con, c, built=frozenset(), root="../"):
    v = view(con, c)
    s = v["s"]
    colors = GAUGES[c["gauge"]][4] if c["gauge"] in GAUGES else ""
    f = (lambda x: fmt(x, c["unit"]))
    gname = GAUGES[c["gauge"]][0] if c["gauge"] in GAUGES else "Debt cycle"
    out = [f"<p class='crumb'><a href='{root}economy.html#cycle'>← Where are we in the cycle?</a> · {E(gname)}"
           + (f" · {E(c['group'])}" if c["group"] != gname else "") + "</p>",
           f"<h1>{E(c['name'])}</h1><div class='ec-v hdr'><span class='big'>{fmt(v['v'], c['unit'])}</span>"
           f"{heat_chip(v)}<small class='hint'>{E(ec._asof(v['asof'], ec._is_monthly(s)))}</small></div>",
           f"<p class='lead'><b>{E(v['words'])}.</b> {E(c['what'])}</p>"]
    if v["heat"] is not None:
        lo, hi = (GAUGES[c["gauge"]][1], GAUGES[c["gauge"]][2])
        out.append(f"<section class='panel'><div class='gz-t'><b class='gz-n'>{v['heat']:.0f}</b><span>/100 on the "
                   f"{E(gname.lower())} gauge</span></div>{bar(v['heat'], colors, big=True)}"
                   f"<div class='gz-l'><span>{E(lo)}</span><span>{E(hi)}</span></div>"
                   + (f"<p class='hint'>{E(v['detail'])}.</p>" if v["detail"] else "") + "</section>")
    elif v["detail"]:
        out.append(f"<p class='hint'>{E(v['detail'])}.</p>")
    windows = (("5Y", 1826), ("20Y", 7305), ("Max", 99999))
    out.append(f"<section class='panel'>{ec.ranged(s, f, color='var(--acc)', windows=windows)}</section>")
    out.append(f"<section class='panel'><h2>Why it matters</h2><p>{E(c['why'])}</p>"
               f"<h2>Who watches it</h2><p>{E(c['who'])}</p>"
               f"<h2>How it's worked out</h2><p>{E(c['how'])}</p></section>")
    if c.get("related"):
        out.append("<section class='panel'><h2>Related</h2><div class='chips'>" + "".join(
            f"<a class='chip' href='{E(_related_href(r, root, built))}'>{E(lab)}</a>" for lab, r in c["related"])
            + "</div></section>")
    out.append("<p class='hint'>Source: " + ", ".join(f"<a href='{E(u)}'>{E(l)}</a>" for l, u in c["src"])
               + f".</p><script>{ec.CHART_JS}</script>")
    return "".join(out)


def write_pages(con, out, page_fn, updated, built=frozenset()):
    """site/e/<slug>.html for each component that doesn't reuse an economy page."""
    import os
    os.makedirs(f"{out}/{ec.DIR}", exist_ok=True)
    n = 0
    for c in COMPONENTS:
        if "page" in c:
            continue
        try:
            body = detail_page(con, c, built)
        except Exception as e:
            print(f"cycle page {c['slug']} skipped ({type(e).__name__}: {e})")
            continue
        with open(f"{out}/{ec.DIR}/{c['slug']}.html", "w", encoding="utf-8") as f:
            f.write(page_fn(c["name"], "economy", body, updated, root="../"))
        n += 1
    return n


def home_line(con, root=""):
    """One line for the home page's Markets block."""
    vs = views(con)
    cyc, _ = gauge(vs, "cycle")
    liq, _ = gauge(vs, "liq")
    bits = []
    if cyc is not None:
        bits.append(f"market cycle <b>{cyc:.0f}/100</b> ({E(GAUGES['cycle'][3][_fifth(cyc)].split(':')[0].lower())})")
    if liq is not None:
        bits.append(f"liquidity <b>{liq:.0f}/100</b> ({E(GAUGES['liq'][3][_fifth(liq)].lower())})")
    if not bits:
        return ""
    return (f"<p class='cyc-home'><a href='{root}economy.html#cycle'>Where we are in the cycle</a>: "
            + " and ".join(bits) + ".</p>")


CSS = """
.cyc-top{padding:16px}.gzs{display:grid;grid-template-columns:1fr 1fr;gap:18px}
.gz .eyebrow{margin:0 0 4px}.gz-t{display:flex;align-items:baseline;gap:6px;flex-wrap:wrap}
.gz-n{font-size:38px;font-weight:800;letter-spacing:-1px;font-variant-numeric:tabular-nums}.gz-t span{color:var(--mut);font-weight:600}
.gzb{position:relative;height:8px;border-radius:999px;margin:8px 0 4px}.gzb.big{height:12px;margin:10px 0 6px}
.gzb.good-bad{background:linear-gradient(90deg,var(--up),color-mix(in srgb,var(--warn) 70%,var(--up)) 45%,var(--warn) 65%,var(--down))}
.gzb.bad-good{background:linear-gradient(90deg,var(--down),var(--warn) 35%,color-mix(in srgb,var(--warn) 70%,var(--up)) 55%,var(--up))}
.gzb i{position:absolute;top:50%;width:14px;height:14px;margin:-7px 0 0 -7px;border-radius:50%;background:var(--fg);
border:3px solid var(--card);box-shadow:0 0 0 1px var(--line)}.gzb.big i{width:20px;height:20px;margin:-10px 0 0 -10px}
.gz-l{display:flex;justify-content:space-between;font-size:11.5px;color:var(--mut);text-transform:uppercase;letter-spacing:.5px}
.gzrs{display:grid;gap:2px;margin-top:10px}.gzr{display:grid;grid-template-columns:minmax(0,1.3fr) 1fr 30px;gap:10px;align-items:center;
color:var(--fg);text-decoration:none;font-size:13px;padding:3px 0}.gzr:hover span{color:var(--acc)}
.gzr span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.gzr b{text-align:right;font-variant-numeric:tabular-nums}.gzr .gzb{margin:0}
.cyc-ph{margin:14px 0 0;padding:10px 12px;border-radius:12px;background:var(--card2);font-size:14px}
.ec.cyc .gzb{height:6px;margin:2px 0}.ec.cyc .gzb i{width:12px;height:12px;margin:-6px 0 0 -6px;border-width:2px}
.ec.cyc .ec-r b{color:var(--fg);font-weight:600}
.cyc-home{margin:8px 0 12px;font-size:13.5px}
@media(max-width:640px){.gzs{grid-template-columns:1fr;gap:22px}.gz-n{font-size:32px}.gzr span{white-space:normal}}
"""


# ---------------------------------------------------------------- selftest

def make_fixture(con, today=datetime.date(2026, 11, 20)):
    """Fake long histories for every series, for selftests and screenshots."""
    ec.ensure_tables(con)
    rows = []

    def add(fid, start, step, f):
        d, n = start, 0
        while d <= today:
            rows.append((fid, d, f(n, d)))
            n += 1
            d += datetime.timedelta(days=step)

    def months(fid, year, f):
        for y in range(year, today.year + 1):
            for m in range(1, 13):
                d = datetime.date(y, m, 1)
                if d <= today:
                    rows.append((fid, d, f(y + (m - 1) / 12, d)))

    def quarters(fid, year, f):
        for y in range(year, today.year + 1):
            for m in (1, 4, 7, 10):
                d = datetime.date(y, m, 1)
                if d <= today - datetime.timedelta(days=150):
                    rows.append((fid, d, f(y + (m - 1) / 12)))

    months(CAPE_ID, 1881, lambda t, d: 15 + 6 * math.sin(t / 7) + max(0, t - 1990) * .4)
    add("SP500", today - datetime.timedelta(days=3650), 1, lambda n, d: 2000 * 1.0004 ** n * (1 + .05 * math.sin(n / 60)))
    add("BAA10Y", datetime.date(1986, 1, 2), 1, lambda n, d: 2.4 + .8 * math.sin(n / 700) - (.6 if d.year > 2024 else 0))
    add("BAMLH0A0HYM2", today - datetime.timedelta(days=1095), 1, lambda n, d: 3.2 - n / 3000)
    add("VIXCLS", datetime.date(1990, 1, 2), 1, lambda n, d: 19 + 6 * math.sin(n / 90) if d.year < 2026 else 15.5)
    months("UMCSENT", 1952, lambda t, d: 85 + 10 * math.sin(t / 4) - (30 if t > 2025 else 0))
    add("WALCL", datetime.date(2002, 12, 18), 7, lambda n, d: 7.2e5 + n * 9000 if n < 1000 else 7.2e5 + 9e6 - (n - 1000) * 6000)
    add("WTREGEN", datetime.date(2002, 12, 18), 7, lambda n, d: 2e5 + 6e5 * (n > 900) + 2e5 * math.sin(n / 13))
    add("RRPONTSYD", datetime.date(2013, 9, 23), 1, lambda n, d: max(0, 2000 * math.sin(n / 1300)))
    months("M2REAL", 1959, lambda t, d: 1000 * 1.025 ** (t - 1959) * (1 + .01 * math.sin(t)))
    add("DTWEXBGS", datetime.date(2006, 1, 2), 1, lambda n, d: 100 + 20 * math.sin(n / 1500))
    add("DFII10", datetime.date(2003, 1, 2), 1, lambda n, d: 1.5 + 1.4 * math.sin(n / 1200))
    quarters("DRTSCILM", 1990, lambda t: 10 * math.sin(t))
    add("NFCI", datetime.date(1971, 1, 8), 7, lambda n, d: -.3 + .4 * math.sin(n / 200))
    quarters("NCBEILQ027S", 1945, lambda t: 2e5 * 1.075 ** (t - 1945))
    quarters("GDP", 1945, lambda t: 230 * 1.063 ** (t - 1945))
    quarters("GFDEGDQ188S", 1966, lambda t: 40 + max(0, t - 1980) * 1.6)
    quarters("A091RC1Q027SBEA", 1960, lambda t: 6 * 1.08 ** (t - 1960))
    quarters("FGRECPT", 1960, lambda t: 100 * 1.065 ** (t - 1960))
    quarters("TDSP", 1980, lambda t: 11 + 2 * math.sin(t / 6))
    ec._bulk(con, "econ", rows, ("id", "d", "v"))


def selftest():
    import duckdb
    page = ("<table id='datatable'><tr><th>Date</th><th>Value</th></tr>"
            "<tr class='odd'><td class='left'>Nov 1, 2026</td><td class='right'>&#x2002;\n38.12\n"
            "<abbr title='Value is estimated'>&#8224;</abbr></td></tr>"
            "<tr><td class='left'>Oct 1, 2026</td><td class='right'>&#x2002;37.50</td></tr>"
            "<tr><td>Jan 1, 1881</td><td>18.47</td></tr></table>")
    got = parse_multpl(page)
    assert got == [(datetime.date(1881, 1, 1), 18.47), (datetime.date(2026, 10, 1), 37.5),
                   (datetime.date(2026, 11, 1), 38.12)], got
    d = datetime.date
    nl = net_liquidity([(d(2026, 1, 7), 7_000_000), (d(2026, 1, 14), 7_100_000)],
                       [(d(2026, 1, 7), 800_000)], [(d(2026, 1, 13), 100)])
    assert [round(v, 2) for _, v in nl] == [6.2, 6.2], nl  # 7.1T - 0.8T - 0.1T
    bf = buffett([(d(2026, 4, 1), 60_000_000)], [(d(2026, 4, 1), 30_000)],
                 [(d(2026, 6, 30), 6000), (d(2026, 11, 20), 6600)])
    assert bf[0] == (d(2026, 6, 30), 200.0) and abs(bf[1][1] - 220) < 1e-9, bf
    assert ey_gap([(d(2026, 1, 1), 40)], [(d(2025, 12, 31), 2.0)]) == [(d(2026, 1, 1), .5)]
    va = vs_avg([(d(2026, 1, i), 100.0) for i in range(1, 4)] + [(d(2026, 1, 4), 130.0)], n=3)
    assert va[0][1] == 0 and abs(va[-1][1] - (130 / 110 - 1) * 100) < 1e-9
    assert pctile([(d(2026, 1, i), float(i)) for i in range(1, 11)], 9) == 90
    assert lin(0, -8, 8) == 50 and lin(20, -8, 8) == 100 and lin(5, 10, -10) == 25
    assert "riskiest" in phase(85, 20) and "riskiest" not in phase(85, 43) and "best time to buy" in phase(10, 80) and phase(None, 50) == ""
    con = duckdb.connect()
    empty = section(con)
    assert "Arrives with the next daily update" in empty and home_line(con) == ""
    make_fixture(con)
    vs = views(con)
    assert all(v["v"] is not None for v in vs.values()), [k for k, v in vs.items() if v["v"] is None]
    cyc, groups = gauge(vs, "cycle")
    assert 0 <= cyc <= 100 and set(groups) == {"Valuation", "Credit", "Sentiment"}, groups
    assert vs["cape"]["heat"] > 90 and vs["vix"]["heat"] > 50, (vs["cape"]["heat"], vs["vix"]["heat"])
    assert vs["fedbs"]["heat"] < 50 and vs["interest"]["tone"] in ("good", "ok", "bad")
    sec = section(con)
    assert "Where are we in the cycle?" in sec and "href='e/cape.html'" in sec and "href='e/vix.html'" in sec
    assert "Together:" in sec and "id='cy-liquidity'" in sec and "href='#cy-valuation'" in sec
    pg = detail_page(con, C["netliq"], {"SPY"})
    assert "Druckenmiller" in pg and "fred.stlouisfed.org/series/WTREGEN" in pg and "data-pts=" in pg
    assert "href='../s/SPY.html'" in pg and "href='../e/fedbs.html'" in pg and "economy.html#cycle" in pg
    assert "Higher than" in detail_page(con, C["buffett"]) and "economy.html#cycle" in home_line(con)
    print("cycle selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        import duckdb
        from schema import DB_PATH
        refresh(duckdb.connect(DB_PATH))
