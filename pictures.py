"""Company logos and official portraits for the website, all free.

  logos       Small company logos by ticker (Financial Modeling Prep's public
              logo images, then Parqet's, then the nvstly/icons set on GitHub;
              no key for any).
  congress    Official member portraits, which are public domain, from the
              @unitedstates project (by Bioguide ID).
  officials   White House and cabinet portraits: the lead image of their
              Wikipedia article (Wikimedia Commons; usually the official,
              public-domain portrait).

Pictures are downloaded once by the daily run, kept in the database, and
written next to the pages when the site is built, so visitors' browsers
never call these services. Anything without a picture gets a neat
lettered circle instead.

    py pictures.py AAPL        # fetch one logo and say what happened
    py pictures.py --selftest
"""

import datetime
import html
import os
import sys
import time
import urllib.parse

import requests

import companyintel

LOGO_URLS = ["https://financialmodelingprep.com/image-stock/{tkr}.png",
             "https://assets.parqet.com/logos/symbol/{tkr}?format=png",
             "https://raw.githubusercontent.com/nvstly/icons/main/ticker_icons/{tkr}.png"]
PORTRAIT_URLS = ["https://raw.githubusercontent.com/unitedstates/images/gh-pages/congress/225x275/{bioguide}.jpg",
                 "https://unitedstates.github.io/images/congress/225x275/{bioguide}.jpg"]
WIKI_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
RETRY_DAYS = 30          # try again for a missing picture after this long
PER_RUN = 400            # downloads per run, at most
MAX_BYTES = 400_000
HAVE = {}   # key -> file name; filled by write(), read by the html helpers
EXT = {"image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg", "image/webp": "webp",
       "image/gif": "gif"}

E = html.escape


def ensure_tables(con):
    con.execute("""CREATE TABLE IF NOT EXISTS pictures (
        key VARCHAR PRIMARY KEY, fetched_on DATE, ext VARCHAR, img BLOB)""")


def logo_key(tkr):
    return "l-" + "".join(c for c in (tkr or "").upper() if c.isalnum() or c in ".-")


def member_key(bioguide):
    return "c-" + (bioguide or "")


def official_key(slug):
    return "w-" + (slug or "")


def _image(r):
    """(ext, bytes) if the response is a real, small image, else None."""
    ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if r.status_code != 200 or ctype not in EXT:
        return None
    if not 200 < len(r.content) <= MAX_BYTES:  # tiny = a blank placeholder
        return None
    return EXT[ctype], r.content


def _first(s, urls):
    for u in urls:
        try:
            time.sleep(0.2)
            got = _image(s.get(u, timeout=30))
        except requests.RequestException:
            continue
        if got:
            return got
    return None


def fetch_logo(s, tkr):
    t = urllib.parse.quote(tkr.upper().replace(".", "-"), safe="-")
    return _first(s, [u.format(tkr=t) for u in LOGO_URLS])


def fetch_member(s, bioguide):
    return _first(s, [u.format(bioguide=bioguide) for u in PORTRAIT_URLS])


def fetch_official(s, name):
    title = urllib.parse.quote(name.replace(" ", "_"), safe="")
    try:
        time.sleep(0.3)
        r = s.get(WIKI_SUMMARY.format(title=title), timeout=30)
        thumb = ((r.json() or {}).get("thumbnail") or {}).get("source") if r.status_code == 200 else None
    except (requests.RequestException, ValueError):
        return None
    return _first(s, [thumb]) if thumb else None


def wanted(con, tickers):
    """[(key, kind, arg)] for everything the site could show a picture for."""
    out = [(logo_key(t), "logo", t) for t in sorted(set(tickers)) if t]
    try:
        out += [(member_key(b), "member", b) for (b,) in con.execute(
            "SELECT bioguide FROM members WHERE bioguide IS NOT NULL").fetchall()]
    except Exception:  # rosters not loaded yet
        pass
    try:
        out += [(official_key(slug), "official", name) for slug, name in con.execute(
            "SELECT slug, max(name) FROM wh_trades GROUP BY 1").fetchall()]
    except Exception:
        pass
    return out


def refresh(con, tickers, today=None):
    ensure_tables(con)
    today = today or datetime.date.today()
    have = dict(con.execute("SELECT key, fetched_on FROM pictures WHERE img IS NOT NULL "
                            "OR fetched_on > ?", [today - datetime.timedelta(days=RETRY_DAYS)]).fetchall())
    todo = [w for w in wanted(con, tickers) if w[0] not in have][:PER_RUN]
    s = requests.Session()
    s.headers["User-Agent"] = companyintel.UA
    got = 0
    for key, kind, arg in todo:
        pic = (fetch_logo(s, arg) if kind == "logo" else
               fetch_member(s, arg) if kind == "member" else fetch_official(s, arg))
        con.execute("INSERT OR REPLACE INTO pictures VALUES (?, ?, ?, ?)",
                    [key, today, pic and pic[0], pic and pic[1]])
        got += bool(pic)
    print(f"pictures: tried {len(todo)}, found {got}")


def write(con, out):
    """Write every stored picture to out/img/ and remember what exists."""
    HAVE.clear()
    try:
        rows = con.execute("SELECT key, ext, img FROM pictures WHERE img IS NOT NULL").fetchall()
    except Exception:
        return HAVE
    os.makedirs(f"{out}/img", exist_ok=True)
    for key, ext, img in rows:
        with open(f"{out}/img/{key}.{ext}", "wb") as f:
            f.write(img)
        HAVE[key] = f"{key}.{ext}"
    return HAVE


