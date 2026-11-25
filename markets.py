"""Prediction markets: what bettors expect about the economy, Washington and
companies, from Polymarket and Kalshi's free public data (no keys).

  markets    the most-traded open questions about the Fed, inflation, tariffs,
             shutdowns, elections, laws and big companies, with the market's
             current odds
  big bets   recent single bets of $10,000+ on those questions (Polymarket)

Odds are crowd guesses with money behind them, not facts. They move fast, so
the page says when they were checked.

    py markets.py              # fetch and print the top questions
    py markets.py --selftest
"""

import datetime
import html
import json
import re
import sys
import time

import requests

import companyintel

POLY_MARKETS = "https://gamma-api.polymarket.com/markets"
POLY_TRADES = "https://data-api.polymarket.com/trades"
KALSHI_EVENTS = "https://api.elections.kalshi.com/trade-api/v2/events"
BIG_BET = 10_000
E = html.escape

TOPICS = [  # (topic, pattern) -- first match wins
    ("Fed & rates", r"\bfed\b|fomc|interest rate|rate (cut|hike)|powell|warsh|basis points|bps\b"),
    ("Inflation & jobs", r"inflation|\bcpi\b|\bpce\b|unemployment|jobs report|payrolls|recession|\bgdp\b"),
    ("Trade & tariffs", r"tariff|trade deal|trade war|\bimports?\b|export ban|sanction"),
    ("Congress & laws", r"shutdown|debt ceiling|\bbill\b|\bact\b|congress|senate|\bhouse\b|filibuster|"
                        r"supreme court|executive order|impeach|\btax(es)?\b"),
    ("Elections", r"election|primary|nominee|governor|midterm|president|win the .* seat|electoral"),
    ("Markets & companies", r"s&p|nasdaq|\bdow\b|stock|shares|ipo|earnings|market cap|\$[A-Z]{1,5}\b|"
                            r"apple|nvidia|tesla|microsoft|amazon|google|alphabet|meta\b|openai|"
                            r"largest company|merger|acquire|bankrupt"),
]
SKIP = re.compile(r"\b(nba|nfl|mlb|nhl|ufc|fifa|premier league|champions league|super bowl|"
                  r"grand slam|tennis|golf|f1|formula 1|world cup|vs\.?|bitcoin|ethereum|solana|"
                  r"crypto|token|memecoin|weather|temperature|oscar|grammy|album|movie|tweets?)\b", re.I)
KALSHI_CATS = {"Economics", "Financials", "Politics", "Companies", "Elections", "World"}


def topic(text):
    t = text or ""
    if SKIP.search(t):
        return None
    for name, pat in TOPICS:
        if re.search(pat, t, re.I):
            return name
    return None


def ensure_tables(con):
    con.execute("""CREATE TABLE IF NOT EXISTS pred_markets (
        id VARCHAR PRIMARY KEY, src VARCHAR, topic VARCHAR, question VARCHAR, outcome VARCHAR,
        prob DOUBLE, volume24 DOUBLE, volume DOUBLE, end_date VARCHAR, url VARCHAR, checked TIMESTAMP)""")
    con.execute("""CREATE TABLE IF NOT EXISTS pred_bets (
        id VARCHAR PRIMARY KEY, ts TIMESTAMP, question VARCHAR, outcome VARCHAR, side VARCHAR,
        value DOUBLE, price DOUBLE, url VARCHAR, who VARCHAR)""")


def _json_list(x):
    if isinstance(x, list):
        return x
    try:
        return json.loads(x or "[]")
    except ValueError:
        return []


def parse_poly(markets, now):
    out = []
    for m in markets:
        q = m.get("question") or ""
        tp = topic(q)
        if not tp:
            continue
        outs, prices = _json_list(m.get("outcomes")), _json_list(m.get("outcomePrices"))
        if not outs or len(outs) != len(prices):
            continue
        k = outs.index("Yes") if "Yes" in outs else max(range(len(prices)), key=lambda i: float(prices[i]))
        ev = (m.get("events") or [{}])[0].get("slug") or m.get("slug")
        out.append((f"poly-{m.get('id')}", "Polymarket", tp, q, outs[k], float(prices[k]),
                    float(m.get("volume24hr") or 0), float(m.get("volume") or 0),
                    (m.get("endDate") or "")[:10], f"https://polymarket.com/event/{ev}", now))
    return out


