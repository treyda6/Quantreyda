"""One page per stock: price chart with the buys marked, health, news, and a
plain-English "About this stock" explainer.

The explainer is written from the data the tracker already has (filings,
financials, prices, the leaderboard), so it costs nothing to run and never
makes up facts it doesn't have.

  href(tkr)                       site-relative link to a stock page
  pick(con, today, idea_tickers)  which tickers get a page
  render(con, tkr, today, records=None) -> HTML body
"""

import bisect
import datetime
import html
import re

import activists
import bills
import calendars
import committees
import companyintel
import funds
import health
import leaders
import news
import pictures
import profiles
import economy
import markets
import moves
import secextras
import today as front

E = html.escape
MAX_PAGES = 300  # full-research pages; every other traded ticker gets a basic one
VALID = re.compile(r"[A-Z][A-Z0-9.\-/]{0,7}")

CSS = """
.sh{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;flex-wrap:wrap}
.sh h1{font-size:38px;letter-spacing:-1px;margin:0}.sh .co{color:var(--mut)}
.sh .big{font-size:30px;font-weight:800;font-variant-numeric:tabular-nums;text-align:right}
.sh .big span{display:block;font-size:14px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:18px;margin:14px 0;box-shadow:var(--shadow)}
.panel h2{margin:0 0 10px;font-size:18px}
.chart{width:100%;height:auto;display:block}
.chart .grid{stroke:var(--line);stroke-width:1}
.chart text{fill:var(--mut);font-size:11px;font-family:inherit}
.chart .mk{cursor:help}.chart .mk:hover circle,.chart .mk:hover rect{stroke-width:3}
.legend{display:flex;gap:16px;flex-wrap:wrap;color:var(--mut);font-size:13px;margin-top:8px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;vertical-align:-1px}
.legend i.ins{border-radius:2px;transform:rotate(45deg)}
.two{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.qa h3{font-size:15px;margin:14px 0 4px}.qa p{margin:0;color:var(--fg)}
.qa ul{margin:4px 0 0;padding-left:18px}
.list{list-style:none;padding:0;margin:0}.list li{padding:9px 0;border-top:1px solid var(--line);font-size:14px}
.list li:first-child{border-top:0}.list .d{color:var(--mut);font-variant-numeric:tabular-nums;margin-right:8px}
.kv{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:10px}
.kv div{background:var(--card2);border-radius:12px;padding:8px 10px}
.kv span{display:block;color:var(--mut);font-size:11px;text-transform:uppercase;letter-spacing:.5px}
.kv b{font-size:15px}
.back{color:var(--mut);text-decoration:none;font-size:14px}
@media (max-width:640px){.two{grid-template-columns:1fr}.sh h1{font-size:30px}.sh .big{font-size:24px}
.kv{grid-template-columns:repeat(2,1fr)}.chart text{font-size:22px}}
"""


def slug(tkr):
    return re.sub(r"[^A-Za-z0-9]+", "-", tkr).strip("-").upper()


def href(tkr, root=""):
    return f"{root}s/{slug(tkr)}.html"


def pick(con, today, idea_tickers):
    """Ideas first, then the most-bought stocks of the last 6 months."""
    start = today - datetime.timedelta(days=180)
    out = list(dict.fromkeys(t for t in idea_tickers if t))
    for sql in ("""SELECT tkr, count(*) FROM trades WHERE tx_type = 'Purchase' AND tkr IS NOT NULL
                   AND filed_date BETWEEN ? AND ? GROUP BY 1 ORDER BY 2 DESC LIMIT 200""",
                """SELECT upper(trim(ticker)), sum(shares * price_per_share) FROM insider_trades
                   WHERE trans_code = 'P' AND price_per_share > 0 AND ticker IS NOT NULL
                   AND try_cast(filed_date AS DATE) BETWEEN ? AND ?
                   GROUP BY 1 HAVING sum(shares * price_per_share) >= 100000
                   ORDER BY 2 DESC LIMIT 150"""):
        for t, _ in profiles._all(con, sql, [start, today]):
            if t and t not in out and VALID.fullmatch(t):
                out.append(t)
    return out[:MAX_PAGES]


# ---------------------------------------------------------------- data