def _find(kind, key):
    return HAVE.get(key)


def initials(name):
    bits = [b for b in (name or "").replace(",", " ").split()
            if b[:1].isalpha() and len(b.strip(".")) > 1]  # skip middle initials
    return ((bits[0][0] + (bits[-1][0] if len(bits) > 1 else "")) if bits else "?").upper()


def logo_html(tkr, root="", size=""):
    """A rounded logo, or the ticker's first letter in a tile."""
    f = _find("logo", logo_key(tkr))
    inner = (f"<img src='{root}img/{E(f)}' alt='' loading='lazy'>" if f
             else E((tkr or "?")[:1].upper()))
    return f"<span class='lg{' ' + size if size else ''}{'' if f else ' mono'}'>{inner}</span>"


def face_html(key, name, root="", party=None):
    """A round portrait, or the person's initials in a circle tinted by party."""
    f = _find("face", key) if key else None
    tint = {"R": " r", "D": " d"}.get((party or "")[:1].upper(), "")
    inner = (f"<img src='{root}img/{E(f)}' alt='' loading='lazy'>" if f
             else E(initials(name)))
    return f"<span class='face{tint}{'' if f else ' mono'}'>{inner}</span>"


def face_src(key, root=""):
    f = _find("face", key) if key else None
    return f"{root}img/{f}" if f else ""


def logo_src(tkr, root=""):
    f = _find("logo", logo_key(tkr))
    return f"{root}img/{f}" if f else ""


CSS = """
.lg{display:inline-flex;align-items:center;justify-content:center;width:34px;height:34px;flex:none;
border-radius:10px;background:#fff;overflow:hidden;border:1px solid var(--line);vertical-align:middle}
.lg img{width:100%;height:100%;object-fit:contain;padding:3px;box-sizing:border-box}
.lg.mono{background:var(--chip);color:var(--mut);font-weight:800;font-size:15px}
.lg.big{width:54px;height:54px;border-radius:14px;font-size:22px}
.face{display:inline-flex;align-items:center;justify-content:center;width:34px;height:34px;flex:none;
border-radius:50%;overflow:hidden;background:var(--chip);color:var(--mut);font-weight:700;font-size:12px;
vertical-align:middle;box-shadow:0 0 0 2px var(--card)}
.face img{width:100%;height:100%;object-fit:cover;object-position:50% 15%}
.face.r{box-shadow:0 0 0 2px #e5484d}.face.d{box-shadow:0 0 0 2px #3b82f6}
.face.mono.r{background:color-mix(in srgb,#e5484d 18%,var(--chip))}
.face.mono.d{background:color-mix(in srgb,#3b82f6 18%,var(--chip))}
.pic{display:flex;align-items:center;gap:10px;min-width:0}.pic .t{min-width:0}
.list .pic,.lb .pic{display:inline-flex;vertical-align:middle}.list .face{width:26px;height:26px;font-size:10px}
.pic .t small{display:block;color:var(--mut);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:220px}
"""


def selftest():
    import duckdb

    class R:
        def __init__(self, code, ctype, n):
            self.status_code, self.headers, self.content = code, {"Content-Type": ctype}, b"x" * n
    assert _image(R(200, "image/png", 5000)) == ("png", b"x" * 5000)
    assert _image(R(200, "text/html", 5000)) is None and _image(R(404, "image/png", 5000)) is None
    assert _image(R(200, "image/png", 50)) is None, "a blank placeholder isn't a logo"
    assert logo_key("brk.b") == "l-BRK.B" and initials("Nancy Pelosi") == "NP" and initials("") == "?"
    assert initials("PELOSI, NANCY") == "PN" and initials("Morrone Louis H.") == "ML"
    con = duckdb.connect()
    ensure_tables(con)
    con.execute("INSERT INTO pictures VALUES ('l-AAA', current_date, 'png', 'abc'::BLOB), "
                "('c-A1', current_date, 'jpg', 'abc'::BLOB), ('l-ZZZ', current_date, NULL, NULL)")
    import tempfile
    out = tempfile.mkdtemp()
    write(con, out)
    assert os.path.exists(f"{out}/img/l-AAA.png") and not os.path.exists(f"{out}/img/l-ZZZ.png")
    assert "<img src='../img/l-AAA.png'" in logo_html("AAA", "../")
    assert logo_html("ZZZ") == "<span class='lg mono'>Z</span>"
    assert "img/c-A1.jpg" in face_html("c-A1", "Ann Alpha") and "face d mono" in face_html("c-B2", "Bo B", party="Democrat")
    assert ">BB<" in face_html("c-B2", "Bo Bee")
    con.execute("CREATE TABLE members (bioguide VARCHAR)")
    con.execute("INSERT INTO members VALUES ('A1'), ('B2')")
    keys = [k for k, _, _ in wanted(con, ["AAA", "BBB"])]
    assert keys == ["l-AAA", "l-BBB", "c-A1", "c-B2"], keys
    HAVE.clear()
    print("pictures selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    elif len(sys.argv) > 1:
        se = requests.Session()
        se.headers["User-Agent"] = companyintel.UA
        got = fetch_logo(se, sys.argv[1])
        print(f"{sys.argv[1]}: {'found ' + got[0] + f' ({len(got[1])} bytes)' if got else 'no logo'}")
    else:
        print(__doc__)
