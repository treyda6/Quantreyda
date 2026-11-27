"""The home page: a morning briefing across everything the site tracks.

Insider and Congress buying is one input among several here, not the
headline. Top to bottom:

  1. Today's briefing: the date, one plain sentence, and three numbers.
  2. Markets & economy and Headlines, side by side.
  3. Ideas worth researching (the top three; the rest are on ideas.html).
  4. What the crowd expects: the busiest prediction-market questions.
  5. Every section of the site as a tile, so nothing is more than a tap away.

Other sections plug in without editing this file's logic: a section whose
module defines `home_teaser(con, today) -> HTML` gets that snippet on its
tile (or in its home block). A planned section whose page isn't built yet
gets a short "coming soon" page so no link is ever dead.

    write(con, today, out, data, page, updated)   # called last by dashboard.build
    py home.py --selftest
"""

import datetime
import html
import importlib
import os
import sys

import markets
import today as front

E = html.escape

# key, href, title, one-line blurb, modules that may define home_teaser, planned
SECTIONS = (
    ("economy", "economy.html", "Markets & economy",
     "Stocks, rates, oil, gold, crypto and the economic numbers that move them.", ("economy",), True),
    ("calendar", "calendar.html", "Calendar", "Earnings, economic releases and IPOs coming up.",
     ("calendars",), True),
    ("news", "news.html", "News", "The market headlines worth knowing, updated hourly.",
     ("headlines",), True),
    ("ideas", "ideas.html", "Ideas", "Stocks with several kinds of smart money behind them, "
     "with a health check on each company.", (), False),
    ("screener", "screener.html", "Small caps", "Small companies with the numbers to grow big.",
     ("screener",), True),
    ("themes", "themes.html", "Future themes", "Long-term trends and the companies positioned for them.",
     ("themes",), True),
    ("funds", "funds.html", "Funds", "What proven long-term investors own and just changed.",
     ("funds", "sections"), False),
    ("odds", "markets.html", "Odds", "What prediction markets expect from the Fed, Washington "
     "and big companies.", (), False),
    ("congress", "congress.html", "Congress", "Stock trades members of Congress disclosed.", (), False),
    ("insiders", "insiders.html", "Insiders", "Executives buying and selling their own company's stock.",
     (), False),
    ("trades", "trades.html", "Search all trades", "Every Congress, White House and insider trade "
     "from the last 180 days.", (), False),
    ("leaders", "leaders.html", "Leaderboard", "Whose buys have actually beaten the market.", (), False),
    ("backtest", "backtest.html", "Did it work?", "Whether copying each kind of buyer ever beat "
     "an index fund.", (), False),
)
BY_KEY = {s[0]: s for s in SECTIONS}
MARK = "<!-- home: coming soon -->"

CSS = """
.brief .stat b{font-size:26px}.brief .stat b a{color:inherit;text-decoration:none}
.duo{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:18px}
.blk{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:18px;box-shadow:var(--shadow);
display:flex;flex-direction:column;gap:10px;min-width:0}
.blk h2,.hh h2{margin:0;font-size:18px}.hh{display:flex;justify-content:space-between;align-items:baseline;gap:10px;margin:34px 0 12px}
.blk .hh{margin:0}.go{font-size:13px;font-weight:600;text-decoration:none;white-space:nowrap}
.soon{font-size:11px;font-weight:700;letter-spacing:.4px;text-transform:uppercase;color:var(--mut);
background:var(--chip);border-radius:999px;padding:2px 9px;white-space:nowrap}
.hxs{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px}
.hx{display:flex;flex-direction:column;gap:6px;background:var(--card);border:1px solid var(--line);border-radius:16px;
padding:14px 16px;color:var(--fg);text-decoration:none;transition:border-color .15s,transform .15s}
.hx:hover{border-color:color-mix(in srgb,var(--acc) 45%,var(--line));transform:translateY(-1px)}
.hx .th{display:flex;justify-content:space-between;align-items:center;gap:8px}.hx b{font-size:15px}
.hx small{color:var(--mut);font-size:13px;line-height:1.4}.hx .tz{font-size:13px}
.hx.later{opacity:.75}
@media(max-width:760px){.duo{grid-template-columns:1fr}.brief .stats{grid-template-columns:1fr}
.brief .stat{display:flex;justify-content:space-between;align-items:baseline;gap:10px}.brief .stat b{font-size:22px}}
"""


def teaser(con, today, key):
    """HTML from the first of the section's modules that defines home_teaser, or ''."""
    for name in BY_KEY[key][4]:
        try:
            fn = getattr(importlib.import_module(name), "home_teaser", None)
        except ImportError:
            continue
        if fn:
            try:
                return fn(con, today) or ""
            except Exception as e:  # a broken teaser shouldn't take the home page down
                print(f"home teaser {name} skipped ({type(e).__name__}: {e})")
    return ""


def missing(out):
    """Planned sections whose page hasn't been built in `out` yet."""
    def built(href):  # a placeholder left from an earlier build doesn't count
        try:
            with open(os.path.join(out, href), encoding="utf-8") as f:
                return MARK not in f.read()
        except OSError:
            return False
    return {k for k, href, *_, planned in SECTIONS if planned and not built(href)}


def _block(con, today, soon, key, empty):
    _, href, title, blurb, _, _ = BY_KEY[key]
    body = teaser(con, today, key)
    ready = key not in soon
    link = (f"<a class='go' href='{href}'>Open {E(title.split(' &')[0])} →</a>" if ready
            else "<span class='soon'>Coming soon</span>")
    return (f"<section class='blk'><div class='hh'><h2>{E(title)}</h2>{link}</div>"
            + (body or f"<p class='hint'>{E(empty if ready else blurb)}</p>") + "</section>")


