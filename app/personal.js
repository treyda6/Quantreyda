// Quantreyda personal layer: your accounts, watchlist, alerts and layout
// choices. Everything here lives in this browser's storage only. The site is
// the same public file for everyone; nothing personal is ever sent anywhere
// (the only request this makes is for the public mine.json price file).
//
// Loaded in <head> on every page so hidden menu items never flash in.
(function () {
  "use strict";
  var KEY = "qt-mine";
  var me = document.currentScript;
  var ROOT = me ? me.src.replace(/personal\.js(\?.*)?$/, "") : "";

  var KINDS = {
    roth: "Roth IRA", custodial_roth: "Custodial Roth IRA", trad: "Traditional IRA",
    utma: "UTMA / UGMA (custodial)", brokerage: "Taxable brokerage", k401: "401(k) / 403(b)",
    hsa: "HSA", college529: "529 college plan", other: "Other"
  };
  var IRA_LIMIT = { year: 2026, base: 7500, catchup: 1100 };  // IRS limits for Roth + traditional IRAs combined

  function blank() {
    return {
      v: 1, accounts: [], watch: [], journal: [],
      rules: { insBuy: true, insSell: false, polBuy: true, polSell: true, news: true, move: 5, holdings: true },
      prefs: { navHidden: [], hide: {}, homeCard: true, notify: false, privacy: false, focus: true },
      seen: {}, notified: {}
    };
  }

  function fill(s) {  // add any fields an older save is missing
    var b = blank();
    s = s && typeof s === "object" ? s : {};
    ["accounts", "watch", "journal"].forEach(function (k) { if (!Array.isArray(s[k])) s[k] = []; });
    ["rules", "prefs"].forEach(function (k) {
      s[k] = Object.assign({}, b[k], s[k] && typeof s[k] === "object" ? s[k] : {});
    });
    ["seen", "notified"].forEach(function (k) { if (!s[k] || typeof s[k] !== "object") s[k] = {}; });
    if (!Array.isArray(s.prefs.navHidden)) s.prefs.navHidden = [];
    if (!s.prefs.hide || typeof s.prefs.hide !== "object") s.prefs.hide = {};
    s.v = 1;
    return s;
  }

  function load() {
    try { return fill(JSON.parse(localStorage.getItem(KEY) || "null")); } catch (e) { return blank(); }
  }
  var saveOk = true;
  function save(s) {
    try { localStorage.setItem(KEY, JSON.stringify(s)); saveOk = true; } catch (e) { saveOk = false; }
    return saveOk;
  }

  // Tickers: filings write BRK.B; people type brk-b, BRK/B, " brk.b "
  function norm(t) { return String(t || "").trim().toUpperCase().replace(/[\/\-]/g, ".").replace(/[^A-Z0-9.]/g, ""); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function slug(text) {
    return String(text).replace(/\(\d+\)\s*$/, "").trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  }
  function pageKey() {
    var p = location.pathname;
    if (/\/s\/[^\/]*$/.test(p)) return "s/stock";  // one layout for every stock page
    return p.split("/").pop() || "index.html";
  }
  function hasStuff(s) {
    return s.watch.length > 0 || s.journal.length > 0 || s.accounts.some(function (a) { return a.holdings.length; });
  }

  // ---------------------------------------------------------------- alerts
  function tracked(s) {
    var out = {};
    s.watch.forEach(function (w) { if (!w.mute) out[w.t] = "watch"; });
    if (s.rules.holdings) s.accounts.forEach(function (a) {
      a.holdings.forEach(function (h) { if (!(h.t in out)) out[h.t] = "own"; });
    });
    s.watch.forEach(function (w) { if (w.mute) delete out[w.t]; });
    return out;
  }

  function daysAgo(d, asof) {
    return Math.round((Date.parse(asof) - Date.parse(d)) / 864e5);
  }

  function alerts(s, data) {
    var want = tracked(s), r = s.rules, out = [], asof = data.asof;
    (data.events || []).forEach(function (e) {
      if (!(e.t in want) || daysAgo(e.d, asof) > 30) return;
      var ok = e.k === "news" ? r.news
        : e.k === "ins" ? (e.s === "buy" ? r.insBuy : r.insSell)
        : (e.s === "buy" ? r.polBuy : r.polSell);
      if (!ok) return;
      out.push({ id: [e.k, e.t, e.d, e.w || e.x || "", e.s || ""].join("|"), t: e.t, d: e.d, k: e.k, e: e });
    });
    var th = +r.move || 0, q = data.quotes || {};
    if (th > 0) Object.keys(want).forEach(function (t) {
      var x = q[t];
      if (!x) return;
      var r1 = x[1], r5 = x[2], d = x[7];
      if (r1 != null && Math.abs(r1) * 100 >= th)
        out.push({ id: "move|" + t + "|" + d + "|1d", t: t, d: d, k: "move", e: { r: r1, span: "today" } });
      else if (r5 != null && Math.abs(r5) * 100 >= th * 2)
        out.push({ id: "move|" + t + "|" + d + "|5d", t: t, d: d, k: "move", e: { r: r5, span: "this week" } });
    });
    out.sort(function (a, b) { return a.d < b.d ? 1 : a.d > b.d ? -1 : 0; });
    out.forEach(function (a) { a.unread = !s.seen[a.id] && daysAgo(a.d, asof) <= 14; a.why = want[a.t]; });
    // decision-journal reviews that have come due sit on top until you write the review
    var due = dueReviews(s).map(function (j) {
      return { id: "jr|" + j.id + "|" + j.review, t: j.t, d: j.review, k: "journal", e: j, why: "journal", unread: !s.seen["jr|" + j.id + "|" + j.review] };
    });
    return due.concat(out);
  }

  // ---------------------------------------------------------------- decision journal
  function today() {  // local date, YYYY-MM-DD
    var d = new Date();
    return new Date(d.getTime() - d.getTimezoneOffset() * 6e4).toISOString().slice(0, 10);
  }
  function dueReviews(s) {
    var now = today();
    return s.journal.filter(function (j) { return !j.closed && j.review && j.review <= now; })
      .sort(function (a, b) { return a.review < b.review ? -1 : 1; });
  }

  function alertText(a) {
    var e = a.e;
    if (a.k === "move") return (e.r >= 0 ? "Up " : "Down ") + Math.abs(e.r * 100).toFixed(1) + "% " + e.span;
    if (a.k === "news") return "Company news: " + (e.x || "SEC filing");
    if (a.k === "journal") return "Time to review why you bought it" + (e.d ? " (bought " + e.d + ")" : "");
    var who = e.w || "", group = e.g || "";
    if (a.k === "ins") return "Insider " + (e.s === "buy" ? "bought" : "sold") + (e.a ? " " + e.a : "") + (who ? ": " + who : "");
    return group + " " + (e.s === "buy" ? "buy" : "sale") + (e.a ? " (" + e.a + ")" : "") + (who ? ": " + who : "");
  }

  function markSeen(s, list) {
    var keep = {};
    list.forEach(function (a) { keep[a.id] = 1; });
    // forget ids that have aged out of the feed so storage stays small
    Object.keys(s.seen).forEach(function (id) { if (!(id in keep)) delete s.seen[id]; });
    list.forEach(function (a) { s.seen[a.id] = 1; });
  }

  // ---------------------------------------------------------------- data
  var dataPromise = null;
  function getData() {
    if (!dataPromise) dataPromise = fetch(ROOT + "mine.json", { cache: "no-cache" })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .catch(function () { return { asof: new Date().toISOString().slice(0, 10), quotes: {}, events: [], ideas: {}, health: {}, names: {}, pages: {} }; })
      .then(function (data) {
        var ts = state.watch.map(function (w) { return w.t; });
        state.accounts.forEach(function (a) { a.holdings.forEach(function (h) { ts.push(h.t); }); });
        return fillQuotes(data, ts);
      });
    return dataPromise;
  }

  // mine.json carries quotes for the popular list (universe.txt) and recently
  // traded stocks. Anything else you hold or watch is priced from the
  // whole-market lookup files (look/d/<TICKER>.json, written daily by
  // lookup.py), fetched one by one from this site, so no list of what you own
  // ever leaves your browser except as ordinary page requests.
  var lookTried = {};
  function lookSlug(t) { return norm(t).replace(/[^A-Z0-9]+/g, "-").replace(/^-|-$/g, ""); }
  function quoteFrom(doc) {
    var d = doc && doc.px && doc.px.d;
    if (!d || !d.t || d.t.length < 2) return null;
    var ts = [], cs = d.c, t = 0;
    for (var i = 0; i < d.t.length; i++) { t = i ? t + d.t[i] : d.t[i]; ts.push(t); }
    var n = cs.length, last = ts[n - 1];
    function ret(k) { return k >= 0 && k < n - 1 && cs[k] ? Math.round((cs[n - 1] / cs[k] - 1) * 1e4) / 1e4 : null; }
    var iy = -1;
    for (var j = 0; j < n; j++) if (ts[j] >= last - 365) { iy = j; break; }
    var tail = cs.slice(Math.max(iy, 0));
    return [cs[n - 1], ret(n - 2), ret(n - 6), ret(n - 22), ts[0] <= last - 365 ? ret(iy) : null,
            Math.max.apply(null, tail), Math.min.apply(null, tail), new Date(last * 864e5).toISOString().slice(0, 10)];
  }
  function fillQuotes(data, tickers) {
    data.quotes = data.quotes || {}; data.names = data.names || {};
    var todo = (tickers || []).map(norm).filter(function (t) { return t && !data.quotes[t] && !lookTried[t]; });
    return Promise.all(todo.map(function (t) {
      lookTried[t] = 1;
      return fetch(ROOT + "look/d/" + lookSlug(t) + ".json").then(function (r) { return r.ok ? r.json() : null; })
        .then(function (doc) {
          var q = quoteFrom(doc);
          if (q) data.quotes[t] = q;
          if (doc && doc.n && !data.names[t]) data.names[t] = doc.n;
        }).catch(function () { /* offline or unknown: the page asks for a manual price */ });
    })).then(function () { return data; });
  }

  function priceOf(h, data) {
    var q = data.quotes[h.t];
    if (h.px > 0) return { c: +h.px, r1: q ? q[1] : null, manual: true };
    return q ? { c: q[0], r1: q[1] } : null;
  }

  function totals(accounts, data) {
    var v = 0, prev = 0, cost = 0, costKnown = 0, missing = 0;
    accounts.forEach(function (a) {
      v += +a.cash || 0; prev += +a.cash || 0;
      a.holdings.forEach(function (h) {
        var p = priceOf(h, data), sh = +h.sh || 0;
        if (!p) { missing++; return; }
        var val = p.c * sh;
        v += val;
        prev += p.r1 != null ? val / (1 + p.r1) : val;
        if (h.cost > 0) { cost += h.cost * sh; costKnown += val; }
      });
    });
    return { value: v, day: v - prev, dayPct: prev ? v / prev - 1 : 0, cost: cost,
             gain: cost ? costKnown - cost : null, missing: missing };
  }

  function money(x, cents) {
    if (x == null || isNaN(x)) return "—";
    var n = Math.abs(x).toLocaleString("en-US", { minimumFractionDigits: cents ? 2 : 0, maximumFractionDigits: cents ? 2 : 0 });
    return (x < 0 ? "-$" : "$") + n;
  }
  function pct(x, plus) {
    if (x == null || isNaN(x)) return "—";
    return (plus !== false && x >= 0 ? "+" : "") + (x * 100).toFixed(1) + "%";
  }

  // ---------------------------------------------------------------- focus mode
  // On by default: trims what long-term investors (Buffett, Munger, Lynch...)
  // would call noise. Hourly headlines, the live intraday chart, options bets,
  // prediction odds, analyst calls, most Congress trades and trending/buzz.
  // Kept: insider buying, fund holdings, the economy, small caps, fundamentals.
  var FOCUS = {
    // whole pages: off the menu and tab bar, with a note if you open one anyway
    pages: { "news.html": "hourly headlines", "markets.html": "prediction-market odds", "congress.html": "Congress trades" },
    // sections, by heading (as a slug), on each page layout
    units: {
      "index.html": ["trending-now", "what-the-crowd-expects"],
      "moves.html": ["analyst-calls-this-week", "unusual-options-activity"],
      "ideas.html": ["congress-buys"],
      "s/stock": ["latest-headlines", "what-analysts-say", "options-activity", "what-bettors-expect"]
    },
    feed: ["news", "congress", "wh", "bets", "analysts"],  // home live-feed kinds left out
    hides: ["Hourly news headlines", "The live 1-day chart", "Unusual options bets", "Prediction-market odds",
            "Analyst upgrades and price targets", "Congress and White House trades", "Trending tickers and buzz"],
    keeps: ["Insider buying", "Fund holdings (13F)", "The economy", "Small caps", "Company fundamentals"]
  };
  function focusOn() { return !!state.prefs.focus; }

  function focusCss() {
    var css = Object.keys(FOCUS.pages).map(function (f) {
      return ".bar a.nav[href$='" + f + "'],.tabs a[href$='" + f + "'],.hx:has(a.hl[href='" + f + "'])";
    }).join(",") + "{display:none!important}";
    css += FOCUS.feed.map(function (k) { return ".ops .pill[data-f='" + k + "'],.ops .feed li[data-k='" + k + "']"; }).join(",") +
      ",.lk-ranges button[data-r='1D'],.chip.buzz{display:none!important}";
    return css;
  }

  // ---------------------------------------------------------------- page layout
  var state = load();

  function navStyle() {
    var css = state.prefs.navHidden.map(function (k) {
      var f = k.replace(/[^a-z0-9_.\-]/gi, "");
      return ".bar a.nav[href$='" + f + "'],.tabs a[href$='" + f + "']{display:none!important}";
    }).join("");
    if (state.prefs.privacy) css += ".qt-money{filter:blur(7px)}";
    if (focusOn()) css += focusCss();
    var root = document.documentElement;
    root.classList.toggle("qt-focus", focusOn());
    // the home feed's own script reads this so its counts and "Show more" skip these kinds
    if (focusOn()) root.dataset.feedSkip = FOCUS.feed.join(" "); else delete root.dataset.feedSkip;
    var el = document.getElementById("qt-prefs-style");
    if (!el) { el = document.createElement("style"); el.id = "qt-prefs-style"; document.head.appendChild(el); }
    el.textContent = css;
  }
  navStyle();

  var CSS = [
    ".qt-badge{display:inline-block;min-width:17px;height:17px;padding:0 5px;margin-left:5px;border-radius:999px;",
    "background:var(--down,#ff5c63);color:#fff;font:700 11px/17px Inter,system-ui,sans-serif;text-align:center;vertical-align:1px}",
    ".tabs a{position:relative}.tabs .qt-badge{position:absolute;top:-4px;left:calc(50% + 6px);margin:0}",
    ".qt-hidden{display:none!important}",
    "body.qt-custom .qt-hidden{display:revert!important;opacity:.35}",
    ".qt-unit-btn{font:600 12px Inter,system-ui,sans-serif;margin-left:10px;padding:3px 10px;border-radius:999px;cursor:pointer;",
    "border:1px solid var(--line);background:var(--card);color:var(--fg);vertical-align:middle}",
    ".qt-unit-btn.off{background:var(--acc);color:var(--acc-ink);border-color:var(--acc)}",
    ".qt-custombar{position:sticky;top:57px;z-index:19;display:flex;gap:10px;align-items:center;flex-wrap:wrap;",
    "max-width:1180px;margin:0 auto;padding:10px 20px;background:var(--card2);border-bottom:1px solid var(--line);font-size:14px}",
    ".qt-custombar a{font-weight:700}",
    ".qt-home{display:flex;flex-wrap:wrap;gap:6px 18px;align-items:center;margin:0 0 18px;padding:14px 18px;",
    "border:1px solid var(--line);border-radius:16px;background:var(--card);box-shadow:var(--shadow);text-decoration:none;color:var(--fg)}",
    ".qt-home b{font-size:18px}.qt-home .mut{font-size:13px}.qt-home .go{margin-left:auto;color:var(--acc);font-weight:700}",
    ".qt-home .up{color:var(--up)}.qt-home .down{color:var(--down)}",
    ".qt-focus-btn{display:inline-flex;align-items:center;gap:6px;margin-right:8px;white-space:nowrap}",
    ".qt-focus-btn i{width:8px;height:8px;border-radius:50%;border:1.5px solid var(--mut)}",
    ".qt-focus-btn.on{border-color:var(--acc);color:var(--acc)}.qt-focus-btn.on i{background:var(--acc);border-color:var(--acc)}",
    ".qt-focusnote{display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center;margin:0 0 18px;padding:12px 16px;font-size:14px;",
    "border:1px solid color-mix(in srgb,var(--acc) 30%,var(--line));border-radius:14px;background:color-mix(in srgb,var(--acc) 8%,var(--card))}",
    ".qt-focusnote button{font:600 13px Inter,system-ui,sans-serif;padding:5px 12px;border-radius:999px;cursor:pointer;",
    "border:1px solid var(--line);background:var(--card);color:var(--fg)}",
    "@media(max-width:700px){.qt-focus-btn{padding:6px 9px}.qt-focus-btn span{display:none}}"
  ].join("");

  // A page "unit" is a top-level heading with everything under it until the
  // next heading, a top-level collapsible list, or the summary box at the top.
  // Works on any page, including ones added later.
  function textOf(el) {
    var c = el.cloneNode(true);
    c.querySelectorAll(".qt-unit-btn").forEach(function (b) { b.remove(); });
    return c.textContent.trim();
  }

  function units(main) {
    var out = [], cur = null, kids = Array.prototype.slice.call(main.children);
    kids.forEach(function (el) {
      if (el.matches(".qt-home,.qt-custombar,script,style")) return;
      var h2 = el.tagName === "H2" ? el : el.matches(".hh") ? el.querySelector("h2") : null;
      if (h2) {  // a heading, or a heading row (heading + "see all" link)
        cur = { id: "h-" + slug(textOf(h2)), label: textOf(h2), head: h2, els: [el] };
        out.push(cur);
      } else if (el.tagName === "DETAILS" && el.querySelector("summary")) {
        var sm = el.querySelector("summary");
        if (cur) cur.els.push(el);  // hiding the heading hides its lists too
        out.push({ id: "d-" + slug(textOf(sm)), label: textOf(sm), head: sm, els: [el], inner: true });
      } else if (el.matches(".hero") && !cur) {
        out.push({ id: "hero", label: "Summary box", head: el, els: [el], box: true });
      } else if (cur) {
        cur.els.push(el);
      }
    });
    // pages built from <section> blocks or cards (stock pages, Congress, home...)
    main.querySelectorAll("section, .blk").forEach(function (sec) {
      var h = sec.querySelector(":scope > h2, :scope > .hh > h2");
      if (!h || (sec.parentElement && sec.parentElement.closest("section, .blk"))) return;
      out.push({ id: "s-" + slug(textOf(h)), label: textOf(h), head: h, els: [sec] });
    });
    return out;
  }

  function byFocus(key, u) {  // ids carry a kind prefix (h-, s-, d-); focus lists the slug
    return focusOn() && (FOCUS.units[key] || []).indexOf(u.id.replace(/^[a-z]-/, "")) >= 0;
  }

  function applyHide(main, custom) {
    var key = pageKey(), hidden = state.prefs.hide[key] || [];
    var list = units(main), off = new Set();
    list.forEach(function (u) {
      if (hidden.indexOf(u.id) >= 0 || byFocus(key, u)) u.els.forEach(function (el) { off.add(el); });
    });
    list.forEach(function (u) { u.els.forEach(function (el) { el.classList.toggle("qt-hidden", off.has(el)); }); });
    list.forEach(function (u) {
      var off = hidden.indexOf(u.id) >= 0;
      if (!custom) return;
      if (byFocus(key, u)) {  // Focus mode owns this one; say so instead of offering a toggle
        if (!u.head.querySelector(":scope > .qt-unit-btn")) {
          var tag = document.createElement("span");
          tag.className = "qt-unit-btn off"; tag.textContent = "Hidden by Focus";
          tag.title = "Turn Focus off (top of the page) to see it";
          u.head.appendChild(tag);
        }
        return;
      }
      var b = u.head.querySelector(":scope > .qt-unit-btn");
      if (!b) {
        b = document.createElement("button");
        b.type = "button"; b.className = "qt-unit-btn";
        b.addEventListener("click", function (ev) {
          ev.preventDefault(); ev.stopPropagation();
          var list = state.prefs.hide[key] || (state.prefs.hide[key] = []), i = list.indexOf(u.id);
          if (i >= 0) list.splice(i, 1); else list.push(u.id);
          if (!list.length) delete state.prefs.hide[key];
          save(state); applyHide(main, true);
        });
        if (u.box) u.head.insertBefore(b, u.head.firstChild); else u.head.appendChild(b);
      }
      b.textContent = off ? "Show" : "Hide";
      b.classList.toggle("off", off);
    });
  }

  function customBar(main) {
    var bar = document.createElement("div");
    bar.className = "qt-custombar";
    bar.innerHTML = "<span><b>Customizing this page.</b> Tap Hide or Show next to each section. " +
      "Saved on this device only.</span><span style='flex:1'></span>" +
      "<button type='button' class='qt-unit-btn' id='qt-reset'>Show everything</button>" +
      "<a href='" + location.pathname + "'>Done</a>";
    var header = document.querySelector("header");
    (header && header.parentNode ? header.parentNode : document.body).insertBefore(bar, header ? header.nextSibling : main);
    bar.querySelector("#qt-reset").onclick = function () {
      delete state.prefs.hide[pageKey()]; save(state); applyHide(main, true);
    };
  }

  function setFocus(on) {
    state.prefs.focus = !!on; save(state); location.reload();
  }

  function focusButton() {  // in the header, next to the light/dark toggle, on every page
    var theme = document.getElementById("theme");
    if (!theme || document.getElementById("qt-focus")) return;
    var b = document.createElement("button");
    b.type = "button"; b.id = "qt-focus"; b.className = "toggle qt-focus-btn" + (focusOn() ? " on" : "");
    b.setAttribute("aria-pressed", focusOn() ? "true" : "false");
    b.title = focusOn() ? "Focus is on: hourly news, odds, options, analyst calls, Congress trades and trending are hidden. Tap to show everything."
                        : "Focus is off. Tap to hide short-term noise and keep what matters for long-term investing.";
    b.innerHTML = "<i></i>Focus<span>" + (focusOn() ? " on" : " off") + "</span>";
    b.setAttribute("aria-label", "Focus mode " + (focusOn() ? "on" : "off"));
    b.onclick = function () { setFocus(!focusOn()); };
    theme.parentNode.insertBefore(b, theme);
  }

  function focusNote(main) {  // you opened a page Focus keeps off the menu
    var what = FOCUS.pages[pageKey()];
    if (!focusOn() || !what) return;
    var n = document.createElement("div");
    n.className = "qt-focusnote";
    n.innerHTML = "<span><b>Focus mode keeps " + esc(what) + " off your menu.</b> For a long-term investor it's mostly short-term noise. " +
      "It's here if you want it.</span><span style='flex:1'></span><button type='button'>Turn Focus off</button>";
    n.querySelector("button").onclick = function () { setFocus(false); };
    main.insertBefore(n, main.firstChild);
  }

  function badge(n) {
    document.querySelectorAll("a[href$='mine.html']").forEach(function (a) {
      if (!a.closest(".bar,.tabs")) return;
      var b = a.querySelector(".qt-badge");
      if (!n) { if (b) b.remove(); return; }
      if (!b) { b = document.createElement("span"); b.className = "qt-badge"; (a.querySelector("span") || a).appendChild(b); }
      b.textContent = n > 99 ? "99+" : n;
    });
  }

  function notify(list) {
    if (!state.prefs.notify || !("Notification" in window) || Notification.permission !== "granted") return;
    var fresh = list.filter(function (a) { return a.unread && !state.notified[a.id]; });
    if (!fresh.length) return;
    var body = fresh.slice(0, 3).map(function (a) { return a.t + ": " + alertText(a); }).join("\n") +
      (fresh.length > 3 ? "\n+" + (fresh.length - 3) + " more" : "");
    var title = fresh.length + " new Quantreyda alert" + (fresh.length > 1 ? "s" : "");
    var opts = { body: body, tag: "qt-alerts", icon: ROOT + "icon-192.png", data: { url: ROOT + "mine.html#alerts" } };
    var done = function () {
      fresh.forEach(function (a) { state.notified[a.id] = 1; });
      var keep = {};
      list.forEach(function (a) { if (state.notified[a.id]) keep[a.id] = 1; });
      state.notified = keep; save(state);
    };
    if (navigator.serviceWorker && navigator.serviceWorker.getRegistration) {
      navigator.serviceWorker.getRegistration().then(function (reg) {
        if (reg && reg.showNotification) return reg.showNotification(title, opts);
        new Notification(title, opts);
      }).then(done, function () {});
    } else {
      try { new Notification(title, opts); done(); } catch (e) {}
    }
  }

  function homeCard(main, data, list) {
    if (!state.prefs.homeCard || pageKey() !== "index.html") return;
    var held = state.accounts.filter(function (a) { return a.holdings.length || +a.cash; });
    var unread = list.filter(function (a) { return a.unread; }).length;
    if (!held.length && !state.watch.length) return;
    var t = totals(held, data), cls = t.day >= 0 ? "up" : "down";
    var a = document.createElement("a");
    a.className = "qt-home"; a.href = ROOT + "mine.html";
    a.innerHTML = (held.length ? "<span><span class='mut'>Your accounts</span><br><b class='qt-money'>" + money(t.value) + "</b></span>" +
      "<span><span class='mut'>Today</span><br><b class='" + cls + "'><span class='qt-money'>" + money(t.day) + "</span> " + pct(t.dayPct) + "</b></span>" : "") +
      "<span><span class='mut'>Alerts</span><br><b>" + (unread ? unread + " new" : "None new") + "</b></span>" +
      "<span class='go'>Mine →</span>";
    main.insertBefore(a, main.firstChild);
  }

  function boot() {
    var st = document.createElement("style"); st.textContent = CSS; document.head.appendChild(st);
    var main = document.querySelector("main");
    var custom = /[?&]customize\b/.test(location.search);
    focusButton();
    if (main && !document.getElementById("mine-app")) {
      if (custom) { document.body.classList.add("qt-custom"); customBar(main); }
      applyHide(main, custom);
      focusNote(main);
    }
    if (!hasStuff(state) || document.getElementById("mine-app")) return;  // the Mine page draws its own
    getData().then(function (data) {
      var list = alerts(state, data);
      badge(list.filter(function (a) { return a.unread; }).length);
      if (main) homeCard(main, data, list);
      notify(list);
    });
  }

  window.QT = {
    KEY: KEY, ROOT: ROOT, KINDS: KINDS, IRA_LIMIT: IRA_LIMIT,
    load: load, save: save, blank: blank, fill: fill, norm: norm, esc: esc, getData: getData,
    alerts: alerts, alertText: alertText, dueReviews: dueReviews, today: today, markSeen: markSeen, badge: badge, notify: notify,
    FOCUS: FOCUS, focusOn: focusOn,
    priceOf: priceOf, totals: totals, fillQuotes: fillQuotes, quoteFrom: quoteFrom, money: money, pct: pct, navStyle: navStyle,
    saveFailed: function () { return !saveOk; },
    get state() { return state; }, set state(s) { state = fill(s); }
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})();
