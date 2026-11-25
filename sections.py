"""The Congress, Insiders and Funds pages: phone-first overviews.

Each page has the same building blocks, all plain HTML and CSS (no scripts):

  faces        a scrolling row of the most active people this month
  treemap      the biggest recent trades as tiles sized by dollars, green for
               buys and red for sales; switch between single trades and each
               stock's total, and between 7, 30 and 90 days
  recent       the latest trades, with "Show all trades"
  strategy     the "copy them" portfolio from the weekly backtest: its value
               over time against the S&P 500, yearly growth, last year, and a
               risk-adjusted score (Sharpe ratio)

    page_congress(con, rows, today, bt, pages)   -> HTML body
    page_insiders(con, rows, today, bt, pages)
    page_funds(con, today, pages)
"""

import datetime
import html
import math

import pictures

E = html.escape
PERIODS = (7, 30, 90)


def _money(x):
    a = abs(x or 0)
    return (f"${a / 1e9:.1f}B" if a >= 1e9 else f"${a / 1e6:.1f}M" if a >= 1e6
            else f"${a / 1e3:.0f}k" if a >= 1e3 else f"${a:.0f}")


def _pct(x, plus=True):
    return "—" if x is None else f"{x * 100:{'+' if plus else ''}.1f}%"


def _stock_href(tkr, pages, root=""):
    return (f"{root}s/{E(tkr)}.html" if tkr in pages
            else f"{root}trades.html?q={E(tkr)}")


def dollars(r):
    """A trade's size in dollars: the middle of a disclosed range, or the
    exact value for insiders."""
    lo, hi = r.get("lo") or 0, r.get("hi")
    return (lo + hi) / 2 if hi and hi == hi else lo


# ------------------------------------------------------------------ treemap

def squarify(values, x=0.0, y=0.0, w=100.0, h=100.0):
    """Squarified treemap: [(x, y, w, h)] in the same order as `values`
    (sorted largest first). Plain percentages, so it scales with the page."""
    vals = [v for v in values if v > 0]
    total = sum(vals)
    if not vals:
        return []
    scale = w * h / total
    areas = [v * scale for v in vals]
    out = []

    def worst(row, side):
        s = sum(row)
        return max(max(side * side * a / (s * s), (s * s) / (side * side * a)) for a in row)

    i = 0
    while i < len(areas):
        side = min(w, h)
        row = [areas[i]]
        i += 1
        while i < len(areas) and worst(row + [areas[i]], side) <= worst(row, side):
            row.append(areas[i])
            i += 1
        s = sum(row)
        if w >= h:  # lay the row down the left side
            rw = s / h
            yy = y
            for a in row:
                out.append((x, yy, rw, a / rw))
                yy += a / rw
            x, w = x + rw, w - rw
        else:      # along the top
            rh = s / w
            xx = x
            for a in row:
                out.append((xx, y, a / rh, rh))
                xx += a / rh
            y, h = y + rh, h - rh
    return out


def tiles_largest(rows, n=24):
    big = sorted(rows, key=dollars, reverse=True)[:n]
    return [{"tkr": r["tkr"], "sub": r["who"].split(" (")[0], "v": dollars(r),
             "buy": 1.0 if r["side"] == "buy" else 0.0 if r["side"] == "sell" else .5,
             "tip": f"{r['tkr']}: {r['who']} {r['side']} {r['amt']} (filed {r['filed']})"}
            for r in big]


def tiles_volume(rows, n=24):
    agg = {}
    for r in rows:
        a = agg.setdefault(r["tkr"], {"b": 0.0, "s": 0.0, "n": 0})
        a["b" if r["side"] == "buy" else "s"] += dollars(r) if r["side"] in ("buy", "sell") else 0
        a["n"] += 1
    top = sorted(agg.items(), key=lambda kv: -(kv[1]["b"] + kv[1]["s"]))[:n]
    return [{"tkr": t, "sub": f"{a['n']} trade{'s' * (a['n'] != 1)}", "v": a["b"] + a["s"],
             "buy": a["b"] / (a["b"] + a["s"]) if a["b"] + a["s"] else .5,
             "tip": f"{t}: {_money(a['b'])} bought, {_money(a['s'])} sold in {a['n']} trades"}
            for t, a in top if a["b"] + a["s"] > 0]