def _tile(con, today, soon, s):
    key, href, title, blurb, _, _ = s
    ready = key not in soon
    tz = teaser(con, today, key) if key not in ("economy", "news") else ""
    return (f"<a class='hx{'' if ready else ' later'}' href='{href}'><div class='th'><b>{E(title)}</b>"
            f"{'' if ready else '<span class=soon>Soon</span>'}</div><small>{E(blurb)}</small>"
            + (f"<div class='tz'>{tz}</div>" if tz else "") + "</a>")


def render(con, today, data=None, soon=()):
    """The home page body. `data` is today.collect()'s result (or None if it failed);
    `soon` is the planned sections that aren't built yet."""
    ideas = (data or {}).get("ideas") or []
    n_buys = len((data or {}).get("congress") or []) + len((data or {}).get("clusters") or []) \
        + len((data or {}).get("big") or [])
    odds = markets.top(con, 3)
    if ideas:
        lead = ideas[0]
        line = (f"Strongest research lead: <b>{E(lead['tkr'])}</b>"
                f"{' (' + E(lead['company']) + ')' if lead.get('company') else ''}.")
    else:
        line = "No new research leads today."
    if odds:
        line += (f" The busiest question in the betting markets: {E(odds[0][2])} "
                 f"({odds[0][4] * 100:.0f}% {E(odds[0][3])}).")
    stats = [
        (f"<a href='{front._stock_href(ideas[0]['tkr'])}'>{E(ideas[0]['tkr'])}</a>" if ideas else "—",
         "top idea right now"),
        (str(len(ideas)), "stocks worth researching"),
        (str(n_buys), "notable Congress and insider buys"),
    ]
    out_html = [
        "<div class='hero brief'>",
        f"<div class='eyebrow'>{today:%A, %B} {today.day}</div><h1>Your market briefing</h1>",
        f"<p>{line}</p><div class='stats'>",
        "".join(f"<div class='stat'><b>{v}</b><span>{E(k)}</span></div>" for v, k in stats),
        "</div></div>",
        "<div class='duo'>",
        _block(con, today, soon, "economy", "Market numbers arrive with the next update."),
        _block(con, today, soon, "news", "Headlines arrive with the next update."),
        "</div>",
        "<div class='hh'><h2>Ideas worth researching</h2>"
        "<a class='go' href='ideas.html'>All ideas →</a></div>",
    ]
    if ideas:
        out_html.append("<div class='cards'>" + "".join(front._card(i) for i in ideas[:3]) + "</div>")
    else:
        out_html.append("<div class='empty'>No ideas this week. That's normal; check back tomorrow.</div>")
    if odds:
        out_html.append("<div class='hh'><h2>What the crowd expects</h2>"
                        "<a class='go' href='markets.html'>All odds →</a></div>"
                        "<div class='pms'>" + "".join(markets.card(r) for r in odds) + "</div>")
    out_html.append("<div class='hh'><h2>Explore</h2></div><div class='hxs'>"
                    + "".join(_tile(con, today, soon, s) for s in SECTIONS if s[0] not in ("economy", "news"))
                    + "</div>")
    return "".join(out_html)


def placeholder(key):
    _, _, title, blurb, _, _ = BY_KEY[key]
    return (f"{MARK}<div class='hero'><div class='eyebrow'>Coming soon</div><h1>{E(title)}</h1>"
            f"<p>{E(blurb)}</p><p class='hint'>This section is being built and will appear "
            "here after an upcoming update. <a href='index.html'>Back to the briefing</a>.</p></div>")


def write(con, today, out, data, page, updated):
    """Fill any planned page that wasn't built, then write index.html. Call after every other page."""
    soon = missing(out)
    for key in soon:
        _, href, title, *_ = BY_KEY[key]
        with open(os.path.join(out, href), "w", encoding="utf-8") as f:
            f.write(page(title, key, placeholder(key), updated))
    try:
        body = render(con, today, data, soon)
    except Exception as e:  # never leave the site without a front door
        import traceback
        traceback.print_exc()
        body = (f"<h1>Your market briefing</h1><p>Today's briefing couldn't be built this run "
                f"({E(type(e).__name__)}). The other sections are current.</p>")
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as f:
        f.write(page("Home", "home", body, updated))


def selftest():
    import tempfile
    import duckdb
    con = duckdb.connect()
    body = render(con, datetime.date(2026, 11, 27))
    assert "Your market briefing" in body and "Friday, November 27" in body
    assert "No new research leads" in body and "What the crowd expects" not in body
    for s in SECTIONS:
        if s[0] not in ("economy", "news"):
            assert f"href='{s[1]}'" in body, s
    out = tempfile.mkdtemp()
    open(os.path.join(out, "news.html"), "w").close()  # a section that's already built is left alone
    write(con, datetime.date(2026, 11, 27), out, None, lambda t, k, b, u: f"<title>{t}</title>{b}", "now")
    assert open(os.path.join(out, "news.html")).read() == ""
    assert "Coming soon" in open(os.path.join(out, "economy.html")).read()
    idx = open(os.path.join(out, "index.html")).read()
    assert "Open News" in idx and "Coming soon" in idx
    assert "economy" in missing(out) and "news" not in missing(out), "old placeholders still count as missing"
    print("home selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
