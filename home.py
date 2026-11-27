"""The home page: an ops-center briefing across everything the site tracks.

Phone first. Top to bottom:

  1. A status line and a search bar (tickers jump to their page; anything
     else searches every trade, and it filters the live feed as you type).
  2. Readiness: the overall DEFCON-style level (5 calm .. 1 severe) and each
     area's level, from readiness.levels() (readiness.py).
  3. Trending now: the most-mentioned tickers today with their day move.
  4. Sitrep: one plain sentence and three numbers.
  5. Live feed: insider buys, Congress and White House trades, big
     prediction-market bets, analyst calls, activist stakes and headlines,
     newest first, with filter pills (All, Insiders, Congress, ...).
  6. Markets & economy, ideas worth researching, what the crowd expects,
     and every section of the site as a tile.

On desktop the feed takes the left column and the readouts sit beside it.
Every block is a <section class='blk'> with its own heading, so Mine's
"customize this page" can hide any of them.

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
import json
import os
import sys

import headlines
import links
import markets
import pictures
import stockpage
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
    ("mine", "mine.html", "Mine", "Your accounts, watchlist and alerts. Saved only on this device.",
     (), False),
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
    ("moves", "moves.html", "Market moves", "Sector heatmap, analyst upgrades and downgrades, and "
     "unusual options bets.", ("moves",), False),
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
.blk{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:18px;box-shadow:var(--shadow);
display:flex;flex-direction:column;gap:10px;min-width:0}
.blk h2,.hh h2{margin:0;font-size:18px}.hh{display:flex;justify-content:space-between;align-items:baseline;gap:10px;margin:34px 0 12px}
.blk .hh{margin:0}.go{font-size:13px;font-weight:600;text-decoration:none;white-space:nowrap}
.soon{font-size:11px;font-weight:700;letter-spacing:.4px;text-transform:uppercase;color:var(--mut);
background:var(--chip);border-radius:999px;padding:2px 9px;white-space:nowrap}
.hxs{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px}
.hx{position:relative;display:flex;flex-direction:column;gap:6px;background:var(--card);border:1px solid var(--line);border-radius:16px;
padding:14px 16px;color:var(--fg);text-decoration:none;transition:border-color .15s,transform .15s}
.hx:hover{border-color:color-mix(in srgb,var(--acc) 45%,var(--line));transform:translateY(-1px)}
.hx .th{display:flex;justify-content:space-between;align-items:center;gap:8px}.hx b{font-size:15px}
.hx small{color:var(--mut);font-size:13px;line-height:1.4}.hx .tz{font-size:13px}
.hx.later{opacity:.75}.hx .hl{color:var(--fg);text-decoration:none}
.hx .hl:after{content:'';position:absolute;inset:0;border-radius:16px}.hx .tz{position:relative;z-index:1}

/* ---- ops center (home only; everything is scoped under .ops) ---- */
.ops{--g:#3dff9a;--a:#ffb23e;--r:#ff4d5e;--o:#ff7a3d;--c:#5fd4ff;--glow:0 0 10px;
--panel:color-mix(in srgb,#06130d 72%,var(--card));--edge:color-mix(in srgb,var(--g) 26%,var(--line));
--mono:ui-monospace,"JetBrains Mono","SFMono-Regular",Menlo,Consolas,monospace;position:relative}
[data-theme=light] .ops{--g:#007a43;--a:#a35f00;--r:#c81e33;--o:#c2410c;--c:#0369a1;--glow:0 0 0;
--panel:var(--card);--edge:color-mix(in srgb,var(--g) 22%,var(--line))}
.ops:before{content:'';position:fixed;inset:0;pointer-events:none;z-index:0;
background:linear-gradient(color-mix(in srgb,var(--g) 5%,transparent) 1px,transparent 1px) 0 0/100% 28px,
linear-gradient(90deg,color-mix(in srgb,var(--g) 5%,transparent) 1px,transparent 1px) 0 0/28px 100%}
.ops:after{content:'';position:fixed;inset:0;pointer-events:none;z-index:50;opacity:.5;
background:repeating-linear-gradient(0deg,rgba(0,0,0,.16) 0 1px,transparent 1px 3px)}
[data-theme=light] .ops:after{display:none}
.ops>*{position:relative;z-index:1}
.ops .mono,.ops .blk h2,.ops .tag,.ops .ago,.ops .pill,.ops .tk,.ops .lvl,.ops .stl{font-family:var(--mono)}
.ops .blk{background:var(--panel);border:1px solid var(--edge);border-radius:6px;box-shadow:none;padding:14px 14px 16px;
position:relative}
.ops .blk:before{content:'';position:absolute;inset:-1px;border-radius:6px;pointer-events:none;
background:linear-gradient(var(--g),var(--g)) 0 0/12px 2px,linear-gradient(var(--g),var(--g)) 0 0/2px 12px,
linear-gradient(var(--g),var(--g)) 100% 100%/12px 2px,linear-gradient(var(--g),var(--g)) 100% 100%/2px 12px;
background-repeat:no-repeat;opacity:.8}
.ops .blk h2{font-size:12px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:var(--g);
text-shadow:var(--glow) color-mix(in srgb,var(--g) 55%,transparent)}
.ops .blk h2:before{content:'// ';opacity:.55}
.ops .go{font-family:var(--mono);font-size:12px;letter-spacing:.5px;text-transform:uppercase;color:var(--g)}
.ops-stat{display:flex;gap:10px;align-items:center;flex-wrap:wrap;font-family:var(--mono);font-size:11px;
letter-spacing:1.5px;text-transform:uppercase;color:var(--mut);margin:0 0 10px}
.ops-stat .on{color:var(--g)}.ops-stat .sep{opacity:.4}
.led{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--g);flex:none;
box-shadow:var(--glow) var(--g);animation:led 2.4s ease-in-out infinite}
@keyframes led{50%{opacity:.35}}
@media (prefers-reduced-motion:reduce){.ops .led,.ops .l1 .lvl-d{animation:none}}
.ops-search{position:relative;margin:0 0 14px}
.ops-search label{display:flex;align-items:center;gap:10px;background:var(--panel);border:1px solid var(--edge);
border-radius:6px;padding:0 14px;height:52px}
.ops-search label:focus-within{border-color:var(--g);box-shadow:0 0 0 1px var(--g),var(--glow) color-mix(in srgb,var(--g) 35%,transparent)}
.ops-search .pr{font-family:var(--mono);color:var(--g);font-weight:700}
.ops-search input{flex:1;min-width:0;background:none;border:0;outline:0;color:var(--fg);font:16px var(--mono);
caret-color:var(--g)}
.ops-search input::placeholder{color:var(--mut)}
.ops-sug{display:none;position:absolute;left:0;right:0;top:56px;z-index:40;background:var(--card2);
border:1px solid var(--edge);border-radius:6px;padding:4px;box-shadow:0 14px 40px rgba(0,0,0,.4)}
.ops-sug.open{display:block}
.ops-sug a{display:flex;gap:10px;align-items:center;padding:9px 10px;border-radius:4px;color:var(--fg);
text-decoration:none;font-size:14px}
.ops-sug a b{font-family:var(--mono);color:var(--g);min-width:58px}.ops-sug a span{color:var(--mut)}
.ops-sug a.sel,.ops-sug a:hover{background:color-mix(in srgb,var(--g) 12%,transparent)}
.ops-grid{display:flex;flex-direction:column;gap:14px}.ops-side{display:contents}
.ops-ready{order:1}.ops-trend{order:2}.ops-feed{order:3}.ops-sit{order:4}.ops-mkt{order:5}
.ops-ready .big{display:flex;align-items:center;gap:14px}
.lvl-d{font:800 64px/1 var(--mono);color:var(--lc);text-shadow:var(--glow) var(--lc),0 0 28px color-mix(in srgb,var(--lc) 45%,transparent);
min-width:52px;text-align:center}
.lvl-n{display:flex;flex-direction:column;gap:2px;min-width:0}
.lvl-n .mono{font-size:11px;letter-spacing:2px;color:var(--mut);text-transform:uppercase}
.lvl-n b{font:800 22px/1.1 var(--mono);letter-spacing:2px;color:var(--lc);text-transform:uppercase}
.ladder{display:grid;grid-template-columns:repeat(5,1fr);gap:4px;margin-top:2px}
.ladder i{height:26px;border-radius:3px;border:1px solid color-mix(in srgb,var(--c5) 55%,transparent);
display:flex;align-items:center;justify-content:center;font:700 11px var(--mono);font-style:normal;
color:color-mix(in srgb,var(--c5) 80%,var(--mut));background:color-mix(in srgb,var(--c5) 8%,transparent)}
.ladder i.on{background:var(--c5);color:#000;box-shadow:var(--glow) var(--c5);border-color:var(--c5)}
.l5{--lc:var(--g);--c5:var(--g)}.l4{--lc:var(--c);--c5:var(--c)}.l3{--lc:var(--a);--c5:var(--a)}
.l2{--lc:var(--o);--c5:var(--o)}.l1{--lc:var(--r);--c5:var(--r)}
.l1 .lvl-d{animation:led 1.2s steps(2) infinite}
.ops-ready .why{font-size:14px;margin:0;color:var(--fg);padding:0;list-style:none}
.subs{display:flex;flex-direction:column;gap:2px;margin:2px 0 0;padding:0;list-style:none}
.subs li{display:grid;grid-template-columns:10px minmax(0,1fr) auto;gap:4px 10px;align-items:center;
padding:8px 0;border-top:1px dashed color-mix(in srgb,var(--edge) 70%,transparent)}
.subs .led{background:var(--lc);box-shadow:var(--glow) var(--lc);animation:none}.subs .l1 .led,.subs li.l1 .led{animation:led 1.2s infinite}
.subs .nm{font:700 12px var(--mono);letter-spacing:.5px;text-transform:uppercase;min-width:0}
.subs .segs{display:flex;gap:2px}.subs .segs i{width:12px;height:10px;border-radius:1px;background:color-mix(in srgb,var(--lc) 15%,transparent)}
.subs .segs i.f{background:var(--lc);box-shadow:var(--glow) color-mix(in srgb,var(--lc) 60%,transparent)}
.subs .lvl{display:flex;gap:8px;align-items:center;justify-content:flex-end;font-size:11px;color:var(--lc);letter-spacing:1px;white-space:nowrap}
.subs .nm a{color:inherit;text-decoration:none}.subs .nm a:hover{color:var(--g)}
.subs .nd,.ops-ready.nd{--lc:var(--mut)}.subs .nd .led{opacity:.4}
.subs small{grid-column:2/4;color:var(--mut);font-size:12.5px;line-height:1.35}
.tks{display:flex;gap:8px;overflow-x:auto;scrollbar-width:none;margin:0 -2px;padding:2px}
.tks::-webkit-scrollbar{display:none}
.tk{display:flex;gap:8px;align-items:center;white-space:nowrap;border:1px solid var(--edge);border-radius:4px;
padding:8px 12px;text-decoration:none;color:var(--fg);font-size:14px;font-weight:700;
background:color-mix(in srgb,var(--g) 4%,transparent)}
.tk:hover{border-color:var(--g)}.tk .up{color:var(--g)}.tk .down{color:var(--r)}.tk .flat{color:var(--mut)}
.tk.dn{border-color:color-mix(in srgb,var(--r) 35%,var(--line));background:color-mix(in srgb,var(--r) 5%,transparent)}
.ops-sit p{margin:0;font-size:15px}
.ops-sit .stats{border:0;background:none;padding:0;box-shadow:none;display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin:4px 0 0}
.ops-sit .stat{background:none;border:1px solid var(--edge);border-radius:4px;padding:8px 10px}
.ops-sit .stat b{font:800 22px/1.1 var(--mono);color:var(--g);text-shadow:var(--glow) color-mix(in srgb,var(--g) 40%,transparent)}
.ops-sit .stat b a{color:inherit;text-decoration:none}
.ops-sit .stat span{font:10.5px/1.3 var(--mono);letter-spacing:.5px;text-transform:uppercase;color:var(--mut);display:block;margin-top:3px}
.pills{display:flex;gap:6px;overflow-x:auto;scrollbar-width:none;margin:0 -14px;padding:2px 14px}
.pills::-webkit-scrollbar{display:none}
.pill{flex:none;border:1px solid var(--edge);background:none;color:var(--mut);border-radius:3px;padding:7px 12px;
font-size:12px;font-weight:700;letter-spacing:1px;text-transform:uppercase;cursor:pointer}
.pill.on{background:var(--g);border-color:var(--g);color:#001b0d;box-shadow:var(--glow) color-mix(in srgb,var(--g) 50%,transparent)}
[data-theme=light] .pill.on,[data-theme=light] .ladder i.on{color:#fff}
.pill[hidden]{display:none}
.feed{list-style:none;margin:0 -14px;padding:0}
.feed>li{display:flex;gap:12px;padding:12px 14px;border-top:1px solid color-mix(in srgb,var(--edge) 60%,transparent)}
.feed>li[hidden]{display:none}
.feed .fb{flex:1;min-width:0;display:flex;flex-direction:column;gap:6px}
.feed .ft{font-size:15.5px;line-height:1.4;color:var(--fg);text-decoration:none;overflow-wrap:anywhere}
.feed a.ft:hover{text-decoration:underline}
.feed .fm{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}
.feed .tag{font-size:11px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:var(--kc,var(--g))}
.feed .tag:before{content:'';display:inline-block;width:6px;height:6px;margin-right:6px;vertical-align:1px;background:currentColor;border-radius:1px}
.feed .ago{font-size:11.5px;color:var(--mut);margin-left:auto}
.feed .xs{display:flex;gap:6px;flex-wrap:wrap}
.feed .x{font:700 12px var(--mono);color:var(--fg);background:var(--chip);border:1px solid var(--line);border-radius:3px;
padding:3px 8px;text-decoration:none}
.feed .x:hover{border-color:var(--g);color:var(--g)}
.feed .lg{flex:none;width:44px;height:44px}
.k-insiders{--kc:var(--g)}.k-congress{--kc:var(--c)}.k-wh{--kc:#c99bff}.k-bets{--kc:var(--a)}
.k-news{--kc:var(--mut)}.k-analysts{--kc:#8b9cff}.k-activists{--kc:var(--o)}
[data-theme=light] .k-wh{--kc:#7c3aed}[data-theme=light] .k-analysts{--kc:#4f5fe0}
.feed-more{font-family:var(--mono);font-size:12px;letter-spacing:1px;text-transform:uppercase;border:1px dashed var(--edge);
background:none;color:var(--g);border-radius:4px;padding:10px;cursor:pointer;width:100%}
.feed-none{padding:14px 0;color:var(--mut);font-family:var(--mono);font-size:13px}
.ops-mkt .movers a{border-radius:4px;border-color:var(--edge);background:none}
.ops .cards .card,.ops .pm,.ops .hx{border-radius:6px;border-color:var(--edge);background:var(--panel);box-shadow:none}
.ops-more{display:flex;flex-direction:column;gap:14px;margin-top:14px}
@media(min-width:1000px){
.ops-grid{display:grid;grid-template-columns:minmax(0,1fr) 400px;align-items:start;gap:16px}
.ops-side{display:flex;flex-direction:column;gap:16px;grid-column:2;grid-row:1;position:sticky;top:72px}
.ops-feed{grid-column:1;grid-row:1}.ops-more{margin-top:16px;gap:16px}}
@media(max-width:700px){.ops .lvl-d{font-size:54px}
.subs small{display:none}.subs li{padding:6px 0}.ops-stat{font-size:10px;letter-spacing:1px;gap:6px}
.ops-stat .bt{display:none}.ops-sit .stats{grid-template-columns:repeat(3,1fr)}
.ops-ready .big{gap:10px}.lvl-n b{font-size:19px}.feed .ft{font-size:15px}}
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


def _block(con, today, soon, key, empty, cls=""):
    _, href, title, blurb, _, _ = BY_KEY[key]
    body = teaser(con, today, key)
    ready = key not in soon
    link = (f"<a class='go' href='{href}'>Open {E(title.split(' &')[0])} →</a>" if ready
            else "<span class='soon'>Coming soon</span>")
    return (f"<section class='blk{' ' + cls if cls else ''}'><div class='hh'><h2>{E(title)}</h2>{link}</div>"
            + (body or f"<p class='hint'>{E(empty if ready else blurb)}</p>") + "</section>")


def _tile(con, today, soon, s):
    key, href, title, blurb, _, _ = s
    ready = key not in soon
    tz = teaser(con, today, key) if key not in ("economy", "news") else ""
    # a div, not one big link: a teaser can hold links of its own, and links can't nest
    return (f"<div class='hx{'' if ready else ' later'}'><div class='th'><a class='hl' href='{href}'>"
            f"<b>{E(title)}</b></a>{'' if ready else '<span class=soon>Soon</span>'}</div>"
            f"<small>{E(blurb)}</small>" + (f"<div class='tz'>{tz}</div>" if tz else "") + "</div>")


# ------------------------------------------------------------------ readiness

LEVEL_NAMES = {5: "Normal", 4: "Watch", 3: "Elevated", 2: "High", 1: "Severe"}  # as readiness.py
def readiness_levels(con, today):
    """readiness.levels(): the overall level and each area's, or None before the first readings."""
    try:
        import readiness
        got = readiness.levels(con, today)
        return got if got and (got.get("overall") or {}).get("level") else None
    except Exception as e:  # a broken board shouldn't take the home page down
        print(f"home readiness skipped ({type(e).__name__}: {e})")
        return None


