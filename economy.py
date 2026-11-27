"""Markets and the economy: commodity, metal and crypto prices with their
trends and what moves with them, plus the economic numbers that say how the
US economy is doing, each explained in plain English.

  prices      oil, gas, gold, silver, copper, crops, bitcoin and more, from
              Yahoo Finance (same free source as the stock charts)
  related     stocks and funds tied to each one, and how closely their weekly
              moves actually tracked it over the last year (correlation)
  economy     ~30 numbers from FRED (the St. Louis Fed's free database, no
              key): jobs, inflation, rates, the yield curve, stress, housing,
              consumers -- each with a plain reading (good / watch / weak)

    py economy.py              # fetch prices and FRED numbers into the DB
    py economy.py --selftest
"""

import bisect
import csv
import datetime
import html
import io
import json
import math
import re
import sys
import time

import requests

import links
import prices

E = html.escape
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
UA = "QuantreydaBot/1.0 (https://github.com/treyda6/Quantreyda; personal research tool)"
DIR = "e"  # detail pages live in site/e/<slug>.html
PRICE_YEARS = 5
ECON_YEARS = 12

# ------------------------------------------------------------------ assets
# (yahoo symbol, slug, name, group, unit, scale, what it is, what moves it,
#  related [(ticker, why)])
# scale: Yahoo quotes crops and cattle in US cents; multiply to show dollars.
ASSETS = [
    ("CL=F", "oil", "Crude oil (WTI)", "Energy", "per barrel", 1,
     "The US benchmark for crude oil, priced for delivery in Cushing, Oklahoma.",
     "OPEC+ output, US shale drilling, global growth (China especially), wars and sanctions, and "
     "the dollar. Oil feeds into gasoline, diesel, jet fuel and plastics, so it shows up in inflation "
     "a month or two later.",
     [("XLE", "Energy sector fund"), ("USO", "Fund that holds oil futures"), ("XOM", "Exxon: biggest US oil producer"),
      ("CVX", "Chevron: oil major"), ("OXY", "Occidental: shale driller, very oil-sensitive"),
      ("COP", "ConocoPhillips: pure producer"), ("SLB", "SLB: drilling services, wins when drilling grows"),
      ("DAL", "Delta: jet fuel is a big cost, so it often moves against oil")]),
    ("BZ=F", "brent", "Crude oil (Brent)", "Energy", "per barrel", 1,
     "The world benchmark for crude, from the North Sea. Most oil outside the US is priced off it.",
     "The same forces as WTI, plus Middle East and Russia supply news. The Brent-WTI gap widens when "
     "US oil is plentiful but hard to export.",
     [("BNO", "Fund that holds Brent futures"), ("XOM", "Exxon"), ("CVX", "Chevron"),
      ("BP", "BP: European oil major"), ("SHEL", "Shell: European oil major")]),
    ("NG=F", "natgas", "Natural gas", "Energy", "per million BTU", 1,
     "US natural gas at Henry Hub, Louisiana. Heats homes and makes about 40% of US electricity.",
     "Weather above all (cold winters, hot summers), storage levels, LNG exports to Europe and Asia, "
     "and power demand from data centers.",
     [("UNG", "Fund that holds gas futures"), ("EQT", "EQT: largest US gas producer"),
      ("CTRA", "Coterra: gas and oil producer"), ("LNG", "Cheniere: ships US gas abroad as LNG"),
      ("XLU", "Utilities: gas is a cost, but they pass it through")]),
    ("RB=F", "gasoline", "Gasoline", "Energy", "per gallon", 1,
     "Wholesale gasoline in New York harbor, before taxes and station markup (add roughly $1 for the pump).",
     "Crude oil prices, refinery outages, summer driving season and hurricanes on the Gulf Coast.",
     [("UGA", "Fund that holds gasoline futures"), ("VLO", "Valero: largest US refiner"),
      ("MPC", "Marathon Petroleum: refiner"), ("PSX", "Phillips 66: refiner")]),
    ("HO=F", "diesel", "Diesel / heating oil", "Energy", "per gallon", 1,
     "Wholesale diesel and heating oil. Trucks, trains, farms and ships run on it.",
     "Crude oil, refinery capacity, winter heating demand and freight activity. A good real-time read "
     "on how busy the goods economy is.",
     [("VLO", "Valero: refiner"), ("MPC", "Marathon Petroleum: refiner"), ("PSX", "Phillips 66: refiner"),
      ("UPS", "UPS: fuel is a major cost")]),
    ("GC=F", "gold", "Gold", "Precious metals", "per troy ounce", 1,
     "The oldest store of value. Central banks have been buying record amounts since 2022.",
     "Real interest rates (gold pays nothing, so it shines when rates after inflation fall), the dollar, "
     "fear and war, and central-bank buying.",
     [("GLD", "Fund that holds physical gold"), ("IAU", "Cheaper gold fund"), ("GDX", "Gold miners fund: moves 2-3x gold"),
      ("NEM", "Newmont: largest gold miner"), ("AEM", "Agnico Eagle: gold miner"),
      ("WPM", "Wheaton: buys future metal output from miners")]),
    ("SI=F", "silver", "Silver", "Precious metals", "per troy ounce", 1,
     "Half money metal, half industrial metal (solar panels, electronics).",
     "Gold's direction, plus factory demand. Usually swings harder than gold both ways.",
     [("SLV", "Fund that holds physical silver"), ("SIL", "Silver miners fund"),
      ("PAAS", "Pan American Silver: miner"), ("AG", "First Majestic: miner"), ("WPM", "Wheaton: silver and gold streams")]),
    ("PL=F", "platinum", "Platinum", "Precious metals", "per troy ounce", 1,
     "A rare metal used in car exhaust catalysts, jewelry and hydrogen fuel cells. Mostly mined in South Africa.",
     "Diesel car sales, South African mine output and power cuts, and hydrogen hopes.",
     [("PPLT", "Fund that holds physical platinum"), ("SBSW", "Sibanye-Stillwater: platinum miner")]),
    ("PA=F", "palladium", "Palladium", "Precious metals", "per troy ounce", 1,
     "Used mainly in gasoline car catalytic converters. Russia mines about 40% of it.",
     "Gasoline car production, electric-car adoption (bad for it), and Russian supply.",
     [("PALL", "Fund that holds physical palladium"), ("SBSW", "Sibanye-Stillwater: miner")]),
    ("HG=F", "copper", "Copper", "Industrial metals", "per pound", 1,
     "\"Dr. Copper\": in wiring, buildings, cars, power grids and data centers, so its price is a read on "
     "world growth.",
     "China's building and factories, grid and electric-car buildout, AI data centers, and mine supply.",
     [("CPER", "Fund that holds copper futures"), ("FCX", "Freeport-McMoRan: largest listed copper miner"),
      ("SCCO", "Southern Copper: miner"), ("TECK", "Teck: copper and zinc miner"), ("COPX", "Copper miners fund")]),
    ("ZC=F", "corn", "Corn", "Farm goods", "per bushel", .01,
     "The biggest US crop: animal feed, ethanol in gasoline, and food.",
     "US weather and harvests, ethanol demand, exports to China and Mexico, and fertilizer costs.",
     [("CORN", "Fund that holds corn futures"), ("ADM", "ADM: buys, stores and processes grain"),
      ("DE", "Deere: farmers buy tractors when crops pay"), ("CTVA", "Corteva: seeds and crop chemicals"),
      ("BG", "Bunge: grain trader")]),
    ("ZW=F", "wheat", "Wheat", "Farm goods", "per bushel", .01,
     "Bread, pasta and flour worldwide. Russia and Ukraine together export about a quarter of it.",
     "Black Sea war and weather, harvests in the US, Canada, Australia and Argentina.",
     [("WEAT", "Fund that holds wheat futures"), ("ADM", "ADM: grain"), ("BG", "Bunge: grain trader"),
      ("GIS", "General Mills: flour is a cost")]),
    ("ZS=F", "soybeans", "Soybeans", "Farm goods", "per bushel", .01,
     "Animal feed and cooking oil, and a big US export to China.",
     "Trade deals and tariffs with China, Brazil's harvest, and biodiesel rules.",
     [("SOYB", "Fund that holds soybean futures"), ("ADM", "ADM: crushes soybeans"), ("BG", "Bunge"),
      ("DE", "Deere")]),
    ("KC=F", "coffee", "Coffee", "Farm goods", "per pound", .01,
     "Arabica coffee beans. Brazil and Vietnam grow most of the world's coffee.",
     "Frost and drought in Brazil, tariffs, and shipping costs.",
     [("SBUX", "Starbucks: beans are a cost"), ("KDP", "Keurig Dr Pepper: coffee pods")]),
    ("CC=F", "cocoa", "Cocoa", "Farm goods", "per metric ton", 1,
     "The bean behind chocolate. Ivory Coast and Ghana grow about 60% of it.",
     "West African weather and crop disease. It hit records in 2024 after failed harvests.",
     [("HSY", "Hershey: cocoa is its biggest cost"), ("MDLZ", "Mondelez: Cadbury, Oreo")]),
    ("LE=F", "cattle", "Live cattle", "Farm goods", "per pound", .01,
     "Cattle ready for slaughter: the starting price of beef.",
     "Herd sizes (the US herd is the smallest in 70 years), feed costs, drought and imports.",
     [("TSN", "Tyson Foods: beef packer"), ("MCD", "McDonald's: beef is a cost")]),
    ("BTC-USD", "bitcoin", "Bitcoin", "Crypto", "per coin", 1,
     "The first and biggest cryptocurrency, with a fixed supply of 21 million coins.",
     "Money flowing into bitcoin funds, interest rates and risk appetite (it often trades like a tech "
     "stock), rules in Washington, and the four-year 'halving' cycle.",
     [("IBIT", "BlackRock's bitcoin fund"), ("MSTR", "Strategy: holds hundreds of thousands of bitcoin"),
      ("COIN", "Coinbase: largest US crypto exchange"), ("MARA", "MARA: bitcoin miner"),
      ("RIOT", "Riot: bitcoin miner"), ("HOOD", "Robinhood: crypto trading is a big earner")]),
    ("ETH-USD", "ethereum", "Ethereum", "Crypto", "per coin", 1,
     "The second-biggest crypto: a network that runs apps, stablecoins and tokenized assets.",
     "Bitcoin's direction, stablecoin and tokenization growth, and fund flows.",
     [("ETHA", "BlackRock's ether fund"), ("COIN", "Coinbase"), ("HOOD", "Robinhood")]),
    ("SOL-USD", "solana", "Solana", "Crypto", "per coin", 1,
     "A fast, cheap crypto network popular for trading apps and meme coins.",
     "Crypto risk appetite, network activity, and fund launches.",
     [("COIN", "Coinbase"), ("HOOD", "Robinhood")]),
]
A = {a[1]: a for a in ASSETS}
GROUPS = ["Energy", "Precious metals", "Industrial metals", "Farm goods", "Crypto"]