def _marks(con, tkr, since):
    """Congress and insider buys of this stock since `since`, oldest first."""
    out = []
    for d, filed, last, first, chamber, owner, lo, hi in profiles._all(con, """
            SELECT txn_date, filed_date, last_name, first_name, chamber, owner, amount_low, amount_high
            FROM trades WHERE tkr = ? AND tx_type = 'Purchase' AND txn_date >= ?
            ORDER BY txn_date""", [tkr, since]):
        who = f"{'Sen.' if chamber == 'S' else 'Rep.'} {first or ''} {last}".replace("  ", " ")
        if owner and str(owner).lower() not in ("self", "none", "nan"):
            who += f" ({front.OWNER.get(str(owner).lower(), str(owner).lower())})"
        out.append({"d": d, "kind": "congress", "who": who, "filed": filed,
                    "ck": (chamber, last, first), "amt": front.digest._bracket(lo or 0, hi or 0)})
    for d, filed, name, role, value in profiles._all(con, """
            SELECT try_cast(trans_date AS DATE), max(try_cast(filed_date AS DATE)), owner_name,
                   max(owner_relationship), sum(shares * price_per_share)
            FROM insider_trades WHERE upper(trim(ticker)) = ? AND trans_code = 'P'
              AND price_per_share > 0 AND try_cast(trans_date AS DATE) >= ?
            GROUP BY 1, 3 ORDER BY 1""", [tkr, since]):
        if d:
            out.append({"d": d, "kind": "insider", "who": name, "filed": filed,
                        "role": profiles.nice_role(role), "amt": profiles.money(value)})
    for d, filed, slug, name, title, lo, hi in profiles._all(con, """
            SELECT tx_date, filed, slug, name, title, amount_low, amount_high FROM wh_trades
            WHERE tkr = ? AND kind = 'stock' AND tx_type = 'Purchase' AND tx_date >= ?
            ORDER BY tx_date""", [tkr, since]):
        out.append({"d": d, "kind": "whitehouse", "who": name, "slug": slug, "role": title,
                     "filed": filed, "amt": front.digest._bracket(lo or 0, hi)})
    return sorted(out, key=lambda m: m["d"])


def _sells(con, tkr, since):
    r = profiles._one(con, """SELECT count(*) FROM trades WHERE tkr = ? AND tx_type LIKE 'Sale%'
                              AND txn_date >= ?""", [tkr, since])
    s = profiles._one(con, """SELECT count(DISTINCT accession_number), sum(shares * price_per_share)
                              FROM insider_trades WHERE upper(trim(ticker)) = ? AND trans_code = 'S'
                              AND try_cast(trans_date AS DATE) >= ?""", [tkr, since])
    return (r[0] if r else 0), (s[0] if s else 0), (s[1] if s and s[1] else 0)


# ---------------------------------------------------------------- chart