def _lvl(x):
    try:
        return max(1, min(5, int(x)))
    except (TypeError, ValueError):
        return 5


def _segs(level):
    """Five bars, filled from the left as the threat rises (level 5 = one bar, level 1 = five)."""
    return "<span class='segs' aria-hidden='true'>" + "".join(
        f"<i class='{'f' if n < 6 - level else ''}'></i>" for n in range(5)) + "</span>"


def _sub_li(s):
    lv = s.get("level")
    if lv is None:  # a domain with no data yet
        right, cls = "<span class='lvl'>NO DATA</span>", "nd"
    else:
        lv = _lvl(lv)
        right = (f"<span class='lvl'>{_segs(lv)}<span>{lv} · "
                 f"{E((s.get('label') or LEVEL_NAMES[lv]).upper())}</span></span>")
        cls = f"l{lv}"
    name = E(s.get("name") or "")
    if s.get("href"):
        name = f"<a href='{E(s['href'])}'>{name}</a>"
    return (f"<li class='{cls}'><span class='led'></span><span class='nm'>{name}</span>{right}"
            + (f"<small>{E(s['why'])}</small>" if s.get("why") else "") + "</li>")


def _ready_html(r):
    if not r:
        return ("<section class='blk ops-ready nd'><div class='hh'><h2>Readiness</h2>"
                "<a class='go' href='readiness.html'>Board →</a></div>"
                "<div class='big'><span class='lvl-d' aria-hidden='true'>–</span><div class='lvl-n'>"
                "<span class='mono'>Overall level</span><b>Awaiting readings</b></div></div>"
                "<p class='why'>The first threat and preparedness check runs with the next daily update.</p></section>")
    ov = r.get("overall") or {}
    lv = _lvl(ov.get("level"))
    label = ov.get("label") or LEVEL_NAMES[lv]
    href = r.get("href") or ""
    link = f"<a class='go' href='{E(href)}'>Full board →</a>" if href else ""
    ladder = "".join(f"<i class='l{n}{' on' if n == lv else ''}'>{n}</i>" for n in (5, 4, 3, 2, 1))
    subs = "".join(_sub_li(s) for s in (r.get("subs") or []))
    return (f"<section class='blk ops-ready l{lv}'><div class='hh'><h2>Readiness</h2>{link}</div>"
            f"<div class='big'><span class='lvl-d' aria-hidden='true'>{lv}</span>"
            f"<div class='lvl-n'><span class='mono'>Overall level {lv} of 5</span><b>{E(label)}</b></div></div>"
            f"<div class='ladder' role='img' aria-label='Level {lv}: 5 is calm, 1 is severe'>{ladder}</div>"
            + (f"<p class='why'>{E(ov['why'])}</p>" if ov.get("why") else "")
            + (f"<ul class='subs'>{subs}</ul>" if subs else "") + "</section>")


