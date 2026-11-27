// Quantreyda lookup: the search box in the header, look.html?s=TICKER, and
// the "Price history and financials" panel on stock pages.
//
// Everything is read from static files the daily build writes (lookup.py):
//   look/index.json      every US stock, ETF and mutual fund
//   look/d/<SLUG>.json   one ticker's prices, dividends, splits, financials
//
// Buy and Sell open the same symbol at Fidelity in a new tab. This app never
// places an order and never sees your Fidelity login: you review and submit
// the trade on Fidelity's own site or app.
(function () {
  "use strict";
  var me = document.currentScript;
  var BASE = me ? new URL(".", me.src).href : "./";
  var FID_QUOTE = "https://digital.fidelity.com/prgw/digital/research/quote/dashboard/summary?symbol=";
  var KIND_LABEL = { stock: "Stock", etf: "ETF", fund: "Mutual fund", money: "Money market fund" };
  var RANGES = [["5D", 7], ["1M", 31], ["6M", 183], ["YTD", 0], ["1Y", 366], ["5Y", 1827], ["Max", 1e9]];
  var DAY = 864e5;
  // 1D: TradingView's free chart widget (no key). It loads from tradingview.com
  // in the browser, so it's live (or a few minutes delayed for some exchanges).
  var TV_JS = "https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js";
  var TV_EXCH = { "Nasdaq": "NASDAQ", "NYSE": "NYSE", "NYSE Arca": "AMEX", "NYSE American": "AMEX", "Cboe BZX": "CBOE" };

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function norm(t) { return String(t || "").trim().toUpperCase().replace(/[\/\-]/g, ".").replace(/[^A-Z0-9.]/g, ""); }
  function slug(t) { return norm(t).replace(/[^A-Z0-9]+/g, "-").replace(/^-|-$/g, ""); }
  function yahoo(t) { return norm(t).replace(/\./g, "-"); }
  function fidelity(t) { return FID_QUOTE + encodeURIComponent(yahoo(t)); }

  // ------------------------------------------------------------ formatting
  function money(v, dp) {
    if (v == null || !isFinite(v)) return "—";
    var a = Math.abs(v), s = v < 0 ? "-" : "";
    if (a >= 1e12) return s + "$" + (a / 1e12).toFixed(2) + "T";
    if (a >= 1e9) return s + "$" + (a / 1e9).toFixed(2) + "B";
    if (a >= 1e6) return s + "$" + (a / 1e6).toFixed(1) + "M";
    if (a >= 1e4) return s + "$" + Math.round(a / 1e3) + "K";
    return s + "$" + a.toFixed(dp == null ? 2 : dp);
  }
  function price(v) {
    if (v == null || !isFinite(v)) return "—";
    return "$" + v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: v < 1 ? 4 : 2 });
  }
  function num(v) {
    if (v == null || !isFinite(v)) return "—";
    var a = Math.abs(v);
    if (a >= 1e9) return (v / 1e9).toFixed(2) + "B";
    if (a >= 1e6) return (v / 1e6).toFixed(1) + "M";
    return Math.round(v).toLocaleString("en-US");
  }
  function pct(v, dp) {
    if (v == null || !isFinite(v)) return "—";
    return (v >= 0 ? "+" : "") + (v * 100).toFixed(dp == null ? 1 : dp) + "%";
  }
  function plainPct(v) { return v == null || !isFinite(v) ? "—" : (v * 100).toFixed(1) + "%"; }
  function ratio(v) { return v == null || !isFinite(v) || v <= 0 ? "—" : v.toFixed(v < 10 ? 1 : 0); }
  function cls(v) { return v == null ? "" : v >= 0 ? "up" : "down"; }
  function fmtDate(t, opt) {
    return new Date(t * DAY).toLocaleDateString("en-US", Object.assign({ timeZone: "UTC" }, opt || { month: "short", day: "numeric", year: "numeric" }));
  }
  function iso2t(s) { return Math.round(Date.parse(s + "T00:00:00Z") / DAY); }

  // ------------------------------------------------------------ data
  var indexP = null;
  function loadIndex() {
    if (!indexP) indexP = fetch(BASE + "look/index.json").then(function (r) {
      if (!r.ok) throw new Error(r.status); return r.json();
    }).then(function (j) {
      var kinds = j.kinds || ["stock", "etf", "fund", "money"];
      return j.s.map(function (r) {
        return { t: r[0], n: r[1], k: kinds[r[2]] || "stock", page: !!r[3], has: !!r[4], nl: (r[0] + " " + r[1]).toLowerCase() };
      });
    }).catch(function () { indexP = null; return []; });
    return indexP;
  }
  function decode(s) {  // {"t": [epoch day, gaps...], "c": [...]} -> [[t, close]]
    var out = [], t = 0;
    if (!s || !s.t) return out;
    for (var i = 0; i < s.t.length; i++) { t = i ? t + s.t[i] : s.t[i]; out.push([t, s.c[i]]); }
    return out;
  }
  function loadDoc(t) {
    return fetch(BASE + "look/d/" + slug(t) + ".json").then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; });
  }
  function hrefFor(row) {
    return row.page ? BASE + "s/" + slug(row.t) + ".html" : BASE + "look.html?s=" + encodeURIComponent(row.t);
  }

  // ------------------------------------------------------------ search box
  function search(rows, q) {
    q = q.trim().toLowerCase();
    if (!q) return [];
    var up = norm(q), exact = [], pre = [], word = [], any = [];
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i], t = r.t;
      if (t === up) exact.push(r);
      else if (t.indexOf(up) === 0 && up) pre.push(r);
      else if (r.nl.indexOf(" " + q) >= 0) word.push(r);
      else if (q.length > 2 && r.nl.indexOf(q) >= 0) any.push(r);
      if (pre.length + word.length > 400) break;
    }
    var rank = function (a, b) { return (b.has - a.has) || (a.t.length - b.t.length) || (a.t < b.t ? -1 : 1); };
    return exact.concat(pre.sort(rank), word.sort(rank), any.sort(rank)).slice(0, 8);
  }
  function wire(input) {
    if (!input || input.dataset.wired) return;
    input.dataset.wired = "1";
    var box = input.parentNode.querySelector(".lk-sugg");
    if (!box) { box = document.createElement("div"); box.className = "lk-sugg"; box.hidden = true; input.parentNode.appendChild(box); }
    var hits = [], on = -1;
    function draw() {
      box.innerHTML = hits.map(function (r, i) {
        return "<a href='" + esc(hrefFor(r)) + "' class='" + (i === on ? "on" : "") + "'><b>" + esc(r.t) + "</b><span>" + esc(r.n) +
          "</span><em>" + esc(KIND_LABEL[r.k] || "") + "</em></a>";
      }).join("") || (input.value.trim() ? "<a href='" + esc(BASE + "look.html?s=" + encodeURIComponent(norm(input.value))) +
        "'><b>" + esc(norm(input.value)) + "</b><span>Look it up anyway</span></a>" : "");
      box.hidden = !box.innerHTML;
    }
    function update() {
      loadIndex().then(function (rows) { hits = search(rows, input.value); on = hits.length ? 0 : -1; draw(); });
    }
    input.addEventListener("focus", function () { loadIndex(); if (input.value) update(); });
    input.addEventListener("input", update);
    input.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault(); if (!hits.length) return;
        on = (on + (e.key === "ArrowDown" ? 1 : hits.length - 1)) % hits.length; draw();
      } else if (e.key === "Enter") {
        e.preventDefault();
        var v = norm(input.value); if (!v) return;
        loadIndex().then(function (rows) {
          var r = hits[on] || rows.filter(function (x) { return x.t === v; })[0];
          location.href = r ? hrefFor(r) : BASE + "look.html?s=" + encodeURIComponent(v);
        });
      } else if (e.key === "Escape") { box.hidden = true; input.blur(); }
    });
    document.addEventListener("click", function (e) { if (!input.parentNode.contains(e.target)) box.hidden = true; });
  }

  // ------------------------------------------------------------ chart
  function series(doc) {
    var px = doc.px || {};
    var all = decode(px.m).concat(decode(px.w), decode(px.d));
    all.sort(function (a, b) { return a[0] - b[0]; });
    var out = [];
    all.forEach(function (p) { if (!out.length || p[0] > out[out.length - 1][0]) out.push(p); });
    return out;
  }
  function slice(all, key) {
    if (!all.length) return [];
    var last = all[all.length - 1][0], from;
    if (key === "YTD") from = iso2t(new Date(last * DAY).getUTCFullYear() + "-01-01") - 1;
    else from = last - (RANGES.filter(function (r) { return r[0] === key; })[0] || [0, 366])[1];
    var pts = all.filter(function (p) { return p[0] > from; });
    if (key === "5D") pts = all.slice(-5);
    if (pts.length < 2) pts = all.slice(-2);
    var step = Math.ceil(pts.length / 700);  // keep the SVG light
    if (step > 1) pts = pts.filter(function (p, i) { return i % step === 0 || i === pts.length - 1; });
    return pts;
  }
  function nearest(all, t) {  // close on or before day t
    var lo = 0, hi = all.length - 1, best = null;
    while (lo <= hi) { var m = (lo + hi) >> 1; if (all[m][0] <= t) { best = all[m]; lo = m + 1; } else hi = m - 1; }
    return best;
  }
  function drawChart(el, pts, events, cmp, logScale) {
    var W = Math.max(300, Math.round(el.clientWidth || 900)), H = W < 600 ? 230 : 300, L = 4, R = W < 600 ? 52 : 62, T = 14, B = 28;
    var t0 = pts[0][0], t1 = pts[pts.length - 1][0];
    var base = pts[0][1];
    var vals = pts.map(function (p) { return p[1]; });
    var cmpPts = null;
    if (cmp && cmp.length) {  // the S&P 500 rebased to the same starting price
      var c0 = nearest(cmp, t0);
      if (c0) cmpPts = cmp.filter(function (p) { return p[0] >= t0 && p[0] <= t1; })
        .map(function (p) { return [p[0], p[1] / c0[1] * base]; });
      if (cmpPts) cmpPts.forEach(function (p) { vals.push(p[1]); });
    }
    var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals), pad = (hi - lo) * 0.08 || hi * 0.02 || 1;
    var X = function (t) { return L + (t - t0) / Math.max(1, t1 - t0) * (W - L - R); };
    var Y, inv;
    if (logScale && lo > 0 && hi / lo > 8) {  // decades of growth: equal % moves look equal
      var a = Math.log(lo) - 0.05, z = Math.log(hi) + 0.05;
      Y = function (v) { return T + (z - Math.log(Math.max(v, 1e-9))) / (z - a) * (H - T - B); };
      inv = function (f) { return Math.exp(a + (z - a) * f); };
    } else {
      lo -= pad; hi += pad;
      Y = function (v) { return T + (hi - v) / (hi - lo) * (H - T - B); };
      inv = function (f) { return lo + (hi - lo) * f; };
    }
    var up = pts[pts.length - 1][1] >= base, col = up ? "var(--up)" : "var(--down)";
    var line = pts.map(function (p, i) { return (i ? "L" : "M") + X(p[0]).toFixed(1) + "," + Y(p[1]).toFixed(1); }).join("");
    var g = "";
    for (var k = 0; k < 5; k++) {
      var v = inv((k + 0.5) / 5);
      g += "<line class='grid' x1='" + L + "' x2='" + (W - R) + "' y1='" + Y(v).toFixed(1) + "' y2='" + Y(v).toFixed(1) + "'/>" +
        "<text x='" + (W - R + 6) + "' y='" + (Y(v) + 4).toFixed(1) + "'>" + (v < 10 ? "$" + v.toFixed(2) : "$" + Math.round(v).toLocaleString("en-US")) + "</text>";
    }
    var span = t1 - t0, fmt = span > 1500 ? { year: "numeric", timeZone: "UTC" } : span > 200 ? { month: "short", year: "2-digit", timeZone: "UTC" } : { month: "short", day: "numeric", timeZone: "UTC" };
    var nx = W < 600 ? 3 : 5;
    for (var j = 1; j <= nx; j++) {
      var tt = t0 + span * j / (nx + 1);
      g += "<text x='" + X(tt).toFixed(1) + "' y='" + (H - 8) + "' text-anchor='middle'>" + esc(new Date(tt * DAY).toLocaleDateString("en-US", fmt)) + "</text>";
    }
    var ev = "";
    (events || []).forEach(function (e) {
      if (e[0] < t0 || e[0] > t1) return;
      if (e[1] === "D" && t1 - t0 > 1100) return;  // too many dots on long ranges
      var x = X(e[0]).toFixed(1), y = H - B;
      ev += "<g class='mk'><title>" + esc(e[2]) + "</title><circle cx='" + x + "' cy='" + (y - 6) + "' r='" + (e[1] === "S" ? 7 : 5) +
        "' fill='" + (e[1] === "S" ? "var(--warn)" : "var(--ins)") + "' opacity='.85'/>" +
        (e[1] === "S" ? "<text x='" + x + "' y='" + (y - 3) + "' text-anchor='middle' class='ev'>S</text>" : "") + "</g>";
    });
    var cmpLine = cmpPts && cmpPts.length > 1 ? "<path d='" + cmpPts.map(function (p, i) {
      return (i ? "L" : "M") + X(p[0]).toFixed(1) + "," + Y(p[1]).toFixed(1);
    }).join("") + "' fill='none' stroke='var(--mut)' stroke-width='1.5' stroke-dasharray='4 4'/>" : "";
    var id = "lkf" + Math.random().toString(36).slice(2, 7);
    el.innerHTML = "<svg class='chart lk-chart' viewBox='0 0 " + W + " " + H + "' role='img' aria-label='Price chart'>" +
      "<defs><linearGradient id='" + id + "' x1='0' y1='0' x2='0' y2='1'><stop offset='0' stop-color='" + col +
      "' stop-opacity='.22'/><stop offset='1' stop-color='" + col + "' stop-opacity='0'/></linearGradient></defs>" + g +
      "<path d='" + line + "L" + X(t1).toFixed(1) + "," + (H - B) + "L" + X(t0).toFixed(1) + "," + (H - B) + "Z' fill='url(#" + id + ")'/>" +
      cmpLine + "<path d='" + line + "' fill='none' stroke='" + col + "' stroke-width='2' stroke-linejoin='round'/>" + ev +
      "<line class='lk-x' y1='" + T + "' y2='" + (H - B) + "' visibility='hidden'/><circle class='lk-dot' r='4.5' fill='" + col +
      "' visibility='hidden'/></svg><div class='lk-tip' hidden></div>";
    var svg = el.querySelector("svg"), xl = svg.querySelector(".lk-x"), dot = svg.querySelector(".lk-dot"), tip = el.querySelector(".lk-tip");
    function move(e) {
      var r = svg.getBoundingClientRect(), cx = (e.touches ? e.touches[0].clientX : e.clientX) - r.left;
      var t = t0 + (cx / r.width * W - L) / (W - L - R) * (t1 - t0);
      var p = nearest(pts, t) || pts[0];
      var x = X(p[0]), y = Y(p[1]);
      xl.setAttribute("x1", x); xl.setAttribute("x2", x); xl.setAttribute("visibility", "visible");
      dot.setAttribute("cx", x); dot.setAttribute("cy", y); dot.setAttribute("visibility", "visible");
      var ch = p[1] / base - 1;
      tip.innerHTML = "<b>" + price(p[1]) + "</b> <span class='" + cls(ch) + "'>" + pct(ch) + "</span><br><span class='mut'>" + fmtDate(p[0]) + "</span>";
      tip.hidden = false;
      var left = x / W * r.width;
      tip.style.left = Math.min(Math.max(left - 60, 0), r.width - 130) + "px";
    }
    function leave() { xl.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); tip.hidden = true; }
    svg.addEventListener("mousemove", move); svg.addEventListener("touchmove", move, { passive: true });
    svg.addEventListener("touchstart", move, { passive: true });
    svg.addEventListener("mouseleave", leave); svg.addEventListener("touchend", leave);
  }

  function tvSymbol(t, exch) {
    var x = TV_EXCH[exch];
    return (x ? x + ":" : "") + norm(t);
  }
  function theme() { return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark"; }
  function tvChart(box, t, exch) {
    var th = theme();
    if (box.dataset.tv === th) return;  // already showing, same theme
    box.dataset.tv = th;
    box.innerHTML = "<div class='tradingview-widget-container' style='height:100%;width:100%'>" +
      "<div class='tradingview-widget-container__widget' style='height:100%;width:100%'></div></div>";
    var sc = document.createElement("script");
    sc.src = TV_JS; sc.async = true; sc.type = "text/javascript";
    sc.text = JSON.stringify({
      autosize: true, symbol: tvSymbol(t, exch), interval: "5", range: "1D", timezone: "America/New_York",
      theme: th, style: "1", locale: "en", allow_symbol_change: false, hide_side_toolbar: true,
      save_image: false, calendar: false, support_host: "https://www.tradingview.com",
      backgroundColor: th === "light" ? "#ffffff" : "#12151b", gridColor: th === "light" ? "rgba(0,0,0,0.06)" : "rgba(255,255,255,0.06)"
    });
    box.firstChild.appendChild(sc);
  }

  // ------------------------------------------------------------ stats
  function ttm(fin, key) {  // sum of the last 4 quarters, if all there
    if (!fin || !fin.q || !fin.q[key]) return null;
    var v = fin.q[key].slice(-4);
    if (v.length < 4 || v.some(function (x) { return x == null; })) return null;
    var e = fin.qe.slice(-4);
    if ((iso2t(e[3]) - iso2t(e[0])) > 300) return null;  // a gap: not four straight quarters
    return v.reduce(function (a, b) { return a + b; }, 0);
  }
  function lastOf(arr) { if (!arr) return null; for (var i = arr.length - 1; i >= 0; i--) if (arr[i] != null) return arr[i]; return null; }
  function latestInstant(fin, key) {
    var q = fin && fin.q && fin.q[key], a = fin && fin.a && fin.a[key];
    var vq = lastOf(q), va = lastOf(a);
    return vq != null ? vq : va;
  }
  function trailingRet(all, years) {
    if (all.length < 2) return null;
    var last = all[all.length - 1], start = nearest(all, last[0] - Math.round(years * 365.25));
    if (!start || last[0] - start[0] < years * 365.25 - 20) return null;
    var r = last[1] / start[1] - 1;
    return years > 1 ? Math.pow(1 + r, 1 / years) - 1 : r;
  }
  function stats(doc, all) {
    var fin = doc.fin, last = all.length ? all[all.length - 1] : null, prev = all.length > 1 ? all[all.length - 2] : null;
    var p = last ? last[1] : null, out = [];
    var yr = all.filter(function (x) { return last && x[0] > last[0] - 366; }).map(function (x) { return x[1]; });
    var divs = (doc.px && doc.px.div || []).map(function (d) { return [iso2t(d[0]), d[1]]; });
    var div12 = divs.filter(function (d) { return last && d[0] > last[0] - 366; }).reduce(function (a, d) { return a + d[1]; }, 0);
    out.push(["Previous close", price(prev ? prev[1] : null)]);
    out.push(["52-week range", yr.length ? price(Math.min.apply(null, yr)) + " – " + price(Math.max.apply(null, yr)) : "—"]);
    [[1, "1-year return"], [3, "3-year (a year)"], [5, "5-year (a year)"], [10, "10-year (a year)"]].forEach(function (r) {
      var v = trailingRet(all, r[0]); out.push([r[1], pct(v), cls(v)]);
    });
    if (fin) {
      var so = fin.so || lastOf(fin.q && fin.q.sh) || lastOf(fin.a && fin.a.sh);
      var cap = so && p ? so * p : null;
      var eps = ttm(fin, "eps"), rev = ttm(fin, "rev") || lastOf(fin.a.rev), eq = latestInstant(fin, "eq");
      var ni = ttm(fin, "ni") || lastOf(fin.a.ni);
      if (eps == null && fin.a.eps) eps = lastOf(fin.a.eps);
      out.push(["Market cap", money(cap)]);
      out.push(["P/E (past 12 months)", eps > 0 && p ? ratio(p / eps) : eps != null && eps <= 0 ? "Losing money" : "—"]);
      out.push(["EPS (past 12 months)", eps != null ? "$" + eps.toFixed(2) : "—"]);
      out.push(["Price / sales", cap && rev ? ratio(cap / rev) : "—"]);
      out.push(["Price / book", cap && eq > 0 ? ratio(cap / eq) : "—"]);
      out.push(["Profit margin", ni != null && rev ? plainPct(ni / rev) : "—"]);
      out.push(["Shares outstanding", num(so)]);
      if (div12 && eps > 0) out.push(["Payout ratio", plainPct(div12 / eps)]);
    }
    out.push([doc.k === "stock" ? "Dividend yield" : "Yield (past 12 months)", div12 && p ? plainPct(div12 / p) + " ($" + div12.toFixed(2) + ")" : "None"]);
    var ld = divs[divs.length - 1];
    if (ld) out.push(["Last dividend", "$" + ld[1].toFixed(ld[1] < 0.1 ? 4 : 2) + ", " + fmtDate(ld[0])]);
    var sp = doc.px && doc.px.spl || [];
    if (sp.length) { var s = sp[sp.length - 1]; out.push(["Last split", splitText(s[1]) + ", " + fmtDate(iso2t(s[0]))]); }
    if (doc.fam) out.push(["Fund family", esc(doc.fam)]);
    return out;
  }
  function splitText(r) { return r >= 1 ? +r.toFixed(2) + "-for-1" : "1-for-" + +(1 / r).toFixed(2) + " (reverse)"; }

  // ------------------------------------------------------------ statements
  var ROWS = {
    income: [["rev", "Revenue"], ["gp", "Gross profit"], ["oi", "Operating income"], ["ni", "Net income"],
             ["eps", "EPS (diluted)", "eps"], ["sh", "Diluted shares", "num"],
             ["gm", "Gross margin", "pct"], ["om", "Operating margin", "pct"], ["nm", "Net margin", "pct"], ["g", "Revenue growth", "pct"]],
    balance: [["assets", "Total assets"], ["liab", "Total liabilities"], ["eq", "Shareholders' equity"],
              ["cash", "Cash"], ["debt", "Long-term debt"], ["ca", "Current assets"], ["cl", "Current liabilities"],
              ["cr", "Current ratio", "x"], ["de", "Debt / equity", "x"]],
    cash: [["ocf", "Operating cash flow"], ["capex", "Capital spending"], ["fcf", "Free cash flow"],
           ["div", "Dividends paid"], ["buy", "Stock buybacks"]]
  };
  function columns(fin, period) {
    var src = period === "q" ? fin.q : fin.a, ends = period === "q" ? fin.qe : fin.fy;
    var n = ends.length, cols = [];
    function v(k, i) { return src[k] ? src[k][i] : null; }
    for (var i = n - 1; i >= 0; i--) {
      var c = { end: ends[i] };
      Object.keys(src).forEach(function (k) { c[k] = v(k, i); });
      c.fcf = c.ocf != null && c.capex != null ? c.ocf - c.capex : null;
      c.gm = c.gp != null && c.rev ? c.gp / c.rev : null;
      c.om = c.oi != null && c.rev ? c.oi / c.rev : null;
      c.nm = c.ni != null && c.rev ? c.ni / c.rev : null;
      c.cr = c.ca && c.cl ? c.ca / c.cl : null;
      c.de = c.debt != null && c.eq > 0 ? c.debt / c.eq : null;
      var back = period === "q" ? 4 : 1, pr = i - back >= 0 ? v("rev", i - back) : null;
      c.g = c.rev && pr ? c.rev / pr - 1 : null;
      cols.push(c);
    }
    return cols;
  }
  function cell(v, kind) {
    if (v == null) return "<td class='mut'>—</td>";
    if (kind === "pct") return "<td class='" + (v < 0 ? "down" : "") + "'>" + plainPct(v) + "</td>";
    if (kind === "eps") return "<td>" + (v < 0 ? "-$" : "$") + Math.abs(v).toFixed(2) + "</td>";
    if (kind === "num") return "<td>" + num(v) + "</td>";
    if (kind === "x") return "<td>" + v.toFixed(2) + "</td>";
    return "<td class='" + (v < 0 ? "down" : "") + "'>" + money(v, 0) + "</td>";
  }
  function statement(fin, tab, period) {
    var cols = columns(fin, period), rows = ROWS[tab];
    var label = function (c) {
      var d = new Date(iso2t(c.end) * DAY);
      return period === "q" ? d.toLocaleDateString("en-US", { month: "short", year: "numeric", timeZone: "UTC" })
        : "FY " + (d.getUTCMonth() < 2 ? d.getUTCFullYear() - 1 : d.getUTCFullYear());
    };
    var ttmCol = null;
    if (period === "a" && tab !== "balance") {
      ttmCol = {};
      ["rev", "gp", "oi", "ni", "eps", "ocf", "capex", "div", "buy"].forEach(function (k) { ttmCol[k] = ttm(fin, k); });
      ttmCol.fcf = ttmCol.ocf != null && ttmCol.capex != null ? ttmCol.ocf - ttmCol.capex : null;
      ttmCol.gm = ttmCol.gp != null && ttmCol.rev ? ttmCol.gp / ttmCol.rev : null;
      ttmCol.om = ttmCol.oi != null && ttmCol.rev ? ttmCol.oi / ttmCol.rev : null;
      ttmCol.nm = ttmCol.ni != null && ttmCol.rev ? ttmCol.ni / ttmCol.rev : null;
      if (ttmCol.rev == null && ttmCol.ni == null && ttmCol.ocf == null) ttmCol = null;
      else if (cols[0] && fin.qe.length && fin.qe[fin.qe.length - 1] <= cols[0].end) ttmCol = null;  // same as the last year
    }
    var have = rows.filter(function (r) {
      return cols.some(function (c) { return c[r[0]] != null; });
    });
    if (!have.length) return "<p class='hint'>Nothing on file for this statement.</p>";
    var head = "<tr><th></th>" + (ttmCol ? "<th>Past 12 mo</th>" : "") + cols.map(function (c) { return "<th>" + label(c) + "</th>"; }).join("") + "</tr>";
    var body = have.map(function (r) {
      return "<tr><th scope='row'>" + r[1] + "</th>" + (ttmCol ? cell(ttmCol[r[0]], r[2]) : "") +
        cols.map(function (c) { return cell(c[r[0]], r[2]); }).join("") + "</tr>";
    }).join("");
    return "<div class='wrap'><table class='lk-fin'><thead>" + head + "</thead><tbody>" + body + "</tbody></table></div>";
  }
  function bars(items, fmt) {  // [[label, value, value2?]]: small two-series bar chart
    var W = 900, H = 170, T = 10, B = 24, max = 0, min = 0;
    items.forEach(function (it) { [it[1], it[2]].forEach(function (v) { if (v != null) { max = Math.max(max, v); min = Math.min(min, v); } }); });
    if (max === min) return "";
    var Y = function (v) { return T + (max - v) / (max - min) * (H - T - B); };
    var bw = (W - 20) / items.length, two = items.some(function (it) { return it[2] != null; });
    var g = "<line class='grid' x1='0' x2='" + W + "' y1='" + Y(0).toFixed(1) + "' y2='" + Y(0).toFixed(1) + "'/>";
    items.forEach(function (it, i) {
      var x = 10 + i * bw, w = two ? bw * 0.36 : bw * 0.6;
      [[it[1], "var(--acc)", 0], [it[2], "var(--ins)", 1]].forEach(function (s) {
        if (s[0] == null) return;
        var y = Y(Math.max(0, s[0])), h = Math.abs(Y(s[0]) - Y(0));
        var xx = two ? x + bw * 0.12 + s[2] * w : x + bw * 0.2;
        g += "<rect x='" + xx.toFixed(1) + "' y='" + y.toFixed(1) + "' width='" + w.toFixed(1) + "' height='" + Math.max(1, h).toFixed(1) +
          "' rx='3' fill='" + (s[0] < 0 ? "var(--down)" : s[1]) + "'><title>" + esc(it[0] + ": " + fmt(s[0])) + "</title></rect>";
      });
      g += "<text x='" + (x + bw / 2).toFixed(1) + "' y='" + (H - 6) + "' text-anchor='middle'>" + esc(it[0]) + "</text>";
    });
    return "<svg class='chart lk-bars' viewBox='0 0 " + W + " " + H + "' role='img'>" + g + "</svg>";
  }

  // ------------------------------------------------------------ valuation history
  function valuation(doc, all) {
    var fin = doc.fin;
    if (!fin || !fin.fy.length || !all.length) return "";
    var divs = (doc.px && doc.px.div || []).map(function (d) { return [iso2t(d[0]), d[1]]; });
    var rows = [];
    fin.fy.forEach(function (end, i) {
      var t = iso2t(end), p = nearest(all, t);
      if (!p || t - p[0] > 40) return;
      var sh = fin.a.sh ? fin.a.sh[i] : null, eps = fin.a.eps ? fin.a.eps[i] : null, rev = fin.a.rev ? fin.a.rev[i] : null;
      var eq = fin.a.eq ? fin.a.eq[i] : null, cap = sh ? sh * p[1] : null;
      var dv = divs.filter(function (d) { return d[0] > t - 366 && d[0] <= t; }).reduce(function (a, d) { return a + d[1]; }, 0);
      rows.push({ end: end, p: p[1], cap: cap, pe: eps > 0 ? p[1] / eps : null, ps: cap && rev ? cap / rev : null,
                  pb: cap && eq > 0 ? cap / eq : null, y: dv ? dv / p[1] : null });
    });
    if (!rows.length) return "";
    rows.reverse();
    var yr = function (e) { var d = new Date(iso2t(e) * DAY); return "FY " + (d.getUTCMonth() < 2 ? d.getUTCFullYear() - 1 : d.getUTCFullYear()); };
    var line = function (label, f) { return "<tr><th scope='row'>" + label + "</th>" + rows.map(function (r) { return "<td>" + f(r) + "</td>"; }).join("") + "</tr>"; };
    return "<section class='panel'><h2>Valuation over the years</h2><p class='hint'>What the market paid for each year's results: the price at the end of each fiscal year against that year's numbers. A P/E far above its own history means a lot of good news is already in the price.</p>" +
      "<div class='wrap'><table class='lk-fin'><thead><tr><th></th>" + rows.map(function (r) { return "<th>" + yr(r.end) + "</th>"; }).join("") + "</tr></thead><tbody>" +
      line("Price at year end", function (r) { return price(r.p); }) +
      line("Market cap", function (r) { return money(r.cap, 0); }) +
      line("P/E", function (r) { return ratio(r.pe); }) +
      line("Price / sales", function (r) { return ratio(r.ps); }) +
      line("Price / book", function (r) { return ratio(r.pb); }) +
      line("Dividend yield", function (r) { return r.y ? plainPct(r.y) : "—"; }) +
      "</tbody></table></div></section>";
  }

  // ------------------------------------------------------------ dividends
  function dividends(doc) {
    var divs = (doc.px && doc.px.div || []), spl = (doc.px && doc.px.spl || []);
    if (!divs.length && !spl.length) return "";
    var byYear = {};
    divs.forEach(function (d) { var y = d[0].slice(0, 4); byYear[y] = (byYear[y] || 0) + d[1]; });
    var years = Object.keys(byYear).sort().slice(-15);
    var thisYear = String(new Date().getUTCFullYear());
    var chart = years.length > 1 ? bars(years.map(function (y) { return [y === thisYear ? y + "*" : "’" + y.slice(2), byYear[y]]; }),
      function (v) { return "$" + v.toFixed(2); }) : "";
    var recent = divs.slice(-10).reverse().map(function (d) {
      return "<li><span class='d'>" + fmtDate(iso2t(d[0])) + "</span>$" + d[1].toFixed(d[1] < 0.1 ? 4 : 2) + " a share</li>";
    }).join("");
    var splits = spl.slice().reverse().map(function (s) {
      return "<li><span class='d'>" + fmtDate(iso2t(s[0])) + "</span>" + splitText(s[1]) + " split</li>";
    }).join("");
    var growth = null;
    var full = years.filter(function (y) { return y !== thisYear; });
    if (full.length >= 6) growth = Math.pow(byYear[full[full.length - 1]] / byYear[full[full.length - 6]], 1 / 5) - 1;
    return "<section class='panel'><h2>Dividends and splits</h2>" +
      (chart ? "<p class='hint'>Dividends paid per share each year" + (growth != null && isFinite(growth) ? ", growing " + plainPct(growth) + " a year over the last 5 full years" : "") +
        ". * this year so far.</p>" + chart : "") +
      "<div class='two'>" + (recent ? "<div><h3>Recent payments</h3><ul class='list'>" + recent + "</ul></div>" : "") +
      (splits ? "<div><h3>Stock splits</h3><ul class='list'>" + splits + "</ul></div>" : "") + "</div>" +
      "<p class='hint'>Amounts are adjusted for later splits, like on Yahoo Finance.</p></section>";
  }

  // ------------------------------------------------------------ Fidelity
  function tradeBar(t, kind) {
    var url = esc(fidelity(t));
    return "<div class='lk-trade'><a class='lk-btn buy' href='" + url + "' target='_blank' rel='noopener noreferrer' data-act='Buy'>Buy</a>" +
      "<a class='lk-btn sell' href='" + url + "' target='_blank' rel='noopener noreferrer' data-act='Sell'>Sell</a>" +
      "<button type='button' class='lk-btn ghost' data-watch='" + esc(t) + "'>☆ Watch</button>" +
      "<span class='hint'>Opens " + esc(t) + " at Fidelity" + (kind === "fund" || kind === "money" ? " (fund page)" : "") +
      ", where you sign in and place the order yourself. Nothing is traded from here.</span></div>";
  }
  function toast(msg) {
    var el = document.querySelector(".lk-toast");
    if (!el) { el = document.createElement("div"); el.className = "lk-toast"; document.body.appendChild(el); }
    el.textContent = msg; el.classList.add("on");
    clearTimeout(el._t); el._t = setTimeout(function () { el.classList.remove("on"); }, 4200);
  }
  function wireTrade(root, t) {
    root.querySelectorAll(".lk-btn[data-act]").forEach(function (a) {
      a.addEventListener("click", function () {
        try { if (navigator.clipboard) navigator.clipboard.writeText(yahoo(t)); } catch (e) { /* not allowed: fine */ }
        toast("Opening " + t + " at Fidelity. Tap Trade there and choose " + a.dataset.act + ". (" + yahoo(t) + " is copied, in case you need to paste it.)");
      });
    });
    var w = root.querySelector("[data-watch]");
    if (!w) return;
    function state() {
      var QT = window.QT; if (!QT) { w.hidden = true; return null; }
      var on = QT.state.watch.some(function (x) { return x.t === t; });
      w.textContent = on ? "★ Watching" : "☆ Watch";
      return on;
    }
    state();
    w.addEventListener("click", function () {
      var QT = window.QT; if (!QT) return;
      var S = QT.state;
      if (state()) S.watch = S.watch.filter(function (x) { return x.t !== t; });
      else S.watch.push({ t: t, note: "", mute: false });
      QT.state = S; QT.save(QT.state); state();
      toast(state() ? t + " added to your watchlist on this device (Mine page)." : t + " removed from your watchlist.");
    });
  }

  // ------------------------------------------------------------ the page / panel
  function render(el, doc, t, row) {
    var mode = el.dataset.mode, all = doc ? series(doc) : [];
    var name = (doc && doc.n) || (row && row.n) || "", kind = (doc && doc.k) || (row && row.k) || "stock";
    var last = all.length ? all[all.length - 1] : null, prev = all.length > 1 ? all[all.length - 2] : null;
    var day = last && prev ? last[1] / prev[1] - 1 : null;
    var html = "", head = "";
    if (mode === "page") {
      document.title = t + (name ? " · " + name : "") + " · Quantreyda";
      var bits = [name, KIND_LABEL[kind] || kind, doc && doc.x !== KIND_LABEL[kind] ? doc.x : ""].filter(Boolean);
      head += "<div class='sh'><div><h1>" + esc(t) + "</h1><div class='co'>" + esc(bits.join(" · ")) + "</div></div>" +
        (last ? "<div class='big'>" + price(last[1]) + "<span class='" + cls(day) + "'>" +
          (day != null ? (last[1] - prev[1] >= 0 ? "+" : "-") + price(Math.abs(last[1] - prev[1])).slice(1) + " (" + pct(day, 2) + ")" : "") +
          " · close " + fmtDate(last[0], { month: "short", day: "numeric" }) + "</span></div>" : "") + "</div>";
      if (row && row.page) head += "<p><a href='" + esc(BASE + "s/" + slug(t) + ".html") + "'>Full research page: who's buying, health check, news →</a></p>";
    } else {
      html += "<h2>Price history and financials</h2>";
    }
    html += tradeBar(t, kind);
    if (!doc) {
      el.innerHTML = head + "<section class='panel'>" + html + "<p>We don't have data for <b>" + esc(t) + "</b> yet. " +
        (row ? "It's on the list; prices fill in over the next daily runs." : "Check the ticker. If it's right, it may be an OTC stock or a fund Nasdaq doesn't list.") +
        " You can still open it at Fidelity above.</p></section>";
      wireTrade(el, t);
      return;
    }
    if (all.length > 1) {
      var live = kind === "stock" || kind === "etf";  // funds price once a day: nothing intraday to show
      html += "<div class='lk-ranges' role='tablist'>" + (live ? "<button type='button' data-r='1D'>1D</button>" : "") + RANGES.map(function (r) {
        return "<button type='button' data-r='" + r[0] + "'>" + r[0] + "</button>";
      }).join("") + "<label class='lk-cmp'><input type='checkbox'> vs S&amp;P 500</label><span class='lk-chg'></span></div><div class='lk-plot'></div><div class='lk-tv' hidden></div>" +
        "<div class='legend lk-leg'><span><i style='background:var(--ins)'></i>Dividend</span><span><i style='background:var(--warn)'></i>Split</span><span class='lk-cmpkey' hidden>- - - S&amp;P 500, same start</span><span>Daily closes, as of " + fmtDate(last[0]) + "</span></div>";
    } else {
      html += "<p class='hint'>No price history yet; it fills in on the next daily runs.</p>";
    }
    var st = stats(doc, all);
    html += "<div class='kv lk-kv'>" + st.map(function (s) { return "<div><span>" + s[0] + "</span><b class='" + (s[2] || "") + "'>" + s[1] + "</b></div>"; }).join("") + "</div>";
    var body = "";
    if (doc.fin && (doc.fin.fy.length || doc.fin.qe.length)) {
      var fin = doc.fin, n = fin.fy.length;
      var items = fin.fy.map(function (e, i) {
        var d = new Date(iso2t(e) * DAY), y = d.getUTCMonth() < 2 ? d.getUTCFullYear() - 1 : d.getUTCFullYear();
        return ["’" + String(y).slice(2), fin.a.rev ? fin.a.rev[i] : null, fin.a.ni ? fin.a.ni[i] : null];
      });
      body += "<section class='panel'><h2>Financial statements</h2><p class='hint'>From " + esc(fin.name || "the company") + "'s SEC filings: " + n +
        " fiscal years and the last " + fin.qe.length + " quarters. Green: revenue. Blue: net income.</p>" + bars(items, function (v) { return money(v, 0); }) +
        "<div class='lk-tabs'><button type='button' data-tab='income' class='on'>Income</button><button type='button' data-tab='balance'>Balance sheet</button>" +
        "<button type='button' data-tab='cash'>Cash flow</button><span class='sp'></span><button type='button' data-per='a' class='on'>Annual</button>" +
        "<button type='button' data-per='q'>Quarterly</button></div><div class='lk-stmt'></div>" +
        "<p class='hint'>Fourth quarters and quarterly cash flow are worked out from the year-to-date totals companies report. Free cash flow = operating cash flow − capital spending.</p></section>";
      body += valuation(doc, all);
    } else if (kind === "stock") {
      body += "<section class='panel'><h2>Financial statements</h2><p class='hint'>No SEC financial data on file. Foreign companies that file 20-F reports, and some trusts and partnerships, don't report in the format we read.</p></section>";
    } else {
      body += "<section class='panel'><h2>About this " + (kind === "etf" ? "ETF" : "fund") + "</h2><p>" + esc(name) + (doc.fam ? ", from " + esc(doc.fam) : "") +
        ". For its fees (expense ratio), holdings and ratings, open it at <a href='" + esc(fidelity(t)) + "' target='_blank' rel='noopener noreferrer'>Fidelity</a>. " +
        "The returns above are price only" + (kind === "money" ? "; a money market fund holds near $1 and pays you through its yield" : "; payouts are listed below") + ".</p></section>";
    }
    body += dividends(doc);
    body += "<p class='hint'>Prices: Yahoo Finance daily closes (free, unofficial). Financials: SEC EDGAR. Updated each morning. The 1D chart is TradingView's, loaded live. Research, not advice.</p>";
    html = head + "<section class='panel'>" + html + "</section>";
    el.innerHTML = html + body;
    if (mode === "panel") el.classList.add("lk-done");
    wireTrade(el, t);

    // chart
    if (all.length > 1) {
      var plot = el.querySelector(".lk-plot"), chg = el.querySelector(".lk-chg"), cmpBox = el.querySelector(".lk-cmp input");
      var events = (doc.px.div || []).map(function (d) { return [iso2t(d[0]), "D", "Dividend $" + d[1] + " · " + d[0]]; })
        .concat((doc.px.spl || []).map(function (s) { return [iso2t(s[0]), "S", splitText(s[1]) + " split · " + s[0]]; }));
      var spy = null, cur = all.length > 260 ? "1Y" : "Max";
      if (t === "SPY") cmpBox.parentNode.hidden = true;
      var tv = el.querySelector(".lk-tv"), leg = el.querySelector(".lk-leg");
      var show = function (key) {
        el.querySelectorAll(".lk-ranges button").forEach(function (b) { b.classList.toggle("on", b.dataset.r === key); });
        var intraday = key === "1D";
        tv.hidden = !intraday; plot.hidden = intraday; leg.style.display = intraday ? "none" : "";
        cmpBox.parentNode.style.visibility = intraday ? "hidden" : "";
        if (intraday) {
          chg.innerHTML = "Live chart from TradingView <span class='mut'>(some exchanges a few minutes delayed)</span>";
          tvChart(tv, t, doc.x);
          return;
        }
        cur = key;
        var pts = slice(all, key), ch = pts[pts.length - 1][1] / pts[0][1] - 1;
        var spyTxt = "";
        if (cmpBox.checked && spy && spy.length) {
          var a = nearest(spy, pts[0][0]), b = nearest(spy, pts[pts.length - 1][0]);
          if (a && b) spyTxt = " · S&P 500 " + pct(b[1] / a[1] - 1);
        }
        chg.innerHTML = "<b class='" + cls(ch) + "'>" + pct(ch) + "</b> " + (key === "Max" ? "since " + fmtDate(pts[0][0], { year: "numeric", timeZone: "UTC" }) : "over " + key) + esc(spyTxt);
        drawChart(plot, pts, events, cmpBox.checked ? spy : null, key === "Max");
        el.querySelector(".lk-cmpkey").hidden = !(cmpBox.checked && spy);
      };
      el.querySelectorAll(".lk-ranges button").forEach(function (b) { b.addEventListener("click", function () { show(b.dataset.r); }); });
      cmpBox.addEventListener("change", function () {
        if (cmpBox.checked && !spy) loadDoc("SPY").then(function (d) { spy = d ? series(d) : []; show(cur); });
        else show(cur);
      });
      if (window.MutationObserver) new MutationObserver(function () { if (!tv.hidden) tvChart(tv, t, doc.x); })
        .observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
      show(cur);
    }
    // statements
    var stmt = el.querySelector(".lk-stmt");
    if (stmt) {
      var tab = "income", per = "a";
      var draw = function () {
        stmt.innerHTML = statement(doc.fin, tab, per);
        el.querySelectorAll(".lk-tabs [data-tab]").forEach(function (b) { b.classList.toggle("on", b.dataset.tab === tab); });
        el.querySelectorAll(".lk-tabs [data-per]").forEach(function (b) { b.classList.toggle("on", b.dataset.per === per); });
      };
      el.querySelectorAll(".lk-tabs button").forEach(function (b) {
        b.addEventListener("click", function () { if (b.dataset.tab) tab = b.dataset.tab; if (b.dataset.per) per = b.dataset.per; draw(); });
      });
      draw();
    }
  }

  function boot() {
    wire(document.getElementById("lk-q"));
    wire(document.getElementById("lk-q2"));
    var el = document.getElementById("lk");
    if (!el) return;
    var t = el.dataset.tkr || new URLSearchParams(location.search).get("s");
    if (!t) { var q2 = document.getElementById("lk-q2"); if (q2) q2.focus(); return; }
    t = norm(t);
    if (el.dataset.mode === "page") el.innerHTML = "<h1>" + esc(t) + "</h1><p class='hint'>Loading…</p>";
    Promise.all([loadDoc(t), el.dataset.mode === "page" ? loadIndex() : Promise.resolve([])]).then(function (r) {
      var row = (r[1] || []).filter(function (x) { return x.t === t; })[0] || null;
      render(el, r[0], t, row);
    });
  }

  window.QTLook = { search: search, decode: decode, slice: slice, statement: statement, columns: columns, ttm: ttm, fidelity: fidelity, norm: norm, slug: slug };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})();