# Every asset is also checked against these, to find which parts of the
# market move with it.
SECTORS = [("SPY", "S&P 500"), ("XLE", "Energy"), ("XLB", "Materials"), ("XLI", "Industrials"),
           ("XLK", "Technology"), ("XLF", "Financials"), ("XLV", "Health care"), ("XLY", "Shopping & cars"),
           ("XLP", "Groceries & staples"), ("XLU", "Utilities"), ("XLRE", "Real estate"),
           ("XLC", "Media & telecom"), ("TLT", "Long-term Treasury bonds"), ("UUP", "US dollar")]


# --------------------------------------------------------------- indicators
# (FRED id, slug, name, group, transform, unit, what it is, why it matters,
#  rule(v) -> (tone, reading), reference line, related [(label, slug-or-ticker)])
# transforms: level | yoy (% vs a year earlier) | diff3 (avg monthly change, 3 months)
# tones: good | ok | bad | info

def _t(good, ok, hi_good=True):
    """Threshold rule: higher is better (or worse if hi_good is False)."""
    def rule(v):
        if hi_good:
            return "good" if v >= good else "ok" if v >= ok else "bad"
        return "good" if v <= good else "ok" if v <= ok else "bad"
    return rule


def _rd(rule, words):
    """Pair a tone rule with words for each tone."""
    def f(v):
        t = rule(v)
        return t, words[t]
    return f


def _band(lo, hi, words):
    def f(v):
        return ("good" if lo <= v <= hi else "ok"), words["in" if lo <= v <= hi else ("lo" if v < lo else "hi")]
    return f


def _info(cuts):
    """No good/bad, just a description: cuts = [(upper bound, words), ...]."""
    def f(v):
        for ub, w in cuts:
            if v < ub:
                return "info", w
        return "info", cuts[-1][1]
    return f


def _infl(v):
    """Inflation vs the Fed's 2% goal: too low is a warning sign too."""
    if v < 0:
        return "bad", "Prices are falling: a sign of a weak economy"
    if v < 1.3:
        return "ok", "Below the 2% goal"
    if v <= 2.6:
        return "good", "Near the Fed's 2% goal"
    if v <= 3.5:
        return "ok", "A bit too high"
    return "bad", "Far above the 2% goal"