def treemap_html(tiles, pages, root=""):
    if not tiles:
        return "<div class='tm-empty'>No trades in this period.</div>"
    # area grows a little slower than dollars (power 0.6) so one giant trade
    # doesn't squeeze everything else into slivers; order is still by size
    rects = squarify([t["v"] ** 0.6 for t in tiles])
    out = []
    for t, (x, y, w, h) in zip(tiles, rects):
        area = w * h
        big = ("xl" if area > 900 else "l" if area > 300 else "m" if area > 90
               else "s" if area > 25 and min(w, h) > 4 else "xs")
        mix = round(t["buy"] * 100)
        out.append(
            f"<a class='tile {big}' href='{_stock_href(t['tkr'], pages, root)}' title='{E(t['tip'])}' "
            f"style='left:{x:.3f}%;top:{y:.3f}%;width:{w:.3f}%;height:{h:.3f}%;"
            f"--mix:{mix}%'><b>{E(t['tkr'])}</b><span>{E(t['sub'])}</span></a>")
    return "<div class='tm'>" + "".join(out) + "</div>"


def treemap_block(rows, today, pages, key, root=""):
    """Two modes x three periods, switched with radio buttons (CSS only)."""
    radios = ("".join(f"<input type='radio' name='{key}m' id='{key}m{m}' class='r-{m}'{' checked' if m == 'L' else ''}>"
                      for m in "LV")
              + "".join(f"<input type='radio' name='{key}p' id='{key}p{p}' class='r-p{p}'{' checked' if p == 30 else ''}>"
                        for p in PERIODS))
    maps = []
    for p in PERIODS:
        since = str(today - datetime.timedelta(days=p))
        rs = [r for r in rows if r["filed"] >= since and r["side"] in ("buy", "sell")]
        maps.append(f"<div class='tmv L p{p}'>{treemap_html(tiles_largest(rs), pages, root)}</div>")
        maps.append(f"<div class='tmv V p{p}'>{treemap_html(tiles_volume(rs), pages, root)}</div>")
    seg = (f"<div class='seg'><label for='{key}mL'>Largest trades</label>"
           f"<label for='{key}mV'>Totals by stock</label></div>")
    per = ("<div class='per'>" + "".join(f"<label for='{key}p{p}'>{p}D</label>" for p in PERIODS) + "</div>")
    return (f"<section class='tmb'>{radios}{seg}<div class='sec-h'><h2>Largest recent transactions</h2>"
            f"{per}</div><div class='tms'>{''.join(maps)}</div>"
            "<div class='tm-key'><span>Sales</span><span>Purchases</span></div>"
            "<p class='hint'>Bigger tile = more money. Tap a tile for the stock.</p></section>")


# ------------------------------------------------------------------- pieces

def faces_row(people):
    """people: [(face_html, name, sub, href)]"""
    if not people:
        return ""
    return ("<div class='faces'>" + "".join(
        f"<a href='{E(h)}'>{f}<b>{E(n)}</b><small>{E(s)}</small></a>" for f, n, s, h in people)
        + "</div>")


def _short_date(d):
    try:
        return f"{datetime.date.fromisoformat(d[:10]):%b %d}"
    except ValueError:
        return E(d)


