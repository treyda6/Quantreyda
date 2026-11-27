"""What a stock is worth, and whether the business is any good.

Four scorecards for every US company that files numbers with the SEC, the
questions great long-term investors ask before price or news:

  value   What is it worth? Owner earnings (cash from operations minus
          capital spending minus stock pay, Buffett's yardstick), a simple
          two-stage discounted cash flow, the margin of safety against
          today's price, and the growth rate today's price already assumes.
  quality Is it a good business? Return on invested capital over up to ten
          years, free cash flow, debt next to cash flow, and whether the
          share count is shrinking or ballooning.
  flags   What would a short seller (Burry) look for? Profits running ahead
          of cash (accruals), customers paying slower, inventory piling up,
          heavy stock pay, goodwill from acquisitions, thin interest cover,
          and for tracked stocks: short interest, auditor changes,
          restatements, late filings and new risk warnings.
  owner   Do the people running it act like owners? Growth in free cash flow
          per share, returns on the money they reinvested, whether buybacks
          actually shrank the share count, acquisitions, and whether CEO pay
          moved with shareholders' returns.

Every number is shown with its assumptions, in plain English. They're rough
screens built from the SEC's tagged numbers, not a full analysis: banks,
insurers, REITs and young companies need different yardsticks, and the page
says so when it can tell.

Data: SEC companyfacts (lookup.py already reads it weekly; extras() adds the
handful of extra lines these scores need), the latest Yahoo close, FINRA
short interest (secextras.py) and the SEC filing index (refresh_filings()).

    py value.py --selftest
"""

import datetime
import json
import statistics
import sys
import time

import health
import fmt

DISCOUNT = 0.10        # the return you ask for: about what stocks have paid over the long run
TERMINAL = 0.025       # growth after year 10: roughly inflation plus a little
FAST, FADE = 5, 5      # years at the company's own growth rate, then years fading to TERMINAL
G_MIN, G_MAX = -0.03, 0.15
FILINGS_EVERY = 30     # days before re-reading a company's filing index
FILINGS_PER_RUN = 150
LOOKBACK_YEARS = 3     # auditor changes, restatements and late filings this recent count

# Extra annual lines on top of lookup.FLOW / lookup.INSTANT
FLOW_X = {
    "tax": ["IncomeTaxExpenseBenefit"],
    "pti": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
    "int": ["InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt"],
    "sbc": ["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"],
    "da": ["DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet",
           "DepreciationAndAmortization"],
    "acq": ["PaymentsToAcquireBusinessesNetOfCashAcquired"],
    "pay": health.CEO_PAY,        # proxy "pay versus performance" table
    "tsr": ["TotalShareholderRtnAmt"],  # value of $100 invested, same table
}
INST_X = {
    "gw": ["Goodwill"], "ar": ["AccountsReceivableNetCurrent"], "inv": ["InventoryNet"],
    "ltd": ["LongTermDebt"], "ltdn": ["LongTermDebtNoncurrent"], "ltdc": ["LongTermDebtCurrent"],
    "stb": ["ShortTermBorrowings", "CommercialPaper"], "dc": ["DebtCurrent"],
}


# ------------------------------------------------------------------ inputs

def extras(facts, fy):
    """{key: [value per fiscal year in fy]} for the lines lookup.financials
    doesn't keep. "td" is total debt (long-term incl. current part + short-term)."""
    import lookup  # lookup imports this module; import lazily
    out = {}
    for k, cs in FLOW_X.items():
        p = lookup._periods(facts, cs, "USD") or lookup._periods(facts, cs, "pure")
        ann = lookup._annual(p)
        out[k] = [lookup._near(ann, e) for e in fy]
    inst = {k: lookup._instants(facts, cs) for k, cs in INST_X.items()}
    for k in ("gw", "ar", "inv"):
        out[k] = [lookup._near(inst[k], e) for e in fy]
    td = []
    for e in fy:
        v = {k: lookup._near(inst[k], e) for k in ("ltd", "ltdn", "ltdc", "stb", "dc")}
        if v["ltd"] is not None:          # LongTermDebt already includes its current part
            td.append(v["ltd"] + (v["stb"] or 0))
        elif any(v[k] is not None for k in ("ltdn", "ltdc", "dc", "stb")):
            cur = v["dc"] if v["dc"] is not None else (v["ltdc"] or 0) + (v["stb"] or 0)
            td.append((v["ltdn"] or 0) + cur)
        else:
            td.append(None)
    out["td"] = td
    for k in [k for k, v in out.items() if all(x is None for x in v)]:
        del out[k]
    for k, v in out.items():
        out[k] = [None if x is None else (round(x, 4) if abs(x) < 1000 else round(x)) for x in v]
    return out


# ------------------------------------------------------------------ helpers

def _col(fin, k):
    n = len(fin.get("fy") or [])
    v = (fin.get("a") or {}).get(k) or (fin.get("x") or {}).get(k)
    return list(v) if v else [None] * n


def _last(arr):
    for i in range(len(arr) - 1, -1, -1):
        if arr[i] is not None:
            return arr[i]
    return None


def _cagr(a, b, years):
    if a is None or b is None or a <= 0 or b <= 0 or years < 1:
        return None
    return (b / a) ** (1 / years) - 1


def _span(arr, max_years=None):
    """(first index, last index) of non-empty values, the first at most max_years back."""
    idx = [i for i, v in enumerate(arr) if v is not None]
    if len(idx) < 2:
        return None
    hi = idx[-1]
    lo = [i for i in idx if max_years is None or hi - i <= max_years][0]
    return (lo, hi) if hi > lo else None


def _interp(x, pts):
    """Piecewise-linear score: pts [(x, score)] with x increasing."""
    if x <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return pts[-1][1]


def _pct(x, dp=0):
    return f"{x * 100:.{dp}f}%"


def _money(x):
    return fmt.money(x)


def _px(x):
    """A per-share amount: $47.51."""
    return f"-${-x:,.2f}" if x < 0 else f"${x:,.2f}"


def _years(fin, i, j):
    fy = fin["fy"]
    return round((datetime.date.fromisoformat(fy[j]) - datetime.date.fromisoformat(fy[i])).days / 365.25)


