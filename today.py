"""The dashboard's front page, built for a quick read instead of a wall of tables.

Layout, top to bottom:
  1. "This week in 30 seconds": three numbers and one plain sentence.
  2. Ideas worth researching: up to 8 cards, best first. Each card has the
     ticker, the company, a coloured health badge, and short "why it's here"
     lines in plain English.
  3. Everything else, folded away: full lists of congress buys, insider
     buying, contracts and fund moves, one line each. Click to open.
  4. "How to read this": a short glossary, also folded.

Ranking: more independent kinds of support (Congress, insiders, a top fund,
a big contract) ranks higher; a Solid company gets a bump and a Shaky one a
penalty, since this is for long-term holdings; a ⚑ committee link and bigger
dollar amounts break ties.
"""

import datetime
import html

import committees
import companyintel
import contracts
import digest
import funds
import health
import leaders
import activists
import bills
import whitehouse
import profiles
import signals

E = html.escape
HEALTH_HELP = {
    "Solid": "Healthy: sales steady or growing, profitable, reasonable debt.",
    "Mixed": "One warning sign. Read why before buying.",
    "Shaky": "Two or more warning signs. Be careful.",
    "Unknown": "No company financials (often an ETF, fund or foreign company).",
}

CSS = """
.hero{position:relative;overflow:hidden;background:radial-gradient(120% 140% at 0% 0%,
color-mix(in srgb,var(--acc) 16%,var(--card)) 0%,var(--card) 55%);border:1px solid var(--line);
border-radius:22px;padding:26px 26px 22px;margin:4px 0 8px;box-shadow:var(--shadow)}
.hero .eyebrow{color:var(--acc);font-weight:700;font-size:12px;letter-spacing:1.2px;text-transform:uppercase}
.hero p{margin:6px 0 0;font-size:16.5px;max-width:860px}
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:20px}
.stat{background:color-mix(in srgb,var(--bg) 55%,transparent);border:1px solid var(--line);border-radius:16px;padding:14px 16px}
.stat b{display:block;font-size:34px;line-height:1.05;letter-spacing:-1px;font-variant-numeric:tabular-nums}
.stat span{color:var(--mut);font-size:13px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:18px;
display:flex;flex-direction:column;gap:12px;box-shadow:var(--shadow);transition:transform .15s,border-color .15s}
.card:hover{transform:translateY(-2px);border-color:color-mix(in srgb,var(--acc) 45%,var(--line))}
.card .top{display:flex;justify-content:space-between;align-items:flex-start;gap:10px}
.card .tkr{font-size:26px;font-weight:800;letter-spacing:-.5px;color:var(--fg);text-decoration:none}
.card .co{color:var(--mut);font-size:13px}
.px{text-align:right}.px b{display:block;font-size:18px;font-variant-numeric:tabular-nums}
.px span{font-size:13px;font-weight:700}.up{color:var(--up)}.down{color:var(--down)}
.spark{width:100%;height:54px;display:block}
.conv{font-size:13px;color:var(--mut)}.conv b{color:var(--fg);font-size:15px}
.meter{height:6px;border-radius:9px;background:var(--chip);margin-top:6px;overflow:hidden}
.meter i{display:block;height:100%;border-radius:9px;background:var(--mut)}
.c-High .meter i{background:var(--up)}.c-Medium .meter i{background:var(--warn)}
.stats4{display:grid;grid-template-columns:repeat(4,1fr);gap:6px}
.stats4 div{background:var(--card2);border-radius:12px;padding:8px 10px}
.stats4 span{display:block;color:var(--mut);font-size:11px;text-transform:uppercase;letter-spacing:.5px}
.stats4 b{font-size:14px;font-variant-numeric:tabular-nums}
.why{margin:0;padding-left:18px;font-size:14px}.why li{margin:3px 0}
.foot{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:auto}
.badge{font-size:12px;font-weight:700;border-radius:999px;padding:3px 10px;white-space:nowrap}
.b-Solid{background:color-mix(in srgb,var(--up) 18%,transparent);color:var(--up)}
.b-Mixed{background:color-mix(in srgb,var(--warn) 18%,transparent);color:var(--warn)}
.b-Shaky{background:color-mix(in srgb,var(--down) 18%,transparent);color:var(--down)}
.b-Unknown,.b-none{background:var(--chip);color:var(--mut)}
.chips{display:flex;gap:6px;flex-wrap:wrap}
.chip{font-size:12px;font-weight:600;background:var(--chip);border-radius:8px;padding:3px 9px}
.chip.flag{color:var(--warn)}.chip.buzz{color:var(--ins)}
.hint{color:var(--mut);font-size:13px}
.who{position:relative;border-bottom:1px dotted var(--mut);cursor:help;outline:none}
.bio{display:none;position:absolute;left:0;top:calc(100% + 8px);z-index:30;width:300px;
background:var(--card2);color:var(--fg);border:1px solid var(--line);border-radius:14px;padding:12px 14px;
box-shadow:0 12px 40px rgba(0,0,0,.35);font-size:13px;font-weight:400;line-height:1.45;text-align:left}
.bio b{display:block;font-size:14px;margin-bottom:4px}.bio .bl{display:block;color:var(--mut);margin-top:3px}
.who:hover .bio,.who:focus .bio,.who:focus-within .bio{display:block}
details{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:12px 18px;margin:10px 0}
details summary{cursor:pointer;font-weight:700;font-size:15px;list-style:none}
details summary::-webkit-details-marker{display:none}
details summary:before{content:'+';display:inline-block;width:18px;color:var(--acc);font-weight:800}
details[open] summary:before{content:'–'}
details summary span{color:var(--mut);font-weight:500}
details ul{padding-left:18px;margin:12px 0 4px}details li{margin:6px 0}
.empty{color:var(--mut);padding:10px 0}
@media (max-width:640px){main{padding:16px 12px}.hero{padding:20px 16px;border-radius:18px}
h1{font-size:24px}.stats{grid-template-columns:1fr 1fr 1fr;gap:8px}.stat{padding:10px}
.stat b{font-size:24px}.stat span{font-size:11px}.cards{grid-template-columns:1fr}
.stats4{grid-template-columns:repeat(2,1fr)}.bio{width:min(300px,80vw)}
.bar{gap:4px;padding:10px 12px}.bar a.nav{padding:6px 9px;font-size:13px}.upd{display:none}
.logo{margin-right:6px}}
"""