def recent_list(rows, n=10, more=""):
    items = []
    for r in rows[:n]:
        lg = (f"<span class='lg'><img src='{E(r['logo'])}' alt='' loading='lazy'></span>" if r.get("logo")
              else f"<span class='lg mono'>{E(r['tkr'][:1])}</span>")
        tint = {"R": " r", "D": " d"}.get(r.get("party"), "")
        face = (f"<span class='face{tint}'><img src='{E(r['face'])}' alt='' loading='lazy'></span>" if r.get("face")
                else f"<span class='face mono{tint}'>{E(r.get('ini') or '?')}</span>")
        tag = r["src"] + (f" · {r['party']}" if r.get("party") else "")
        side = {"buy": "Purchase", "sell": "Sale"}.get(r["side"], "Other")
        xs = "" if r.get("xs") is None else (f"<small class='{'up' if r['xs'] >= 0 else 'down'}'>"
                                             f"{_pct(r['xs'])} vs S&amp;P</small>")
        items.append(
            f"<li><div class='pic'>{lg}<div class='t'><b>{E(r['tkr'])}</b><small>{_short_date(r['filed'])}</small></div></div>"
            f"<div class='pic'>{face}<div class='t'>{E(r['who'].split(' (')[0])}<small>{E(tag)}</small></div></div>"
            f"<div class='amt {r['side']}'><b>{E(r['amt'])}</b><small>{side}</small>{xs}</div></li>")
    if not items:
        return "<p class='hint'>Nothing filed yet.</p>"
    return (f"<ul class='rt'>{''.join(items)}</ul>"
            + (f"<a class='more' href='{E(more)}'>Show all trades →</a>" if more else ""))


def line_chart(dates, nav, spy, w=640, h=200):
    lo = min(min(nav), min(spy)) * .97
    hi = max(max(nav), max(spy)) * 1.03
    n = len(nav)

    def pts(vals):
        return " ".join(f"{i / (n - 1) * w:.1f},{h - (v - lo) / (hi - lo) * h:.1f}" for i, v in enumerate(vals))
    area = f"0,{h} " + pts(nav) + f" {w},{h}"
    return (f"<svg class='sc' viewBox='0 0 {w} {h}' preserveAspectRatio='none' role='img' "
            f"aria-label='Strategy value against the S&amp;P 500'>"
            f"<polygon points='{area}' class='a'/><polyline points='{pts(spy)}' class='s'/>"
            f"<polyline points='{pts(nav)}' class='n'/></svg>"
            f"<div class='sc-x'><span>{E(dates[0][:7])}</span><span>{E(dates[-1][:7])}</span></div>")


def strategy_card(st):
    good = lambda x: "" if x is None else (" up" if x >= 0 else " down")
    sharpe = "—" if st.get("sharpe") is None else f"{st['sharpe']:.2f}"
    return (
        f"<section class='strat'><div class='sec-h'><div><h2>{E(st['label'])}</h2>"
        f"<div class='big{good(st['total'])}'>{_pct(st['total'])} since {E(st['start'][:4])}</div>"
        f"<small class='hint'>S&amp;P 500 over the same time: {_pct(st['spy_total'])}</small></div></div>"
        + line_chart(st["dates"], st["nav"], st["spy"])
        + "<div class='sc-key'><span><i class='n'></i>Copying them</span><span><i class='s'></i>S&amp;P 500</span></div>"
        f"<div class='stats'><div><span>Per year</span><b class='{good(st['cagr']).strip()}'>{_pct(st['cagr'])}</b></div>"
        f"<div><span>Last 12 months</span><b class='{good(st['one_year']).strip()}'>{_pct(st['one_year'])}</b>"
        f"<small>S&amp;P {_pct(st['spy_one_year'])}</small></div>"
        f"<div><span>Sharpe</span><b>{sharpe}</b></div></div>"
        f"<p class='hint'>{E(st['about'])} Bought the day after each disclosure, held a year, equal "
        "amounts, no costs or taxes. Sharpe compares the return with how bumpy the ride was: "
        "about 0.5 is typical for the stock market, higher is better.</p></section>")


def strategies_html(bt, groups):
    st = [s for s in (bt or {}).get("strategies", []) if s.get("group") in groups]
    if not st:
        return ("<section class='strat'><h2>Copying them, as a portfolio</h2><p class='hint'>The "
                "first results appear after Sunday's backtest.</p></section>")
    return "".join(strategy_card(s) for s in st)