def dcf(oe, g, r=DISCOUNT, tg=TERMINAL, fast=FAST, fade=FADE):
    """Present value of owner earnings `oe` growing at g for `fast` years,
    fading straight down to tg over `fade` years, then tg forever."""
    pv, cash = 0.0, oe
    for yr in range(1, fast + fade + 1):
        rate = g if yr <= fast else g + (tg - g) * (yr - fast) / fade
        cash *= 1 + rate
        pv += cash / (1 + r) ** yr
    return pv + cash * (1 + tg) / (r - tg) / (1 + r) ** (fast + fade)


def implied_growth(price_cap, oe, net_cash, r=DISCOUNT):
    """The growth rate (first five years) that makes the DCF equal today's
    market value. None when no rate between -20% and +60% does."""
    f = lambda g: dcf(oe, g, r) + net_cash - price_cap
    lo, hi = -0.20, 0.60
    if oe <= 0 or f(lo) > 0 or f(hi) < 0:
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if f(mid) < 0 else (lo, mid)
    return (lo + hi) / 2


# ------------------------------------------------------------------ the scorecard

def score(fin, price=None, ext=None):
    """Pure: compact financials (lookup.financials, with "x" from extras())
    + last price + optional tracked-stock extras -> the four scorecards.

    Each card: {"s": 0-100 or None, "g": grade word, "l": [[+1/0/-1, text], ...]}
    plus the headline numbers the screen sorts on."""
    fy = fin.get("fy") or []
    n = len(fy)
    if n < 2:
        return None
    ext = ext or {}
    C = lambda k: _col(fin, k)
    rev, oi, ni, ocf, capex = C("rev"), C("oi"), C("ni"), C("ocf"), C("capex")
    eq, cash, assets, liab, ca = C("eq"), C("cash"), C("assets"), C("liab"), C("ca")
    sh, gp, div, buy = C("sh"), C("gp"), C("div"), C("buy")
    tax, pti, intr, sbc, acq = C("tax"), C("pti"), C("int"), C("sbc"), C("acq")
    gw, ar, inv, pay, tsr = C("gw"), C("ar"), C("inv"), C("pay"), C("tsr")
    td = [t if t is not None else d for t, d in zip(C("td"), C("debt"))]

    fcf = [o - (c or 0) if o is not None else None for o, c in zip(ocf, capex)]
    oe = [f - (s or 0) if f is not None else None for f, s in zip(fcf, sbc)]
    rate = [min(max(t / p, 0), 0.35) if t is not None and p and p > 0 else 0.21 for t, p in zip(tax, pti)]
    nopat = [o * (1 - r) if o is not None else None for o, r in zip(oi, rate)]
    ic = [e + (d or 0) - (c or 0) if e is not None else None for e, d, c in zip(eq, td, cash)]
    roic = []
    for i in range(n):
        base = [x for x in (ic[i - 1] if i else None, ic[i]) if x is not None]
        avg = sum(base) / len(base) if base else None
        roic.append(nopat[i] / avg if nopat[i] is not None and avg and avg > 0 else None)
    roe = [x / e if x is not None and e and e > 0 else None for x, e in zip(ni, eq)]

    shares = (_last((fin.get("q") or {}).get("sh") or []) or _last(sh) or fin.get("so"))
    cash0, debt0 = _last(cash) or 0, _last(td) or 0
    cap = price * shares if price and shares else None
    a_last, l_last = _last(assets), _last(liab)
    financial = (_last(ca) is None and a_last and l_last and l_last / a_last > 0.8)

    out = {"fy": fy[-1], "fin": bool(financial), "cap": cap}

    # ---------------------------------------------------------------- value
    v = {"l": []}
    L = v["l"]
    recent = [x for x in (ni if financial else oe)[-3:] if x is not None]
    base = sum(recent) / len(recent) if len(recent) >= 2 else None
    what = "net income" if financial else "owner earnings"
    g_bits = []
    s = _span([x / y if x is not None and y else None for x, y in zip(rev, sh)], 6)
    if s:
        g_bits.append(("sales per share", _cagr(rev[s[0]] / sh[s[0]], rev[s[1]] / sh[s[1]], _years(fin, *s))))
    flow = ni if financial else oe
    ps = [x / y if x is not None and y else None for x, y in zip(flow, sh)]
    s2 = _span(ps, 6)
    if s2:
        g_bits.append((f"{what} per share", _cagr(ps[s2[0]], ps[s2[1]], _years(fin, *s2))))
    known = [(k, g) for k, g in g_bits if g is not None]
    hist = min(g for _, g in known) if known else None
    g = min(max(hist if hist is not None else 0.03, G_MIN), G_MAX)
    net_cash = 0 if financial else cash0 - debt0
    if base is None or base <= 0 or not shares:
        v.update(s=None, g="No estimate")
        L.append([-1, f"It hasn't made steady positive {what} over the last three years, so there's "
                      "nothing to value from cash flows yet. Buffett and Munger would pass until it does."
                  if base is not None else "Not enough cash-flow history in its SEC filings to estimate a value."])
    else:
        ivs = (dcf(base, g) + net_cash) / shares
        lo = (base / DISCOUNT + net_cash) / shares
        hi = (dcf(base, min(g + 0.03, G_MAX + 0.03)) + net_cash) / shares
        v.update(iv=round(ivs, 2), lo=round(lo, 2), hi=round(hi, 2), gr=round(g, 4), oe=round(base))
        if financial:
            L.append([0, "Looks like a bank, insurer or other lender (no current assets, mostly liabilities). "
                         "Cash flow and debt mean something different there, so this uses net income and "
                         "ignores debt. Book value and return on equity matter more."])
        L.append([0, f"{what.capitalize()} averaged {_money(base)} a year over the last {len(recent)} years "
                     f"({_px(base / shares)} a share)"
                     + ("" if financial else ": cash from operations, minus capital spending, minus stock pay")
                     + "."])
        L.append([0, f"Assumes {_pct(g, 1)} growth a year for {FAST} years"
                     + (f" (the lower of {' and '.join(f'{k} {_pct(x, 1)}' for k, x in known)} a year lately)"
                        if known else " (a default; not enough history)")
                     + f", fading to {_pct(TERMINAL, 1)} by year {FAST + FADE}, valued at a {_pct(DISCOUNT)} yearly return"
                     + ("" if financial else f", plus {_money(cash0)} cash, minus {_money(debt0)} debt") + "."])
        L.append([0, f"Range: {_px(lo)} a share if it never grows, {_px(ivs)} on the assumptions above, "
                     f"{_px(hi)} if it grows 3 points faster."])
        if price:
            mos = 1 - price / ivs if ivs > 0 else None
            v["mos"] = round(mos, 4) if mos is not None else None
            if mos is None:
                v.update(s=0, g="No margin")
            else:
                v["s"] = round(_interp(mos, [(-1, 0), (-0.25, 20), (0, 45), (0.25, 75), (0.5, 100)]))
                v["g"] = ("Cheap" if mos >= 0.33 else "Below value" if mos >= 0.1 else "About fair"
                          if mos > -0.1 else "Above value" if mos > -0.5 else "Far above value")
                L.insert(0, [1 if mos >= 0.25 else -1 if mos < -0.1 else 0,
                             f"Estimated value {_px(ivs)} a share vs {_px(price)} today: "
                             + (f"a {_pct(mos)} margin of safety." if mos >= 0
                                else f"the price is {_pct(price / ivs - 1)} above the estimate.")])
            ig = implied_growth(cap, base, net_cash) if cap else None
            if ig is not None:
                v["ig"] = round(ig, 4)
                L.append([1 if hist is not None and ig < hist - 0.02 else -1 if hist is not None and ig > hist + 0.05 else 0,
                          f"Today's price already assumes about {_pct(ig, 1)} growth a year for {FAST} years"
                          + (f"; it managed {_pct(hist, 1)} lately." if hist is not None else ".")])
            if cap and not financial:
                ev = cap - net_cash
                if ev > 0:
                    v["ey"] = round(base / ev, 4)
                    L.append([1 if base / ev >= 0.07 else -1 if base / ev < 0.03 else 0,
                              f"Owner-earnings yield {_pct(base / ev, 1)}: what the whole business (stock plus "
                              f"debt, minus cash) earns for its price. A 10-year Treasury pays about 4%."])
        else:
            v.update(s=None, g="No price")
    out["v"] = v

    # ---------------------------------------------------------------- quality
    q = {"l": []}
    L, parts = q["l"], []
    r_known = [x for x in roic if x is not None]
    if financial:
        e_known = [x for x in roe if x is not None]
        if e_known:
            med = statistics.median(e_known)
            q["roe"] = round(med, 4)
            parts.append(_interp(med, [(0, 0), (0.06, 6), (0.10, 14), (0.15, 25)]))
            L.append([1 if med >= 0.12 else -1 if med < 0.08 else 0,
                      f"Return on equity: median {_pct(med)} over {len(e_known)} years "
                      f"(the yardstick for lenders; 12%+ is good)."])
    elif r_known:
        med = statistics.median(r_known)
        q["roic"] = round(med, 4)
        good = sum(x >= 0.10 for x in r_known)
        parts.append(_interp(med, [(0, 0), (0.05, 7), (0.10, 14), (0.15, 20), (0.20, 25)]))
        L.append([1 if med >= 0.15 else -1 if med < 0.08 else 0,
                  f"Return on invested capital: median {_pct(med)} over {len(r_known)} years, above 10% in "
                  f"{good} of them. Good businesses earn 15%+ on the money put into them, year after year."])
        q["roic_y"] = [None if x is None else round(x, 4) for x in roic]
    f_known = [x for x in fcf if x is not None]
    if f_known and not financial:
        pos = sum(x > 0 for x in f_known)
        last_rev, last_f = _last(rev), _last(fcf)
        margin = last_f / last_rev if last_rev and last_f is not None else None
        q["fcfm"] = round(margin, 4) if margin is not None else None
        both = [x >= 0.10 for x in r_known] + [x > 0 for x in f_known]
        parts.append(25 * sum(both) / len(both))
        L.append([1 if pos == len(f_known) else -1 if pos < len(f_known) / 2 else 0,
                  f"Free cash flow was positive in {pos} of {len(f_known)} years"
                  + (f"; last year {_pct(margin)} of sales." if margin is not None else ".")])
    if not financial and (td[-1] is not None or cash[-1] is not None):
        nd = debt0 - cash0
        avg_f = sum(f_known[-3:]) / len(f_known[-3:]) if f_known else None
        if nd <= 0:
            parts.append(25)
            L.append([1, f"More cash ({_money(cash0)}) than debt ({_money(debt0)})."])
        elif avg_f and avg_f > 0:
            yrs = nd / avg_f
            q["nd"] = round(yrs, 2)
            parts.append(_interp(yrs, [(0, 25), (1, 22), (3, 15), (5, 7), (8, 0)]))
            L.append([1 if yrs < 2 else -1 if yrs > 4 else 0,
                      f"Debt minus cash is {_money(nd)}: {yrs:.1f} years of free cash flow to pay it off."])
        else:
            parts.append(0)
            L.append([-1, f"Owes {_money(nd)} more than it holds in cash, with no free cash flow to pay it down."])
        last_oi, last_int = _last(oi), _last(intr)
        if last_oi is not None and last_int and last_int > 0:
            cov = last_oi / last_int
            q["cov"] = round(cov, 2)
            if 1.5 <= cov < 3:  # below 1.5 it's a red flag instead
                L.append([-1, f"Operating profit covers interest only {cov:.1f} times."])
    s = _span(sh, 10)
    if s:
        ch = _cagr(sh[s[0]], sh[s[1]], _years(fin, *s))
        if ch is not None:
            q["shc"] = round(ch, 4)
            parts.append(_interp(ch, [(-0.02, 25), (0, 20), (0.02, 10), (0.05, 3), (0.08, 0)]))
            L.append([1 if ch <= -0.005 else -1 if ch > 0.02 else 0,
                      f"Share count {'down' if ch < 0 else 'up'} {_pct(abs(ch), 1)} a year over "
                      f"{_years(fin, *s)} years" + (" (buybacks: your slice grows)." if ch < -0.005 else
                                                     " (dilution: your slice shrinks)." if ch > 0.02 else ".")])
    gm = [g_ / r_ for g_, r_ in zip(gp, rev) if g_ is not None and r_]
    if len(gm) >= 5:
        spread = max(gm) - min(gm)
        L.append([1 if spread < 0.05 else -1 if spread > 0.15 else 0,
                  f"Gross margin {_pct(gm[-1])}, ranging {_pct(min(gm))}–{_pct(max(gm))} over {len(gm)} years"
                  + (" (steady: pricing power)." if spread < 0.05 else " (swings: little pricing power)."
                     if spread > 0.15 else ".")])
    if parts:
        q["s"] = round(sum(parts) / len(parts) * 4)
        q["g"] = "Excellent" if q["s"] >= 80 else "Good" if q["s"] >= 60 else "Average" if q["s"] >= 40 else "Weak"
    else:
        q.update(s=None, g="Unknown")
        L.append([0, "Not enough in its SEC filings to judge the business."])
    out["q"] = q

    # ---------------------------------------------------------------- red flags
    f = {"l": [], "ok": []}
    flags, ok = f["l"], f["ok"]

    def flag(sev, text, url=None):
        flags.append([sev, text] + ([url] if url else []))
    i, j = n - 1, n - 2
    avg_assets = [x for x in (assets[j], assets[i]) if x]
    if ni[i] is not None and ocf[i] is not None and avg_assets and not financial:
        acc = (ni[i] - ocf[i]) / (sum(avg_assets) / len(avg_assets))
        f["acc"] = round(acc, 4)
        if ni[i] > 0 and ocf[i] < 0:
            flag(2, "Reported a profit but cash from operations was negative: the profit isn't turning into cash.")
        elif acc > 0.10:
            flag(2, f"Profits ran {_pct(acc)} of assets ahead of cash last year (high accruals): "
                    "earnings that aren't cash often get revised down.")
        elif acc > 0.05:
            flag(1, f"Profits ran {_pct(acc)} of assets ahead of cash last year (accruals). Worth a look.")
        else:
            ok.append("Profits are backed by cash")
    rg = rev[i] / rev[j] - 1 if rev[i] and rev[j] else None
    if rg is not None and ar[i] and ar[j] and ar[i] / rev[i] > 0.05:
        ag = ar[i] / ar[j] - 1
        if ag - rg > 0.15:
            flag(1, f"Money owed by customers grew {_pct(ag)} while sales grew {_pct(rg)}: customers paying "
                    "slower, or sales pulled forward.")
        else:
            ok.append("Customers pay on time")
    if rg is not None and inv[i] and inv[j] and inv[i] / rev[i] > 0.05:
        vg = inv[i] / inv[j] - 1
        if vg - rg > 0.20:
            flag(1, f"Inventory grew {_pct(vg)} while sales grew {_pct(rg)}: goods may be piling up unsold.")
        else:
            ok.append("Inventory in line with sales")
    if sbc[i] and ocf[i] and ocf[i] > 0:
        r_ = sbc[i] / ocf[i]
        if r_ > 0.30:
            flag(1, f"Stock pay to employees was {_pct(r_)} of operating cash flow: a real cost that cash "
                    "flow figures leave out.")
        else:
            ok.append("Stock pay is modest")
    if gw[i] and assets[i] and gw[i] / assets[i] > 0.40:
        flag(1, f"Goodwill (what it paid above book value for acquisitions) is {_pct(gw[i] / assets[i])} "
                "of assets: growth was bought, and write-downs are possible.")
    elif assets[i]:
        ok.append("Not built on acquisitions")
    if not financial and oi[i] is not None and intr[i] and intr[i] > 0 and oi[i] / intr[i] < 1.5:
        flag(2, f"Operating profit covers interest only {oi[i] / intr[i]:.1f} times: little room if business slows.")
    if q.get("shc") is not None and q["shc"] > 0.05:
        flag(1, f"Share count growing {_pct(q['shc'], 1)} a year: it keeps raising money by selling stock.")
    tracked = bool(ext)
    sh_ = ext.get("short") or {}
    if sh_.get("short") and shares:
        sp = sh_["short"] / shares
        f["short"] = round(sp, 4)
        if sp > 0.20:
            flag(2, f"{_pct(sp)} of its shares are sold short: many professionals are betting it falls.")
        elif sp > 0.10:
            flag(1, f"{_pct(sp)} of its shares are sold short (high).")
        else:
            ok.append(f"Short interest low ({_pct(sp, 1)} of shares)")
        if (sh_.get("days") or 0) > 8:
            flag(1, f"It would take short sellers {sh_['days']:.0f} days of normal trading to buy back.")
    fl = ext.get("filings")
    if fl:
        if fl.get("auditor"):
            d, u = fl["auditor"][0]
            flag(2, f"Changed auditors ({d}, 8-K item 4.01). Burry's first question: why?", u)
        if fl.get("restate"):
            d, u = fl["restate"][0]
            flag(2, f"Said earlier financial statements can't be relied on ({d}, 8-K item 4.02: a restatement).", u)
        if fl.get("late"):
            d, u, form = fl["late"][0]
            flag(2, f"Filed a late-filing notice ({form}, {d}): it couldn't finish its report on time.", u)
        if not (fl.get("auditor") or fl.get("restate") or fl.get("late")):
            ok.append(f"No auditor change, restatement or late filing in {LOOKBACK_YEARS} years")
    rk = ext.get("risks")
    if rk and rk.get("new"):
        if len(rk["new"]) >= 3:
            flag(1, f"{len(rk['new'])} new risk warnings in its latest annual report.", rk.get("url"))
        else:
            ok.append("Few new risk warnings")
    pts = sum(x[0] for x in flags)
    f["n"] = len(flags)
    f["s"] = max(0, 100 - 25 * pts)  # higher is cleaner, like the other cards
    f["g"] = "Clean" if not flags else "Watch" if pts <= 2 else "Red flags"
    if not tracked:
        f["note"] = ("Short interest, auditor changes, restatements and late filings are checked for the "
                     "stocks with full research pages.")
    out["f"] = f

    # ---------------------------------------------------------------- owners
    o = {"l": []}
    L, parts = o["l"], []
    fps = [x / y if x is not None and y else None for x, y in zip(fcf if not financial else ni, sh)]
    s = _span(fps, 5)
    if s:
        a0 = sum(x for x in fps[max(0, s[0] - 1): s[0] + 1] if x is not None) / len([x for x in fps[max(0, s[0] - 1): s[0] + 1] if x is not None])
        a1 = sum(x for x in fps[s[1] - 1: s[1] + 1] if x is not None) / len([x for x in fps[s[1] - 1: s[1] + 1] if x is not None])
        gr = _cagr(a0, a1, _years(fin, *s))
        label = "Net income" if financial else "Free cash flow"
        if gr is not None:
            o["fpsg"] = round(gr, 4)
            parts.append(_interp(gr, [(-0.05, 0), (0, 8), (0.05, 16), (0.10, 22), (0.15, 25)]))
            L.append([1 if gr >= 0.08 else -1 if gr < 0 else 0,
                      f"{label} per share grew {_pct(gr, 1)} a year over {_years(fin, *s)} years "
                      "(smoothed): the number owners actually get."])
        elif a1 is not None and a1 <= 0:
            parts.append(0)
            L.append([-1, f"{label} per share is negative."])
    if not financial:
        s = _span([x if x is not None and y is not None else None for x, y in zip(nopat, ic)], 5)
        if s and ic[s[1]] - ic[s[0]] > 0:
            inc = (nopat[s[1]] - nopat[s[0]]) / (ic[s[1]] - ic[s[0]])
            o["iroic"] = round(inc, 4)
            parts.append(_interp(inc, [(0, 0), (0.08, 10), (0.15, 20), (0.25, 25)]))
            L.append([1 if inc >= 0.15 else -1 if inc < 0.06 else 0,
                      f"Every extra dollar it put into the business over {_years(fin, *s)} years added "
                      f"{inc * 100:.0f}¢ a year in after-tax operating profit (return on reinvested money)."])
    k = slice(-5, None)
    spent = sum(x for x in buy[k] if x) + sum(x for x in div[k] if x)
    f5 = sum(x for x in fcf[k] if x is not None)
    if spent and f5 > 0:
        L.append([0, f"Returned {_money(spent)} to shareholders in the last {min(5, n)} years "
                     f"({_pct(spent / f5)} of free cash flow) through dividends and buybacks."])
    bb = sum(x for x in buy[k] if x)
    if bb and q.get("shc") is not None:
        s = _span(sh, 5)
        ch5 = _cagr(sh[s[0]], sh[s[1]], _years(fin, *s)) if s else None
        if ch5 is not None:
            if ch5 > 0:
                parts.append(4)
                L.append([-1, f"Spent {_money(bb)} on buybacks, yet the share count still rose: the buybacks only "
                              "soaked up stock handed to employees."])
            else:
                parts.append(_interp(-ch5, [(0, 10), (0.02, 20), (0.04, 25)]))
                L.append([1, f"Buybacks of {_money(bb)} actually shrank the share count ({_pct(-ch5, 1)} a year)."])
    ac = sum(x for x in acq[k] if x)
    if ac and f5 > 0 and ac / f5 > 0.5:
        L.append([-1, f"Spent {_money(ac)} buying other companies in {min(5, n)} years ({_pct(ac / f5)} of free cash "
                      "flow). Big acquirers often overpay; check that returns held up."])
    pays = [(fy[x], pay[x], tsr[x]) for x in range(n) if pay[x]]
    if pays:
        yr, p_last, _ = pays[-1]
        o["pay"] = p_last
        nil = _last(ni)
        line = f"CEO pay {_money(p_last)} in {yr[:4]}"
        if nil and nil > 0:
            line += f" ({_pct(p_last / nil, 2 if p_last / nil < 0.01 else 1)} of net income)"
        with_tsr = [x for x in pays if x[2]]
        if len(with_tsr) >= 2:
            (y0, p0, t0), (y1, p1, t1) = with_tsr[0], with_tsr[-1]
            pg, tg_ = p1 / p0 - 1, t1 / t0 - 1
            if pg > 0.25 and tg_ < 0:
                parts.append(0)
                L.append([-1, line + f". Pay rose {_pct(pg)} from {y0[:4]} to {y1[:4]} while shareholders' "
                                     f"returns fell {_pct(-tg_)}: pay isn't tied to owners' results."])
            elif pg < tg_ + 0.25:
                parts.append(25)
                L.append([1, line + f". Pay {'rose' if pg >= 0 else 'fell'} {_pct(abs(pg))} from {y0[:4]} to "
                                    f"{y1[:4]} while $100 in the stock {'grew' if tg_ >= 0 else 'fell'} "
                                    f"{_pct(abs(tg_))}: roughly in step with owners."])
            else:
                parts.append(12)
                L.append([0, line + f". Pay rose {_pct(pg)} from {y0[:4]} to {y1[:4]}, faster than "
                                    f"shareholders' {_pct(tg_)}."])
        else:
            L.append([0, line + "."])
    if parts:
        o["s"] = round(sum(parts) / len(parts) * 4)
        o["g"] = "Owner-minded" if o["s"] >= 70 else "Mixed" if o["s"] >= 45 else "Poor"
    else:
        o.update(s=None, g="Unknown")
        L.append([0, "Not enough history to judge how management uses the cash."])
    out["o"] = o
    return out