def _clean(v):
    return None if v is None or (isinstance(v, float) and v != v) else v


def _days_ago(d, today):
    if d is None:
        return ""
    d = d.date() if hasattr(d, "date") else d
    n = (today - d).days
    return "today" if n <= 0 else "yesterday" if n == 1 else f"{n} days ago"


OWNER = {"spouse": "their spouse", "dependent child": "their child", "joint": "jointly",
         "child": "their child"}


def friendly_who(r):
    """'Rep. April McClain Delaney (their child)' instead of 'April ... (H), dependent child'."""
    name = f"{r.first_name or ''} {str(r.last_name or '').strip(' ,.')}".strip()
    who = f"{'Sen.' if r.chamber == 'S' else 'Rep.'} {name}"
    o = str(r.owner or "").strip().lower()
    if o and o not in ("self", "none", "nan"):
        who += f" ({OWNER.get(o, o)})"
    return who


def who_html(label, lines, title=None):
    """A name with a hover/tap card. Everything is escaped here, so callers
    can drop the result straight into HTML."""
    body = "".join(f"<span class='bl'>{E(x)}</span>" for x in lines)
    return (f"<span class='who' tabindex='0'>{E(label)}<span class='bio' role='tooltip'>"
            f"<b>{E(title or label)}</b>{body}</span></span>")


def member_html(con, r, label):
    m = profiles.member(con, r.last_name, r.first_name, r.chamber)
    return who_html(label, profiles.member_lines(m), m.get("name"))


def insider_html(con, name, cik=None):
    return who_html(name, profiles.insider_lines(profiles.insider(con, cik, name)))


def official_html(con, slug, name):
    o = whitehouse.official(con, slug)
    return who_html(name, whitehouse.official_lines(o), name)


def conviction(i):
    """0-100 score and the plain-English breakdown behind it.

      up to 40  how many different kinds of buyers (10 each: Congress,
                insiders, top fund, contract)
      up to 20  how much money went in (log scale: ~$15k = 4, $1M = 16, $10M+ = 20)
      up to 20  company health (Solid 20, Mixed 10, unknown 8, Shaky 0)
      up to 25  how informed the buyers look: a cluster of insiders 10, an
                executive buying 5, a Congress member on an overseeing committee 5,
                an activist investor taking a 5%+ stake 5, the buyer sponsoring a
                moving bill in that industry 5
      up to 10  the best buyer's past record on the leaderboard (beat SPY by 5%+
                on average and more than half the time 10, ahead at all 5)
    Capped at 100.
    """
    import math
    breadth = min(len(i["kinds"]), 4) * 10
    size = 0 if i["dollars"] <= 0 else max(0, min(20, round(4 * math.log10(i["dollars"] / 1000))))
    hp = {"Solid": 20, "Mixed": 10, "Shaky": 0}.get(i.get("health"), 8)
    informed = (10 * i["cluster"] + 5 * i["officer"] + 5 * bool(i["flag"])
                + 5 * ("Activist" in i["kinds"]) + 5 * bool(i.get("bill")))
    track, track_why = i.get("track") or (0, None)
    why = [f"{len(i['kinds'])} kind(s) of buyer: +{breadth}",
           f"about {digest._money(i['dollars'])} put in: +{size}" if i["dollars"] else "no dollar amount: +0",
           f"health {i.get('health') or 'not checked'}: +{hp}",
           f"informed-buyer signs: +{informed}"]
    if track_why:
        why.append(f"buyer's {track_why}: +{track}")
    return min(100, breadth + size + hp + informed + track), why


