"""Run every module's offline selftest. No network, no real DB.

    py selftest.py
"""
import importlib

MODULES = ["entities", "scrape_senate", "scrape_house", "scrape_insiders",
           "daily", "signals", "digest", "backtest"]

for name in MODULES:
    print(f"--- {name}")
    importlib.import_module(name).selftest()
print("\nall selftests ok")
