"""Number formatting shared by every page and email, so $1.2M reads the
same everywhere."""


def money(x, none="—"):
    """$1.2T, $3.4B, $250M, $1.5M, $15k, $900. None/NaN -> `none`."""
    if x is None or x != x:
        return none
    a = abs(float(x))
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "k")):
        if a >= div * 0.9995:
            v = a / div
            s = f"{v:.1f}" if v < 100 else f"{v:.0f}"
            return ("-" if x < 0 else "") + "$" + s.removesuffix(".0") + suf
    return ("-" if x < 0 else "") + f"${a:.0f}"


def pct(x, plus=True, none="—"):
    """+12.3% from 0.123."""
    if x is None or x != x:
        return none
    return f"{x * 100:{'+' if plus else ''}.1f}%"


def selftest():
    assert money(None) == "—" and money(float("nan"), "?") == "?"
    assert money(2_500_000) == "$2.5M" and money(1_000_000) == "$1M" and money(250e6) == "$250M"
    assert money(15_000) == "$15k" and money(-3.4e9) == "-$3.4B" and money(1.2e12) == "$1.2T"
    assert money(999_999) == "$1M" and money(950) == "$950"
    assert pct(0.1234) == "+12.3%" and pct(-0.05, plus=False) == "-5.0%" and pct(None) == "—"
    print("fmt selftest ok")


if __name__ == "__main__":
    selftest()