def parse_kalshi(events, now):
    out = []
    for ev in events:
        title = ev.get("title") or ""
        if ev.get("category") and ev["category"] not in KALSHI_CATS:
            continue
        tp = topic(title + " " + (ev.get("sub_title") or ""))
        if not tp:
            continue
        mk = [m for m in ev.get("markets") or [] if m.get("last_price_dollars") is not None]
        if not mk:
            continue
        vol24 = sum(float(m.get("volume_24h_fp") or 0) for m in mk)
        vol = sum(float(m.get("volume_fp") or 0) for m in mk)
        top = max(mk, key=lambda m: float(m.get("last_price_dollars") or 0))  # the favourite
        sub = top.get("yes_sub_title") or "Yes"
        q = title if len(mk) == 1 else f"{title}"
        out.append((f"kalshi-{ev.get('event_ticker')}", "Kalshi", tp, q, sub,
                    float(top["last_price_dollars"]), vol24, vol, (top.get("close_time") or "")[:10],
                    f"https://kalshi.com/markets/{(ev.get('series_ticker') or '').lower()}", now))
    return out


def parse_bets(trades):
    out = []
    for t in trades:
        q = t.get("title") or ""
        value = float(t.get("size") or 0) * float(t.get("price") or 0)
        if value < BIG_BET or not topic(q):
            continue
        ts = datetime.datetime.fromtimestamp(int(t.get("timestamp") or 0), datetime.timezone.utc)
        out.append((t.get("transactionHash") or f"{t.get('proxyWallet')}-{t.get('timestamp')}",
                    ts.replace(tzinfo=None), q, t.get("outcome") or "", (t.get("side") or "").lower(),
                    value, float(t.get("price") or 0),
                    f"https://polymarket.com/event/{t.get('eventSlug') or t.get('slug')}",
                    t.get("name") or t.get("pseudonym") or (t.get("proxyWallet") or "")[:8]))
    return out