# ------------------------------------------------------------------ live feed

KINDS = (  # filter pill key, pill label, tag shown on each item
    ("insiders", "Insiders", "Insider buying"),
    ("congress", "Congress", "Congress trade"),
    ("wh", "White House", "White House trade"),
    ("bets", "Big bets", "Prediction market"),
    ("analysts", "Analysts", "Analyst call"),
    ("activists", "Activists", "Activist stake"),
    ("news", "News", "News"),
)
TAG = {k: t for k, _, t in KINDS}


def _money(x):
    x = float(x or 0)
    return f"${x / 1e9:.1f}B" if x >= 1e9 else f"${x / 1e6:.1f}M" if x >= 1e6 else f"${x / 1e3:.0f}K"


def _when(d):
    """(iso string, date-only?) for a date or datetime."""
    if isinstance(d, datetime.datetime):
        return d.replace(microsecond=0, tzinfo=None).isoformat() + "Z", False
    if isinstance(d, datetime.date):
        return d.isoformat(), True
    try:  # pandas Timestamp and friends
        return _when(d.to_pydatetime() if hasattr(d, "to_pydatetime") else datetime.date.fromisoformat(str(d)[:10]))
    except Exception:
        return None, True


def events(con, today, data=None, n=80):
    """Everything that happened lately, as plain dicts, newest first:
    {k: kind, d: iso time, day: date-only?, h: title HTML, x: [tickers], u: link}."""
    data = data or {}
    out = []

    def add(kind, when, h, x=(), u=""):
        d, day = _when(when)
        if d:
            out.append({"k": kind, "d": d, "day": day, "h": h, "x": [t for t in x if t], "u": u})

    by_day = {}  # single insider buys, grouped per day like a wire summary
    for r in data.get("big") or []:
        by_day.setdefault(_when(r["when"])[0], []).append(r)
    for d, rows in by_day.items():
        rows = sorted(rows, key=lambda r: -r["value"])
        tot = sum(r["value"] for r in rows)
        top = rows[0]
        n_co = len({r["tkr"] for r in rows})
        h = (f"Insiders bought {_money(tot)} of their own companies' stock"
             + (f" across {n_co} companies" if n_co > 1 else f" at {E(top.get('company') or top['tkr'])}")
             + f". Biggest: {E(top['who'])} ({E(top['role'].split(',')[0])}), {_money(top['value'])} of {E(top['tkr'])}.")
        add("insiders", rows[0]["when"], h, list(dict.fromkeys(r["tkr"] for r in rows)))
    for r in data.get("clusters") or []:
        add("insiders", r["when"], f"{r['n']} insiders at {E(r.get('company') or r['tkr'])} bought "
            f"{_money(r['value'])} together{' (including an executive)' if r.get('officer') else ''}.", [r["tkr"]])
    for r in data.get("congress") or []:
        add("congress", r.get("filed"), f"{E(r['who'])} bought {E(r['amount'])} of {E(r['tkr'])}"
            + (f" ({r['n']} purchases)" if r.get("n", 1) > 1 else "")
            + (" <span class='fam'>committee</span>" if r.get("flag") else "") + ".", [r["tkr"]])
    for r in data.get("whitehouse") or []:
        add("wh", r.get("filed"), f"{r['who_h']} ({E(r['title'])}) bought {E(r['amount'])} of {E(r['tkr'])}.",
            [r["tkr"]])
    for r in data.get("activists") or []:
        add("activists", r.get("filed"), f"{E(r['investor'])} took a 5%+ stake in "
            f"{E(r.get('company') or r['tkr'])} and may push for changes.", [r["tkr"]], r.get("url") or "")
    for ts, q, outcome, side, value, price, url, who in markets.bets(con, 25):
        verb = "placed a" if side != "sell" else "cashed out a"
        pick = f" on <b>{E(outcome)}</b>" if outcome and outcome not in ("Yes",) else ""
        add("bets", ts, f"A trader just {verb} {_money(value)} bet{pick}: {E(q)}"
            + (f" (at {price * 100:.0f}¢)" if price else ""), (), url)
    try:
        import analysts
        for tkr, d, firm, action, frm, to, *_ in analysts.recent(con, today, 7, 40):
            if action in ("Upgrade", "Downgrade"):
                add("analysts", d, f"{E(firm)} {'upgraded' if action == 'Upgrade' else 'downgraded'} {E(tkr)}"
                    + (f" to {E(to)}" if to else "") + ".", [tkr])
    except Exception as e:
        print(f"home analyst calls skipped ({type(e).__name__}: {e})")
    # newest first; a date-only item sorts as midday of its day
    out.sort(key=lambda e: e["d"] if not e["day"] else e["d"] + "T12:00:00Z", reverse=True)
    return out[:n]