# -------------------------------------------------------------------- pages

def _active(rows, n=12):
    count = {}
    for r in rows:
        k = r["who"].split(" (")[0]
        count.setdefault(k, [0, r])[0] += 1
    return [r for _, r in sorted(count.values(), key=lambda x: -x[0])][:n]


def page_congress(con, rows, today, bt, pages):
    cg = [r for r in rows if r["src"] in ("Senate", "House")]
    month = [r for r in cg if r["filed"] >= str(today - datetime.timedelta(days=30))]
    people = []
    for r in _active(month):
        tint = {"R": " r", "D": " d"}.get(r.get("party"), "")
        f = (f"<span class='face big{tint}'><img src='{E(r['face'])}' alt='' loading='lazy'></span>"
             if r.get("face") else f"<span class='face big mono{tint}'>{E(r.get('ini') or '?')}</span>")
        name = r["who"].split(" (")[0]
        people.append((f, name, f"{r['src']}" + (f" · {r['party']}" if r.get("party") else ""),
                       f"trades.html?q={name.split()[-1]}"))
    return ("<div class='sec-top'><h1>Congress trading</h1><p class='hint'>Most active this month</p></div>"
            + faces_row(people)
            + treemap_block(cg, today, pages, "c")
            + "<section><div class='sec-h'><h2>Recent trades</h2></div>"
            + recent_list(cg, 10, "trades.html?src=Congress") + "</section>"
            + strategies_html(bt, ("Every stock Congress bought", "Politicians with a winning record")))


def page_insiders(con, rows, today, bt, pages):
    ins = [r for r in rows if r["src"] == "Insider"]
    month = [r for r in ins if r["filed"] >= str(today - datetime.timedelta(days=30))]
    count = {}
    for r in month:
        count[r["tkr"]] = count.get(r["tkr"], 0) + 1
    people = [(pictures.logo_html(t, size="big"), t, f"{n} filings", _stock_href(t, pages))
              for t, n in sorted(count.items(), key=lambda kv: -kv[1])[:12]]
    return ("<div class='sec-top'><h1>Insider trading</h1><p class='hint'>Executives and directors "
            "buying or selling their own company's stock (SEC Form 4). Busiest companies this month:</p></div>"
            + faces_row(people)
            + treemap_block(ins, today, pages, "i")
            + "<section><div class='sec-h'><h2>Recent trades</h2></div>"
            + recent_list(ins, 10, "trades.html?src=Insider") + "</section>"
            + strategies_html(bt, ("Insiders buying together",)))


def page_funds(con, today, pages):
    import activists
    import funds
    mgr = funds.managers()
    moves = {}
    try:
        for cik, tkr, issuer, change, value, period in con.execute(
                """SELECT cik, tkr, issuer, change, value, period FROM fund_changes
                   WHERE change IN ('new', 'added') ORDER BY value DESC NULLS LAST""").fetchall():
            moves.setdefault(cik, []).append((tkr, issuer, change, period))
    except Exception:  # no 13Fs fetched yet
        pass
    cards = []
    for cik, (label, person) in mgr.items():
        src = pictures.face_src(pictures.fund_key(cik))
        pic = (f"<img src='{E(src)}' alt='' loading='lazy'>" if src
               else f"<span class='mono'>{E(pictures.initials(person or label))}</span>")
        mv = moves.get(cik, [])[:3]
        lines = "".join(
            f"<li>{pictures.logo_html(t) if t else ''}<span>{E(t or (i or '')[:18])}</span>"
            f"<small>{E(c)}</small></li>" for t, i, c, _ in mv)
        q = f"Quarter to {mv[0][3]}" if mv else "Latest quarter"
        cards.append(f"<div class='fund'><div class='ph'>{pic}<b>{E(person or label)}</b></div>"
                     f"<div class='fb'><small>{E(label)} · {E(str(q))}</small>"
                     + (f"<ul>{lines}</ul>" if lines else "<p class='hint'>No new buys last quarter.</p>")
                     + "</div></div>")
    filings = activists.recent_13f(con, 25)
    rows = "".join(f"<tr><td><a href='{E(u)}'>{E(f.title())}</a></td><td>{d:%b %d, %Y}</td></tr>"
                   for d, f, u in filings)
    return ("<div class='sec-top'><h1>Funds</h1><p class='hint'>What long-term investors bought last "
            "quarter, from their 13F filings (filed up to 45 days after each quarter ends). Edit "
            "<code>funds.txt</code> to follow others.</p></div>"
            + "<div class='funds'>" + "".join(cards) + "</div>"
            + "<section><div class='sec-h'><h2>Recently reported 13F filings</h2></div>"
            + (f"<div class='wrap'><table><thead><tr><th>Fund</th><th>Filed</th></tr></thead><tbody>{rows}"
               "</tbody></table></div>" if rows else "<p class='hint'>These fill in from the daily SEC index over the next week.</p>")
            + "</section>")


