"""Short profiles for the dashboard: companies, members of Congress, insiders.

Everything here comes from tables the tracker already fills -- no extra
downloads -- so a profile is only as complete as the data collected so far.

  company(con, tkr)        sector, sales, growth, profit, P/E, debt, price,
                           1-year change and a small price history for a chart
  member(con, last, first, chamber)
                           party, state, years in office, committees, and their
                           trading record in this database
  insider(con, owner_cik, owner_name)
                           role(s), companies, and their buy/sell record
"""

import datetime

import committees


def _one(con, sql, params):
    try:
        return con.execute(sql, params).fetchone()
    except Exception:  # table not created yet on a fresh database
        return None


def _all(con, sql, params):
    try:
        return con.execute(sql, params).fetchall()
    except Exception:
        return []


def money(x):
    if x is None or x != x:
        return None
    a = abs(x)
    s = f"${a / 1e9:.1f}B" if a >= 1e9 else f"${a / 1e6:.1f}M" if a >= 1e6 else f"${a / 1e3:.0f}k"
    return ("-" if x < 0 else "") + s


def company(con, tkr):
    out = {"tkr": tkr}
    h = _one(con, """SELECT company, revenue, revenue_growth, net_income, debt, equity, pe
                     FROM health WHERE tkr = ?""", [tkr])
    if h:
        out.update(name=h[0], revenue=h[1], growth=h[2], net_income=h[3], pe=h[6],
                   debt_ratio=(h[4] / h[5]) if h[4] is not None and h[5] else None)
    c = _one(con, "SELECT name, sic_desc FROM companies WHERE tkr = ?", [tkr])
    if c:
        out.setdefault("name", c[0])
        out["sector"] = (c[1] or "").title() or None
    px = _all(con, """SELECT d, close FROM prices WHERE tkr = ? AND d >= ?
                      ORDER BY d""", [tkr, datetime.date.today() - datetime.timedelta(days=372)])
    if len(px) >= 20:
        out["spark"] = [round(p[1], 4) for p in px[:: max(1, len(px) // 60)]]
        out["price"] = px[-1][1]
        out["chg_1y"] = px[-1][1] / px[0][1] - 1
    return out


def member(con, last, first, chamber):
    rows = _all(con, """SELECT bioguide, first, nickname, last, party, state, since, committees
                        FROM members WHERE chamber = ?""", [chamber])
    hit = committees.match_people(rows, last, first)
    out = {"chamber": chamber}
    if hit:
        b, fn, nick, ln, party, state, since, comms = hit[0]
        out.update(name=f"{nick or fn} {ln}", party=party, state=state, since=since,
                   committees=[c for c in (comms or "").split("; ") if c])
        import fec
        out["fec"] = fec.member(con, b)
    rec = _one(con, """SELECT count(*), count(*) FILTER (WHERE tx_type = 'Purchase'),
                              count(*) FILTER (WHERE tx_type LIKE 'Sale%'),
                              min(txn_date), count(DISTINCT tkr)
                       FROM trades WHERE last_name = ? AND chamber = ?""", [last, chamber])
    if rec and rec[0]:
        out.update(trades=rec[0], buys=rec[1], sells=rec[2], first_trade=rec[3], tickers=rec[4])
        out["top"] = [t for t, _ in _all(con, """SELECT tkr, count(*) n FROM trades
            WHERE last_name = ? AND chamber = ? AND tkr IS NOT NULL
            GROUP BY 1 ORDER BY n DESC LIMIT 3""", [last, chamber])]
    return out


def insider(con, owner_cik, owner_name):
    key, val = ("owner_cik", owner_cik) if owner_cik else ("owner_name", owner_name)
    rows = _all(con, f"""SELECT upper(trim(ticker)), min(issuer_name),
            string_agg(DISTINCT owner_relationship, ', '),
            sum(shares * price_per_share) FILTER (WHERE trans_code = 'P'),
            sum(shares * price_per_share) FILTER (WHERE trans_code = 'S'),
            count(DISTINCT accession_number), max(filed_date)
        FROM insider_trades WHERE {key} = ? AND price_per_share > 0
        GROUP BY 1 ORDER BY 6 DESC""", [val])
    out = {"name": owner_name, "companies": []}
    for tkr, issuer, role, bought, sold, filings, last in rows[:5]:
        out["companies"].append({"tkr": tkr, "issuer": issuer, "role": nice_role(role),
                                 "bought": bought or 0, "sold": sold or 0, "filings": filings,
                                 "last": str(last or "")[:10]})
    return out


ROLE_WORDS = {"director": "Director", "officer": "Officer", "tenpercentowner": "10% owner",
              "10% owner": "10% owner", "other": "Other insider"}


def nice_role(raw):
    """'director, officer, tenpercentowner' -> 'Director, Officer & 10% owner'."""
    seen = []
    for r in (raw or "").replace(";", ",").split(","):
        k = r.strip().lower().replace(" ", "") if "%" not in r else r.strip().lower()
        w = ROLE_WORDS.get(k, r.strip().capitalize())
        if w and w not in seen:
            seen.append(w)
    if not seen:
        return "Insider"
    return seen[0] if len(seen) == 1 else ", ".join(seen[:-1]) + " & " + seen[-1]


PARTY = {"Democrat": "D", "Republican": "R", "Independent": "I"}


def member_lines(m):
    """Plain lines for a hover card."""
    lines = []
    if m.get("name"):
        tag = PARTY.get(m.get("party"), (m.get("party") or "?")[:1])
        lines.append(f"{'Senator' if m['chamber'] == 'S' else 'Representative'} "
                     f"({tag}-{m.get('state') or '?'})"
                     + (f", in office since {m['since']}" if m.get("since") else ""))
        if m.get("committees"):
            lines.append("Committees: " + ", ".join(m["committees"][:5]))
        if m.get("fec"):
            f = m["fec"]
            lines.append(f"Campaign money ({f['cycle'] - 1}-{f['cycle'] % 100:02d}): raised "
                         f"{money(f['raised']) or '$0'}"
                         + (f", {money(f['cash'])} in the bank" if f.get("cash") else "") + " (FEC)")
    else:
        lines.append("No longer in Congress, or not matched to the current roster.")
    if m.get("trades"):
        lines.append(f"Trading record here: {m['trades']} trades ({m['buys']} buys, "
                     f"{m['sells']} sells) in {m['tickers']} stocks since "
                     f"{str(m['first_trade'])[:4]}.")
        if m.get("top"):
            lines.append("Trades most: " + ", ".join(m["top"]))
    return lines


def insider_lines(i):
    lines = []
    for c in i["companies"][:3]:
        bits = [f"{c['role']} at {c['issuer'] or c['tkr']} ({c['tkr']})"]
        if c["bought"]:
            bits.append(f"bought {money(c['bought'])}")
        if c["sold"]:
            bits.append(f"sold {money(c['sold'])}")
        n = c["filings"]
        lines.append(", ".join(bits) + f" in {n} filing{'' if n == 1 else 's'}")
    if len(i["companies"]) > 3:
        lines.append(f"Also an insider at {len(i['companies']) - 3} more compan"
                     f"{'y' if len(i['companies']) == 4 else 'ies'}.")
    return lines or ["No other filings on record yet."]



def selftest():
    import duckdb
    import signals
    con = duckdb.connect()
    signals.make_fixture(con)
    committees.ensure_tables(con)
    leg = [{"id": {"bioguide": "A1"}, "name": {"first": "Ann", "last": "Alpha"},
            "terms": [{"type": "rep", "start": "2015-01-03"}, {"type": "sen", "start": "2019-01-03",
                                                              "party": "Republican", "state": "TX"}]}]
    com = [{"thomas_id": "SSAS", "name": "Senate Committee on Armed Services"},
           {"thomas_id": "SSFI", "name": "Senate Committee on Finance"}]
    mem = {"SSAS": [{"bioguide": "A1"}], "SSFI": [{"bioguide": "A1"}], "SSAS01": [{"bioguide": "A1"}]}
    rows = committees.build_members(leg, com, mem)
    assert rows == [("A1", "S", "Ann", None, "Alpha", "Republican", "TX", 2019,
                     "Armed Services; Finance")], rows
    con.executemany("INSERT INTO members VALUES (?,?,?,?,?,?,?,?,?)", rows)
    m = member(con, "Alpha", "A", "S")
    lines = member_lines(m)
    assert lines[0] == "Senator (R-TX), in office since 2019", lines
    assert "Armed Services" in lines[1] and "Trading record here: 1 trades (1 buys" in lines[2], lines
    assert member_lines(member(con, "Nobody", "X", "H"))[0].startswith("No longer in Congress")
    import fec
    fec.ensure_tables(con)
    con.execute("INSERT INTO fec_member VALUES ('A1', current_date, 'S1', 'ALPHA, ANN', 2026, 3.4e6, 1.2e6)")
    assert ("Campaign money (2025-26): raised $3.4M, $1.2M in the bank (FEC)"
            in member_lines(member(con, "Alpha", "A", "S"))), member_lines(member(con, "Alpha", "A", "S"))

    ins = insider(con, "1", "Ann")
    assert ins["companies"][0]["tkr"] == "XYZ" and ins["companies"][0]["role"] == "Officer"
    assert nice_role("director, officer, tenpercentowner") == "Director, Officer & 10% owner"
    assert insider_lines(ins) == ["Officer at XYZ (XYZ), bought $30k in 1 filing"], insider_lines(ins)

    con.execute("CREATE TABLE prices (tkr VARCHAR, d DATE, close DOUBLE)")
    today = datetime.date.today()
    con.executemany("INSERT INTO prices VALUES ('AAA', ?, ?)",
                    [(today - datetime.timedelta(days=300 - i * 10), 100 + i) for i in range(30)])
    co = company(con, "AAA")
    assert abs(co["chg_1y"] - 29 / 100) < 1e-9 and co["price"] == 129 and len(co["spark"]) >= 20
    assert company(con, "ZZZ") == {"tkr": "ZZZ"}
    assert money(-2.5e9) == "-$2.5B" and money(None) is None
    print("profiles selftest ok")


if __name__ == "__main__":
    selftest()