def chart(px, marks, w=900, h=300):
    """SVG line chart of (date, close) with a marker on each buy (hover for who)."""
    if len(px) < 2:
        return "<p class='hint'>No price history saved for this stock yet.</p>"
    L, R, T, B = 52, 12, 12, 26
    d0, d1 = px[0][0], px[-1][0]
    span = max(1, (d1 - d0).days)
    lo, hi = min(p[1] for p in px), max(p[1] for p in px)
    pad = (hi - lo) * 0.08 or 1
    lo, hi = lo - pad, hi + pad

    def X(d):
        return L + (d - d0).days / span * (w - L - R)

    def Y(v):
        return T + (hi - v) / (hi - lo) * (h - T - B)

    pts = " ".join(f"{X(d):.1f},{Y(v):.1f}" for d, v in px)
    up = px[-1][1] >= px[0][1]
    col = "var(--up)" if up else "var(--down)"
    area = f"M{X(d0):.1f},{h - B} L" + pts.replace(" ", " L") + f" L{X(d1):.1f},{h - B} Z"
    g = []
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        g.append(f"<line class='grid' x1='{L}' x2='{w - R}' y1='{Y(v):.1f}' y2='{Y(v):.1f}'/>"
                 f"<text x='{L - 6}' y='{Y(v) + 4:.1f}' text-anchor='end'>${v:,.0f}</text>")
    months = sorted({(d.year, d.month) for d, _ in px})
    step = max(1, len(months) // 6)
    for y, m in months[1::step]:
        d = datetime.date(y, m, 1)
        g.append(f"<text x='{X(d):.1f}' y='{h - 8}' text-anchor='middle'>{d:%b '%y}</text>")
    dates = [d for d, _ in px]
    mk = []
    for m in marks:
        k = bisect.bisect_left(dates, m["d"])
        if k >= len(px) or m["d"] < d0:
            continue
        x, y = X(px[k][0]), Y(px[k][1])
        tip = E(f"{m['d']:%b %d, %Y}: {m['who']} bought {m['amt']}"
                + (f" (made public {m['filed']:%b %d})" if m.get("filed") else ""))
        if m["kind"] == "congress":
            shape = (f"<circle cx='{x:.1f}' cy='{y:.1f}' r='6' fill='var(--acc)' "
                     f"stroke='var(--card)' stroke-width='2'/>")
        elif m["kind"] == "whitehouse":
            shape = (f"<rect x='{x - 5.5:.1f}' y='{y - 5.5:.1f}' width='11' height='11' rx='2' "
                     f"fill='var(--warn)' stroke='var(--card)' stroke-width='2'/>")
        else:
            shape = (f"<rect x='{x - 5:.1f}' y='{y - 5:.1f}' width='10' height='10' fill='var(--ins)' "
                     f"stroke='var(--card)' stroke-width='2' transform='rotate(45 {x:.1f} {y:.1f})'/>")
        mk.append(f"<g class='mk'><title>{tip}</title>{shape}</g>")
    return (f"<svg class='chart' viewBox='0 0 {w} {h}' role='img' aria-label='Price chart with buys marked'>"
            f"<defs><linearGradient id='fill' x1='0' y1='0' x2='0' y2='1'>"
            f"<stop offset='0' stop-color='{col}' stop-opacity='.22'/>"
            f"<stop offset='1' stop-color='{col}' stop-opacity='0'/></linearGradient></defs>"
            f"{''.join(g)}<path d='{area}' fill='url(#fill)'/>"
            f"<polyline points='{pts}' fill='none' stroke='{col}' stroke-width='2' "
            f"stroke-linejoin='round'/>{''.join(mk)}</svg>")


# ---------------------------------------------------------------- explainer

def _size(rev):
    if rev is None:
        return None
    if rev < 500e6:
        return "a small company", " Smaller companies can move a lot more, in both directions."
    if rev < 10e9:
        return "a mid-sized company", ""
    return "a large company", " Big, established companies tend to move less than small ones."


def _people(n, one, many):
    return f"{n} {one if n == 1 else many}"


def explain(tkr, pr, h, marks, sells, records, flags, news_rows, stakes=(), ci=None):
    """[(question, answer html)] written from the data, in plain English."""
    qa = []
    name = pr.get("name") or (h or {}).get("company") or tkr
    sector = (pr.get("sector") or "").lower()
    s = f"<b>{E(name)}</b> ({E(tkr)})" if name != tkr else f"<b>{E(tkr)}</b>"
    s += f" is in the <b>{E(sector)}</b> business." if sector else " is a publicly traded company."
    size = _size(pr.get("revenue"))
    if size:
        s += (f" It brought in {E(profiles.money(pr['revenue']))} in sales last year, "
              f"which makes it {size[0]}.{size[1]}")
    qa.append(("What is this company?", f"<p>{s}</p>"))

    bits = []
    verdict = (h or {}).get("verdict")
    if verdict and verdict != "Unknown":
        bits.append(f"The tracker's quick check rates it <b>{E(verdict)}</b>: {E(h.get('reasons') or '')}.")
    g = pr.get("growth")
    if g is not None:
        bits.append("Sales are " + (f"<b>growing</b> ({front._pct(g)} last year)." if g > 0.02 else
                                     f"<b>shrinking</b> ({front._pct(g)} last year)." if g < -0.02 else
                                     "<b>flat</b> compared with the year before."))
    ni = pr.get("net_income")
    if ni is not None:
        bits.append(f"It <b>made a profit</b> of {E(profiles.money(ni))}." if ni > 0 else
                    f"It <b>lost money</b> ({E(profiles.money(ni))}). Check how much cash it has left "
                    "and whether it keeps selling new shares to fund itself.")
    dr = pr.get("debt_ratio")
    if dr is not None:
        bits.append("Debt is <b>high</b> compared with what shareholders own." if dr > 2 else
                    "Debt looks <b>manageable</b>." if dr < 1 else "Debt is <b>moderate</b>.")
    ci = ci or {}
    if ci.get("buybacks"):
        bits.append(f"It spent <b>{E(profiles.money(ci['buybacks']))}</b> buying back its own "
                    "stock last year, which usually means management thinks the shares are worth it "
                    "(and it leaves each remaining share a bigger slice).")
    if ci.get("dividends"):
        bits.append(f"It paid {E(profiles.money(ci['dividends']))} in dividends.")
    qa.append(("Is the business healthy?", "<p>" + (" ".join(bits) or
               "Not enough SEC financial data yet (common for funds, foreign companies and very new "
               "listings). Read the latest annual report before deciding anything.") + "</p>"))

    pe, chg = pr.get("pe"), pr.get("chg_1y")
    if pe:
        p = (f"It trades at a P/E of <b>{pe:.0f}</b>, meaning you pay about ${pe:.0f} for each $1 of "
             "yearly profit. ")
        p += ("That's on the cheap side; the S&P 500 has usually been in the high teens to low 20s. "
              "Cheap can mean a bargain, or that investors expect trouble." if pe < 15 else
              "That's in the normal range for a US stock." if pe <= 30 else
              "That's expensive: the price assumes strong growth, and these can fall hard if growth slows.")
    elif ni is not None and ni <= 0:
        p = "There's no P/E because it isn't profitable right now, so the price rests on hopes for future profits."
    else:
        p = "No P/E yet (it needs both a price and a year of profit)."
    if chg is not None:
        p += f" The stock is <b>{front._pct(chg)}</b> over the past year."
    qa.append(("Is the stock expensive?", f"<p>{p}</p>"))

    wh = [m for m in marks if m["kind"] == "whitehouse"]
    cong = [m for m in marks if m["kind"] == "congress"]
    ins = [m for m in marks if m["kind"] == "insider"]
    b = []
    if cong:
        b.append(f"<b>{len(cong)}</b> Congress purchase{'s' if len(cong) != 1 else ''} by "
                 f"{_people(len({m['who'] for m in cong}), 'person', 'people')}")
    if ins:
        b.append(f"<b>{len(ins)}</b> open-market insider purchase{'s' if len(ins) != 1 else ''} by "
                 f"{_people(len({m['who'] for m in ins}), 'insider', 'insiders')}")
    if wh:
        b.append(f"<b>{len(wh)}</b> purchase{'s' if len(wh) != 1 else ''} by "
                 + ", ".join(sorted({f"{m['who']} ({m['role']})" for m in wh})))
    p = ("In the last two years: " + "; ".join(b) + "." if b else
         "No Congress, insider or White House buys on record in the last two years.")
    cs, ins_n, ins_v = sells
    if cs or ins_n:
        p += (f" On the other side there were {cs} Congress sale{'s' if cs != 1 else ''} and "
              f"{ins_n} insider sale filing{'s' if ins_n != 1 else ''}"
              + (f" ({E(profiles.money(ins_v))})" if ins_v else "") + ".")
    if ins:
        p += (" Insider buys matter most: executives know the business, and they're spending their own "
              "money. Insider sales matter less (taxes, houses, diversifying).")
    if stakes:
        f0 = stakes[0]
        p += (f" <b>{E(f0[2])}</b> {E(activists.FORM_WORDS.get(f0[1], 'filed a 5% stake'))} "
              f"on {f0[0]:%b %d, %Y}.")
    if flags:
        p += (f" ⚑ {E(flags[0])} sits on a committee that oversees this industry, which is worth "
              "knowing but isn't proof of anything.")
    best = None
    for m in marks:
        key = (leaders.key_congress(*m["ck"]) if m.get("ck") else
               leaders.key_official(m["slug"]) if m.get("slug") else leaders.key_insider(m["who"]))
        rec = (records or {}).get(key)
        if rec and (best is None or rec["avg"] > best[1]["avg"]):
            best = (m["who"], rec)
    if best:
        p += (f" Of these buyers, {E(best[0])} has the best record on the leaderboard: "
              f"{best[1]['avg'] * 100:+.0f}% vs the S&P 500 six months after their buys "
              f"({best[1]['n']} buys).")
    qa.append(("Who's been buying, and should I care?", f"<p>{p}</p>"))

    dc = []
    if ci.get("contracts"):
        dc.append(f"It received <b>{E(profiles.money(ci['contracts']))}</b> in federal contracts "
                  "over the last 12 months")
    if ci.get("lobbying"):
        issues = ", ".join(ci.get("lobbying_issues") or [])
        dc.append(f"spent <b>{E(profiles.money(ci['lobbying']))}</b> on lobbying"
                  + (f" (mostly on {E(issues)})" if issues else ""))
    if ci.get("pac"):
        lean = ci.get("pac_rep")
        side = ("" if lean is None else
                f", {lean * 100:.0f}% of it to Republicans" if lean >= .5 else
                f", {(1 - lean) * 100:.0f}% of it to Democrats")
        dc.append(f"gave <b>{E(profiles.money(ci['pac']))}</b> to federal candidates through its "
                  f"PAC this election cycle{side}")
    if dc:
        txt = "; it ".join(dc) if dc[0].startswith("It") else "It " + "; it ".join(dc)
        qa.append(("How tied is it to Washington?",
                   f"<p>{txt}. Companies that depend on government money or rules have more to gain "
                   "or lose from what Congress and agencies do, which is one reason a "
                   "politician's trade in it can be worth a closer look.</p>"))
    sh = ci.get("short") or {}
    if sh.get("days") is not None and (sh["days"] >= 5 or (sh.get("chg") or 0) >= 25):
        qa.append(("Are people betting against it?",
                   f"<p>Short sellers (who profit if the price falls) hold about "
                   f"<b>{sh['short'] / 1e6:,.1f} million shares</b>, "
                   + (f"{'up' if sh['chg'] >= 0 else 'down'} {abs(sh['chg']):.0f}% in two weeks, " if sh.get("chg") is not None else "")
                   + f"and it would take them <b>{sh['days']:.1f} days</b> of normal trading to buy "
                   "them back. That's a lot of doubt: find out what they think is wrong before "
                   "buying. It can also make the price jumpy if good news forces them to buy.</p>"))
    if ci.get("buzz") and ci["buzz"] >= 2:
        qa.append(("Why is everyone looking it up?",
                   f"<p>Its Wikipedia page got <b>{ci['buzz']:.1f}×</b> its usual traffic this week "
                   f"(about {ci['views']:,.0f} views a day). A spike usually follows news; check the "
                   "filings below for what happened, and be careful buying into hype.</p>"))
    todo = []
    tenk = next((r for r in news_rows if r[1] in ("10-K", "20-F")), None)
    q = next((r for r in news_rows if "quarterly results" in r[2]), None)
    todo.append(f"Skim the <a href='{E(tenk[3])}'>latest annual report</a>: the business section "
                "and the risk factors." if tenk else
                "Skim the latest annual report (10-K): the business section and the risk factors.")
    if q:
        todo.append(f"Read the <a href='{E(q[3])}'>latest earnings release</a> ({q[0]:%b %d}).")
    if ni is not None and ni <= 0:
        todo.append("Since it's losing money: how many months of cash does it have?")
    if pe and pe > 30:
        todo.append("Ask what happens to the price if growth slows to average.")
    todo.append("Keep any one stock to a small slice of your Roth (many people stay at 5% or less) "
                "and the core in a broad index fund.")
    qa.append(("What should I check before buying?",
               "<ul>" + "".join(f"<li>{t}</li>" for t in todo) + "</ul>"))
    return qa


# ---------------------------------------------------------------- page

def render(con, tkr, today, records=None):
    since = today - datetime.timedelta(days=730)
    pr = profiles.company(con, tkr)
    h = health.lookup(con, [tkr]).get(tkr)
    px = profiles._all(con, "SELECT d, close FROM prices WHERE tkr = ? AND d BETWEEN ? AND ? "
                       "ORDER BY d", [tkr, since, today])
    px = [(d if isinstance(d, datetime.date) else d.date(), v) for d, v in px if v]
    marks = _marks(con, tkr, since)
    flags = []
    for m in marks:
        if m.get("ck"):
            f = committees.committee_flag(con, tkr, m["ck"][1], m["ck"][2], m["ck"][0])
            if f and m["who"] not in flags:
                flags.append(m["who"])
    news_rows = news.recent(con, tkr, 8)
    name = pr.get("name") or (h or {}).get("company") or ""

    price = ""
    if pr.get("price"):
        up = (pr.get("chg_1y") or 0) >= 0
        price = (f"<div class='big'>${pr['price']:,.2f}<span class='{'up' if up else 'down'}'>"
                 f"{front._pct(pr.get('chg_1y'))} past year</span></div>")
    head = (f"<a class='back' href='../index.html'>← Ideas</a><div class='sh'><div class='pic'>"
            f"{pictures.logo_html(tkr, '../', 'big')}<div class='t'><h1>{E(tkr)}</h1>"
            f"<div class='co'>{E(name)}{' · ' + E(pr['sector']) if pr.get('sector') else ''}</div></div></div>"
            f"{price}</div>")
    legend = ("<div class='legend'><span><i style='background:var(--acc)'></i>Congress buy</span>"
              "<span><i class='ins' style='background:var(--ins)'></i>Insider buy</span>"
              "<span><i style='background:var(--warn);border-radius:2px'></i>White House or Fed buy</span>"
              "<span>Hover a marker for who and how much.</span></div>")
    chart_panel = f"<section class='panel'><h2>Two years of price, with the buys</h2>{chart(px, marks)}{legend}</section>"

    ci = companyintel.intel(con, tkr)
    ci.update(secextras.intel(con, tkr))
    qa = explain(tkr, pr, h, marks, _sells(con, tkr, since), records, flags, news_rows,
                 activists.for_ticker(con, tkr), ci)
    about = ("<section class='panel qa'><h2>About this stock, in plain English</h2>"
             + "".join(f"<h3>{E(q)}</h3>{a}" for q, a in qa)
             + "<p class='hint' style='margin-top:12px'>Written automatically from public filings and "
               "prices. A starting point for research, not advice.</p></section>")

    stats = [("Sales", profiles.money(pr.get("revenue")) or "—"), ("Growth", front._pct(pr.get("growth"))),
             ("Profit", profiles.money(pr.get("net_income")) or "—"),
             ("P/E", f"{pr['pe']:.0f}" if pr.get("pe") else "—"),
             ("Debt / equity", f"{pr['debt_ratio']:.1f}" if pr.get("debt_ratio") is not None else "—"),
             ("Checked", str((h or {}).get("checked_on") or "—")[:10])]
    health_panel = (f"<section class='panel'><h2>Health check</h2><div class='foot'>"
                    f"{front._badge((h or {}).get('verdict'))}<span class='hint'>"
                    f"{E((h or {}).get('reasons') or 'Not checked yet.')}</span></div><div class='kv'>"
                    + "".join(f"<div><span>{k}</span><b>{E(v)}</b></div>" for k, v in stats)
                    + "</div></section>")
    sx = ci
    sh = sx.get("short") or {}
    hh = h or {}
    split = ""
    r = hh.get("split_ratio")
    if r and r == r and hh.get("split_date"):  # NaN != NaN: no split on file
        split = (f"{r:g}-for-1" if r >= 1 else f"1-for-{1 / r:g} (reverse)") + f", {str(hh.get('split_date'))[:4]}"
    intel_rows = [("Buybacks (last year)", profiles.money(ci.get("buybacks")) or "—"),
                  ("Dividends (last year)", profiles.money(ci.get("dividends")) or "—"),
                  ("Lobbying (12 months)", profiles.money(ci.get("lobbying")) if "lobbying" in ci else "—"),
                  ("Federal contracts (12 mo)", profiles.money(ci.get("contracts")) if "contracts" in ci else "—"),
                  ("Wikipedia views / day", f"{ci['views']:,.0f}" if ci.get("views") else "—"),
                  ("Buzz vs usual", f"{ci['buzz']:.1f}×" if ci.get("buzz") else "—"),
                  ("PAC gifts to candidates", profiles.money(ci.get("pac")) if "pac" in ci else "—"),
                  ("…to Republicans / Democrats", f"{ci['pac_rep'] * 100:.0f}% / {100 - ci['pac_rep'] * 100:.0f}%"
                   if ci.get("pac_rep") is not None else "—"),
                  (f"CEO pay ({hh.get('ceo_pay_year')})" if isinstance(hh.get("ceo_pay_year"), str) else "CEO pay",
                   profiles.money(hh.get("ceo_pay")) or "—"),
                  ("Last stock split", split or "—"),
                  ("Shares sold short", f"{sh['short'] / 1e6:,.1f}M" if sh.get("short") else "—"),
                  ("Days to cover", f"{sh['days']:.1f}" if sh.get("days") is not None else "—")]
    intel_panel = ("<section class='panel'><h2>Money, Washington and buzz</h2><div class='kv'>"
                   + "".join(f"<div><span>{k}</span><b>{E(v or '—')}</b></div>" for k, v in intel_rows)
                   + "</div>"
                   + (f"<p class='hint'>Lobbying on: {E(', '.join(ci['lobbying_issues']))}.</p>"
                      if ci.get("lobbying_issues") else "")
                   + (f"<p class='hint'>Its PAC gave most to: {E(', '.join(ci['pac_top']))}.</p>"
                      if ci.get("pac_top") else "")
                   + "<p class='hint'>Sources: SEC cash-flow statements and proxy pay tables, "
                     "Senate lobbying disclosures, USAspending.gov, Wikipedia page views, FEC "
                     "filings (this election cycle), FINRA short interest"
                   + (f" (as of {E(sh['date'])})" if sh.get("date") else "") + ".</p></section>")
    rk = sx.get("risks") or {}
    risks_panel = ""
    if rk:
        grew = rk["words"] / rk["prev_words"] - 1 if rk.get("prev_words") else None
        risks_panel = (
            "<section class='panel'><h2>New warnings in its annual report</h2>"
            f"<p>The risk section of its <a href='{E(rk['url'])}'>latest annual report</a> "
            f"({E(rk['filed'])}) lists {rk['n']} risks"
            + (f" and is {abs(grew) * 100:.0f}% {'longer' if grew >= 0 else 'shorter'} than last year's"
               if grew is not None and abs(grew) >= .05 else "") + ". "
            + (f"{len(rk['new'])} of them are new this year:</p><ul class='list'>"
               + "".join(f"<li>{E(x)}</li>" for x in rk["new"]) + "</ul>"
               if rk["new"] else "None of them are new this year.</p>")
            + "<p class='hint'>Companies add a warning when they see a new problem coming. Each "
              "one is worth a minute: is it about the whole industry, or just this company?</p></section>")
    bl = bills.for_ticker(con, tkr)
    if bl:
        bills_panel = ("<section class='panel'><h2>Bills moving in its industry</h2><ul class='list'>"
                       + "".join(f"<li><span class='d'>{d:%b %d}</span><a href='{E(u)}'>{E(t or '')}</a>"
                                 f" <span class='hint'>· {E(a or '')} · {E(sp or '')}</span></li>"
                                 for t, pol, sc, d, a, u, sp in bl)
                       + "</ul><p class='hint'>From Congress.gov: bills in this industry's policy area "
                         "that a committee or chamber acted on recently.</p></section>")
    else:
        bills_panel = ""
    if news_rows:
        items = "".join(f"<li><span class='d'>{d:%b %d}</span><a href='{E(u)}'>{E(w)}</a> "
                        f"<span class='hint'>({E(f)})</span></li>" for d, f, w, u in news_rows)
    else:
        items = "<li class='hint'>No recent filings saved yet. They're fetched on the daily run.</li>"
    news_panel = (f"<section class='panel'><h2>Recent news from its SEC filings</h2>"
                  f"<ul class='list'>{items}</ul></section>")

    rows = []
    for m in sorted(marks, key=lambda m: m["d"], reverse=True)[:12]:
        if m["kind"] == "congress":
            r = type("R", (), {"last_name": m["ck"][1], "first_name": m["ck"][2], "chamber": m["ck"][0]})
            who = front.member_html(con, r, m["who"], face=True, root="../")
        elif m["kind"] == "whitehouse":
            who = front.official_html(con, m["slug"], m["who"], face=True, root="../") + f" <span class='hint'>({E(m['role'])})</span>"
        else:
            who = front.insider_html(con, m["who"], face=True) + f" <span class='hint'>({E(m['role'])})</span>"
        rows.append(f"<li><span class='d'>{m['d']:%b %d, %Y}</span>{who} bought {E(m['amt'] or '')}</li>")
    buys_panel = (f"<section class='panel'><h2>Who bought</h2><ul class='list'>"
                  + ("".join(rows) or "<li class='hint'>No buys in the last two years.</li>")
                  + "</ul></section>")
    return (head + chart_panel + about + f"<div class='two'>{health_panel}{news_panel}</div>"
            + calendars.stock_panel(con, tkr, today) + intel_panel + risks_panel + markets.stock_panel(con, tkr, name)
            + moves.stock_panel(con, tkr) + economy.stock_panel(con, tkr) + bills_panel + buys_panel
            + funds.owners_panel(con, tkr))


def selftest():
    import duckdb
    import signals
    con = duckdb.connect()
    signals.make_fixture(con)
    con.execute("CREATE TABLE IF NOT EXISTS prices (tkr VARCHAR, d DATE, close DOUBLE)")
    d0 = datetime.date(2024, 6, 1)
    con.executemany("INSERT INTO prices VALUES (?,?,?)",
                    [("AAA", d0 + datetime.timedelta(days=k), 50 + k * 0.1) for k in range(400)])
    news.ensure_table(con)
    con.execute("INSERT INTO news VALUES ('AAA','2025-05-01','8-K','Reported quarterly results','https://x/q')")
    import whitehouse
    whitehouse.ensure_tables(con)
    con.execute("""INSERT INTO wh_trades VALUES ('d1',1,'vance-jd','JD Vance','Vice President',
        'Aaa Inc (AAA)','stock','AAA','Purchase','2025-04-01','2025-04-20',15001,50000,false,'l')""")
    activists.ensure_tables(con)
    con.execute("INSERT INTO stakes VALUES ('a1','13D','2025-05-12','AAA','Aaa','Pershing Road','u')")
    today = datetime.date(2025, 6, 1)
    assert slug("BRK.B") == "BRK-B" and href("BRK.B", "../") == "../s/BRK-B.html"
    body = render(con, "AAA", today)
    assert "<svg class='chart'" in body and "<circle" in body, body[:500]
    assert "About this stock" in body and "Who&#x27;s been buying" in body
    assert "Congress purchase" in body and "latest earnings release" in body
    assert "fill='var(--warn)'" in body and "JD Vance (Vice President)" in body
    assert "Pershing Road</b> took a 5%+ stake as an activist" in body
    companyintel.ensure_tables(con)
    companyintel._save(con, "AAA", "lobbying", 3.3e6, "Defense")
    companyintel._save(con, "AAA", "contracts", 4.2e9, None)
    companyintel._save(con, "AAA", "buzz", 900, "Aaa Inc|3.2")
    companyintel._save(con, "AAA", "pac", 412000, "AAA INC PAC|0.62|Smith For Senate; Jones For Congress")
    body = render(con, "AAA", today)
    companyintel._save(con, "AAA", "short", 6e6, '{"date": "2026-11-14", "short": 6e6, "days": 7.5, "chg": 20.0}')
    companyintel._save(con, "AAA", "risks", 1, '{"filed": "2026-02-01", "url": "https://sec/x", "n": 30, '
                       '"prev_n": 28, "words": 12000, "prev_words": 10000, "new": ["Tariffs could raise our costs."]}')
    body = render(con, "AAA", today)
    assert "Are people betting against it?" in body and "7.5 days" in body, body
    assert "New warnings in its annual report" in body and "20% longer" in body and "Tariffs could" in body
    assert "gave <b>$412k</b> to federal candidates through its PAC" in body and "62% of it to Republicans" in body, body
    assert "Smith For Senate" in body
    assert "How tied is it to Washington?" in body and "$4.2B</b> in federal contracts" in body
    assert "3.2×" in body and "Money, Washington and buzz" in body
    bills.ensure_table(con)
    import committees as cm
    cm.ensure_tables(con)
    con.execute("INSERT OR REPLACE INTO companies VALUES ('AAA', 1, 'Aaa', 3760, 'missiles', NULL)")
    con.execute("""INSERT INTO bills VALUES (119,'S','77','Better Missiles Act',
        'Armed Forces and National Security','defense','A1','Sen. Alpha',current_date,
        'Passed Senate','https://congress.gov/x',NULL)""")
    assert "Bills moving in its industry" in render(con, "AAA", today)
    assert "<script" not in body.lower()
    assert "AAA" in pick(con, today, ["ZZZ"]) and pick(con, today, ["ZZZ"])[0] == "ZZZ"
    empty = render(duckdb.connect(), "QQQQ", today)
    assert "No price history" in empty and "No buys" in empty
    print("stockpage selftest ok")


if __name__ == "__main__":
    selftest()