def _ago(d, day, now):
    if day:
        n = (now.date() - datetime.date.fromisoformat(d)).days
        return "today" if n <= 0 else "1d ago" if n == 1 else f"{n}d ago"
    m = max(0, (now - datetime.datetime.fromisoformat(d.rstrip("Z"))).total_seconds() / 60)
    return f"{m:.0f}m ago" if m < 60 else f"{m / 60:.0f}h ago" if m < 1440 else f"{m / 1440:.0f}d ago"


def _x(t, pages, root=""):
    if t in pages:
        return f"<a class='x' href='{root}{stockpage.href(t)}'>{E(t)}</a>"
    return f"<a class='x' href='{E(links.look(t, root))}'>{E(t)}</a>"


def _event_li(e, pages, now):
    logo = pictures.logo_html(e["x"][0]) if e["x"] and e["k"] != "news" else ""
    title = (f"<a class='ft' href='{E(e['u'])}' rel='noopener'>{e['h']}</a>" if e["u"]
             else f"<div class='ft'>{e['h']}</div>")
    xs = "".join(_x(t, pages) for t in e["x"][:8])
    return (f"<li class='k-{e['k']}' data-k='{e['k']}' data-d='{e['d']}'{' data-day' if e['day'] else ''}>"
            f"<div class='fb'>{title}" + (f"<div class='xs'>{xs}</div>" if xs else "")
            + f"<div class='fm'><span class='tag'>{E(TAG[e['k']])}</span>"
            f"<span class='ago'>{_ago(e['d'], e['day'], now)}</span></div></div>{logo}</li>")