CSS = """
.sec-top h1{margin:6px 0 2px}.sec-top .hint{margin:0 0 10px}
.sec-h{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
.sec-h h2{margin:26px 0 12px}
.faces{display:flex;gap:16px;overflow-x:auto;padding:6px 2px 12px;scrollbar-width:none}
.faces::-webkit-scrollbar{display:none}
.faces a{flex:none;width:86px;text-align:center;color:var(--fg);text-decoration:none;display:flex;flex-direction:column;align-items:center;gap:6px}
.faces b{font-size:12.5px;font-weight:600;line-height:1.2}.faces small{color:var(--mut);font-size:11px}
.face.big,.lg.big{width:64px;height:64px;font-size:20px}
.tmb>input{position:absolute;opacity:0;pointer-events:none}
.seg{display:flex;background:var(--chip);border-radius:999px;padding:4px;margin-top:8px}
.seg label{flex:1;text-align:center;padding:9px;border-radius:999px;font-weight:600;font-size:14px;color:var(--mut);cursor:pointer}
.per{display:flex;gap:4px;background:var(--chip);border-radius:999px;padding:3px}
.per label{padding:5px 11px;border-radius:999px;font-size:13px;font-weight:700;color:var(--mut);cursor:pointer}
.tmb .r-L:checked~.seg label[for$=mL],.tmb .r-V:checked~.seg label[for$=mV],
.tmb .r-p7:checked~.sec-h label[for$=p7],.tmb .r-p30:checked~.sec-h label[for$=p30],
.tmb .r-p90:checked~.sec-h label[for$=p90]{background:var(--card);color:var(--fg);box-shadow:var(--shadow)}
.tmv{display:none}
.tmb .r-L:checked~.r-p7:checked~.tms .L.p7,.tmb .r-L:checked~.r-p30:checked~.tms .L.p30,
.tmb .r-L:checked~.r-p90:checked~.tms .L.p90,.tmb .r-V:checked~.r-p7:checked~.tms .V.p7,
.tmb .r-V:checked~.r-p30:checked~.tms .V.p30,.tmb .r-V:checked~.r-p90:checked~.tms .V.p90{display:block}
.tm{position:relative;width:100%;aspect-ratio:1/1;max-height:560px;border-radius:16px;overflow:hidden;background:var(--card)}
@media(min-width:700px){.tm{aspect-ratio:16/9}}
.tile{position:absolute;box-sizing:border-box;border:2px solid var(--bg);display:flex;flex-direction:column;
align-items:center;justify-content:center;text-align:center;overflow:hidden;color:#fff;text-decoration:none;padding:4px;
background:color-mix(in srgb,#1f9d63 var(--mix),#c8343b)}
.tile:hover{filter:brightness(1.12)}
.tile b{font-size:13px;line-height:1.1}.tile span{font-size:10px;opacity:.85;line-height:1.2;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tile.m b{font-size:17px}.tile.l b{font-size:24px}.tile.xl b{font-size:34px}
.tile.l span,.tile.xl span{font-size:13px}.tile.s span,.tile.xs span,.tile.xs b{display:none}
.tile.s b{font-size:11px}
.tm-key{display:flex;justify-content:space-between;margin-top:8px;padding:6px 12px;border-radius:10px;color:#fff;
font-size:13px;font-weight:600;background:linear-gradient(90deg,#c8343b,#8a4a3a 45%,#3d7a52 55%,#1f9d63)}
.tm-empty{padding:40px;text-align:center;color:var(--mut);background:var(--card);border-radius:16px}
.rt{list-style:none;margin:0;padding:0;background:var(--card);border:1px solid var(--line);border-radius:16px;overflow:hidden}
.rt li{display:grid;grid-template-columns:1fr 1.4fr auto;gap:10px;align-items:center;padding:10px 14px;border-bottom:1px solid var(--line)}
.rt li:last-child{border-bottom:0}.rt .t small,.rt .amt small{display:block;color:var(--mut);font-size:11.5px}
.rt .amt{text-align:right;border-left:3px solid var(--line);padding-left:10px}
.rt .amt.buy{border-color:var(--up)}.rt .amt.sell{border-color:var(--down)}
.rt .amt.buy small:first-of-type{color:var(--up)}.rt .amt.sell small:first-of-type{color:var(--down)}
.rt .amt small.up{color:var(--up)}.rt .amt small.down{color:var(--down)}
.more{display:block;text-align:center;padding:12px;margin-top:8px;border-radius:12px;background:var(--chip);color:var(--fg);font-weight:600;text-decoration:none}
.strat{margin-top:22px;background:var(--card);border:1px solid color-mix(in srgb,var(--acc) 40%,var(--line));border-radius:20px;padding:4px 18px 16px;box-shadow:var(--shadow)}
.strat .big{font-size:20px;font-weight:800}.strat .big.up{color:var(--up)}.strat .big.down{color:var(--down)}
.sc{width:100%;height:200px;display:block;margin-top:12px}
.sc .a{fill:color-mix(in srgb,var(--acc) 14%,transparent)}.sc .n{fill:none;stroke:var(--acc);stroke-width:2;vector-effect:non-scaling-stroke}
.sc .s{fill:none;stroke:var(--mut);stroke-width:1.5;stroke-dasharray:4 3;vector-effect:non-scaling-stroke}
.sc-x{display:flex;justify-content:space-between;color:var(--mut);font-size:12px}
.sc-key{display:flex;gap:16px;font-size:12.5px;color:var(--mut);margin:6px 0}
.sc-key i{display:inline-block;width:14px;height:3px;border-radius:2px;margin-right:6px;vertical-align:middle}
.sc-key i.n{background:var(--acc)}.sc-key i.s{background:var(--mut)}
.stats{display:grid;grid-template-columns:repeat(3,1fr);border:1px solid var(--line);border-radius:14px;margin:10px 0}
.stats div{padding:10px;text-align:center;border-left:1px solid var(--line)}.stats div:first-child{border-left:0}
.stats span{display:block;color:var(--mut);font-size:12px}.stats b{font-size:19px}.stats small{display:block;color:var(--mut);font-size:11px}
.stats b.up{color:var(--up)}.stats b.down{color:var(--down)}
.funds{display:flex;gap:14px;overflow-x:auto;padding:4px 2px 12px;scroll-snap-type:x mandatory}
.fund{flex:none;width:250px;scroll-snap-align:start;background:var(--card);border:1px solid var(--line);border-radius:18px;overflow:hidden}
.fund .ph{position:relative;height:220px;background:var(--chip);display:flex;align-items:center;justify-content:center}
.fund .ph img{width:100%;height:100%;object-fit:cover;object-position:50% 20%}
.fund .ph .mono{font-size:48px;font-weight:800;color:var(--mut)}
.fund .ph b{position:absolute;left:0;right:0;bottom:0;padding:30px 14px 10px;color:#fff;font-size:18px;
background:linear-gradient(transparent,rgba(0,0,0,.75))}
.fund .fb{padding:10px 14px 12px}.fund .fb small{color:var(--mut)}
.fund ul{list-style:none;margin:8px 0 0;padding:0}.fund li{display:flex;align-items:center;gap:8px;padding:4px 0}
.fund li .lg{width:26px;height:26px;font-size:11px;border-radius:8px}.fund li small{margin-left:auto;color:var(--up);font-weight:700}
@media(max-width:640px){.rt li{grid-template-columns:1fr 1.25fr auto;padding:10px}.rt .lg,.rt .face{width:30px;height:30px}}
"""