def collect(con, today, days=7, records=None):
    """Everything the page shows, as plain Python data (testable).
    records: {buyer key: {n, avg, beat}} from leaders.json, for the track-record points."""
    records = records or {}
    start = today - datetime.timedelta(days=days)
    month = today - datetime.timedelta(days=30)
    ideas = {}

    def idea(t):
        return ideas.setdefault(t, {"tkr": t, "kinds": set(), "why": [], "score": 0.0,
                                    "flag": "", "company": None, "dollars": 0.0,
                                    "cluster": False, "officer": False, "buyers": set()})

    data = {"congress": [], "clusters": [], "big": [], "contracts": [], "funds": [],
            "whitehouse": [], "activists": [], "wh_summary": [], "stale": 0}

    if signals.has_table(con, "senate_trades"):
        cb = signals.congress_buys(con, start, today)
        data["stale"] = int((cb.lag_days > digest.MAX_LAG_DAYS).sum())
        cb = cb[~(cb.lag_days > digest.MAX_LAG_DAYS)]
        for (t, ln), g in cb.groupby(["tkr", "last_name"], sort=False):
            r = g.iloc[0]
            who = friendly_who(r)
            who_h = member_html(con, r, who)
            flag = committees.committee_flag(con, t, r.last_name, r.first_name, r.chamber)
            bill, bill_url = bills.bill_flag(con, t, r.last_name, r.first_name, r.chamber)
            lo, hi = g.amount_low.sum(), g.amount_high.sum()
            row = {"tkr": t, "who": who, "who_h": who_h, "amount": digest._bracket(lo, hi), "lo": float(lo),
                   "n": len(g), "lag": _clean(g.lag_days.min()), "filed": g.event_date.max(),
                   "flag": flag, "bill": bill, "bill_url": bill_url}
            data["congress"].append(row)
            i = idea(t)
            i["kinds"].add("Congress")
            i["buyers"].add(leaders.key_congress(r.chamber, r.last_name, r.first_name))
            i["score"] += min(lo / 50_000, 3)
            i["dollars"] += float(lo)
            late = f", {int(row['lag'])} days after the trade" if row["lag"] is not None else ""
            n = f" ({len(g)} purchases)" if len(g) > 1 else ""
            o = str(r.owner or "").strip().lower()
            fam = (" <span class='fam' title='Traded in a spouse or child account'>family</span>"
                   if o and o not in ("self", "none", "nan", "joint") else "")
            i["why"].append(f"{who_h}{fam} bought {E(row['amount'])}{n}, disclosed"
                            f" {_days_ago(row['filed'], today)}{late}.")
            if flag:
                i["flag"] = flag
                i["score"] += 1
            if bill:
                i["bill"] = bill
                i["score"] += 1
                i["why"].append(f"⚖ {who_h} sponsored a related bill that's moving in Congress: "
                                f"<a href='{E(bill_url)}'>{E(bill[:90])}</a>.")
        data["congress"].sort(key=lambda r: -r["lo"])

    if signals.has_table(con, "insider_trades"):
        for r in signals.insider_clusters(con, start, today).itertuples():
            buyers = ", ".join(insider_html(con, b.strip()) for b in (r.buyers or "").split(";")
                               if b.strip())
            data["clusters"].append({"tkr": r.tkr, "company": r.issuer_name, "buyers_h": buyers,
                                     "n": int(r.insiders), "value": float(r.total_value),
                                     "officer": bool(r.any_officer), "when": r.event_date})
            i = idea(r.tkr)
            i["kinds"].add("Insiders")
            i["company"] = i["company"] or r.issuer_name
            i["score"] += 2
            i["dollars"] += float(r.total_value)
            i["cluster"] = True
            i["buyers"].update(leaders.key_insider(b) for b in (r.buyers or "").split(";") if b.strip())
            i["officer"] = i["officer"] or bool(r.any_officer)
            exe = ", including an executive" if r.any_officer else ""
            i["why"].append(f"{int(r.insiders)} company insiders bought "
                            f"{E(digest._money(r.total_value))} of stock with their own "
                            f"money{exe}: {buyers}.")
        for r in signals.big_insider_buys(con, start, today).head(25).itertuples():
            who_h = insider_html(con, r.owner_name)
            data["big"].append({"tkr": r.tkr, "company": r.issuer_name, "who": r.owner_name,
                                "who_h": who_h,
                                "role": (r.owner_relationship or "insider").lower().replace(",", ", "),
                                "value": float(r.total_value), "when": r.event_date})
            i = idea(r.tkr)
            if "Insiders" not in i["kinds"]:
                i["kinds"].add("Insiders")
                i["buyers"].add(leaders.key_insider(r.owner_name))
                i["company"] = i["company"] or r.issuer_name
                i["score"] += 1
                i["dollars"] += float(r.total_value)
                i["officer"] = i["officer"] or "OFFICER" in str(r.owner_relationship or "")
                role = (r.owner_relationship or "insider").lower().split(",")[0]
                i["why"].append(f"{who_h} ({E(role)}) bought "
                                f"{E(digest._money(r.total_value))} of stock.")

    # officials report up to 45 days after a trade, so look back a little further
    for slug, name, title, t, traded, filed, lo, hi, n in whitehouse.stock_buys(
            con, today - datetime.timedelta(days=max(days, 30)), today):
        who_h = official_html(con, slug, name)
        amount = digest._bracket(lo, hi)
        data["whitehouse"].append({"tkr": t, "who_h": who_h, "title": title, "amount": amount,
                                   "n": n, "filed": filed})
        i = idea(t)
        i["kinds"].add(whitehouse.group(title))
        i["buyers"].add(leaders.key_official(slug))
        i["score"] += min((lo or 0) / 50_000, 3)
        i["dollars"] += float(lo or 0)
        times = f" ({n} purchases)" if n > 1 else ""
        i["why"].append(f"{who_h} ({E(title)}) bought {E(amount)}{times}, reported "
                        f"{_days_ago(filed, today)}.")
    data["wh_summary"] = whitehouse.summary(con, today - datetime.timedelta(days=60), today)

    for t, company, investor, form, filed, url in activists.recent(con, start, today):
        data["activists"].append({"tkr": t, "company": company, "investor": investor,
                                  "filed": filed, "url": url})
        i = idea(t)
        i["company"] = i["company"] or company
        if "Activist" not in i["kinds"]:
            i["kinds"].add("Activist")
            i["score"] += 2
            i["why"].append(f"{E(investor)} took a 5%+ stake and filed as an activist, meaning "
                            f"they may push for changes ({_days_ago(filed, today)}).")

    c = contracts.recent(con, start, today)
    if c is not None:
        for r in c.head(20).itertuples():
            data["contracts"].append({"tkr": r.tkr, "amount": float(r.amount or 0),
                                      "agency": r.agency, "what": r.description})
            i = idea(r.tkr)
            i["kinds"].add("Contract")
            i["score"] += 1
            i["why"].append(E(f"Won a {digest._money(r.amount)} government contract "
                              f"from the {r.agency}."))

    f = funds.changes(con, since=start)
    if f is not None:
        for r in f.itertuples():
            data["funds"].append({"fund": r.fund, "change": r.change,
                                  "tkr": r.tkr, "issuer": r.issuer})

    # 30-day context: a ticker that also shows up elsewhere this month
    for t, why in digest.agreement(con, today, 30).items():
        i = idea(t)
        for w in why:
            kind = w.split(":")[0]
            kind = {"Funds": "Top fund"}.get(kind, kind)
            if kind not in i["kinds"]:
                i["kinds"].add(kind)
                i["score"] += 1
                i["why"].append(E(w.split(": ", 1)[1].capitalize() + " (last 30 days)."))
    for t, names in funds.holders(con, list(ideas)).items():
        i = ideas[t]
        if "Top fund" not in i["kinds"]:
            i["kinds"].add("Top fund")
            i["score"] += 1
            i["why"].append(E(f"Owned by {', '.join(names)}."))

    hl = health.lookup(con, list(ideas))
    for t, i in ideas.items():
        h = hl.get(t)
        i["health"] = h["verdict"] if h else None
        i["health_why"] = h["reasons"] if h else "Not checked yet."
        i["company"] = (h or {}).get("company") or i["company"]
        i["score"] += 3 * len(i["kinds"]) + {"Solid": 2, "Shaky": -3}.get(i["health"], 0)
        i["profile"] = profiles.company(con, t)
        i["intel"] = companyintel.intel(con, t)
        i["company"] = i["company"] or i["profile"].get("name")
        i["track"] = max((leaders.record_points(records.get(k)) for k in i["buyers"]),
                         key=lambda p: p[0], default=(0, None))
        i["conviction"], i["conviction_why"] = conviction(i)
    data["ideas"] = sorted(ideas.values(), key=lambda i: (-i["conviction"], -i["score"]))
    return data