def refresh(con):
    ensure_tables(con)
    now = datetime.datetime.utcnow().replace(microsecond=0)
    s = requests.Session()
    s.headers["User-Agent"] = companyintel.UA
    rows, notes = [], []
    try:
        poly = []
        for off in (0, 200, 400):
            r = s.get(POLY_MARKETS, params={"closed": "false", "order": "volume24hr", "ascending": "false",
                                            "limit": 200, "offset": off}, timeout=60)
            r.raise_for_status()
            poly += r.json()
            time.sleep(0.3)
        rows += parse_poly(poly, now)
    except (requests.RequestException, ValueError) as e:
        notes.append(f"Polymarket {type(e).__name__}")
    try:
        evs, cursor = [], ""
        for _ in range(8):
            r = s.get(KALSHI_EVENTS, params={"status": "open", "with_nested_markets": "true",
                                             "limit": 200, "cursor": cursor}, timeout=60)
            r.raise_for_status()
            j = r.json()
            evs += j.get("events") or []
            cursor = j.get("cursor") or ""
            if not cursor:
                break
            time.sleep(0.3)
        rows += parse_kalshi(evs, now)
    except (requests.RequestException, ValueError) as e:
        notes.append(f"Kalshi {type(e).__name__}")
    if rows:
        con.execute("DELETE FROM pred_markets")  # odds go stale; keep only today's
        con.executemany("INSERT OR REPLACE INTO pred_markets VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    try:
        r = s.get(POLY_TRADES, params={"limit": 500, "takerOnly": "true", "filterType": "CASH",
                                       "filterAmount": BIG_BET}, timeout=60)
        if r.status_code != 200:  # filters refused: take the latest trades, filter here
            notes.append(f"big-bet filter HTTP {r.status_code}")
            r = s.get(POLY_TRADES, params={"limit": 500}, timeout=60)
        r.raise_for_status()
        bets = parse_bets(r.json())
        con.executemany("INSERT OR REPLACE INTO pred_bets VALUES (?,?,?,?,?,?,?,?,?)", bets)
        con.execute("DELETE FROM pred_bets WHERE ts < ?", [now - datetime.timedelta(days=14)])
    except (requests.RequestException, ValueError) as e:
        notes.append(f"big bets {type(e).__name__} {getattr(e.response, 'status_code', '') or ''}".strip())
    print(f"prediction markets: {len(rows)} questions" + (f" ({'; '.join(notes)})" if notes else ""))


def top(con, n=40):
    try:
        return con.execute("""SELECT src, topic, question, outcome, prob, volume24, volume, end_date, url, checked
                              FROM pred_markets ORDER BY volume24 DESC LIMIT ?""", [n]).fetchall()
    except Exception:
        return []


def bets(con, n=15):
    try:
        return con.execute("""SELECT ts, question, outcome, side, value, price, url, who FROM pred_bets
                              ORDER BY ts DESC LIMIT ?""", [n]).fetchall()
    except Exception:
        return []


COMMON = {"target", "visa", "match", "block", "square", "gap", "progressive", "general", "united",
          "american", "national", "first", "global", "energy", "meta"}


def for_company(con, tkr, name):
    """Open questions that name this company (by ticker like $TSLA or its name)."""
    base = companyintel.base_name(name or "")
    pats = [re.escape("$" + tkr)]
    if base and (" " in base or (len(base) >= 5 and base.lower() not in COMMON)):
        pats.append(r"\b" + re.escape(base) + r"\b")
    rx = re.compile("|".join(pats), re.I)
    return [r for r in top(con, 400) if rx.search(r[2])][:5]


def _money(x):
    return f"${x / 1e6:.1f}M" if x >= 1e6 else f"${x / 1e3:.0f}k"


def card(r):
    src, tp, q, outcome, p, v24, vol, end, url, _ = r
    pct = max(0, min(100, round(p * 100)))
    lead = "" if outcome == "Yes" else f"<small class='hint'>Favourite: {E(outcome)}</small>"
    return (f"<a class='pm' href='{E(url)}'><div class='pm-h'><span class='chip'>{E(tp)}</span>"
            f"<small class='hint'>{E(src)}{' · ends ' + E(end) if end else ''}</small></div>"
            f"<b>{E(q)}</b>{lead}<div class='pm-bar'><i style='width:{pct}%'></i></div>"
            f"<div class='pm-f'><span class='pm-p'>{pct}%</span>"
            f"<small class='hint'>{_money(v24)} traded today · {_money(vol)} total</small></div></a>")


def page(con):
    rows = top(con, 60)
    when = f"{rows[0][9]:%b %d, %H:%M} UTC" if rows and rows[0][9] else "—"
    topics = []
    for r in rows:
        if r[1] not in topics:
            topics.append(r[1])
    body = ["<div class='sec-top'><h1>What bettors expect</h1><p class='hint'>The most-traded open "
            "questions on Polymarket and Kalshi about the economy, Washington and big companies. "
            f"The percentage is the market's odds of the answer shown. Checked {E(when)}. Crowd "
            "guesses with money behind them, not facts.</p></div>"]
    if not rows:
        body.append("<p class='hint'>The first check runs with tomorrow's update.</p>")
    for tp in topics:
        group = [r for r in rows if r[1] == tp][:8]
        body.append(f"<h2>{E(tp)}</h2><div class='pms'>" + "".join(card(r) for r in group) + "</div>")
    bl = bets(con)
    if bl:
        body.append("<h2>Big recent bets ($10k+)</h2><ul class='rt bets'>" + "".join(
            f"<li><div class='t'><a href='{E(u)}'>{E(q)}</a><small>{ts:%b %d %H:%M} UTC · {E(w or '')}</small></div>"
            f"<div class='amt {'buy' if side == 'buy' else 'sell'}'><b>{_money(v)}</b>"
            f"<small>{'Bet on' if side == 'buy' else 'Sold'} {E(o)} at {pr * 100:.0f}%</small></div></li>"
            for ts, q, o, side, v, pr, u, w in bl) + "</ul>")
    return "".join(body)


def stock_panel(con, tkr, name):
    rows = for_company(con, tkr, name)
    if not rows:
        return ""
    return ("<section class='panel'><h2>What bettors expect</h2><div class='pms'>"
            + "".join(card(r) for r in rows)
            + "</div><p class='hint'>Open questions on prediction markets that name this company. "
              "Odds are crowd guesses, not facts.</p></section>")


CSS = """
.pms{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:12px}
.pm{display:flex;flex-direction:column;gap:8px;background:var(--card);border:1px solid var(--line);border-radius:16px;
padding:14px;color:var(--fg);text-decoration:none}.pm:hover{border-color:color-mix(in srgb,var(--acc) 45%,var(--line))}
.pm-h{display:flex;justify-content:space-between;align-items:center;gap:8px}.pm b{font-size:15px;line-height:1.35}
.pm-bar{height:8px;border-radius:999px;background:var(--chip);overflow:hidden}
.pm-bar i{display:block;height:100%;border-radius:999px;background:var(--acc)}
.pm-f{display:flex;justify-content:space-between;align-items:baseline;gap:8px}.pm-p{font-size:22px;font-weight:800}
.bets li{grid-template-columns:1fr auto}.bets a{color:var(--fg)}
"""


def selftest():
    now = datetime.datetime(2026, 11, 25, 12)
    assert topic("Will the Fed cut rates in December?") == "Fed & rates"
    assert topic("Lakers vs. Celtics") is None and topic("Bitcoin above $100k?") is None
    assert topic("Will there be a government shutdown?") == "Congress & laws"
    pm = [{"id": 1, "question": "Fed decision in December: 25 bps cut?", "outcomes": '["Yes", "No"]',
           "outcomePrices": '["0.82", "0.18"]', "volume24hr": 1.5e6, "volume": 9e7, "endDate": "2026-12-10T00:00:00Z",
           "events": [{"slug": "fed-december"}], "slug": "x"},
          {"id": 2, "question": "Lakers vs. Celtics", "outcomes": '["Lakers","Celtics"]', "outcomePrices": '["0.5","0.5"]'}]
    rows = parse_poly(pm, now)
    assert len(rows) == 1 and rows[0][4] == "Yes" and rows[0][5] == .82 and rows[0][9].endswith("/fed-december"), rows
    ks = [{"event_ticker": "KXTARIFF-26", "series_ticker": "KXTARIFF", "title": "New tariffs on EU goods by March?",
           "category": "Economics", "markets": [{"last_price_dollars": "0.3100", "volume_24h_fp": "5000",
                                                  "volume_fp": "90000", "yes_sub_title": "Yes"}]},
          {"event_ticker": "KXNBA", "title": "NBA champion", "category": "Sports", "markets": []}]
    kr = parse_kalshi(ks, now)
    assert len(kr) == 1 and kr[0][2] == "Trade & tariffs" and abs(kr[0][5] - .31) < 1e-9, kr
    tr = [{"title": "Fed decision in December: 25 bps cut?", "size": 50000, "price": .8, "side": "BUY",
           "outcome": "Yes", "timestamp": 1764072000, "eventSlug": "fed-december", "transactionHash": "0x1",
           "pseudonym": "Calm-Owl"},
          {"title": "Fed decision in December: 25 bps cut?", "size": 100, "price": .8, "side": "BUY",
           "timestamp": 1764072000, "transactionHash": "0x2"}]
    b = parse_bets(tr)
    assert len(b) == 1 and b[0][5] == 40000 and b[0][8] == "Calm-Owl", b
    import duckdb
    con = duckdb.connect()
    ensure_tables(con)
    con.executemany("INSERT INTO pred_markets VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows + kr + [
        ("poly-9", "Polymarket", "Markets & companies", "Will Nvidia be the largest company at year end?",
         "Yes", .64, 2e5, 4e6, "2026-12-31", "https://polymarket.com/event/nv", now)])
    con.executemany("INSERT INTO pred_bets VALUES (?,?,?,?,?,?,?,?,?)", b)
    pg = page(con)
    assert "What bettors expect" in pg and "82%" in pg and "Big recent bets" in pg and "$40k" in pg
    assert "<script" not in pg
    assert [r[2] for r in for_company(con, "NVDA", "NVIDIA CORP")] == ["Will Nvidia be the largest company at year end?"]
    assert for_company(con, "TGT", "Target Corp") == []
    assert "What bettors expect" in stock_panel(con, "NVDA", "Nvidia Corp")
    print("markets selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        import duckdb
        from schema import DB_PATH
        c = duckdb.connect(DB_PATH)
        refresh(c)
        for r in top(c, 20):
            print(f"{r[4] * 100:5.0f}%  {r[2]}  ({r[0]})")
