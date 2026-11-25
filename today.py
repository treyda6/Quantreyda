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
import contracts
import digest
import funds
import health
import signals

E = html.escape
HEALTH_HELP = {
    "Solid": "Healthy: sales steady or growing, profitable, reasonable debt.",
    "Mixed": "One warning sign. Read why before buying.",
    "Shaky": "Two or more warning signs. Be careful.",
    "Unknown": "No company financials (often an ETF, fund or foreign company).",
}

CSS = """
.hero{background:var(--chip);border-radius:16px;padding:20px 22px;margin:4px 0 26px}
.hero h1{margin:0 0 4px}.hero p{margin:6px 0 0;font-size:16px}
.stats{display:flex;gap:12px;flex-wrap:wrap;margin-top:16px}
.stat{background:var(--bg);border-radius:12px;padding:12px 16px;min-width:150px;flex:1}
.stat b{display:block;font-size:28px;line-height:1.1}.stat span{color:var(--mut);font-size:13px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));gap:14px}
.card{border:1px solid var(--line);border-radius:14px;padding:16px;display:flex;flex-direction:column;gap:8px}
.card .top{display:flex;justify-content:space-between;align-items:baseline;gap:10px}
.card .tkr{font-size:22px;font-weight:700;letter-spacing:.3px}
.card .co{color:var(--mut);font-size:13px;margin-top:-6px}
.card ul{margin:0;padding-left:18px}.card li{margin:2px 0}
.card .more{font-size:13px;margin-top:auto}
.badge{font-size:12px;font-weight:600;border-radius:999px;padding:3px 10px;white-space:nowrap}
.b-Solid{background:#dff3e4;color:#14532d}.b-Mixed{background:#fdf0d5;color:#7a4b00}
.b-Shaky{background:#fde2e1;color:#8a1c1c}.b-Unknown,.b-none{background:var(--chip);color:var(--mut)}
@media (prefers-color-scheme:dark){.b-Solid{background:#163d24;color:#a7e3b9}
.b-Mixed{background:#43320f;color:#f3d38b}.b-Shaky{background:#4a1d1d;color:#f5b1ae}}
.chips{display:flex;gap:6px;flex-wrap:wrap}
.chip{font-size:12px;background:var(--chip);border-radius:6px;padding:2px 8px}
.hint{color:var(--mut);font-size:13px}
details{border:1px solid var(--line);border-radius:12px;padding:10px 16px;margin:10px 0}
details summary{cursor:pointer;font-weight:600;font-size:15px}
details summary span{color:var(--mut);font-weight:400}
details ul{padding-left:18px;margin:10px 0 4px}details li{margin:4px 0}
.empty{color:var(--mut);padding:10px 0}
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


def collect(con, today, days=7):
    """Everything the page shows, as plain Python data (testable)."""
    start = today - datetime.timedelta(days=days)
    month = today - datetime.timedelta(days=30)
    ideas = {}

    def idea(t):
        return ideas.setdefault(t, {"tkr": t, "kinds": set(), "why": [], "score": 0.0,
                                    "flag": "", "company": None})

    data = {"congress": [], "clusters": [], "big": [], "contracts": [], "funds": [],
            "stale": 0}

    if signals.has_table(con, "senate_trades"):
        cb = signals.congress_buys(con, start, today)
        data["stale"] = int((cb.lag_days > digest.MAX_LAG_DAYS).sum())
        cb = cb[~(cb.lag_days > digest.MAX_LAG_DAYS)]
        for (t, ln), g in cb.groupby(["tkr", "last_name"], sort=False):
            r = g.iloc[0]
            who = friendly_who(r)
            flag = committees.committee_flag(con, t, r.last_name, r.first_name, r.chamber)
            lo, hi = g.amount_low.sum(), g.amount_high.sum()
            row = {"tkr": t, "who": who, "amount": digest._bracket(lo, hi), "lo": float(lo),
                   "n": len(g), "lag": _clean(g.lag_days.min()), "filed": g.event_date.max(),
                   "flag": flag}
            data["congress"].append(row)
            i = idea(t)
            i["kinds"].add("Congress")
            i["score"] += min(lo / 50_000, 3)
            late = f", {int(row['lag'])} days after the trade" if row["lag"] is not None else ""
            i["why"].append(f"{who} bought {row['amount']}"
                            f"{f' ({len(g)} purchases)' if len(g) > 1 else ''}, disclosed"
                            f" {_days_ago(row['filed'], today)}{late}.")
            if flag:
                i["flag"] = flag
                i["score"] += 1
        data["congress"].sort(key=lambda r: -r["lo"])

    if signals.has_table(con, "insider_trades"):
        for r in signals.insider_clusters(con, start, today).itertuples():
            data["clusters"].append({"tkr": r.tkr, "company": r.issuer_name,
                                     "n": int(r.insiders), "value": float(r.total_value),
                                     "officer": bool(r.any_officer), "when": r.event_date})
            i = idea(r.tkr)
            i["kinds"].add("Insiders")
            i["company"] = i["company"] or r.issuer_name
            i["score"] += 2
            i["why"].append(f"{int(r.insiders)} company insiders bought "
                            f"{digest._money(r.total_value)} of stock with their own money"
                            f"{', including an executive' if r.any_officer else ''}.")
        for r in signals.big_insider_buys(con, start, today).head(25).itertuples():
            data["big"].append({"tkr": r.tkr, "company": r.issuer_name, "who": r.owner_name,
                                "role": (r.owner_relationship or "insider").lower().replace(",", ", "),
                                "value": float(r.total_value), "when": r.event_date})
            i = idea(r.tkr)
            if "Insiders" not in i["kinds"]:
                i["kinds"].add("Insiders")
                i["company"] = i["company"] or r.issuer_name
                i["score"] += 1
                i["why"].append(f"An insider ({(r.owner_relationship or 'insider').lower().split(',')[0]})"
                                f" bought {digest._money(r.total_value)} of stock.")

    c = contracts.recent(con, start, today)
    if c is not None:
        for r in c.head(20).itertuples():
            data["contracts"].append({"tkr": r.tkr, "amount": float(r.amount or 0),
                                      "agency": r.agency, "what": r.description})
            i = idea(r.tkr)
            i["kinds"].add("Contract")
            i["score"] += 1
            i["why"].append(f"Won a {digest._money(r.amount)} government contract "
                            f"from the {r.agency}.")

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
                i["why"].append(w.split(": ", 1)[1].capitalize() + " (last 30 days).")
    for t, names in funds.holders(con, list(ideas)).items():
        i = ideas[t]
        if "Top fund" not in i["kinds"]:
            i["kinds"].add("Top fund")
            i["score"] += 1
            i["why"].append(f"Owned by {', '.join(names)}.")

    hl = health.lookup(con, list(ideas))
    for t, i in ideas.items():
        h = hl.get(t)
        i["health"] = h["verdict"] if h else None
        i["health_why"] = h["reasons"] if h else "Not checked yet."
        i["company"] = (h or {}).get("company") or i["company"]
        i["score"] += 3 * len(i["kinds"]) + {"Solid": 2, "Shaky": -3}.get(i["health"], 0)
    data["ideas"] = sorted(ideas.values(), key=lambda i: -i["score"])
    return data


def _badge(verdict):
    v = verdict or "none"
    label = verdict or "Not checked"
    return (f"<span class='badge b-{E(v)}' title='{E(HEALTH_HELP.get(verdict, ''))}'>"
            f"{E(label)}</span>")


def _card(i):
    chips = "".join(f"<span class='chip'>{E(k)}</span>" for k in sorted(i["kinds"]))
    if i["flag"]:
        chips += f"<span class='chip' title='Their committee oversees this industry'>⚑ {E(i['flag'].split(' (')[0])}</span>"
    why = "".join(f"<li>{E(w)}</li>" for w in i["why"][:4])
    return (f"<div class='card'><div class='top'><span class='tkr'>{E(i['tkr'])}</span>"
            f"{_badge(i['health'])}</div>"
            f"<div class='co'>{E(i['company'] or '')}</div>"
            f"<div class='chips'>{chips}</div><ul>{why}</ul>"
            f"<div class='hint'>Health: {E(i['health_why'])}</div>"
            f"<a class='more' href='trades.html?q={E(i['tkr'])}'>See every trade in {E(i['tkr'])} →</a>"
            f"</div>")


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
                   f"{E(lead['why'][0]) if lead['why'] else ''}")
    else:
        summary = "A quiet week: no new buys worth flagging."
    period = "This week" if days <= 7 else "The last %d weeks" % (days // 7)
    out = [f"<div class='hero'><h1>{period} in 30 seconds</h1>",
           f"<p>{summary}</p>",
           "<div class='stats'>",
           f"<div class='stat'><b>{n_cong}</b><span>Congress buys of $15k+</span></div>",
           f"<div class='stat'><b>{n_ins}</b><span>notable insider buys</span></div>",
           f"<div class='stat'><b>{len(multi)}</b><span>stocks with 2+ kinds of support</span></div>",
           "</div></div>",
           "<h2 style='border:0;padding:0;margin-top:0'>Ideas worth researching</h2>",
           "<p class='hint'>Ranked by how many different kinds of people are buying, with "
           "healthy companies first. A lead to look into, not a buy signal.</p>"]
    out.append("<div class='cards'>" + "".join(_card(i) for i in top) + "</div>" if top else
               "<div class='empty'>No ideas this week. That's normal; check back tomorrow.</div>")

    out.append("<h2>Everything else</h2>")
    def cong_line(r):
        n = f" in {r['n']} purchases" if r["n"] > 1 else ""
        late = f" ({int(r['lag'])} days after the trade)" if r["lag"] is not None else ""
        flag = f" · ⚑ {E(r['flag'])}" if r["flag"] else ""
        return (f"<b>{E(r['tkr'])}</b>: {E(r['who'])} bought {E(r['amount'])}{n}, disclosed "
                f"{E(_days_ago(r['filed'], today))}{late}{flag}")
    out.append(_section("Congress buys", [cong_line(r) for r in data["congress"]]))
    out.append(_section("Insider buying", [
        f"<b>{E(r['tkr'])}</b>{' (' + E(r['company']) + ')' if r['company'] else ''}: "
        f"{r['n']} insiders bought {E(digest._money(r['value']))} together" for r in data["clusters"]] + [
        f"<b>{E(r['tkr'])}</b>{' (' + E(r['company']) + ')' if r['company'] else ''}: "
        f"{E(r['who'])} ({E(r['role'])}) bought {E(digest._money(r['value']))}" for r in data["big"]]))
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
               "<li><b>⚑</b>: the member sits on a committee that oversees that industry.</li>"
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
    assert "The last 12 weeks in 30 seconds" in page and "strongest lead right now is <b>AAA</b>" in page
    assert "Sen. A Alpha bought" in page, page
    assert "b-Solid" in page and "⚑ Armed Services" in page
    assert "<details><summary>Congress buys" in page
    assert "<script" not in page.lower()
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