def _badge(verdict):
    v = verdict or "none"
    label = verdict or "Not checked"
    return (f"<span class='badge b-{E(v)}' title='{E(HEALTH_HELP.get(verdict, ''))}'>"
            f"{E(label)}</span>")


def _spark(vals, up):
    if not vals or len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1
    w, h = 300, 54
    pts = " ".join(f"{k * w / (len(vals) - 1):.1f},{h - 4 - (v - lo) / rng * (h - 8):.1f}"
                   for k, v in enumerate(vals))
    col = "var(--up)" if up else "var(--down)"
    return (f"<svg class='spark' viewBox='0 0 {w} {h}' preserveAspectRatio='none' aria-hidden='true'>"
            f"<polyline points='{pts}' fill='none' stroke='{col}' stroke-width='2' "
            f"vector-effect='non-scaling-stroke' stroke-linejoin='round'/></svg>")


def _pct(x):
    return "—" if x is None else f"{'+' if x >= 0 else ''}{x * 100:.0f}%"


def conviction_label(score):
    return "High" if score >= 70 else "Medium" if score >= 45 else "Low"


def _stock_href(tkr):
    import stockpage  # stockpage imports this module
    return stockpage.href(tkr)


def _card(i):
    pr = i.get("profile") or {}
    chips = "".join(f"<span class='chip'>{E(k)}</span>" for k in sorted(i["kinds"]))
    if i.get("bill"):
        chips += (f"<span class='chip flag' title='The buyer sponsored a related bill: "
                  f"{E(i['bill'][:120])}'>⚖ Bill</span>")
    if i["flag"]:
        chips += (f"<span class='chip flag' title='Their committee oversees this industry'>"
                  f"⚑ {E(i['flag'].split(' (')[0])}</span>")
    ci = i.get("intel") or {}
    if ci.get("buzz") and ci["buzz"] >= 2:
        chips += (f"<span class='chip buzz' title='Wikipedia views vs its usual week'>"
                  f"Buzz {ci['buzz']:.1f}×</span>")
    if ci.get("buybacks"):
        chips += (f"<span class='chip' title='Spent {E(profiles.money(ci['buybacks']))} buying back "
                  f"its own shares last year'>Buying back stock</span>")
    if (ci.get("contracts") or 0) >= 100e6:
        chips += (f"<span class='chip' title='{E(profiles.money(ci['contracts']))} in federal "
                  f"contracts over 12 months'>Gov't customer</span>")
    why = "".join(f"<li>{w}</li>" for w in i["why"][:4])  # already escaped HTML
    price = ""
    if pr.get("price"):
        up = (pr.get("chg_1y") or 0) >= 0
        price = (f"<div class='px'><b>${pr['price']:,.2f}</b>"
                 f"<span class='{'up' if up else 'down'}'>{_pct(pr.get('chg_1y'))} 1y</span></div>")
    stats = [("Sales", profiles.money(pr.get("revenue")) or "—"), ("Growth", _pct(pr.get("growth"))),
             ("Profit", profiles.money(pr.get("net_income")) or "—"),
             ("P/E", f"{pr['pe']:.0f}" if pr.get("pe") else "—")]
    stat_html = "".join(f"<div><span>{k}</span><b>{E(v)}</b></div>" for k, v in stats)
    sc = i["conviction"]
    lvl = conviction_label(sc)
    tip = "".join(f"<span class='bl'>{E(x)}</span>" for x in i["conviction_why"])
    return (f"<article class='card'>"
            f"<div class='top'><div><a class='tkr' href='{E(_stock_href(i['tkr']))}'>{E(i['tkr'])}</a>"
            f"<div class='co'>{E(i['company'] or '')}"
            f"{' · ' + E(pr['sector']) if pr.get('sector') else ''}</div></div>{price}</div>"
            f"{_spark(pr.get('spark'), (pr.get('chg_1y') or 0) >= 0)}"
            f"<div class='conv c-{lvl}'><span class='who' tabindex='0'>Conviction "
            f"<b>{sc}</b>/100 · {lvl}<span class='bio' role='tooltip'><b>How this score adds up</b>"
            f"{tip}</span></span><div class='meter'><i style='width:{sc}%'></i></div></div>"
            f"<div class='stats4'>{stat_html}</div>"
            f"<div class='chips'>{chips}</div><ul class='why'>{why}</ul>"
            f"<div class='foot'>{_badge(i['health'])}<span class='hint'>{E(i['health_why'])}</span></div>"
            f"</article>")