def screen_row(tkr, name, sc, price, page):
    """One row of look/screen.json (lists keep the file small)."""
    v, q, f, o = sc["v"], sc["q"], sc["f"], sc["o"]
    return [tkr, (name or "")[:48], round(sc["cap"]) if sc.get("cap") else None, price,
            v.get("iv"), v.get("mos"), v.get("ig"), v.get("ey"), q.get("s"),
            q.get("roic") if q.get("roic") is not None else q.get("roe"),
            q.get("shc"), f.get("n"), o.get("s"), 1 if page else 0, 1 if sc.get("fin") else 0]


SCREEN_COLS = ["t", "n", "cap", "p", "iv", "mos", "ig", "ey", "q", "roic", "shc", "flags", "own", "page", "fin"]


# ------------------------------------------------------------------ filings (tracked stocks)

def parse_filings(j, today):
    """Auditor changes (8-K item 4.01), non-reliance / restatements (4.02) and
    late-filing notices (NT 10-K, NT 10-Q) in the last LOOKBACK_YEARS, newest
    first, each [date, url] (late: [date, url, form])."""
    from secextras import DOC_URL
    rec = (j.get("filings") or {}).get("recent") or {}
    cut = (today - datetime.timedelta(days=365 * LOOKBACK_YEARS)).isoformat()
    cik = int(j.get("cik") or 0)
    out = {"auditor": [], "restate": [], "late": []}
    forms = rec.get("form") or []
    for k, form in enumerate(forms):
        d = rec["filingDate"][k]
        if d < cut:
            continue
        acc = rec["accessionNumber"][k]
        doc = (rec.get("primaryDocument") or [""] * len(forms))[k]
        url = (DOC_URL.format(cik=cik, acc=acc.replace("-", ""), doc=doc) if doc
               else f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/")
        items = (rec.get("items") or [""] * len(forms))[k] or ""
        if form in ("8-K", "8-K/A"):
            if "4.01" in items:
                out["auditor"].append([d, url])
            if "4.02" in items:
                out["restate"].append([d, url])
        elif form.startswith("NT 10-K") or form.startswith("NT 10-Q") or form.startswith("NT 20-F"):
            out["late"].append([d, url, form])
    for v in out.values():
        v.sort(key=lambda r: r[0], reverse=True)
    return out


def refresh_filings(con, tickers, today=None):
    """Read each tracked company's SEC filing index (monthly) for the events above."""
    import companyintel
    import requests
    companyintel.ensure_tables(con)
    today = today or datetime.date.today()
    have = dict(con.execute("SELECT tkr, checked_on FROM company_intel WHERE source = 'filings'").fetchall())
    due = sorted((t for t in set(tickers) if t not in have or (today - have[t]).days >= FILINGS_EVERY),
                 key=lambda t: have.get(t, datetime.date.min))[:FILINGS_PER_RUN]
    if not due:
        return
    from secextras import SUBMISSIONS
    s = health._session()
    ciks = health.ticker_ciks(s)
    hits = done = 0
    for t in due:
        hit = ciks.get(t) or ciks.get(t.replace(".", "-"))
        if not hit:
            continue
        time.sleep(0.15)
        try:
            j = s.get(SUBMISSIONS.format(cik=hit[0]), timeout=60).json()
        except (requests.RequestException, ValueError) as e:
            print(f"  filings {t}: {type(e).__name__}")
            continue
        got = parse_filings(j, today)
        bad = sum(bool(v) for v in got.values())
        companyintel._save(con, t, "filings", bad, json.dumps(got))
        hits += bool(bad)
        done += 1
    print(f"filing red flags: read {done} companies' filing indexes, {hits} with an auditor change, "
          "restatement or late filing")


def tracked_extras(con):
    """{tkr: {"short": .., "filings": .., "risks": ..}} for every stock that has them."""
    out = {}
    try:
        rows = con.execute("SELECT tkr, source, detail FROM company_intel WHERE source IN "
                           "('short', 'filings', 'risks') AND detail IS NOT NULL").fetchall()
    except Exception:
        return out
    for tkr, src, detail in rows:
        try:
            out.setdefault(tkr, {})[src] = json.loads(detail)
        except ValueError:
            pass
    return out


# ------------------------------------------------------------------ the screen page

def page():
    """Body of value.html: app/value.js draws the sortable table from look/screen.json."""
    presets = [("cheap", "Cheap and good"), ("quality", "Best businesses"), ("owners", "Owner-minded"),
               ("flags", "Red flags"), ("all", "Everything")]
    return ("<div id='vs'><h1>Value screen</h1>"
            "<p class='mut'>Every US company that files numbers with the SEC, scored the way long-term "
            "investors look at a business: what it's worth against today's price (margin of safety), how good "
            "the business is (returns on capital, cash flow, debt, share count), what a short seller would "
            "worry about, and whether management acts like owners. Tap a column to sort, a stock to see why.</p>"
            "<div class='vs-pre' role='tablist'>"
            + "".join(f"<button type='button' data-pre='{k}'>{label}</button>" for k, label in presets)
            + "</div><div class='filters'><input id='vs-q' type='search' placeholder='Filter by ticker or name' "
              "aria-label='Filter'><select id='vs-cap' aria-label='Company size'><option value='0'>Any size</option>"
              "<option value='3e8' selected>$300M+</option><option value='2e9'>$2B+</option>"
              "<option value='1e10'>$10B+</option></select><label class='mut vs-fin'><input type='checkbox' "
              "id='vs-fin'> include banks and insurers</label><span class='mut' id='vs-count'></span></div>"
              "<p class='hint vs-desc'></p>"
              "<div class='wrap'><table class='vs-tbl'><thead></thead><tbody><tr><td class='mut'>Loading…</td></tr>"
              "</tbody></table></div>"
              "<details class='vs-how'><summary>How the scores work</summary>"
              "<p><b>Value</b>: owner earnings (operating cash flow − capital spending − stock pay, averaged over "
              f"3 years), grown at the company's own recent rate (between {_pct(G_MIN)} and {_pct(G_MAX)}) for "
              f"{FAST} years, fading to {_pct(TERMINAL, 1)}, discounted at {_pct(DISCOUNT)} a year, plus cash minus "
              "debt. <b>Margin of safety</b> = how far the price is below that. <b>Priced for</b> is the growth "
              "rate today's price assumes. <b>Yield</b> is owner earnings over enterprise value.</p>"
              "<p><b>Quality</b> (0–100): return on invested capital over up to 10 years, how often it earned 10%+ "
              "and made free cash flow, debt against free cash flow, and the share-count trend.</p>"
              "<p><b>Flags</b>: accruals, receivables or inventory outrunning sales, heavy stock pay, goodwill, "
              "thin interest cover and dilution for every company; short interest, auditor changes, restatements, "
              "late filings and new risk warnings for stocks with research pages.</p>"
              "<p><b>Owners</b> (0–100): free cash flow per share growth, return on reinvested money, whether "
              "buybacks shrank the share count, and CEO pay against shareholder returns.</p>"
              "<p class='hint'>Rough screens from SEC-tagged numbers, not advice. A stock near the top is a place "
              "to start reading the annual report, not a buy signal.</p></details></div>")


CSS = """
.vs-pre{display:flex;gap:6px;flex-wrap:wrap;margin:12px 0 4px}
.vs-pre button{font:inherit;font-size:13px;font-weight:600;padding:7px 13px;border-radius:999px;border:1px solid var(--line);
background:var(--card);color:var(--mut);cursor:pointer}.vs-pre button.on{background:var(--chip);color:var(--fg);border-color:var(--acc)}
.vs-fin{display:flex;gap:6px;align-items:center;white-space:nowrap}.filters .vs-fin input{flex:none;min-width:0;width:auto;padding:0}
#vs-count{align-self:center}
.vs-tbl td{vertical-align:middle}.vs-tbl .num{text-align:right;font-variant-numeric:tabular-nums}
.vs-tbl th[data-k]{cursor:pointer;user-select:none;white-space:nowrap}.vs-tbl th[data-k]:hover{color:var(--fg)}
.vs-tbl th.on{color:var(--acc)}.vs-tbl tr[data-href]{cursor:pointer}
.vs-tbl small{display:block;color:var(--mut);font-size:12px;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.vs-pill{display:inline-block;min-width:34px;text-align:center;font-weight:700;border-radius:8px;padding:2px 7px;background:var(--chip)}
.vs-pill.hi{color:var(--up)}.vs-pill.lo{color:var(--down)}.vs-pill.mid{color:var(--warn)}
@media(max-width:700px){.vs-tbl .c-x{display:none}.vs-tbl td,.vs-tbl th{padding:8px 5px}
.vs-tbl th[data-k]{white-space:normal;font-size:11px;letter-spacing:.2px}.vs-tbl small{max-width:110px}}
.vs-how p{font-size:14px}
.sc-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px;margin-top:8px}
.sc-card{background:var(--card2);border:1px solid var(--line);border-radius:14px;padding:12px 14px}
.sc-card h3{margin:0 0 2px;font-size:14px;display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.sc-card .sc-g{font-size:12px;font-weight:700;border-radius:999px;padding:2px 9px;background:var(--chip)}
.sc-g.hi{color:var(--up)}.sc-g.mid{color:var(--warn)}.sc-g.lo{color:var(--down)}
.sc-big{font-size:24px;font-weight:800;font-variant-numeric:tabular-nums;margin:4px 0}.sc-big small{font-size:13px;color:var(--mut);font-weight:600}
.sc-card ul{list-style:none;margin:6px 0 0;padding:0;display:flex;flex-direction:column;gap:6px}
.sc-card li{font-size:13px;line-height:1.4;padding-left:16px;position:relative}
.sc-card li:before{content:'•';position:absolute;left:2px;color:var(--mut)}
.sc-card li.g:before{content:'▲';color:var(--up);font-size:9px;top:3px}.sc-card li.b:before{content:'▼';color:var(--down);font-size:9px;top:3px}
.sc-card li.s2{font-weight:600}.sc-ok{font-size:12px;color:var(--mut);margin-top:6px}
.sc-bar{height:6px;border-radius:999px;background:var(--chip);overflow:hidden;margin:4px 0 2px}.sc-bar i{display:block;height:100%;background:var(--acc)}
"""


# ------------------------------------------------------------------ selftest

def sample_fin(years=10, rev0=1000e6, growth=0.08, margin=0.20, shares0=100e6, share_chg=-0.02,
               debt=300e6, cash=200e6, sbc_pct=0.02, extra=None):
    fy = [f"{2016 + k}-12-31" for k in range(years)]
    rev = [rev0 * (1 + growth) ** k for k in range(years)]
    oi = [r * margin for r in rev]
    ni = [o * 0.75 for o in oi]
    ocf = [x * 1.1 for x in ni]
    capex = [r * 0.04 for r in rev]
    sh = [shares0 * (1 + share_chg) ** k for k in range(years)]
    eq = [r * 0.6 for r in rev]
    fin = {"fy": fy, "a": {"rev": rev, "oi": oi, "ni": ni, "ocf": ocf, "capex": capex, "sh": sh, "eq": eq,
                           "cash": [cash] * years, "debt": [debt] * years, "assets": [r * 1.5 for r in rev],
                           "liab": [r * 0.9 for r in rev], "ca": [r * 0.5 for r in rev],
                           "gp": [r * 0.5 for r in rev], "buy": [20e6] * years, "div": [10e6] * years},
           "x": {"tax": [o * 0.21 for o in oi], "pti": oi, "sbc": [r * sbc_pct for r in rev],
                 "int": [debt * 0.05] * years, "ar": [r * 0.1 for r in rev], "inv": [r * 0.1 for r in rev],
                 "pay": [None] * (years - 3) + [8e6, 9e6, 10e6], "tsr": [None] * (years - 3) + [100, 120, 150]},
           "qe": [], "q": {}, "name": "Sample Co"}
    for k, val in (extra or {}).items():
        (fin["x"] if k in fin["x"] else fin["a"])[k] = val
    return fin


def selftest():
    # the DCF: no growth is a perpetuity; implied growth inverts it
    assert abs(dcf(100, 0, 0.10, 0.0) - 1000) < 1e-6, dcf(100, 0, 0.10, 0.0)
    v = dcf(100, 0.08)
    g = implied_growth(v, 100, 0)
    assert abs(g - 0.08) < 1e-6, g
    assert implied_growth(1e12, 100, 0) is None and implied_growth(1000, -5, 0) is None

    good = sample_fin()
    sc = score(good, price=20.0)
    v, q, f, o = sc["v"], sc["q"], sc["f"], sc["o"]
    assert v["iv"] > v["lo"] > 0 and v["hi"] > v["iv"], v
    assert 0.07 < v["gr"] < 0.11, v["gr"]  # sales per share grew ~10%, owner earnings per share ~10%
    assert abs(v["mos"] - (1 - 20 / v["iv"])) < 1e-3 and v["s"] is not None, v
    assert v.get("ig") is not None and v.get("ey") is not None, v
    assert "margin of safety" in v["l"][0][1] or "above the estimate" in v["l"][0][1], v["l"]
    assert q["s"] >= 75 and q["g"] in ("Excellent", "Good"), q
    assert q["roic"] > 0.15 and q["shc"] < 0, q
    assert f["n"] == 0 and f["g"] == "Clean" and "note" in f, f
    assert o["s"] >= 70 and o["g"] == "Owner-minded", o
    assert any("roughly in step" in x[1] for x in o["l"]), o["l"]
    assert any("actually shrank" in x[1] for x in o["l"]), o["l"]
    json.dumps(sc)  # all plain types

    # expensive: price far above the estimate
    sc = score(good, price=v["iv"] * 3)
    assert sc["v"]["mos"] < -0.5 and sc["v"]["g"] == "Far above value", sc["v"]

    # a cash-burning, diluting, over-borrowed company with the classic red flags
    n = 10
    bad = sample_fin(margin=0.02, share_chg=0.10, debt=3000e6, cash=50e6, sbc_pct=0.10, extra={
        "ocf": [-50e6] * n, "ar": [100e6 * (1.6 ** k) for k in range(n)],
        "pay": [None] * 7 + [5e6, 7e6, 9e6], "tsr": [None] * 7 + [100, 80, 60]})
    sc = score(bad, price=10.0, ext={"short": {"short": 30e6, "days": 12},
                                     "filings": {"auditor": [["2025-03-01", "https://x/a"]], "restate": [],
                                                 "late": [["2025-04-01", "https://x/l", "NT 10-K"]]},
                                     "risks": {"new": ["a", "b", "c"], "url": "https://x/r"}})
    v, q, f, o = sc["v"], sc["q"], sc["f"], sc["o"]
    assert v["s"] is None and "pass" in v["l"][0][1], v
    assert q["s"] < 40 and q["g"] == "Weak", q
    texts = " ".join(x[1] for x in f["l"])
    for bit in ("cash from operations was negative", "paying slower", "Changed auditors",
                "late-filing notice", "sold short", "new risk warnings", "Share count growing", "days of normal"):
        assert bit in texts, (bit, texts)
    assert f["g"] == "Red flags" and f["s"] == 0 and "note" not in f, f
    assert o["s"] < 45 and any("isn't tied" in x[1] for x in o["l"]), o
    assert any("soaked up" in x[1] for x in o["l"]), o["l"]

    # a bank: valued on net income, judged on return on equity
    bank = sample_fin(extra={"ca": [None] * 10, "liab": [r * 1.4 for r in sample_fin()["a"]["rev"]]})
    sc = score(bank, price=20.0)
    assert sc["fin"] and "bank" in " ".join(t for _, t in sc["v"]["l"]) and "roe" in sc["q"], sc["q"]

    # too little history, or no price
    assert score({"fy": ["2025-12-31"], "a": {}}) is None
    sc = score(good)
    assert sc["v"]["g"] == "No price" and "mos" not in sc["v"]
    row = screen_row("SMP", "Sample Co", score(good, 20.0), 20.0, True)
    assert len(row) == len(SCREEN_COLS) and row[0] == "SMP" and row[-2] == 1

    # extras(): total debt without double-counting the current part
    def f_(end, val, start=None, form="10-K"):
        r = {"end": end, "val": val, "form": form, "fp": "FY", "filed": "2025-02-01"}
        if start:
            r["start"] = start
        return r
    facts = {"facts": {"us-gaap": {
        "LongTermDebt": {"units": {"USD": [f_("2024-12-31", 500)]}},
        "LongTermDebtCurrent": {"units": {"USD": [f_("2024-12-31", 100)]}},
        "ShortTermBorrowings": {"units": {"USD": [f_("2024-12-31", 50)]}},
        "LongTermDebtNoncurrent": {"units": {"USD": [f_("2023-12-31", 300)]}},
        "DebtCurrent": {"units": {"USD": [f_("2023-12-31", 40)]}},
        "ShareBasedCompensation": {"units": {"USD": [f_("2024-12-31", 7, "2024-01-01")]}}},
        "ecd": {"TotalShareholderRtnAmt": {"units": {"USD": [f_("2024-12-31", 140, "2024-01-01", "DEF 14A")]}}}}}
    x = extras(facts, ["2023-12-31", "2024-12-31"])
    assert x["td"] == [340, 550], x["td"]
    assert x["sbc"] == [None, 7] and x["tsr"] == [None, 140] and "gw" not in x, x

    # filing events
    today = datetime.date(2026, 6, 1)
    j = {"cik": "123", "filings": {"recent": {
        "form": ["8-K", "NT 10-K", "8-K", "10-K", "8-K"],
        "filingDate": ["2026-01-05", "2025-04-01", "2024-11-01", "2024-03-01", "2020-01-01"],
        "accessionNumber": ["0001-26-1", "0001-25-2", "0001-24-3", "0001-24-4", "0001-20-5"],
        "primaryDocument": ["a.htm", "b.htm", "c.htm", "d.htm", "e.htm"],
        "items": ["4.01,9.01", "", "4.02", "", "4.01"]}}}
    got = parse_filings(j, today)
    assert [r[0] for r in got["auditor"]] == ["2026-01-05"], got  # 2020 is too old
    assert got["restate"][0][0] == "2024-11-01" and got["late"][0][2] == "NT 10-K", got
    assert got["auditor"][0][1].endswith("/123/0001261/a.htm"), got["auditor"][0][1]

    import duckdb
    import companyintel
    con = duckdb.connect()
    assert tracked_extras(con) == {}
    companyintel.ensure_tables(con)
    companyintel._save(con, "AAA", "filings", 1, json.dumps(got))
    assert tracked_extras(con)["AAA"]["filings"]["late"], tracked_extras(con)
    assert "vs-tbl" in page()
    print("value selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