def _feed_html(evs, pages, now):
    present = {e["k"] for e in evs} | {"news"}
    pills = "<button class='pill on' data-f='all' type='button'>All</button>" + "".join(
        f"<button class='pill' data-f='{k}' type='button'{'' if k in present else ' hidden'}>{E(lab)}</button>"
        for k, lab, _ in KINDS)
    items = "".join(_event_li(e, pages, now) for e in evs)
    return (f"<section class='blk ops-feed'><div class='hh'><h2>Live feed</h2>"
            f"<a class='go' href='trades.html'>All trades →</a></div><div class='pills' role='tablist'>{pills}</div>"
            f"<ul class='feed' data-remote='{E(headlines.remote_url())}' data-local='news.json'>{items}</ul>"
            f"<p class='feed-none' hidden>Nothing here yet. New filings arrive every morning.</p>"
            f"<button class='feed-more' type='button' hidden>Show more</button></section>")


# ------------------------------------------------------------------ trending

def trending(con, evs, n=8):
    """[(tkr, day move or None)]: the most-mentioned tickers in the feed, then the
    biggest movers among large companies, using the latest market snapshot."""
    try:
        snap = {t: p for t, p in con.execute("SELECT tkr, pct FROM market_snap").fetchall()}
        big = [t for (t,) in con.execute(
            "SELECT tkr FROM market_snap WHERE pct IS NOT NULL ORDER BY cap DESC LIMIT 60").fetchall()]
    except Exception:
        snap, big = {}, []
    count = {}
    for e in evs:
        for t in e["x"]:
            count[t] = count.get(t, 0) + 1
    picks = [t for t in sorted(count, key=lambda t: -count[t]) if snap.get(t) is not None][:n // 2]
    picks += [t for t in sorted(big, key=lambda t: -abs(snap[t] or 0)) if t not in picks]
    return [(t, snap.get(t)) for t in picks[:n]]


def _trend_html(rows, pages):
    if not rows:
        return ""
    chips = []
    for t, p in rows:
        cls = "flat" if p is None or abs(p) < .0005 else "up" if p > 0 else "down"
        pct = "" if p is None else f"<span class='{cls}'>{p * 100:+.2f}%</span>"
        tag, href = ("a", f" href='{stockpage.href(t)}'") if t in pages else ("span", "")
        chips.append(f"<{tag} class='tk{' dn' if cls == 'down' else ''}'{href}>{E(t)}{pct}</{tag}>")
    return ("<section class='blk ops-trend'><div class='hh'><h2>Trending now</h2>"
            "<a class='go' href='moves.html'>Moves →</a></div>"
            f"<div class='tks'>{''.join(chips)}</div></section>")


# ------------------------------------------------------------------ search

def _search_html(pages, names):
    # tickers with a page (and a company name when we know it) for the suggestions
    idx = [[t, names.get(t, "")] for t in sorted(pages)]
    blob = json.dumps(idx, separators=(",", ":")).replace("</", "<\\/")
    return ("<form class='ops-search' action='trades.html' method='get' role='search' autocomplete='off'>"
            "<label><span class='pr' aria-hidden='true'>&gt;_</span>"
            "<input type='search' name='q' placeholder='Ticker, member, insider…' "
            "aria-label='Search tickers, members of Congress and insiders' enterkeyhint='search'></label>"
            "<div class='ops-sug' role='listbox'></div>"
            f"<script type='application/json' id='ops-idx'>{blob}</script></form>")


JS = r"""<script>(function(){
var feed=document.querySelector('.ops .feed');if(!feed)return;
var box=feed.closest('section'),more=box.querySelector('.feed-more'),none=box.querySelector('.feed-none');
var f='all',q='',shown=20,STEP=20;
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
function t(li){var d=li.dataset.d;return Date.parse(li.hasAttribute('data-day')?d+'T12:00:00Z':d)}
function ago(li){var d=li.dataset.d;
  if(li.hasAttribute('data-day')){var n=Math.floor((Date.now()-Date.parse(d+'T00:00:00'))/864e5);
    return n<=0?'today':n+'d ago'}
  var m=Math.max(0,(Date.now()-Date.parse(d))/6e4);
  return m<1?'just now':m<60?Math.round(m)+'m ago':m<1440?Math.round(m/60)+'h ago':Math.round(m/1440)+'d ago'}
function draw(){var n=0,all=feed.children;
  for(var i=0;i<all.length;i++){var li=all[i],ok=(f==='all'||li.dataset.k===f)&&(!q||li.textContent.toLowerCase().indexOf(q)>=0);
    li.hidden=!ok||n>=shown;if(ok)n++;var a=li.querySelector('.ago');if(a)a.textContent=ago(li)}
  more.hidden=n<=shown;none.hidden=n>0}
box.querySelectorAll('.pill').forEach(function(b){b.onclick=function(){f=b.dataset.f;shown=STEP;
  box.querySelectorAll('.pill').forEach(function(x){x.classList.toggle('on',x===b)});draw()}});
more.onclick=function(){shown+=STEP;draw()};
var pages={};JSON.parse((document.getElementById('ops-idx')||{}).textContent||'[]').forEach(function(r){pages[r[0]]=r[1]||' '});
function slug(s){return s.replace(/[^A-Za-z0-9]+/g,'-').replace(/^-|-$/g,'').toUpperCase()}
function addNews(items){
  var have={};feed.querySelectorAll('li[data-k=news]').forEach(function(li){li.remove()});
  items.slice(0,60).forEach(function(r){if(!r||!r.t||!r.d||have[r.u])return;have[r.u]=1;
    var li=document.createElement('li');li.className='k-news';li.dataset.k='news';li.dataset.d=r.d;
    var xs=(r.k||[]).slice(0,6).map(function(k){return "<a class='x' href='"+(pages[k]?"s/"+esc(slug(k))+".html"
      :"look.html?s="+encodeURIComponent(k))+"'>"+esc(k)+"</a>"}).join('');
    li.innerHTML="<div class='fb'><a class='ft' href='"+esc(r.u)+"' target='_blank' rel='noopener'>"+esc(r.t)+"</a>"
      +(xs?"<div class='xs'>"+xs+"</div>":"")+"<div class='fm'><span class='tag'>News"+(r.s?' · '+esc(r.s):'')+"</span><span class='ago'></span></div></div>";
    var at=t(li),kids=feed.children,j=0;while(j<kids.length&&t(kids[j])>=at)j++;
    feed.insertBefore(li,kids[j]||null)});
  draw()}
function get(u){if(!u)return Promise.resolve(null);
  return fetch(u+(u.indexOf('?')<0?'?':'&')+'v='+Math.floor(Date.now()/300000),{cache:'no-store'})
    .then(function(r){return r.ok?r.json():null}).catch(function(){return null})}
function load(){Promise.all([get(feed.dataset.remote),get(feed.dataset.local)]).then(function(r){
  var a=r[0],b=r[1],n=(a&&b)?(a.generated>=b.generated?a:b):(a||b);if(n&&n.items)addNews(n.items)})}
draw();load();setInterval(load,15*6e4);setInterval(draw,6e4);
// search: suggestions jump to a stock page; Enter on an exact ticker goes there too
var form=document.querySelector('.ops-search');if(!form)return;
var inp=form.querySelector('input'),sug=form.querySelector('.ops-sug'),keys=Object.keys(pages),sel=-1;
function hits(v){var u=v.toUpperCase(),l=v.toLowerCase(),a=[],b=[];
  keys.forEach(function(k){if(k.indexOf(u)===0)a.push(k);else if(l.length>2&&pages[k].toLowerCase().indexOf(l)>=0)b.push(k)});
  a.sort(function(x,y){return x.length-y.length});return a.concat(b).slice(0,6)}
function paint(){var v=inp.value.trim();q=v.toLowerCase();shown=STEP;draw();
  if(!v){sug.classList.remove('open');return}
  var h=hits(v);sel=-1;
  sug.innerHTML=h.map(function(k){return "<a href='s/"+esc(slug(k))+".html'><b>"+esc(k)+"</b><span>"+esc(pages[k].trim())+"</span></a>"}).join('')
    +"<a href='trades.html?q="+encodeURIComponent(v)+"'><b>&gt;_</b><span>Search every trade for “"+esc(v)+"”</span></a>";
  sug.classList.add('open')}
inp.addEventListener('input',paint);inp.addEventListener('focus',function(){if(inp.value)paint()});
document.addEventListener('click',function(e){if(!form.contains(e.target))sug.classList.remove('open')});
inp.addEventListener('keydown',function(e){var a=sug.querySelectorAll('a');
  if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();sel=(sel+(e.key==='ArrowDown'?1:-1)+a.length)%a.length;
    a.forEach(function(x,i){x.classList.toggle('sel',i===sel)})}
  else if(e.key==='Escape')sug.classList.remove('open')});
form.addEventListener('submit',function(e){var a=sug.querySelectorAll('a'),v=inp.value.trim().toUpperCase();
  if(sel>=0&&a[sel]){e.preventDefault();location.href=a[sel].href}
  else if(pages[v]){e.preventDefault();location.href='s/'+slug(v)+'.html'}});
})()</script>"""


# ------------------------------------------------------------------ page

def render(con, today, data=None, soon=(), pages=(), names=None, now=None):
    """The home page body. `data` is today.collect()'s result (or None if it failed);
    `soon` is the planned sections that aren't built yet; `pages` the tickers with a stock page."""
    pages = set(pages)
    now = now or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    ideas = (data or {}).get("ideas") or []
    n_buys = len((data or {}).get("congress") or []) + len((data or {}).get("clusters") or []) \
        + len((data or {}).get("big") or [])
    odds = markets.top(con, 3)
    if ideas:
        lead = ideas[0]
        line = (f"Strongest research lead: {front._tk(lead['tkr'])}"
                f"{' (' + E(lead['company']) + ')' if lead.get('company') else ''}.")
    else:
        line = "No new research leads today."
    if odds:
        line += (f" The busiest question in the betting markets: {E(odds[0][2])} "
                 f"({odds[0][4] * 100:.0f}% {E(odds[0][3])}).")
    stats = [
        (f"<a href='{front._stock_href(ideas[0]['tkr'])}'>{E(ideas[0]['tkr'])}</a>" if ideas else "—",
         "top idea"),
        (f"<a href='ideas.html'>{len(ideas)}</a>", "worth researching"),
        (f"<a href='trades.html?side=buy'>{n_buys}</a>", "notable buys"),
    ]
    try:
        evs = events(con, today, data)
    except Exception as e:  # the feed is one block; the rest of home still renders
        print(f"home feed skipped ({type(e).__name__}: {e})")
        evs = []
    ready = readiness_levels(con, today)
    clock = now.strftime("%H:%M") + " UTC"
    out_html = [
        "<div class='ops'>",
        f"<div class='ops-stat'><span class='led'></span><span class='on'>Systems online</span>"
        f"<span class='sep'>//</span><span>{today:%a %d %b %Y}</span><span class='sep bt'>//</span>"
        f"<span class='bt'>Built {clock}</span></div>",
        _search_html(pages, names or {}),
        "<div class='ops-grid'><div class='ops-side'>",
        _ready_html(ready),
        _trend_html(trending(con, evs), pages),
        "<section class='blk ops-sit'><div class='hh'><h2>Sitrep</h2>"
        f"<span class='mono hint'>{today:%b} {today.day}</span></div><p>{line}</p><div class='stats'>"
        + "".join(f"<div class='stat'><b>{v}</b><span>{E(k)}</span></div>" for v, k in stats) + "</div></section>",
        _block(con, today, soon, "economy", "Market numbers arrive with the next update.", "ops-mkt"),
        "</div>",
        _feed_html(evs, pages, now),
        "</div><div class='ops-more'>",
        "<section class='blk'><div class='hh'><h2>Ideas worth researching</h2>"
        "<a class='go' href='ideas.html'>All ideas →</a></div>",
    ]
    if ideas:
        out_html.append("<div class='cards'>" + "".join(front._card(i) for i in ideas[:3]) + "</div>")
    else:
        out_html.append("<div class='empty'>No ideas this week. That's normal; check back tomorrow.</div>")
    out_html.append("</section>")
    if odds:
        out_html.append("<section class='blk'><div class='hh'><h2>What the crowd expects</h2>"
                        "<a class='go' href='markets.html'>All odds →</a></div>"
                        "<div class='pms'>" + "".join(markets.card(r) for r in odds) + "</div></section>")
    out_html.append("<section class='blk'><div class='hh'><h2>Explore</h2></div><div class='hxs'>"
                    + "".join(_tile(con, today, soon, s) for s in SECTIONS if s[0] != "economy")
                    + "</div></section></div></div>" + JS)
    return "".join(out_html)


def placeholder(key):
    _, _, title, blurb, _, _ = BY_KEY[key]
    return (f"{MARK}<div class='hero'><div class='eyebrow'>Coming soon</div><h1>{E(title)}</h1>"
            f"<p>{E(blurb)}</p><p class='hint'>This section is being built and will appear "
            "here after an upcoming update. <a href='index.html'>Back to the briefing</a>.</p></div>")


def _pages(con, out):
    """(tickers with a stock page in `out`, {ticker: company name}) for search and chips."""
    try:
        slugs = {f[:-5] for f in os.listdir(os.path.join(out, "s")) if f.endswith(".html")}
    except OSError:
        slugs = set()
    names = {}
    try:
        names.update({t: n for t, n in con.execute("SELECT tkr, name FROM market_snap").fetchall() if n})
    except Exception:
        pass
    try:
        with open(os.path.join(out, "tracked.json"), encoding="utf-8") as f:
            names.update({r["tkr"]: r["name"] for r in json.load(f) if r.get("name")})
    except (OSError, ValueError):
        pass
    pages = {t for t in names if stockpage.slug(t) in slugs}
    pages |= slugs - {stockpage.slug(t) for t in pages}  # a page we have no name for
    return pages, {t: names.get(t, "") for t in pages}


def write(con, today, out, data, page, updated):
    """Fill any planned page that wasn't built, then write index.html. Call after every other page."""
    soon = missing(out)
    for key in soon:
        _, href, title, *_ = BY_KEY[key]
        with open(os.path.join(out, href), "w", encoding="utf-8") as f:
            f.write(page(title, key, placeholder(key), updated))
    try:
        body = render(con, today, data, soon, *_pages(con, out))
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
    now = datetime.datetime(2026, 11, 27, 15, 0)
    body = render(con, datetime.date(2026, 11, 27), now=now)
    assert "Sitrep" in body and "Fri 27 Nov 2026" in body and "class='blk ops-ready" in body
    assert "No new research leads" in body and "What the crowd expects" not in body
    assert "Awaiting readings" in body, "before the first readiness check the panel says so"
    for s in SECTIONS:
        if s[0] != "economy":
            assert f"href='{s[1]}'" in body, s
    # every block is a section with its own heading, so Mine can hide it
    for h in ("Readiness", "Sitrep", "Live feed", "Ideas worth researching", "Explore"):
        assert f"<h2>{h}</h2>" in body, h

    # the feed: newest first, grouped insider buys, tickers link to pages that exist
    d = datetime.date(2026, 11, 27)
    data = {"big": [{"tkr": "AAA", "company": "Aaa Corp", "who": "Jane Doe", "role": "director",
                     "value": 2e6, "when": d},
                    {"tkr": "BBB", "company": "Bbb Inc", "who": "Joe Roe", "role": "officer, ceo",
                     "value": 5e5, "when": d}],
            "congress": [{"tkr": "CCC", "who": "Sen. Smith", "amount": "$1K–$15K", "n": 1,
                          "filed": d - datetime.timedelta(days=2), "flag": ""}],
            "activists": [{"tkr": "DDD", "company": "<Ddd>", "investor": "Elliott", "filed": d,
                           "url": "https://sec.gov/x"}]}
    markets.ensure_tables(con)
    con.execute("INSERT INTO pred_bets VALUES ('h1', TIMESTAMP '2026-11-27 14:00:00', 'Fed cuts in December?', "
                "'Yes', 'buy', 11000, 0.4, 'https://polymarket.com/event/fed', 'whale')")
    evs = events(con, d, data)
    assert [e["k"] for e in evs] == ["bets", "insiders", "activists", "congress"], [e["k"] for e in evs]
    assert evs[1]["x"] == ["AAA", "BBB"] and "$2.5M" in evs[1]["h"] and "across 2 companies" in evs[1]["h"]
    assert "&lt;Ddd&gt;" in evs[2]["h"], "company names are escaped"
    assert "$11K bet" in evs[0]["h"]
    li = _event_li(evs[0], set(), now)
    assert "1h ago" in li and "Prediction market" in li
    assert "href='s/AAA.html'" in _event_li(evs[1], {"AAA"}, now) and "today" in _event_li(evs[1], set(), now)
    assert "2d ago" in _event_li(evs[3], set(), now)
    fb = _feed_html(evs, set(), now)
    assert "data-f='congress' type='button'>" in fb and "data-f='wh' type='button' hidden" in fb

    # trending: most-mentioned first, then big movers
    con.execute("CREATE TABLE market_snap (tkr VARCHAR, name VARCHAR, sector VARCHAR, industry VARCHAR, "
                "cap DOUBLE, last DOUBLE, pct DOUBLE, volume DOUBLE, checked TIMESTAMP)")
    con.execute("INSERT INTO market_snap VALUES ('AAA','Aaa','','',1e9,10,0.012,1,NULL),"
                "('TSLA','Tesla','','',1e12,300,0.0483,1,NULL),('MSFT','Microsoft','','',3e12,400,-0.0086,1,NULL)")
    tr = trending(con, evs)
    assert tr[0] == ("AAA", 0.012) and [t for t, _ in tr][1:] == ["TSLA", "MSFT"], tr
    th = _trend_html(tr, {"TSLA"})
    assert "+4.83%" in th and "href='s/TSLA.html'" in th and "class='tk dn'" in th

    # readiness: the real board, from readiness.py's offline fixture
    import readiness
    rnow = datetime.datetime(2026, 11, 27, 12, 0, tzinfo=datetime.timezone.utc)
    rcon = duckdb.connect()
    readiness.refresh(rcon, readiness.fixture_web(rnow), rnow)
    lv = readiness_levels(rcon, datetime.date(2026, 11, 27))
    assert lv and lv["overall"]["level"] in range(1, 6) and len(lv["subs"]) == 7, lv
    rb = render(rcon, datetime.date(2026, 11, 27), now=now)
    assert f"ops-ready l{lv['overall']['level']}" in rb and "readiness.html#rd-cyber" in rb
    assert "Awaiting readings" not in rb
    r = _ready_html({"overall": {"level": 2, "label": "High", "why": "x"}, "subs": [
        {"name": "Cyber", "level": 1, "why": "<b>"}], "href": "readiness.html"})
    assert "ops-ready l2" in r and "Full board" in r and "SAMPLE" not in r and "&lt;b&gt;" in r
    assert "SEVERE" in r and r.count("class='f'") == 5
    assert "NO DATA" in _sub_li({"name": "Health", "level": None, "label": "NO DATA"})
    assert "href='readiness.html#rd-cyber'" in _sub_li({"name": "Cyber", "level": 3, "href": "readiness.html#rd-cyber"})

    # search index can't break out of its script tag
    assert "<\\/script>" in _search_html({"X"}, {"X": "</script>"})

    out = tempfile.mkdtemp()
    open(os.path.join(out, "news.html"), "w").close()  # a section that's already built is left alone
    os.makedirs(os.path.join(out, "s"))
    open(os.path.join(out, "s", "BRK-B.html"), "w").close()
    pages, names = _pages(con, out)
    assert pages == {"BRK-B"}, pages
    write(con, datetime.date(2026, 11, 27), out, None, lambda t, k, b, u: f"<title>{t}</title>{b}", "now")
    assert open(os.path.join(out, "news.html")).read() == ""
    assert "Coming soon" in open(os.path.join(out, "economy.html")).read()
    idx = open(os.path.join(out, "index.html")).read()
    assert "Live feed" in idx and "Coming soon" in idx and '["BRK-B",""]' in idx
    assert "economy" in missing(out) and "news" not in missing(out), "old placeholders still count as missing"
    print("home selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