def _group_wh(rows):
    out = {}
    for name, title, kind, tx, n, lo, hi, last in rows:
        out.setdefault((name, title), []).append((kind, tx, n, lo, hi))
    return list(out.items())


SHOW = {"bond": "other bond"}


def wh_line(name, title, rows):
    """'Donald J. Trump (President): 52 muni bond purchases ($5.2M–$13M), ...'"""
    verb = {"Purchase": "purchase", "Sale": "sale", "Exchange": "exchange"}
    bits = [f"{n} {SHOW.get(kind, kind)} {verb.get(tx, 'trade')}{'' if n == 1 else 's'} "
            f"({digest._bracket(lo or 0, hi or 0)})"
            for kind, tx, n, lo, hi in rows[:4]]
    return f"<b>{E(name)}</b> ({E(title)}): " + E("; ".join(bits))


def _section(title, items, empty="Nothing this week."):
    body = "".join(f"<li>{x}</li>" for x in items) if items else f"<div class='empty'>{empty}</div>"
    return (f"<details><summary>{E(title)} <span>({len(items)})</span></summary>"
            f"{'<ul>' + body + '</ul>' if items else body}</details>")


def render(data, today, days=7):
    ideas = data["ideas"]
    multi = [i for i in ideas if len(i["kinds"]) >= 2]
    top = ideas[:8]
    n_cong, n_ins = len(data["congress"]), len(data["clusters"]) + len(data["big"])
    if top:
        lead = top[0]
        summary = (f"The strongest lead right now is <b>{E(lead['tkr'])}</b>"
                   f"{' (' + E(lead['company']) + ')' if lead['company'] else ''}. "
                   f"{lead['why'][0] if lead['why'] else ''}")
    else:
        summary = "A quiet week: no new buys worth flagging."
    period = "This week" if days <= 7 else "The last %d weeks" % (days // 7)
    out = [f"<div class='hero'><div class='eyebrow'>{period}</div><h1>The 30-second version</h1>",
           f"<p>{summary}</p>",
           "<div class='stats'>",
           f"<div class='stat'><b>{n_cong}</b><span>Congress buys of $15k+</span></div>",
           f"<div class='stat'><b>{n_ins}</b><span>notable insider buys</span></div>",
           f"<div class='stat'><b>{len(multi)}</b><span>stocks with 2+ kinds of support</span></div>",
           "</div></div>",
           "<h2>Ideas worth researching</h2>",
           "<p class='hint'>Ranked by conviction: how many different kinds of people are "
           "buying, how much money, how healthy the company is, and how informed the buyers "
           "look. Hover a name for who they are. A lead to look into, not a buy signal.</p>"]
    out.append("<div class='cards'>" + "".join(_card(i) for i in top) + "</div>" if top else
               "<div class='empty'>No ideas this week. That's normal; check back tomorrow.</div>")

    out.append("<h2>Everything else</h2>")
    def cong_line(r):
        n = f" in {r['n']} purchases" if r["n"] > 1 else ""
        late = f" ({int(r['lag'])} days after the trade)" if r["lag"] is not None else ""
        flag = f" · ⚑ {E(r['flag'])}" if r["flag"] else ""
        return (f"<b>{E(r['tkr'])}</b>: {r['who_h']} bought {E(r['amount'])}{n}, disclosed "
                f"{E(_days_ago(r['filed'], today))}{late}{flag}")
    out.append(_section("Congress buys", [cong_line(r) for r in data["congress"]]))
    out.append(_section("Insider buying", [
        f"<b>{E(r['tkr'])}</b>{' (' + E(r['company']) + ')' if r['company'] else ''}: "
        f"{r['n']} insiders bought {E(digest._money(r['value']))} together ({r['buyers_h']})"
        for r in data["clusters"]] + [
        f"<b>{E(r['tkr'])}</b>{' (' + E(r['company']) + ')' if r['company'] else ''}: "
        f"{r['who_h']} ({E(r['role'])}) bought {E(digest._money(r['value']))}" for r in data["big"]]))
    out.append(_section("White House, cabinet and Fed stock buys", [
        f"<b>{E(r['tkr'])}</b>: {r['who_h']} ({E(r['title'])}) bought {E(r['amount'])}"
        f"{' in ' + str(r['n']) + ' purchases' if r['n'] > 1 else ''}, reported "
        f"{E(_days_ago(r['filed'], today))}" for r in data["whitehouse"]],
        "No stock buys reported by tracked officials lately."))
    if data["wh_summary"]:
        out.append(_section("White House, cabinet and Fed: everything they reported (60 days)",
                            [wh_line(n, t, rows) for (n, t), rows in _group_wh(data["wh_summary"])]))
    out.append(_section("Activist investors taking 5%+ stakes", [
        f"<b>{E(r['tkr'])}</b>{' (' + E(r['company']) + ')' if r['company'] else ''}: "
        f"{E(r['investor'])} filed a 13D {E(_days_ago(r['filed'], today))} "
        f"(<a href='{E(r['url'])}'>filing</a>)" for r in data["activists"]],
        "No new activist stakes this week."))
    out.append(_section("Big new government contracts", [
        f"<b>{E(r['tkr'])}</b>: {E(digest._money(r['amount']))} from the {E(r['agency'] or '')}"
        for r in data["contracts"]]))
    label = {"new": "started buying", "added": "bought more of", "sold": "sold all of"}
    out.append(_section("What top investors changed (quarterly reports)", [
        f"<b>{E(r['fund'])}</b> {label.get(r['change'], r['change'])} "
        f"<b>{E(r['tkr'] or r['issuer'] or '')}</b>" for r in data["funds"]],
        "No new quarterly reports this week."))
    if data["stale"]:
        out.append(f"<p class='hint'>Left out {data['stale']} Congress buys disclosed more than "
                   f"{digest.MAX_LAG_DAYS} days after the trade, which is too old to act on.</p>")

    out.append("<h2>How to read this</h2><details><summary>Quick guide</summary><ul>"
               "<li><b>Congress</b>: a member of Congress or their family disclosed buying the "
               "stock. They only report a dollar range, up to 45 days late.</li>"
               "<li><b>Insiders</b>: executives or directors bought their own company's stock "
               "on the open market. Insiders sell for many reasons but buy for one.</li>"
               "<li><b>Top fund</b>: a proven long-term investor like Berkshire owns or just "
               "added it (from quarterly 13F reports, up to 45 days old).</li>"
               "<li><b>Contract</b>: the company just started a big government contract.</li>"
               "<li><b>White House</b>: the President, Vice President or a cabinet-level "
               "official reported buying the stock (OGE Form 278-T, in dollar ranges, up to 45 "
               "days late). Most of what they report is bonds, which are listed separately.</li>"
               "<li><b>⚖ Bill</b>: the member who bought it sponsored a bill in that industry "
               "that's moving through Congress (from Congress.gov).</li>"
               "<li><b>Fed</b>: a Federal Reserve governor reported buying it, on the same form. "
               "Fed rules have banned individual stocks since 2022, so this should be rare.</li>"
               "<li><b>Activist</b>: an investor bought 5%+ of the company and filed a 13D, "
               "meaning they may push for changes such as a sale or new board members.</li>"
               "<li><b>⚑</b>: the member sits on a committee that oversees that industry.</li>"
               "<li><b>Conviction</b>: 0 to 100. Up to 40 for how many kinds of buyers, 20 for "
               "how much money, 20 for company health, 25 for signs the buyers know something "
               "(a cluster of insiders, an executive, a committee link, an activist), and 10 "
               "for a buyer with a strong record on the leaderboard. 70+ is High.</li>"
               "<li><b>Dotted names</b>: hover or tap for who they are and their trading record.</li>"
               + "".join(f"<li>{_badge(k)} {E(v)}</li>" for k, v in HEALTH_HELP.items()) +
               "<li>Check the <a href='backtest.html'>Did it work?</a> tab to see whether "
               "each kind of signal has actually beaten an index fund.</li></ul></details>")
    return "".join(out)


def verdict(s):
    """(label, css class, sentence) for one backtest summary row."""
    for h, words in (("12m", "a year"), ("6m", "six months")):
        n = s.get(f"n_{h}") or 0
        if n >= 30:
            avg, med, beat = s[f"avg_excess_{h}"], s[f"median_excess_{h}"], s[f"beat_spy_{h}"]
            better = lambda x: f"{abs(x) * 100:.1f}% {'better' if x >= 0 else 'worse'}"
            sentence = (f"Buying every one and holding for {words}, the typical result was "
                        f"{better(med)} than an S&P 500 index fund (average {better(avg)}). "
                        f"It beat the fund {beat * 100:.0f}% of the time, across {n} examples.")
            if avg > 0 and med > 0 and beat > 0.55:
                return "Has beaten the market", "Solid", sentence
            if avg < 0 and med < 0:
                return "Did worse than the market", "Shaky", sentence
            return "No clear edge", "Mixed", sentence
    return ("Not enough data yet", "none",
            f"Only {s.get('n_6m') or 0} examples have six months of price history so far.")


def render_backtest(bt):
    out = ["<div class='hero'><h1>Did it work?</h1><p>For each kind of signal: if you had "
           "bought every one the day after it was disclosed and held it, would you have done "
           "better than simply owning an S&P 500 index fund? Based on filings from "
           f"{E(bt['start'])} to {E(bt['end'])}. Ignores trading costs.</p></div>",
           "<div class='cards'>"]
    for s in bt["summary"]:
        label, cls, sentence = verdict(s)
        out.append(f"<div class='card'><div class='top'><b>{E(s['signal'])}</b></div>"
                   f"<div><span class='badge b-{cls}'>{E(label)}</span></div>"
                   f"<div>{E(sentence)}</div></div>")
    out.append("</div><p class='hint' style='margin-top:18px'>If nothing has clearly beaten "
               "the market, the honest takeaway is to keep most of a Roth in index funds and "
               "use these lists as ideas to research. Results update every Sunday.</p>")
    return "".join(out)


def selftest():
    import duckdb
    con = duckdb.connect()
    signals.make_fixture(con)
    committees.load_fixture(con)
    con.execute("INSERT INTO committee_members VALUES ('A1','S','A',NULL,'Alpha','SSAS',"
                "'Senate Committee on Armed Services','2025-01-01')")
    funds.load_fixture(con)
    contracts.ensure_table(con)
    con.execute("INSERT INTO big_contracts VALUES ('k1','AAA INC','AAA','Department of Defense',"
                "'Navy','2025-05-18','2030-03-20',250e6,'MISSILES','2025-05-19')")
    health.ensure_table(con)
    con.execute("INSERT INTO health (tkr, checked_on, company, verdict, reasons) VALUES "
                "('AAA','2025-05-19','Aaa Inc','Solid','sales up 12%, profitable')")
    today = datetime.date(2025, 5, 20)
    d = collect(con, today, days=90)
    top = d["ideas"][0]
    assert top["tkr"] == "AAA", [(i["tkr"], i["score"]) for i in d["ideas"]]
    assert {"Congress", "Contract", "Top fund"} <= top["kinds"], top
    assert top["flag"].startswith("Armed Services"), top
    assert top["health"] == "Solid" and top["company"] == "Aaa Inc"
    page = render(d, today, 90)
    assert "The last 12 weeks" in page and "30-second version" in page and "strongest lead right now is <b>AAA</b>" in page
    assert "Sen. A Alpha<span class='bio'" in page, page
    assert "Conviction <b>" in page and top["conviction"] >= 60, top["conviction"]
    assert "b-Solid" in page and "⚑ Armed Services" in page
    rec = {leaders.key_congress("S", "Alpha", "A"): {"n": 8, "avg": 0.09, "beat": 0.7}}
    boosted = collect(con, today, days=90, records=rec)["ideas"][0]
    assert boosted["conviction"] == min(100, top["conviction"] + 10), boosted["conviction_why"]
    assert any("strong past record" in w for w in boosted["conviction_why"])
    assert "<details><summary>Congress buys" in page
    assert "<script" not in page.lower()
    assert ">Ann<span class='bio'" not in page or "Officer at" in page
    whitehouse.ensure_tables(con)
    con.execute("""INSERT INTO wh_trades VALUES ('d1',1,'vance-jd','JD Vance','Vice President',
        'Aaa Inc (AAA)','stock','AAA','Purchase','2025-05-01','2025-05-15',15001,50000,false,'l'),
        ('d1',2,'vance-jd','JD Vance','Vice President','OHIO ST 5% DUE 2030','muni bond',NULL,
        'Purchase','2025-05-01','2025-05-15',50001,100000,false,'l')""")
    activists.ensure_tables(con)
    con.execute("""INSERT INTO stakes VALUES ('a1','13D','2025-05-12','AAA','Aaa Inc',
        'Pershing Road Capital','https://sec/x')""")
    d2 = collect(con, today, days=90)
    aaa = next(i for i in d2["ideas"] if i["tkr"] == "AAA")
    assert {"White House", "Activist"} <= aaa["kinds"], aaa["kinds"]
    assert leaders.key_official("vance-jd") in aaa["buyers"]
    p2 = render(d2, today, 90)
    assert "JD Vance<span class='bio'" in p2 and "Vice President" in p2
    assert "1 muni bond purchase ($50k–$100k)" in p2, p2[p2.find("everything they"):][:600]
    assert "Pershing Road Capital filed a 13D" in p2
    companyintel.ensure_tables(con)
    companyintel._save(con, "AAA", "buzz", 900, "Aaa Inc|3.2")
    companyintel._save(con, "AAA", "contracts", 4.2e9, None)
    p3 = render(collect(con, today, days=90), today, 90)
    assert "Buzz 3.2×" in p3 and "Gov't customer" in p3, p3[:300]
    bills.ensure_table(con)
    con.execute("""INSERT INTO bills VALUES (119,'S','77','Better Missiles Act',
        'Armed Forces and National Security','defense','A1','Sen. Alpha',current_date,
        'Passed Senate','https://congress.gov/x',NULL)""")
    con.execute("INSERT OR REPLACE INTO members VALUES ('A1','S','A',NULL,'Alpha','Republican','TX',2019,'Armed Services')")
    p4 = render(collect(con, today, days=90), today, 90)
    assert "⚖ Bill" in p4 and "Better Missiles Act" in p4
    quiet = render(collect(duckdb.connect(), today), today)
    assert "quiet week" in quiet and "No ideas this week" in quiet
    win = {"signal": "X", "n_12m": 80, "avg_excess_12m": .05, "median_excess_12m": .02,
           "beat_spy_12m": .6}
    assert verdict(win)[0] == "Has beaten the market"
    assert verdict(dict(win, avg_excess_12m=-.03, median_excess_12m=-.04))[0] == "Did worse than the market"
    assert verdict(dict(win, beat_spy_12m=.5))[0] == "No clear edge"
    assert verdict({"signal": "Y", "n_12m": 3, "n_6m": 10})[0] == "Not enough data yet"
    assert "2.0% better" in verdict(win)[2]
    bt = render_backtest({"start": "2020-01-01", "end": "2025-01-01", "summary": [win]})
    assert "Has beaten the market" in bt
    print("today selftest ok")


if __name__ == "__main__":
    selftest()
