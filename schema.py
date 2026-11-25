"""Shared DB shape for both chambers: tables, the `trades` view, amount parsing.

Lives in its own module because two scrapers write the same view -- if each
owned a copy of the DDL, whichever ran last would silently redefine `trades`.
"""

import os
import re

DB_PATH = os.environ.get("QT_DB", "congress_trades.duckdb")


def load_dotenv(path=".env"):
    """Read KEY=VALUE lines from .env into os.environ (no override). Tiny on
    purpose -- one optional setting doesn't justify a python-dotenv dependency."""
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def sec_user_agent():
    """sec.gov 403s any client whose User-Agent lacks a contact email, so the
    SEC scrapers need one -- yours, set once in .env, never hardcoded here."""
    load_dotenv()
    email = os.environ.get("SEC_CONTACT_EMAIL", "").strip()
    if "@" not in email:
        raise SystemExit("Set SEC_CONTACT_EMAIL=you@example.com in .env "
                         "(sec.gov requires a contact email in the User-Agent).")
    return f"quantreyda/0.1 ({email})"

# Both chambers land in the same shape so the view is one SELECT body twice.
# House has no ticker column on the form (the symbol is inside asset_name), so
# `ticker` is always NULL there and entities.py fills `ticker_guess`.
COLUMNS = """first_name VARCHAR, last_name VARCHAR, office VARCHAR,
    filed VARCHAR, link VARCHAR, tx_date VARCHAR, owner VARCHAR,
    ticker VARCHAR, asset_name VARCHAR, asset_type VARCHAR,
    tx_type VARCHAR, amount_raw VARCHAR,
    amount_low BIGINT, amount_high BIGINT,
    ticker_guess VARCHAR, ticker_guess_how VARCHAR"""

TABLES = ("senate_trades", "house_trades")


def _select(table, chamber):
    return rf"""SELECT '{chamber}' AS chamber, last_name,
            -- Senate Exchange rows pack both legs into one cell ("--  AMCR" =
            -- gave up an untickered holding, received AMCR). Take the trailing
            -- symbol, i.e. what they hold afterwards. Plain tickers pass
            -- through; House rows have no ticker cell and fall to the guess.
            coalesce(
                nullif(nullif(regexp_extract(trim(ticker), '([A-Z.\-]+)$', 1),
                              '--'), ''),
                ticker_guess) AS tkr,
            asset_name, tx_type, amount_low, amount_high,
            try_strptime(tx_date, '%m/%d/%Y')::DATE AS txn_date,
            try_strptime(filed, '%m/%d/%Y')::DATE AS filed_date,
            date_diff('day', try_strptime(tx_date, '%m/%d/%Y')::DATE,
                             try_strptime(filed, '%m/%d/%Y')::DATE) AS lag_days,
            -- secondary columns: filtering, auditing, provenance
            asset_type, owner,
            ticker IS NULL AND ticker_guess IS NOT NULL AS tkr_recovered,
            -- a trade dated after its own filing is a typo in the filing
            -- (seen live: a Feb 2026 filing listing a Dec 2026 trade). Keep
            -- the row, but flag it; signals key off filed_date anyway.
            coalesce(try_strptime(tx_date, '%m/%d/%Y')::DATE
                     > try_strptime(filed, '%m/%d/%Y')::DATE, false) AS txn_date_suspect,
            first_name, office, link
        FROM {table}"""


def ensure_schema(con):
    """Create both trade tables and the `trades` view. Idempotent.

    The view is what you query: it coalesces recovered tickers into `tkr`,
    turns MM/DD/YYYY strings into real DATEs so ORDER BY sorts chronologically
    instead of lexically, and unions both chambers behind a `chamber` column.
    """
    for t in TABLES:
        con.execute(f"CREATE TABLE IF NOT EXISTS {t} ({COLUMNS})")
    con.execute("CREATE OR REPLACE VIEW trades AS "
                + _select("senate_trades", "S")
                + " UNION ALL "
                + _select("house_trades", "H"))


def parse_amount(text):
    """'$1,001 - $15,000' -> (1001, 15000). Open-ended high returns None."""
    nums = [int(n.replace(",", "")) for n in re.findall(r"[\d,]+", text or "")]
    if not nums:
        return None, None
    return nums[0], (nums[1] if len(nums) > 1 else None)


# Reports (backtest, leaderboard) are rebuilt weekly, but each GitHub Actions
# run starts from a fresh checkout and only the database is kept between runs.
# Saving a copy in the database keeps the site's weekly pages alive all week.

def save_report(con, path):
    """Store the file at `path` in the database under its path."""
    with open(path, encoding="utf-8") as f:
        body = f.read()
    con.execute("CREATE TABLE IF NOT EXISTS saved_reports (path VARCHAR PRIMARY KEY, body VARCHAR, "
                "saved_at TIMESTAMP)")
    con.execute("INSERT OR REPLACE INTO saved_reports VALUES (?, ?, current_timestamp)", [path, body])


def load_report(con, path):
    """The file at `path` if this run wrote it, else the saved copy, else None."""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read()
    try:
        r = con.execute("SELECT body FROM saved_reports WHERE path = ?", [path]).fetchone()
        return r[0] if r else None
    except Exception:  # table not created yet
        return None
