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
      v: 1, accounts: [], watch: [],
      rules: { insBuy: true, insSell: false, polBuy: true, polSell: true, news: true, move: 5, holdings: true },
      prefs: { navHidden: [], hide: {}, homeCard: true, notify: false, privacy: false },
      seen: {}, notified: {}
    };
  }

  function fill(s) {  // add any fields an older save is missing
    var b = blank();
    s = s && typeof s === "object" ? s : {};
    ["accounts", "watch"].forEach(function (k) { if (!Array.isArray(s[k])) s[k] = []; });
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
  function hasStuff(s) { return s.watch.length > 0 || s.accounts.some(function (a) { return a.holdings.length; }); }

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
    return out;
  }

  function alertText(a) {
    var e = a.e;
    if (a.k === "move") return (e.r >= 0 ? "Up " : "Down ") + Math.abs(e.r * 100).toFixed(1) + "% " + e.span;
    if (a.k === "news") return "Company news: " + (e.x || "SEC filing");
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
      .catch(function () { return { asof: new Date().toISOString().slice(0, 10), quotes: {}, events: [], ideas: {}, health: {}, names: {}, pages: {} }; });
    return dataPromise;
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

  // ---------------------------------------------------------------- page layout
  var state = load();

  function navStyle() {
    var css = state.prefs.navHidden.map(function (k) {
      var f = k.replace(/[^a-z0-9_.\-]/gi, "");
      return ".bar a.nav[href$='" + f + "'],.tabs a[href$='" + f + "']{display:none!important}";
    }).join("");
    if (state.prefs.privacy) css += ".qt-money{filter:blur(7px)}";
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
    ".qt-home .up{color:var(--up)}.qt-home .down{color:var(--down)}"
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
      if (el.tagName === "H2") {
        cur = { id: "h-" + slug(textOf(el)), label: textOf(el), head: el, els: [el] };
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
    // pages built from <section> blocks (stock pages, Congress, Insiders...)
    main.querySelectorAll("section").forEach(function (sec) {
      var h = sec.querySelector(":scope > h2");
      if (!h || (sec.parentElement && sec.parentElement.closest("section"))) return;
      out.push({ id: "s-" + slug(textOf(h)), label: textOf(h), head: h, els: [sec] });
    });
    return out;
  }

  function applyHide(main, custom) {
    var key = pageKey(), hidden = state.prefs.hide[key] || [];
    var list = units(main), off = new Set();
    list.forEach(function (u) { if (hidden.indexOf(u.id) >= 0) u.els.forEach(function (el) { off.add(el); }); });
    list.forEach(function (u) { u.els.forEach(function (el) { el.classList.toggle("qt-hidden", off.has(el)); }); });
    list.forEach(function (u) {
      var off = hidden.indexOf(u.id) >= 0;
      if (!custom) return;
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
    if (main && !document.getElementById("mine-app")) {
      if (custom) { document.body.classList.add("qt-custom"); customBar(main); }
      applyHide(main, custom);
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
    alerts: alerts, alertText: alertText, markSeen: markSeen, badge: badge, notify: notify,
    priceOf: priceOf, totals: totals, money: money, pct: pct, navStyle: navStyle,
    saveFailed: function () { return !saveOk; },
    get state() { return state; }, set state(s) { state = fill(s); }
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})();