INDICATORS = [
    # Growth
    ("A191RL1Q225SBEA", "gdp", "Economic growth (GDP)", "Growth", "level", "%",
     "How fast the economy grew last quarter, after inflation, as a yearly pace.",
     "The broadest scorecard. About 2% is normal for the US. Two negative quarters in a row is the "
     "popular (not official) definition of a recession.",
     _rd(_t(2, 0), {"good": "Growing at a healthy pace", "ok": "Growing slowly", "bad": "Shrinking"}), 0,
     [("S&P 500", "SPY"), ("Copper", "copper"), ("Industrials", "XLI")]),
    ("INDPRO", "industry", "Factory output", "Growth", "yoy", "%",
     "Output of US factories, mines and utilities, compared with a year earlier.",
     "Factories are the most cyclical part of the economy, so they turn first.",
     _rd(_t(1, -1), {"good": "Factories are busier than a year ago", "ok": "Factories flat",
                    "bad": "Factories producing less than a year ago"}), 0,
     [("Industrials", "XLI"), ("Materials", "XLB"), ("Copper", "copper"), ("Diesel", "diesel")]),
    ("RSAFS", "retail", "Retail sales", "Growth", "yoy", "%",
     "What Americans spent at stores, restaurants and online, versus a year earlier (not adjusted for inflation).",
     "Consumers are about two-thirds of the economy. Compare it with inflation: sales growing slower "
     "than prices means people are buying less stuff.",
     _rd(_t(3, 0), {"good": "Shoppers are spending more", "ok": "Spending barely growing",
                   "bad": "Spending is falling"}), 0,
     [("Shopping & cars", "XLY"), ("Amazon", "AMZN"), ("Walmart", "WMT")]),
    ("UMCSENT", "sentiment", "Consumer mood", "Growth", "level", "",
     "University of Michigan survey of how people feel about their finances and the economy (1966 = 100).",
     "Mood can sour before spending does. Very low readings have often marked market bottoms, "
     "because the bad news was already priced in.",
     _rd(_t(80, 65), {"good": "People feel fairly good", "ok": "People are uneasy",
                     "bad": "People feel gloomy"}), None,
     [("Shopping & cars", "XLY"), ("Retail sales", "retail")]),
    # Jobs
    ("UNRATE", "unemployment", "Unemployment rate", "Jobs", "level", "%",
     "The share of people who want a job and can't find one.",
     "Below about 4.5% is a tight job market. It rises slowly at first and then fast once a recession starts.",
     _rd(_t(4.5, 5.5, hi_good=False), {"good": "Jobs are plentiful", "ok": "Job market softening",
                                       "bad": "Jobs are hard to find"}), None,
     [("Recession alarm (Sahm)", "sahm"), ("Jobless claims", "claims")]),
    ("PAYEMS", "payrolls", "Jobs added per month", "Jobs", "diff3", "k",
     "How many jobs employers added each month (3-month average, in thousands).",
     "The headline jobs report. The economy needs roughly 50-100k a month now just to keep up with "
     "population growth (less than before, with lower immigration).",
     _rd(_t(150, 50), {"good": "Strong hiring", "ok": "Slow hiring", "bad": "Hiring has stalled or reversed"}), 0,
     [("Unemployment", "unemployment"), ("Staffing firms", "RHI")]),
    ("ICSA", "claims", "Weekly jobless claims", "Jobs", "thousands", "k",
     "New unemployment applications each week, in thousands.",
     "The fastest jobs number: out every Thursday. A steady climb above ~300k has come before every recession.",
     _rd(_t(250, 300, hi_good=False), {"good": "Few layoffs", "ok": "Layoffs picking up", "bad": "Layoffs are high"}), None,
     [("Unemployment", "unemployment")]),
    ("JTSJOL", "openings", "Job openings", "Jobs", "level", "M",
     "Unfilled jobs employers are advertising, in millions.",
     "More openings than job seekers means workers have leverage and wages rise. Openings fall before layoffs start.",
     _rd(_t(7, 5.5), {"good": "Lots of open jobs", "ok": "Openings back to normal", "bad": "Few openings"}), None,
     [("Unemployment", "unemployment")]),
    ("SAHMREALTIME", "sahm", "Recession alarm (Sahm rule)", "Jobs", "level", "pts",
     "How far the 3-month average unemployment rate has risen above its low of the past year.",
     "When it hits 0.5, the US has been in a recession every time since 1970 (it gave a false alarm in "
     "2024, when new workers rather than layoffs pushed unemployment up).",
     _rd(_t(.3, .49, hi_good=False), {"good": "No recession signal", "ok": "Getting close to the alarm",
                                       "bad": "Recession alarm is ringing"}), .5,
     [("Unemployment", "unemployment"), ("Yield curve", "curve")]),
    # Inflation
    ("CPIAUCSL", "cpi", "Inflation (CPI)", "Inflation", "yoy", "%",
     "How much more a typical basket of things costs than a year ago.",
     "The Fed aims for about 2%. Higher inflation means rates stay high, which weighs on stocks and bonds.",
     _infl, 2,
     [("Gold", "gold"), ("Oil", "oil"), ("Inflation-protected bonds", "TIP")]),
    ("CPILFESL", "corecpi", "Core inflation (CPI)", "Inflation", "yoy", "%",
     "Inflation without food and energy, which jump around.",
     "Shows the underlying trend: what's stuck in rents, services and wages.",
     _infl, 2,
     [("Inflation (CPI)", "cpi")]),
    ("PCEPILFE", "corepce", "Core PCE inflation", "Inflation", "yoy", "%",
     "The Fed's favourite inflation measure (core prices of what people actually spend on).",
     "This is the number the 2% target is written against, so it drives rate decisions.",
     _rd(_t(2.3, 3, hi_good=False), {"good": "At the Fed's target", "ok": "Above target",
                                     "bad": "Well above target"}), 2,
     [("Fed's rate", "fedfunds")]),
    ("T5YIE", "inflexp", "Expected inflation", "Inflation", "level", "%",
     "The yearly inflation bond traders are betting on for the next 5 years.",
     "If expectations drift up, the Fed worries inflation is becoming permanent.",
     _band(1.8, 2.6, {"in": "Markets trust inflation will stay tame", "lo": "Markets expect very low inflation",
                      "hi": "Markets expect inflation to stay high"}), 2,
     [("Gold", "gold"), ("Inflation-protected bonds", "TIP")]),
    # Rates
    ("DFF", "fedfunds", "The Fed's rate", "Rates", "level", "%",
     "The overnight rate the Federal Reserve sets. Every other rate keys off it.",
     "Higher means borrowing is expensive and savings pay more; cuts make money cheaper and usually "
     "help stocks, gold and housing.",
     _info([(2, "Low: money is cheap"), (4, "In the middle"), (99, "High: borrowing is expensive")]), None,
     [("2-year Treasury", "t2"), ("Banks", "XLF"), ("Gold", "gold")]),
    ("DGS2", "t2", "2-year Treasury yield", "Rates", "level", "%",
     "What the US government pays to borrow for 2 years.",
     "Tracks where traders think the Fed's rate is heading over the next couple of years.",
     _info([(2, "Low"), (4, "Middle"), (99, "High")]), None,
     [("The Fed's rate", "fedfunds"), ("Yield curve", "curve")]),
    ("DGS10", "t10", "10-year Treasury yield", "Rates", "level", "%",
     "What the US government pays to borrow for 10 years. The most important rate in the world.",
     "Sets mortgage rates and the value of every stock: when it rises, future profits are worth less today.",
     _info([(2.5, "Low"), (4.5, "Middle"), (99, "High")]), None,
     [("Mortgage rate", "mortgage"), ("Long-term bonds", "TLT"), ("Real estate", "XLRE"), ("Utilities", "XLU")]),
    ("T10Y2Y", "curve", "Yield curve (10y minus 2y)", "Rates", "level", "pts",
     "The 10-year yield minus the 2-year yield.",
     "Normally positive (lending longer pays more). Below zero ('inverted') has come before every "
     "recession since 1980, usually by 6-24 months; the recession often starts after it turns positive again.",
     _rd(_t(.5, 0), {"good": "Normal shape", "ok": "Flat", "bad": "Inverted: a classic recession warning"}), 0,
     [("10-year yield", "t10"), ("2-year yield", "t2"), ("Banks", "XLF")]),
    ("T10Y3M", "curve3m", "Yield curve (10y minus 3m)", "Rates", "level", "pts",
     "The 10-year yield minus the 3-month bill. The version the New York Fed uses to estimate recession odds.",
     "Same story as the 10y-2y curve; economists trust this one slightly more.",
     _rd(_t(.5, 0), {"good": "Normal shape", "ok": "Flat", "bad": "Inverted: a classic recession warning"}), 0,
     [("Yield curve (10y-2y)", "curve")]),
    ("MORTGAGE30US", "mortgage", "30-year mortgage rate", "Rates", "level", "%",
     "The average rate on a new 30-year fixed home loan (Freddie Mac survey, weekly).",
     "Drives home sales and builders. Below about 6% tends to wake the housing market up.",
     _rd(_t(5.5, 7, hi_good=False), {"good": "Affordable", "ok": "Pricey", "bad": "Very expensive"}), None,
     [("Homebuilders", "ITB"), ("Housing starts", "housing"), ("10-year yield", "t10")]),
    # Stress
    ("VIXCLS", "vix", "Fear gauge (VIX)", "Stress", "level", "",
     "How big a swing in the S&P 500 options traders expect over the next month.",
     "Under 20 is calm. Spikes over 30 mean fear; historically those have often been good times to buy "
     "for patient investors.",
     _rd(_t(20, 30, hi_good=False), {"good": "Calm", "ok": "Nervous", "bad": "Fear"}), 20,
     [("S&P 500", "SPY")]),
    ("BAMLH0A0HYM2", "hyspread", "Junk-bond risk premium", "Stress", "level", "pts",
     "Extra yield that risky companies pay over the government to borrow.",
     "Lenders demand more when they smell defaults coming. A jump above ~5-6 points has lined up with "
     "recessions and market crashes.",
     _rd(_t(4, 6, hi_good=False), {"good": "Lenders are relaxed", "ok": "Lenders are cautious",
                                   "bad": "Lenders are scared"}), None,
     [("High-yield bond fund", "HYG"), ("S&P 500", "SPY")]),
    ("STLFSI4", "stress", "Financial stress", "Stress", "level", "",
     "The St. Louis Fed's weekly index of stress in markets and banks. Zero is normal.",
     "Combines 18 market signals into one. It spiked in 2008, 2020 and the 2023 bank failures.",
     _rd(_t(0, 1, hi_good=False), {"good": "Below-normal stress", "ok": "Some stress", "bad": "High stress"}), 0,
     [("Banks", "XLF"), ("Fear gauge", "vix")]),
    ("DTWEXBGS", "dollar", "US dollar", "Stress", "level", "",
     "The dollar's value against the currencies of major trading partners (2006 = 100).",
     "A strong dollar makes imports cheap but hurts US companies that sell abroad, and usually pushes "
     "gold, oil and other commodities down.",
     _info([(110, "Weak by recent standards"), (122, "Middle"), (999, "Strong")]), None,
     [("Dollar fund", "UUP"), ("Gold", "gold"), ("Oil", "oil")]),
    ("M2SL", "m2", "Money supply (M2)", "Stress", "yoy", "%",
     "Cash, checking, savings and money-market funds, versus a year earlier.",
     "Fast growth (like 2020-21) fuels inflation and asset prices; a shrinking money supply (2023) is rare "
     "and squeezes them.",
     _info([(0, "Shrinking: rare and tight"), (7, "Normal growth"), (99, "Growing fast")]), None,
     [("Bitcoin", "bitcoin"), ("Gold", "gold")]),
    # Housing and consumers
    ("HOUST", "housing", "Housing starts", "Housing & consumers", "level", "k",
     "New homes builders started, at a yearly pace (thousands).",
     "Housing leads the economy: each new home means jobs, materials and furniture.",
     _rd(_t(1400, 1100), {"good": "Builders are busy", "ok": "Building has slowed", "bad": "Building has stalled"}), None,
     [("Homebuilders", "ITB"), ("Lumber and materials", "XLB"), ("Mortgage rate", "mortgage")]),
    ("CSUSHPINSA", "homeprices", "Home prices", "Housing & consumers", "yoy", "%",
     "Case-Shiller national home price index, versus a year earlier.",
     "Rising prices help owners feel richer but price out buyers. Falling prices hurt banks and spending.",
     _info([(0, "Falling"), (6, "Rising at a normal pace"), (99, "Rising fast")]), None,
     [("Homebuilders", "ITB"), ("Real estate", "XLRE")]),
    ("DRCCLACBS", "cardlate", "Late credit card payments", "Housing & consumers", "level", "%",
     "Share of credit card balances at banks that are 30+ days late.",
     "An early sign that households are stretched.",
     _rd(_t(3, 4, hi_good=False), {"good": "Households are keeping up", "ok": "More people falling behind",
                                   "bad": "Many people falling behind"}), None,
     [("Card lenders", "COF"), ("Banks", "XLF")]),
    ("PSAVERT", "savings", "Savings rate", "Housing & consumers", "level", "%",
     "The share of after-tax income people save.",
     "A cushion for hard times. Very low means spending can't keep growing without income growth.",
     _rd(_t(6, 3.5), {"good": "Healthy cushion", "ok": "Thin cushion", "bad": "Very thin cushion"}), None,
     [("Retail sales", "retail")]),
]
IND = {i[1]: i for i in INDICATORS}
IND_GROUPS = ["Growth", "Jobs", "Inflation", "Rates", "Stress", "Housing & consumers"]
EXTRA_TICKERS = ["TIP", "ITB", "HYG", "RHI", "COF", "AMZN", "WMT"]


# ------------------------------------------------------------------- fetch

def ensure_tables(con):
    prices.ensure_tables(con)
    con.execute("CREATE TABLE IF NOT EXISTS econ (id VARCHAR, d DATE, v DOUBLE, PRIMARY KEY (id, d))")
    con.execute("CREATE TABLE IF NOT EXISTS econ_fetches (id VARCHAR PRIMARY KEY, fetched DATE, n INTEGER)")


def parse_fred(text):
    """FRED's graph CSV -> [(date, value)]; '.' and blanks are missing days."""
    out = []
    rd = csv.reader(io.StringIO(text))
    next(rd, None)
    for row in rd:
        if len(row) < 2:
            continue
        try:
            out.append((datetime.date.fromisoformat(row[0][:10]), float(row[1])))
        except ValueError:
            continue
    return out


