"""Run every module's offline selftest. No network, no real DB.

    py selftest.py
"""
import importlib

MODULES = ["entities", "scrape_senate", "scrape_house", "scrape_insiders",
           "daily", "signals", "health", "committees", "funds", "contracts", "digest", "backtest",
           "mailer", "alerts", "weekly", "profiles", "news", "whitehouse", "activists", "companyintel", "bills", "fec", "pictures", "secextras", "sections", "markets", "leaders", "today", "stockpage", "fundpage",
           "dashboard"]

for name in MODULES:
    print(f"--- {name}")
    importlib.import_module(name).selftest()
print("\nall selftests ok")
