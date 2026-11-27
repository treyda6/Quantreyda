"""Where things click through to. One place, so every page agrees.

A ticker opens its full research page (s/TKR.html) when the site built one,
and the lookup page (look.html?s=TKR: chart, stats, statements, Buy/Sell)
for anything else, so a symbol is never a dead end and never sends you off
the site.
"""

import re
import urllib.parse


def slug(tkr):
    """File-name form of a ticker: BRK.B -> BRK-B."""
    return re.sub(r"[^A-Za-z0-9]+", "-", tkr or "").strip("-").upper()


def stock_page(tkr, root=""):
    return f"{root}s/{slug(tkr)}.html"


def look(tkr, root=""):
    return f"{root}look.html?s={urllib.parse.quote(tkr or '')}"


def stock(tkr, pages, root=""):
    """Best link for a ticker: its research page if built, else the lookup."""
    return stock_page(tkr, root) if tkr in pages else look(tkr, root)


def person(name, root=""):
    """Every trade by someone: the all-trades page searched for their name."""
    return f"{root}trades.html?q={urllib.parse.quote(name or '')}"


def selftest():
    assert slug("BRK.B") == "BRK-B" and slug("brk/a") == "BRK-A"
    assert stock("BRK.B", {"BRK.B"}, "../") == "../s/BRK-B.html"
    assert stock("VTSAX", set()) == "look.html?s=VTSAX"
    assert look("A&B") == "look.html?s=A%26B"
    assert person("Nancy Pelosi") == "trades.html?q=Nancy%20Pelosi"
    print("links selftest ok")


if __name__ == "__main__":
    selftest()