def selftest():
    rects = squarify([6, 6, 4, 3, 2, 2, 1])
    assert len(rects) == 7
    area = sum(w * h for _, _, w, h in rects)
    assert abs(area - 10000) < 1e-6, area
    assert all(0 <= x and 0 <= y and x + w <= 100.0001 and y + h <= 100.0001 for x, y, w, h in rects)
    assert abs(rects[0][2] * rects[0][3] / 10000 - 6 / 24) < 1e-9
    rows = [{"tkr": "AAA", "who": "Ann Alpha", "side": "buy", "lo": 15001, "hi": 50000, "amt": "$15k–$50k",
             "filed": "2026-11-20", "src": "Senate", "party": "R", "ini": "AA", "xs": .05},
            {"tkr": "AAA", "who": "Bo Beta", "side": "sell", "lo": 1001, "hi": 15000, "amt": "$1k–$15k",
             "filed": "2026-11-01", "src": "House", "party": "D", "ini": "BB", "xs": None},
            {"tkr": "ZZZ", "who": "Cy Gamma (insider)", "side": "sell", "lo": 2e6, "hi": None,
             "amt": "$2.0M", "filed": "2026-11-22", "src": "Insider", "ini": "CG", "xs": -.1}]
    assert dollars(rows[0]) == 32500.5 and dollars(rows[2]) == 2e6
    vol = tiles_volume(rows[:2])
    assert vol[0]["tkr"] == "AAA" and abs(vol[0]["buy"] - 32500.5 / (32500.5 + 8000.5)) < 1e-9
    today = datetime.date(2026, 11, 25)
    block = treemap_block(rows[:2], today, {"AAA"}, "c")
    assert block.count("class='tmv") == 6 and "href='s/AAA.html'" in block and "--mix:100%" in block
    assert "Largest trades" in block and "<script" not in block
    lst = recent_list(rows, more="trades.html?src=Congress")
    assert "Purchase" in lst and "Sale" in lst and "+5.0% vs S&amp;P" in lst and "Show all trades" in lst
    st = {"label": "Copy Congress", "group": "Every stock Congress bought", "about": "Every buy.",
          "dates": ["2020-01-02", "2026-11-20"], "nav": [1, 2.5], "spy": [1, 1.9], "total": 1.5,
          "spy_total": .9, "cagr": .145, "one_year": .03, "spy_one_year": .1, "sharpe": .71, "start": "2020-01-02"}
    card = strategies_html({"strategies": [st]}, ("Every stock Congress bought",))
    assert "Copy Congress" in card and "+150.0% since 2020" in card and "0.71" in card and "+14.5%" in card
    assert "first results" in strategies_html({}, ("x",))

    import duckdb
    con = duckdb.connect()
    page = page_congress(con, rows, today, {"strategies": [st]}, {"AAA"})
    assert "Congress trading" in page and "Ann Alpha" in page and "Cy Gamma" not in page
    assert "Insider trading" in page_insiders(con, rows, today, {}, set())
    assert "Funds" in page_funds(con, today, set())
    print("sections selftest ok")


if __name__ == "__main__":
    selftest()