def related_tickers():
    t = {x for a in ASSETS for x, _ in a[8]} | {s for s, _ in SECTORS} | set(EXTRA_TICKERS)
    for i in INDICATORS:
        t |= {r for _, r in i[10] if r not in A and r not in IND}
    return sorted(t)


def refresh(con, minutes=12):
    ensure_tables(con)
    today = datetime.date.today()
    syms = [a[0] for a in ASSETS]
    prices.fetch(con, syms, start=today - datetime.timedelta(days=365 * PRICE_YEARS + 10), minutes=minutes / 2)
    prices.fetch(con, related_tickers(), start=today - datetime.timedelta(days=740), minutes=minutes / 2)
    s = requests.Session()
    s.headers["User-Agent"] = UA
    start = today.replace(year=today.year - ECON_YEARS)
    ok, bad = 0, []
    for ind in INDICATORS:
        fid = ind[0]
        try:
            r = s.get(FRED_CSV, params={"id": fid, "cosd": start.isoformat()}, timeout=60)
            r.raise_for_status()
            rows = parse_fred(r.text)
            if rows:  # FRED revises old months, so replace the whole series
                con.execute("DELETE FROM econ WHERE id = ?", [fid])
                _bulk(con, "econ", [(fid, d, v) for d, v in rows], ("id", "d", "v"))
                ok += 1
            con.execute("INSERT OR REPLACE INTO econ_fetches VALUES (?,?,?)", [fid, today, len(rows)])
        except requests.RequestException as e:
            bad.append(f"{fid} {type(e).__name__}")
        time.sleep(0.3)
    print(f"economy: {ok}/{len(INDICATORS)} FRED series" + (f" (failed: {', '.join(bad)})" if bad else ""))


# ------------------------------------------------------------------- maths

def _series(con, sql, args):
    try:
        return [(d, float(v)) for d, v in con.execute(sql, args).fetchall() if v is not None]
    except Exception:
        return []


def price_series(con, sym, scale=1):
    return [(d, v * scale) for d, v in _series(con, "SELECT d, close FROM prices WHERE tkr = ? ORDER BY d", [sym])]


def econ_series(con, fid, transform):
    raw = _series(con, "SELECT d, v FROM econ WHERE id = ? ORDER BY d", [fid])
    return transform_series(raw, transform)


def _ago(px, days):
    """Value on or before `days` before the last point."""
    if not px:
        return None
    target = px[-1][0] - datetime.timedelta(days=days)
    k = bisect.bisect_right([d for d, _ in px], target) - 1
    return px[k][1] if k >= 0 else None


def transform_series(raw, transform):
    if transform == "yoy":
        out, dates = [], [d for d, _ in raw]
        for d, v in raw:
            k = bisect.bisect_right(dates, d - datetime.timedelta(days=360)) - 1
            # step back to the observation closest to exactly a year earlier
            if k >= 0 and (d - dates[k]).days <= 380 and raw[k][1]:
                out.append((d, (v / raw[k][1] - 1) * 100))
        return out
    if transform == "thousands":
        return [(d, v / 1000) for d, v in raw]
    if transform == "diff3":
        return [(raw[i][0], (raw[i][1] - raw[i - 3][1]) / 3) for i in range(3, len(raw))]
    return raw


def pct(a, b):
    return (a / b - 1) * 100 if a is not None and b else None


def changes(px):
    """{label: % change} over the usual windows."""
    if not px:
        return {}
    last, d = px[-1][1], px[-1][0]
    ytd = next((v for dd, v in reversed(px) if dd.year < d.year), None)
    return {"1D": pct(last, px[-2][1]) if len(px) > 1 else None, "1W": pct(last, _ago(px, 7)),
            "1M": pct(last, _ago(px, 30)), "YTD": pct(last, ytd), "1Y": pct(last, _ago(px, 365)),
            "5Y": pct(last, _ago(px, 365 * 5)) if (d - px[0][0]).days >= 365 * 5 - 10 else None}


def trend(px):
    """('up'|'down'|'mixed', words) from the 50- and 200-day averages."""
    if len(px) < 200:
        return "mixed", "Not enough history yet"
    last = px[-1][1]
    m50 = sum(v for _, v in px[-50:]) / 50
    m200 = sum(v for _, v in px[-200:]) / 200
    yr = [v for _, v in px[-252:]]
    hi, lo = max(yr), min(yr)
    pos = (last - lo) / (hi - lo) * 100 if hi > lo else 50
    where = ("at a 1-year high" if pos >= 97 else "near its 1-year high" if pos >= 85 else
             "at a 1-year low" if pos <= 3 else "near its 1-year low" if pos <= 15 else
             f"{pos:.0f}% of the way from its 1-year low to high")
    if last > m50 > m200:
        return "up", f"Uptrend: above its 50- and 200-day averages, {where}"
    if last < m50 < m200:
        return "down", f"Downtrend: below its 50- and 200-day averages, {where}"
    return "mixed", f"No clear trend, {where}"


def weekly_returns(px):
    """{(iso year, week): % return} using the last close of each week."""
    wk = {}
    for d, v in px:
        wk[d.isocalendar()[:2]] = v
    keys = sorted(wk)
    return {k: wk[k] / wk[p] - 1 for p, k in zip(keys, keys[1:]) if wk[p]}


def corr(a, b, min_n=26):
    keys = sorted(set(a) & set(b))
    if len(keys) < min_n:
        return None
    x, y = [a[k] for k in keys], [b[k] for k in keys]
    mx, my = sum(x) / len(x), sum(y) / len(y)
    sx = math.sqrt(sum((v - mx) ** 2 for v in x))
    sy = math.sqrt(sum((v - my) ** 2 for v in y))
    if not sx or not sy:
        return None
    return sum((p - mx) * (q - my) for p, q in zip(x, y)) / (sx * sy)


def corr_words(c):
    if c is None:
        return "not enough data"
    a = abs(c)
    s = "very closely" if a >= .7 else "closely" if a >= .5 else "somewhat" if a >= .3 else "barely"
    if a < .15:
        return "no real link"
    return f"moves {s} {'with' if c > 0 else 'against'} it"


def last_year(px, today=None):
    if not px:
        return []
    cut = (today or px[-1][0]) - datetime.timedelta(days=372)
    return [p for p in px if p[0] >= cut]


# ------------------------------------------------------------------- render

def fmt_price(v):
    if v is None:
        return "—"
    if v >= 1000:
        return f"${v:,.0f}"
    if v >= 10:
        return f"${v:,.2f}"
    return f"${v:,.3f}"


def fmt_ind(v, unit):
    if v is None:
        return "—"
    if unit == "k":
        return f"{v:,.0f}k"
    if unit == "M":
        return f"{v / 1000:,.1f}M" if v > 100 else f"{v:,.1f}M"
    if unit == "%":
        return f"{v:.1f}%"
    if unit == "pts":
        return f"{v:+.2f}"
    return f"{v:,.1f}"


def fmt_chg(c):
    if c is None:
        return "<span class='mut'>—</span>"
    cls = "up" if c > 0 else "dn" if c < 0 else ""
    return f"<span class='{cls}'>{c:+.1f}%</span>"


