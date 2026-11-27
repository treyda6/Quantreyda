// Shared by every page: the light/dark switch, the phone menu, and the
// tap-or-hover definitions on metric labels. One cached file instead of a
// copy inside every page.
(function () {
  "use strict";
  var doc = document.documentElement;

  // ---- light / dark
  var b = document.getElementById("theme");
  function label() {
    if (b) b.innerHTML = doc.getAttribute("data-theme") === "dark"
      ? "☀<span class=tl> Light</span>" : "☾<span class=tl> Dark</span>";
  }
  label();
  if (b) b.onclick = function () {
    var t = doc.getAttribute("data-theme") === "dark" ? "light" : "dark";
    doc.setAttribute("data-theme", t);
    try { localStorage.setItem("qt-theme", t); } catch (e) {}
    label();
  };

  // ---- phone menu: close on a tap outside it or Escape
  var m = document.getElementById("nvt");
  if (m) {
    document.addEventListener("click", function (e) {
      if (m.checked && !e.target.closest(".nv,.nvb,#nvt")) m.checked = false;
    });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") m.checked = false; });
  }

  // ---- definitions: a label whose text is in this list gets a dotted
  // underline, and hovering or tapping it explains the number.
  var G = {
    "sales": "Revenue: everything the company sold in its last fiscal year.",
    "revenue": "Everything the company sold in the period, before any costs.",
    "growth": "How much sales grew (or shrank) compared with the year before.",
    "sales growth": "How much sales grew (or shrank) compared with the year before.",
    "revenue growth": "How much sales grew (or shrank) compared with the year before.",
    "profit": "Net income: what was left after every cost, interest and tax last year.",
    "net income": "Profit after every cost, interest payment and tax.",
    "p/e": "Price / earnings: the share price divided by a year of profit per share. 20 means you pay $20 for each $1 of yearly profit. Higher means more good news is already in the price.",
    "p/e (past 12 months)": "Price / earnings over the last four quarters. 20 means you pay $20 for each $1 of yearly profit.",
    "eps (past 12 months)": "Earnings per share: a year of profit divided by the number of shares.",
    "price / sales": "Company value divided by a year of sales. Useful when there's no profit yet; under 2 is cheap for most industries.",
    "price / book": "Company value divided by what it owns minus what it owes. Under 1 can mean cheap, or troubled.",
    "market cap": "What the whole company is worth at today's price: price × shares outstanding.",
    "size": "Market cap: what the whole company is worth at today's price.",
    "debt / equity": "What it owes compared with what shareholders own. Above 2 is a lot of borrowing (normal for banks and utilities).",
    "profit margin": "Profit as a share of sales: how many cents of each $1 sold it keeps.",
    "net margin": "Profit as a share of sales: how many cents of each $1 sold it keeps.",
    "gross margin": "Sales minus the direct cost of what was sold, as a share of sales.",
    "operating margin": "Profit from the business itself (before interest and taxes) as a share of sales.",
    "free cash flow": "Cash from the business minus what it spent on equipment and buildings: the money it could hand back to owners.",
    "operating cash flow": "Cash the day-to-day business brought in.",
    "capital spending": "Money spent on long-lived things like factories, equipment and data centers.",
    "dividend yield": "A year of dividends as a share of today's price.",
    "yield (past 12 months)": "A year of distributions as a share of today's price.",
    "payout ratio": "How much of its profit the company pays out as dividends. Above 100% can't last.",
    "dividends (last year)": "Cash paid to shareholders as dividends last fiscal year.",
    "buybacks (last year)": "Cash spent buying back its own shares last fiscal year, which gives each remaining share a bigger slice.",
    "shares outstanding": "How many shares exist. If this keeps rising, each share owns less of the company.",
    "diluted shares": "Shares including those that options and convertible debt could add.",
    "current ratio": "Short-term assets against short-term bills. Under 1 can mean a cash squeeze.",
    "52-week range": "The lowest and highest closing price over the last year.",
    "previous close": "The last day's closing price.",
    "shares sold short": "Shares borrowed and sold by people betting the price will fall (FINRA, twice a month).",
    "days to cover": "Shares sold short divided by a normal day's trading: how many days the short sellers would need to buy back. Above 5 is crowded.",
    "ceo pay": "The CEO's total pay from the latest proxy statement's pay table.",
    "last stock split": "The most recent time the company split its shares.",
    "lobbying (12 months)": "What the company reported spending on federal lobbying over the last year (Senate LDA filings).",
    "federal contracts (12 mo)": "Federal contract dollars the company was awarded over the last year (USAspending).",
    "pac gifts to candidates": "What the company's political action committee gave to federal candidates this election cycle (FEC).",
    "wikipedia views / day": "Average daily views of the company's Wikipedia page: a rough gauge of public attention.",
    "buzz vs usual": "Recent Wikipedia views compared with the company's normal level.",
    "6m vs s&p": "The stock's return over six months minus the S&P 500's. Positive means it beat the market.",
    "past year vs s&p": "The stocks' return over the past year minus the S&P 500's.",
    "since": "How the stock has done since the trade date minus the S&P 500 over the same time. Green beat the market.",
    "1-year return": "Price change plus dividends over the last year.",
    "3-year (a year)": "Average yearly return over three years, dividends included.",
    "5-year (a year)": "Average yearly return over five years, dividends included.",
    "10-year (a year)": "Average yearly return over ten years, dividends included.",
    "sharpe": "Return compared with how bumpy the ride was. About 0.5 is typical for the stock market; higher is better.",
    "per year": "The average yearly return (compound annual growth).",
    "conviction": "0 to 100: how many kinds of buyers, how much money, how healthy the company is, and how informed the buyers look.",
    "score": "Points for how many signals line up and for the business itself, minus warning signs. The table below the list explains them.",
    "heat": "How much is happening in this theme right now: price momentum, attention, buying and bills, out of 5.",
    "attention": "Wikipedia views of the theme's companies compared with a year ago.",
    "smart-money buys": "Buys by insiders, members of Congress, the White House and followed funds in the last 90 days.",
    "health": "A plain verdict from the company's own SEC filings: Solid, Mixed, Shaky or Unknown.",
    "filed": "When the trade was disclosed: the day the public found out.",
    "traded": "When the trade actually happened. Congress has up to 45 days to report.",
    "solid": "No red flags in the SEC filings: sales steady or growing, profitable, reasonable debt, no big share printing.",
    "mixed": "One warning sign in the SEC filings. Read why before buying.",
    "shaky": "Two or more warning signs in the SEC filings. Be careful.",
    "unknown": "No company financials to check (often an ETF, fund or foreign company).",
    "not checked": "This company's SEC numbers haven't been checked yet. The daily run checks more each day.",
    "congress": "A member of Congress or their family disclosed buying it (STOCK Act).",
    "insiders": "Executives or directors bought their own company's stock (SEC Form 4).",
    "top fund": "A proven long-term investor owns it or just bought more (13F filings).",
    "contract": "The company just started a big federal contract.",
    "white house": "The President, Vice President or a cabinet-level official reported buying it.",
    "fed": "A Federal Reserve governor reported buying it.",
    "activist": "An investor bought 5%+ of the company and may push for changes (13D filing).",
    "money tracked": "The total value of US stocks the followed funds reported in their latest 13F filings."
  };
  var SEL = ".stats4 span,.kv span,.fst span,th,.badge,.chips .chip,dt,[data-term]";
  function key(el) {
    var t = (el.getAttribute("data-term") || el.textContent || "").trim().toLowerCase();
    return G[t] ? t : G[t.replace(/\s*\(.*\)$/, "")] ? t.replace(/\s*\(.*\)$/, "") : null;
  }
  function mark(root) {
    var els = (root || document).querySelectorAll(SEL);
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      if (el.hasAttribute("data-tip") || el.closest("a,button,header,.bio")) continue;
      var k = key(el);
      if (!k) continue;
      el.setAttribute("data-tip", G[k]);
      el.classList.add("tip");
      if (!el.hasAttribute("tabindex")) el.setAttribute("tabindex", "0");
      el.removeAttribute("title");  // ours replaces the slow native one
    }
  }

  var bub = null, cur = null, at = 0;
  function show(el) {
    if (!bub) { bub = document.createElement("div"); bub.className = "tipb"; bub.setAttribute("role", "tooltip"); document.body.appendChild(bub); }
    if (cur !== el) at = Date.now();
    cur = el;
    bub.textContent = el.getAttribute("data-tip");
    bub.style.display = "block";
    var r = el.getBoundingClientRect(), w = bub.offsetWidth, h = bub.offsetHeight;
    var x = Math.max(8, Math.min(r.left, innerWidth - w - 8));
    var y = r.bottom + 8 + h > innerHeight ? r.top - h - 8 : r.bottom + 8;
    bub.style.left = x + "px"; bub.style.top = Math.max(8, y) + "px";
  }
  function hide() { if (bub) bub.style.display = "none"; cur = null; }
  document.addEventListener("mouseover", function (e) {
    var el = e.target.closest && e.target.closest("[data-tip]");
    if (el) show(el); else if (cur && !("ontouchstart" in window)) hide();
  });
  document.addEventListener("focusin", function (e) { if (e.target.hasAttribute && e.target.hasAttribute("data-tip")) show(e.target); });
  document.addEventListener("focusout", hide);
  document.addEventListener("click", function (e) {
    var el = e.target.closest && e.target.closest("[data-tip]");
    // a tap fires hover, focus and click at once: only a later tap closes it
    if (!el) hide(); else if (el !== cur) show(el); else if (Date.now() - at > 400) hide();
  });
  addEventListener("scroll", hide, { passive: true });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") hide(); });

  function start() {
    mark();
    // pages that draw in the browser (lookup, Mine, trades) get the same treatment
    var t = null;
    new MutationObserver(function () { clearTimeout(t); t = setTimeout(mark, 150); })
      .observe(document.body, { childList: true, subtree: true });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
  window.QT_GLOSSARY = G;
})();