def spark(px, w=120, h=34):
    if len(px) < 2:
        return ""
    step = max(1, len(px) // 80)
    pts = px[::step] + ([px[-1]] if (len(px) - 1) % step else [])
    lo, hi = min(v for _, v in pts), max(v for _, v in pts)
    rng = hi - lo or 1
    d0, span = pts[0][0], max(1, (pts[-1][0] - pts[0][0]).days)
    p = " ".join(f"{(d - d0).days / span * w:.1f},{h - 2 - (v - lo) / rng * (h - 4):.1f}" for d, v in pts)
    col = "var(--up)" if pts[-1][1] >= pts[0][1] else "var(--down)"
    return (f"<svg class='espark' viewBox='0 0 {w} {h}' preserveAspectRatio='none' aria-hidden='true'>"
            f"<polyline points='{p}' fill='none' stroke='{col}' stroke-width='1.6' "
            f"vector-effect='non-scaling-stroke' stroke-linejoin='round'/></svg>")


_CID = [0]


def line_chart(px, fmt, ref=None, color=None, w=900, h=300):
    """SVG line chart with a hover readout (see CHART_JS). fmt formats a value."""
    if len(px) < 2:
        return "<p class='hint'>No history saved yet; it fills in with the next daily update.</p>"
    _CID[0] += 1
    gid = f"g{_CID[0]}"
    step = max(1, len(px) // 450)
    px = px[::step] + ([px[-1]] if (len(px) - 1) % step else [])
    L, R, T, B = 92, 14, 14, 26  # wide left margin: phones scale axis text up
    d0, d1 = px[0][0], px[-1][0]
    span = max(1, (d1 - d0).days)
    vals = [v for _, v in px] + ([ref] if ref is not None else [])
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * .08 or abs(hi) * .05 or 1
    lo, hi = lo - pad, hi + pad

    def X(d):
        return L + (d - d0).days / span * (w - L - R)

    def Y(v):
        return T + (hi - v) / (hi - lo) * (h - T - B)

    pts = " ".join(f"{X(d):.1f},{Y(v):.1f}" for d, v in px)
    col = color or ("var(--up)" if px[-1][1] >= px[0][1] else "var(--down)")
    area = f"M{X(d0):.1f},{h - B} L" + pts.replace(" ", " L") + f" L{X(d1):.1f},{h - B} Z"
    g = []
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        g.append(f"<line class='grid' x1='{L}' x2='{w - R}' y1='{Y(v):.1f}' y2='{Y(v):.1f}'/>"
                 f"<text x='{L - 6}' y='{Y(v) + 4:.1f}' text-anchor='end'>{E(fmt(v))}</text>")
    years = span > 900
    if years:
        ticks = [datetime.date(y, 1, 1) for y in range(d0.year + 1, d1.year + 1)]
        ticks = ticks[::max(1, len(ticks) // 7)]
        lab = "%Y"
    else:
        ticks = sorted({datetime.date(d.year, d.month, 1) for d, _ in px})[1:]
        ticks = ticks[::max(1, len(ticks) // 6)]
        lab = "%b '%y"
    for d in ticks:
        g.append(f"<text x='{X(d):.1f}' y='{h - 8}' text-anchor='middle'>{d.strftime(lab)}</text>")
    if ref is not None and lo < ref < hi:
        g.append(f"<line class='ref' x1='{L}' x2='{w - R}' y1='{Y(ref):.1f}' y2='{Y(ref):.1f}'/>")
    data = json.dumps([[d.isoformat(), fmt(v), round(X(d), 1), round(Y(v), 1)] for d, v in px],
                      separators=(",", ":"))
    return (f"<div class='hc'><svg class='chart' viewBox='0 0 {w} {h}' role='img' aria-label='Line chart' "
            f"data-pts='{E(data)}'>"
            f"<defs><linearGradient id='{gid}' x1='0' y1='0' x2='0' y2='1'>"
            f"<stop offset='0' stop-color='{col}' stop-opacity='.22'/>"
            f"<stop offset='1' stop-color='{col}' stop-opacity='0'/></linearGradient></defs>"
            f"{''.join(g)}<path d='{area}' fill='url(#{gid})'/>"
            f"<polyline points='{pts}' fill='none' stroke='{col}' stroke-width='2' stroke-linejoin='round'/>"
            f"<line class='xh' x1='0' x2='0' y1='{T}' y2='{h - B}' visibility='hidden'/>"
            f"<circle class='dot' r='5' fill='{col}' visibility='hidden'/></svg>"
            f"<div class='tip' hidden></div></div>")


def ranged(series, fmt, ref=None, color=None, windows=(("1Y", 365), ("5Y", 365 * 5), ("Max", 99999)), key="r"):
    """Charts for several time windows, switched with CSS-only tabs."""
    if not series:
        return line_chart(series, fmt, color=color)
    last = series[-1][0]
    have = []
    for lab, days in windows:
        sub = [p for p in series if p[0] >= last - datetime.timedelta(days=days)]
        if len(sub) >= 2 and (not have or len(sub) > have[-1][2]):
            have.append((lab, sub, len(sub)))
    if len(have) <= 1:
        return line_chart(series, fmt, ref, color)
    tabs, charts = [], []
    for i, (lab, sub, _) in enumerate(have):
        tabs.append(f"<input type='radio' name='{key}' id='{key}{i}'{' checked' if i == 0 else ''}>"
                    f"<label for='{key}{i}'>{lab}</label>")
        charts.append(f"<div class='rc'>{line_chart(sub, fmt, ref, color)}</div>")
    return f"<div class='ranged'>{''.join(tabs)}<div class='rcs'>{''.join(charts)}</div></div>"


def asset_view(con, a, built=frozenset()):
    sym, slug, name, group, unit, scale, what, moves, rel = a
    px = price_series(con, sym, scale)
    return {"a": a, "px": px, "last": px[-1][1] if px else None, "asof": px[-1][0] if px else None,
            "chg": changes(px), "trend": trend(px)}


def ind_view(con, ind):
    fid, slug, name, group, tf, unit, what, why, rule, ref, rel = ind
    s = econ_series(con, fid, tf)
    v = s[-1][1] if s else None
    tone, words = rule(v) if v is not None else ("info", "No data yet")
    prev = _ago(s, 365) if s else None
    return {"i": ind, "s": s, "v": v, "asof": s[-1][0] if s else None, "tone": tone, "words": words,
            "prev": prev}


def _asof(d, monthly):
    if not d:
        return ""
    return f"{d:%b %Y}" if monthly else f"{d:%b %d, %Y}"


def _is_monthly(s):
    return len(s) > 2 and (s[-1][0] - s[-2][0]).days > 20


TONE = {"good": ("Good", "✓"), "ok": ("Watch", "!"), "bad": ("Weak", "✕"), "info": ("", "•")}


def tone_chip(tone):
    lab, ic = TONE[tone]
    return f"<span class='tone {tone}'><i aria-hidden='true'>{ic}</i>{lab}</span>" if lab else ""


def link_for(ref, root, built):
    """(label, href) for a related item: an asset or indicator slug, or a ticker."""
    if ref in A or ref in IND:
        return f"{root}{DIR}/{ref}.html"
    return links.stock(ref, built, root)


def asset_card(v, root=""):
    a = v["a"]
    c = v["chg"]
    t = v["trend"][0]
    tchip = {"up": "<span class='tr up'>▲ Uptrend</span>", "down": "<span class='tr dn'>▼ Downtrend</span>",
             "mixed": "<span class='tr'>◆ Mixed</span>"}[t]
    return (f"<a class='ec' href='{root}{DIR}/{a[1]}.html'><div class='ec-h'><b>{E(a[2])}</b>{tchip}</div>"
            f"<div class='ec-v'><span class='big'>{fmt_price(v['last'])}</span>"
            f"<small class='hint'>{E(a[4])}</small></div>{spark(last_year(v['px']))}"
            f"<div class='ec-c'><span>Day {fmt_chg(c.get('1D'))}</span><span>Month {fmt_chg(c.get('1M'))}</span>"
            f"<span>Year {fmt_chg(c.get('1Y'))}</span></div></a>")


def ind_card(v, root=""):
    i = v["i"]
    s = v["s"]
    tail = [p for p in s if s and p[0] >= s[-1][0] - datetime.timedelta(days=365 * 5)]
    return (f"<a class='ec' href='{root}{DIR}/{i[1]}.html'><div class='ec-h'><b>{E(i[2])}</b>{tone_chip(v['tone'])}</div>"
            f"<div class='ec-v'><span class='big'>{fmt_ind(v['v'], i[5])}</span>"
            f"<small class='hint'>{E(_asof(v['asof'], _is_monthly(s)))}</small></div>{spark(tail)}"
            f"<div class='ec-r'>{E(v['words'])}</div></a>")


def _mood(inds):
    """(tone counts, recession lights [(label, lit, slug)], 'mixed'/...) for the summary."""
    got = [v for v in inds.values() if v["v"] is not None and v["tone"] != "info"]
    n = {t: sum(1 for v in got if v["tone"] == t) for t in ("good", "ok", "bad")}

    def val(slug):
        v = inds.get(slug)
        return v["v"] if v else None

    lights = []
    for slug, lit, label in (
            ("curve3m", lambda x: x < 0, "Yield curve inverted"),
            ("sahm", lambda x: x >= .5, "Sahm rule triggered"),
            ("claims", lambda x: x >= 300, "Jobless claims above 300k"),
            ("hyspread", lambda x: x >= 5, "Junk-bond premium above 5"),
            ("sentiment", lambda x: x < 60, "Consumer mood very low"),
            ("gdp", lambda x: x < 0, "Economy shrank last quarter")):
        x = val(slug)
        if x is not None:
            lights.append((label, lit(x), slug))
    on = sum(1 for _, l, _ in lights if l)
    if n["bad"] >= len(got) / 3 or on >= 3:
        mood = "under strain"
    elif n["good"] >= len(got) / 2 and on == 0:
        mood = "in decent shape"
    else:
        mood = "mixed"
    return n, lights, mood


def glance(inds):
    """The 'how's the economy?' summary at the top of the page."""
    got = [v for v in inds.values() if v["v"] is not None and v["tone"] != "info"]
    if not got:
        return ("<div class='hero'><div class='eyebrow'>The economy at a glance</div><h1>Markets &amp; economy</h1>"
                "<p>Prices for oil, gold, bitcoin and more, plus the numbers that show how the economy is "
                "doing. The first numbers arrive with the next daily update.</p></div>")
    n, lights, mood = _mood(inds)
    on = sum(1 for _, l, _ in lights if l)

    def val(slug):
        v = inds.get(slug)
        return v["v"] if v else None

    infl = val("corepce")
    fed = val("fedfunds")
    bits = []
    if val("unemployment") is not None:
        bits.append(f"unemployment is {val('unemployment'):.1f}%")
    if infl is not None:
        bits.append(f"core inflation is {infl:.1f}% against the Fed's 2% goal")
    if fed is not None:
        bits.append(f"the Fed's rate is {fed:.2f}%")
    summary = ("Right now " + ", ".join(bits[:-1]) + (" and " if len(bits) > 1 else "") + bits[-1] + ".") if bits else ""
    light_html = "".join(
        f"<a class='lt{' on' if l else ''}' href='{DIR}/{slug}.html'><i aria-hidden='true'></i>{E(lab)}"
        f"<small>{'Warning' if l else 'Not lit'}</small></a>" for lab, l, slug in lights)
    return (f"<div class='hero'><div class='eyebrow'>The economy at a glance</div>"
            f"<h1>The economy looks {mood}</h1><p>{E(summary)}</p>"
            f"<div class='stats'><div class='stat'><b>{n['good']}</b><span>numbers look good</span></div>"
            f"<div class='stat'><b>{n['ok']}</b><span>worth watching</span></div>"
            f"<div class='stat'><b>{n['bad']}</b><span>look weak</span></div></div></div>"
            f"<section class='panel'><h2>Recession warning lights</h2><p class='hint'>Signals that have "
            f"lit up before past recessions. One alone means little; several at once is worth taking "
            f"seriously. {on} of {len(lights)} lit.</p><div class='lts'>{light_html}</div></section>")


def page(con, built=frozenset()):
    assets = [asset_view(con, a) for a in ASSETS]
    inds = {i[1]: ind_view(con, i) for i in INDICATORS}
    body = [glance(inds)]
    have = [v for v in assets if v["px"]]
    if have:
        movers = sorted((v for v in have if v["chg"].get("1M") is not None),
                        key=lambda v: -abs(v["chg"]["1M"]))[:6]
        if movers:
            body.append("<h2>Biggest moves this month</h2><div class='movers'>" + "".join(
                f"<a href='{DIR}/{v['a'][1]}.html'><span>{E(v['a'][2])}</span>{fmt_chg(v['chg']['1M'])}</a>"
                for v in movers) + "</div>")
    try:
        import cycle  # here, not at the top: cycle.py builds on this module
        body.append(cycle.section(con))
    except Exception as e:  # the cycle gauges are one block; the page still renders
        print(f"cycle section skipped ({type(e).__name__}: {e})")
    body.append("<h2 id='prices'>Prices</h2><p class='hint'>Commodities, metals and crypto. Tap any one for its "
                "full chart, what moves it, and the stocks and funds that move with it. Prices are the "
                "latest daily close (crops and cattle converted from cents to dollars).</p>")
    for g in GROUPS:
        cards = [asset_card(v) for v in assets if v["a"][3] == g]
        body.append(f"<h3 class='eg'>{E(g)}</h3><div class='ecs'>{''.join(cards)}</div>")
    body.append("<h2 id='economy'>The economy</h2><p class='hint'>Official numbers from the St. Louis Fed's free "
                "FRED database. <b>Good</b> / <b>Watch</b> / <b>Weak</b> is a rough rule of thumb for each "
                "one, explained on its page. Monthly numbers come out a few weeks after the month ends.</p>")
    for g in IND_GROUPS:
        cards = [ind_card(inds[i[1]]) for i in INDICATORS if i[3] == g]
        body.append(f"<h3 class='eg'>{E(g)}</h3><div class='ecs'>{''.join(cards)}</div>")
    body.append(f"<script>{CHART_JS}</script>")
    return "".join(body)


def _chg_table(c):
    cols = [k for k in ("1D", "1W", "1M", "YTD", "1Y", "5Y") if k in c]
    return ("<div class='chg'>" + "".join(f"<div><span>{k}</span><b>{fmt_chg(c[k])}</b></div>" for k in cols)
            + "</div>")


def asset_page(con, a, built=frozenset(), root="../"):
    v = asset_view(con, a)
    sym, slug, name, group, unit, scale, what, moves, rel = a
    px = v["px"]
    t = v["trend"]
    out = [f"<p class='crumb'><a href='{root}economy.html#prices'>← Markets &amp; economy</a> · {E(group)}</p>",
           f"<h1>{E(name)}</h1><div class='ec-v hdr'><span class='big'>{fmt_price(v['last'])}</span>"
           f"<small class='hint'>{E(unit)}{' · close of ' + format(v['asof'], '%b %d, %Y') if v['asof'] else ''}</small></div>",
           f"<p class='lead'>{E(what)}</p>",
           f"<section class='panel'>{_chg_table(v['chg'])}"
           f"{ranged(px, fmt_price, key='r')}"
           f"<p class='trendline tr {'up' if t[0] == 'up' else 'dn' if t[0] == 'down' else ''}'>{E(t[1])}.</p></section>",
           f"<section class='panel'><h2>What moves the price</h2><p>{E(moves)}</p></section>"]
    wr = weekly_returns(last_year(px))
    rows = []
    for tkr, why in rel:
        c = corr(wr, weekly_returns(last_year(price_series(con, tkr))))
        rows.append((tkr, why, c))
    sec = []
    for tkr, lab in SECTORS:
        c = corr(wr, weekly_returns(last_year(price_series(con, tkr))))
        if c is not None:
            sec.append((tkr, lab, c))
    rel_html = "".join(
        f"<tr><td><a href='{E(link_for(tkr, root, built))}'><b>{E(tkr)}</b></a></td><td>{E(why)}</td>"
        f"<td class='num'>{'' if c is None else f'{c:+.2f}'}</td><td class='mut'>{E(corr_words(c))}</td></tr>"
        for tkr, why, c in rows)
    out.append("<section class='panel'><h2>Stocks and funds tied to it</h2><div class='wrap'><table class='rel'><thead><tr>"
               "<th>Ticker</th><th>Why it's related</th><th title='Correlation of weekly moves, last 12 months'>Link</th>"
               f"<th></th></tr></thead><tbody>{rel_html}</tbody></table></div>"
               "<p class='hint'>“Link” is the correlation of weekly price moves over the last year: +1 means they "
               "moved in lockstep, 0 means no relation, −1 means opposite. A fund that holds the commodity "
               "itself should be near +1; a company is usually lower, since its own news matters too.</p></section>")
    if sec:
        sec.sort(key=lambda r: -r[2])
        top = sec[:3]
        bot = [r for r in sec[::-1][:2] if r[2] < 0]
        chips = "".join(f"<a class='chip' href='{E(link_for(t_, root, built))}'>{E(l)} {c:+.2f}</a>" for t_, l, c in top)
        neg = "".join(f"<a class='chip' href='{E(link_for(t_, root, built))}'>{E(l)} {c:+.2f}</a>" for t_, l, c in bot)
        bars = "".join(
            f"<div class='cb'><span>{E(l)}</span><div class='cbt'><i class='{'p' if c >= 0 else 'n'}' "
            f"style='{'left:50%' if c >= 0 else f'left:{50 + c * 50:.1f}%'};width:{abs(c) * 50:.1f}%'></i></div>"
            f"<b>{c:+.2f}</b></div>" for _, l, c in sec)
        out.append(f"<section class='panel'><h2>Which parts of the market move with it</h2>"
                   f"<p>Moves most with: <span class='chips'>{chips}</span></p>"
                   + (f"<p>Moves against: <span class='chips'>{neg}</span></p>" if neg else "")
                   + f"<div class='cbs'>{bars}</div><p class='hint'>Weekly moves, last 12 months, versus the "
                     f"S&amp;P 500, each sector fund, long-term Treasury bonds and the dollar.</p></section>")
    links = [IND[s] for s in IND if any(r == slug for _, r in IND[s][10])]
    if links:
        out.append("<section class='panel'><h2>Economic numbers to watch with it</h2><div class='chips'>" + "".join(
            f"<a class='chip' href='{root}{DIR}/{i[1]}.html'>{E(i[2])}</a>" for i in links) + "</div></section>")
    out.append(f"<p class='hint'>Source: Yahoo Finance ({E(sym)}), daily closes. Futures prices are for the "
               f"nearest contract, so they can jump a little when one contract rolls into the next.</p>"
               f"<script>{CHART_JS}</script>")
    return "".join(out)


def ind_page(con, ind, built=frozenset(), root="../"):
    v = ind_view(con, ind)
    fid, slug, name, group, tf, unit, what, why, rule, ref, rel = ind
    s = v["s"]
    fmt = (lambda x: fmt_ind(x, unit))
    out = [f"<p class='crumb'><a href='{root}economy.html#economy'>← Markets &amp; economy</a> · {E(group)}</p>",
           f"<h1>{E(name)}</h1><div class='ec-v hdr'><span class='big'>{fmt_ind(v['v'], unit)}</span>"
           f"{tone_chip(v['tone'])}<small class='hint'>{E(_asof(v['asof'], _is_monthly(s)))}</small></div>",
           f"<p class='lead'><b>{E(v['words'])}.</b> {E(what)}</p>"]
    yr = ""
    if v["prev"] is not None and v["v"] is not None:
        yr = (f"<p class='hint'>A year earlier: <b>{fmt_ind(v['prev'], unit)}</b>"
              + (f" · Highest in this chart: <b>{fmt_ind(max(x for _, x in s), unit)}</b>, lowest: "
                 f"<b>{fmt_ind(min(x for _, x in s), unit)}</b>" if s else "") + "</p>")
    refnote = f"<p class='hint'>Dashed line: {fmt_ind(ref, unit)}.</p>" if ref is not None else ""
    out.append(f"<section class='panel'>{ranged(s, fmt, ref, color='var(--acc)', windows=(('2Y', 730), ('5Y', 1826), ('Max', 99999)))}"
               f"{yr}{refnote}</section>")
    out.append(f"<section class='panel'><h2>Why it matters</h2><p>{E(why)}</p></section>")
    if rel:
        out.append("<section class='panel'><h2>Related</h2><div class='chips'>" + "".join(
            f"<a class='chip' href='{E(link_for(r, root, built))}'>{E(lab)}</a>" for lab, r in rel)
            + "</div></section>")
    out.append(f"<p class='hint'>Source: <a href='https://fred.stlouisfed.org/series/{E(fid)}'>FRED, series "
               f"{E(fid)}</a>{' (shown as change versus a year earlier)' if tf == 'yoy' else ''}"
               f"{' (shown as the average monthly change over 3 months)' if tf == 'diff3' else ''}.</p>"
               f"<script>{CHART_JS}</script>")
    return "".join(out)


def home_teaser(con, today=None, root=""):
    """A few numbers for the home page's Markets tile."""
    inds = {i[1]: ind_view(con, i) for i in INDICATORS}
    mood = ""
    if any(v["v"] is not None for v in inds.values()):
        n, lights, m = _mood(inds)
        on = sum(1 for _, l, _ in lights if l)
        mood = (f"<p>The economy looks <b>{E(m)}</b>: {n['good']} numbers look good, {n['ok']} worth "
                f"watching, {n['bad']} weak. {on} of {len(lights)} recession warning lights lit.</p>")
    inds = {k: v for k, v in inds.items() if k in ("unemployment", "corepce", "fedfunds")}
    bits = []
    for slug in ("gold", "oil", "bitcoin"):
        v = asset_view(con, A[slug])
        if v["last"] is not None:
            bits.append(f"<a href='{root}{DIR}/{slug}.html'><span>{E(A[slug][2])}</span> <b>{fmt_price(v['last'])}</b> "
                        f"{fmt_chg(v['chg'].get('1M'))}</a>")
    for slug, v in inds.items():
        if v["v"] is not None:
            bits.append(f"<a href='{root}{DIR}/{slug}.html'><span>{E(v['i'][2])}</span> <b>{fmt_ind(v['v'], v['i'][5])}</b></a>")
    try:
        import cycle
        mood += cycle.home_line(con, root)
    except Exception as e:
        print(f"cycle home line skipped ({type(e).__name__}: {e})")
    if not bits:
        return mood
    return mood + "<div class='movers'>" + "".join(bits) + "</div>"


def stock_panel(con, tkr, root="../"):
    """On a stock page: commodities this stock is tied to (empty if none)."""
    hits = [a for a in ASSETS if any(t == tkr for t, _ in a[8])]
    if not hits:
        return ""
    items = []
    for a in hits[:4]:
        v = asset_view(con, a)
        why = next(w for t, w in a[8] if t == tkr)
        items.append(f"<a class='chip' href='{root}{DIR}/{a[1]}.html' title='{E(why)}'>{E(a[2])} "
                     f"{fmt_price(v['last'])} {fmt_chg(v['chg'].get('1M'))}</a>")
    return ("<section class='panel'><h2>Commodities it's tied to</h2><div class='chips'>" + "".join(items)
            + "</div><p class='hint'>Price and change over the past month. Tap for the full chart and how "
              "closely this stock has tracked it.</p></section>")


def write_pages(con, out, page_fn, updated, built=frozenset()):
    """Write site/e/<slug>.html for every asset and indicator."""
    import os
    os.makedirs(f"{out}/{DIR}", exist_ok=True)
    n = 0
    for a in ASSETS:
        try:
            body = asset_page(con, a, built)
        except Exception as e:
            print(f"economy page {a[1]} skipped ({type(e).__name__}: {e})")
            continue
        with open(f"{out}/{DIR}/{a[1]}.html", "w", encoding="utf-8") as f:
            f.write(page_fn(a[2], "economy", body, updated, root="../"))
        n += 1
    for i in INDICATORS:
        try:
            body = ind_page(con, i, built)
        except Exception as e:
            print(f"economy page {i[1]} skipped ({type(e).__name__}: {e})")
            continue
        with open(f"{out}/{DIR}/{i[1]}.html", "w", encoding="utf-8") as f:
            f.write(page_fn(i[2], "economy", body, updated, root="../"))
        n += 1
    return n


CHART_JS = """(function(){if(window.__ec)return;window.__ec=1;
function setup(svg){var p=JSON.parse(svg.dataset.pts),tip=svg.parentNode.querySelector('.tip'),
xh=svg.querySelector('.xh'),dot=svg.querySelector('.dot'),vb=svg.viewBox.baseVal;
function mv(e){var r=svg.getBoundingClientRect(),x=(e.clientX-r.left)/r.width*vb.width,lo=0,hi=p.length-1;
while(hi-lo>1){var m=(lo+hi)>>1;if(p[m][2]<x)lo=m;else hi=m}var q=Math.abs(p[lo][2]-x)<Math.abs(p[hi][2]-x)?p[lo]:p[hi];
xh.setAttribute('x1',q[2]);xh.setAttribute('x2',q[2]);dot.setAttribute('cx',q[2]);dot.setAttribute('cy',q[3]);
xh.setAttribute('visibility','visible');dot.setAttribute('visibility','visible');
var d=new Date(q[0]+'T12:00:00');tip.innerHTML='<b>'+q[1]+'</b><span>'+d.toLocaleDateString(undefined,{month:'short',day:'numeric',year:'numeric'})+'</span>';
tip.hidden=false;var px=q[2]/vb.width*r.width;tip.style.left=Math.min(Math.max(px,50),r.width-50)+'px'}
function out(){tip.hidden=true;xh.setAttribute('visibility','hidden');dot.setAttribute('visibility','hidden')}
svg.addEventListener('pointermove',mv);svg.addEventListener('pointerdown',mv);svg.addEventListener('pointerleave',out)}
document.querySelectorAll('svg[data-pts]').forEach(setup)})();"""

CSS = """
.ecs{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px}
.ec{display:flex;flex-direction:column;gap:6px;background:var(--card);border:1px solid var(--line);border-radius:16px;
padding:14px;color:var(--fg);text-decoration:none;box-shadow:var(--shadow)}
.ec:hover{border-color:color-mix(in srgb,var(--acc) 45%,var(--line))}
.ec-h{display:flex;justify-content:space-between;align-items:center;gap:8px}.ec-h b{font-size:14.5px}
.ec-v{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}.ec-v .big{font-size:24px;font-weight:800;
letter-spacing:-.5px;font-variant-numeric:tabular-nums}.ec-v.hdr .big{font-size:36px}
.espark{width:100%;height:34px;display:block}
.ec-c{display:flex;justify-content:space-between;gap:6px;font-size:12px;color:var(--mut)}
.ec-c span span,.chg b span{font-weight:700}.up{color:var(--up)}.dn{color:var(--down)}
.ec-r{font-size:13px;color:var(--mut)}
.tr{font-size:11.5px;font-weight:700;color:var(--mut);white-space:nowrap}.tr.up{color:var(--up)}.tr.dn{color:var(--down)}
.trendline{font-size:14px;margin:10px 0 0;white-space:normal}
.tone{display:inline-flex;align-items:center;gap:4px;font-size:11.5px;font-weight:700;border-radius:999px;
padding:2px 8px;background:var(--chip);white-space:nowrap}
.tone i{font-style:normal;font-size:10px}.tone.good{color:var(--up)}.tone.ok{color:var(--warn)}.tone.bad{color:var(--down)}
h3.eg{font-size:15px;color:var(--mut);margin:22px 0 10px;text-transform:uppercase;letter-spacing:.6px}
.movers{display:flex;gap:8px;flex-wrap:wrap}.movers a{display:flex;gap:8px;background:var(--card);border:1px solid var(--line);
border-radius:999px;padding:6px 12px;color:var(--fg);text-decoration:none;font-size:13.5px;font-weight:600}
.lts{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:8px}
.lt{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:10px 12px;border-radius:12px;background:var(--card2);
color:var(--fg);text-decoration:none;font-size:13.5px;font-weight:600}
.lt i{width:10px;height:10px;border-radius:50%;background:var(--line)}.lt small{margin-left:auto;color:var(--mut);font-weight:500}
.lt.on i{background:var(--down);box-shadow:0 0 10px var(--down)}.lt.on small{color:var(--down);font-weight:700}
.crumb{margin:0 0 4px;font-size:13px}.crumb a{text-decoration:none}.lead{font-size:16px;max-width:820px}
.chg{display:grid;grid-template-columns:repeat(6,1fr);gap:6px;margin-bottom:12px}
.chg div{background:var(--card2);border-radius:10px;padding:6px 8px}.chg b{font-size:15px}.chg span{display:block;color:var(--mut);font-size:11px}
.ranged>input{position:absolute;opacity:0;pointer-events:none}
.ranged>label{display:inline-block;font-size:12.5px;font-weight:700;padding:4px 12px;border-radius:999px;cursor:pointer;
color:var(--mut);margin:0 4px 8px 0;border:1px solid var(--line)}
.ranged .rc{display:none}
.ranged>input:nth-of-type(1):checked~.rcs .rc:nth-child(1),.ranged>input:nth-of-type(2):checked~.rcs .rc:nth-child(2),
.ranged>input:nth-of-type(3):checked~.rcs .rc:nth-child(3){display:block}
.ranged>input:checked+label{background:var(--chip);color:var(--fg)}
.ranged>input:focus-visible+label{outline:2px solid var(--acc)}
.hc{position:relative}.chart .ref{stroke:var(--mut);stroke-dasharray:4 4;stroke-width:1}
.chart .xh{stroke:var(--mut);stroke-width:1}.chart .dot{stroke:var(--card);stroke-width:2}
.hc .tip{position:absolute;top:4px;transform:translateX(-50%);background:var(--card2);border:1px solid var(--line);
border-radius:10px;padding:5px 9px;font-size:12px;pointer-events:none;display:flex;gap:8px;white-space:nowrap;box-shadow:var(--shadow)}
.hc .tip[hidden]{display:none}.hc .tip span{color:var(--mut)}
.cbs{display:grid;gap:5px;margin:10px 0}.cb{display:grid;grid-template-columns:170px 1fr 48px;gap:10px;align-items:center;font-size:13px}
.cb b{text-align:right;font-variant-numeric:tabular-nums}.cbt{position:relative;height:10px;background:var(--card2);border-radius:999px}
.cbt::after{content:'';position:absolute;left:50%;top:-2px;bottom:-2px;width:1px;background:var(--mut)}
.cbt i{position:absolute;top:0;bottom:0;border-radius:999px}.cbt i.p{background:var(--up)}.cbt i.n{background:var(--down)}
a.chip{color:var(--fg);text-decoration:none}
@media(max-width:640px){.rel td:nth-child(4),.rel th:nth-child(4){display:none}.chg{grid-template-columns:repeat(3,1fr)}.cb{grid-template-columns:110px 1fr 40px}
.ecs{grid-template-columns:1fr 1fr;gap:8px}.ec{padding:11px}.ec-v .big{font-size:19px}.ec-c{flex-direction:column;gap:0}
.ec-h{flex-direction:column;align-items:flex-start;gap:4px}}
"""


# ---------------------------------------------------------------- selftest

def make_fixture(con, today=datetime.date(2026, 11, 20)):
    """Fake prices and FRED numbers, for selftests and screenshots."""
    import random
    rnd = random.Random(7)
    ensure_tables(con)
    start = today - datetime.timedelta(days=365 * PRICE_YEARS)
    base = {"CL=F": 70, "BZ=F": 74, "NG=F": 3.2, "RB=F": 2.1, "HO=F": 2.4, "GC=F": 1900, "SI=F": 24,
            "PL=F": 950, "PA=F": 1200, "HG=F": 3.9, "ZC=F": 480, "ZW=F": 600, "ZS=F": 1300, "KC=F": 180,
            "CC=F": 2600, "LE=F": 150, "BTC-USD": 30000, "ETH-USD": 2000, "SOL-USD": 40}
    drift = {"GC=F": .0007, "SI=F": .0006, "BTC-USD": .0009, "CC=F": .0006, "LE=F": .0004, "NG=F": -.0002}
    rows, driver = [], {}
    d = start
    days = []
    while d <= today:
        if d.weekday() < 5:
            days.append(d)
        d += datetime.timedelta(days=1)
    for sym, b in base.items():
        v = b
        for d in days:
            shock = rnd.gauss(0, .015)
            driver.setdefault(d, {})[sym] = shock
            v *= 1 + drift.get(sym, .0001) + shock
            rows.append((sym, d, v))
    links = {a[0]: [t for t, _ in a[8]] for a in ASSETS}
    for t in related_tickers():
        src = next((s for s, ts in links.items() if t in ts), "CL=F")
        beta = -.6 if t in ("DAL", "UUP") else .9 if len(t) <= 4 and t.isupper() else .5
        v = 50
        for d in days[-560:]:
            v *= 1 + beta * driver[d][src] + rnd.gauss(0, .01)
            rows.append((t, d, v))
    _bulk(con, "prices", rows, ("tkr", "d", "close"))
    econ = []
    for fid, slug, *_ in INDICATORS:
        tf = IND[slug][4]
        monthly = fid not in ("DFF", "DGS2", "DGS10", "T10Y2Y", "T10Y3M", "VIXCLS", "BAMLH0A0HYM2", "T5YIE",
                              "DTWEXBGS")
        lvl = {"UNRATE": 4.4, "ICSA": 228000, "JTSJOL": 7200, "SAHMREALTIME": .27, "DFF": 3.88, "DGS2": 3.5,
               "DGS10": 4.1, "T10Y2Y": .55, "T10Y3M": .2, "MORTGAGE30US": 6.2, "VIXCLS": 17, "BAMLH0A0HYM2": 3.1,
               "STLFSI4": -.5, "DTWEXBGS": 120, "HOUST": 1310, "DRCCLACBS": 3.05, "PSAVERT": 4.6,
               "A191RL1Q225SBEA": 2.1, "UMCSENT": 52, "T5YIE": 2.35}.get(fid, 100)
        d = today.replace(year=today.year - ECON_YEARS)
        v = lvl
        n = 0
        while d <= today:
            n += 1
            if tf in ("yoy", "diff3"):
                v = 100 * (1.0025 ** n) * (1 + .02 * math.sin(n / 9)) if fid != "PAYEMS" else 140000 + 120 * n
            else:
                v = lvl * (1 + .15 * math.sin(n / (20 if monthly else 600))) if lvl else v
            econ.append((fid, d, v))
            d += datetime.timedelta(days=30 if monthly else 1)
        econ[-1] = (fid, econ[-1][1], lvl if tf == "level" else econ[-1][2])
    _bulk(con, "econ", econ, ("id", "d", "v"))


def _bulk(con, table, rows, cols):
    import pandas as pd
    df = pd.DataFrame(rows, columns=list(cols))  # noqa: F841 -- read by name below
    con.execute(f"INSERT OR REPLACE INTO {table} SELECT * FROM df")


def selftest():
    import duckdb
    csvtxt = "observation_date,UNRATE\n2026-08-01,4.3\n2026-09-01,.\n2026-10-01,4.4\n"
    assert parse_fred(csvtxt) == [(datetime.date(2026, 8, 1), 4.3), (datetime.date(2026, 10, 1), 4.4)]
    raw = [(datetime.date(2025, m, 1), 100 + m) for m in range(1, 13)] + [(datetime.date(2026, 1, 1), 110.1)]
    yoy = transform_series(raw, "yoy")
    assert len(yoy) == 1 and abs(yoy[0][1] - 9.0) < .01, yoy
    d3 = transform_series([(datetime.date(2026, m, 1), 1000 + 30 * m) for m in range(1, 6)], "diff3")
    assert d3[-1][1] == 30
    assert IND["curve"][8](-.3)[0] == "bad" and IND["unemployment"][8](4.1)[0] == "good"
    assert IND["cpi"][8](-1)[0] == "bad" and IND["cpi"][8](.5)[0] == "ok" and IND["cpi"][8](2.1)[0] == "good" and IND["inflexp"][8](2.2)[0] == "good"
    a = {(2026, w): (w % 5 - 2) / 100 for w in range(1, 40)}
    assert abs(corr(a, a) - 1) < 1e-9 and abs(corr(a, {k: -v for k, v in a.items()}) + 1) < 1e-9
    assert corr(a, {(2026, 1): .1}) is None and corr_words(.8) == "moves very closely with it"
    assert transform_series([(datetime.date(2026, 1, 1), 228000)], "thousands")[0][1] == 228 and fmt_ind(150.5, "k") == "150k" and fmt_ind(7200, "M") == "7.2M"
    assert fmt_price(4123.4) == "$4,123" and fmt_price(3.456) == "$3.456"
    con = duckdb.connect()
    make_fixture(con)
    pg = page(con, {"XOM"})
    assert "Recession warning lights" in pg and "Crude oil (WTI)" in pg and "Unemployment rate" in pg
    assert "href='e/oil.html'" in pg and "href='e/curve.html'" in pg and "<svg class='espark'" in pg
    ap = asset_page(con, A["oil"], {"XOM"})
    assert "href='../s/XOM.html'" in ap and "look.html?s=CVX" in ap
    assert "Which parts of the market move with it" in ap and "data-pts=" in ap
    assert re.search(r"<td class='num'>\+0\.\d\d</td>", ap), "related stock correlations shown"
    ip = ind_page(con, IND["unemployment"])
    assert "Why it matters" in ip and "fred.stlouisfed.org/series/UNRATE" in ip and "Jobs are plentiful" in ip
    assert "e/gold.html" in home_teaser(con) and "The economy looks" in home_teaser(con) and home_teaser(empty := duckdb.connect()) == ""
    assert "Commodities it's tied to" in stock_panel(con, "XOM") and stock_panel(con, "ZZZZ") == ""
    empty = duckdb.connect()
    assert "next daily update" in page(empty)  # renders before the first fetch
    assert "No history saved yet" in asset_page(empty, A["gold"])
    print("economy selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        import duckdb
        from schema import DB_PATH
        c = duckdb.connect(DB_PATH)
        refresh(c)
