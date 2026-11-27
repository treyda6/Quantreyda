// Quantreyda value scores: the "What it's worth" scorecards on stock and
// lookup pages (look.js calls QTValue.panel), and the sortable Value screen
// (value.html, from look/screen.json). The numbers and sentences are worked
// out on the daily build by value.py; this file only lays them out.
(function () {
  "use strict";
  var me = document.currentScript;
  var BASE = me ? new URL(".", me.src).href : "./";

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function ok(v) { return v != null && isFinite(v); }
  function px(v) { return ok(v) ? "$" + v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "—"; }
  function pct(v, dp, plus) { return ok(v) ? (plus && v > 0 ? "+" : "") + (v * 100).toFixed(dp == null ? 0 : dp) + "%" : "—"; }
  function money(v) {
    if (!ok(v)) return "—";
    var a = Math.abs(v);
    return a >= 1e12 ? "$" + (a / 1e12).toFixed(1) + "T" : a >= 1e9 ? "$" + (a / 1e9).toFixed(1) + "B" : "$" + Math.round(a / 1e6) + "M";
  }
  function band(s) { return !ok(s) ? "" : s >= 70 ? "hi" : s >= 45 ? "mid" : "lo"; }
  function slug(t) { return String(t).toUpperCase().replace(/[^A-Z0-9]+/g, "-").replace(/^-|-$/g, ""); }

  // ------------------------------------------------------------ scorecards
  function lines(l) {
    return "<ul>" + (l || []).map(function (x) {
      var c = x[0] > 0 ? "g" : x[0] < 0 ? "b" : "", text = esc(x[1]);
      if (x[2]) text += " <a href='" + esc(x[2]) + "' target='_blank' rel='noopener noreferrer'>Filing</a>";
      return "<li class='" + c + "'>" + text + "</li>";
    }).join("") + "</ul>";
  }
  function card(title, c, big, sub, body) {
    var s = c.s;
    return "<div class='sc-card'><h3>" + title + "<span class='sc-g " + band(s) + "'>" + esc(c.g || "") + "</span></h3>" +
      "<div class='sc-big'>" + big + (sub ? " <small>" + sub + "</small>" : "") + "</div>" +
      (ok(s) ? "<div class='sc-bar' title='" + s + " of 100'><i style='width:" + Math.max(2, s) + "%'></i></div>" : "") + body + "</div>";
  }
  function panel(sc) {
    if (!sc) return "";
    var v = sc.v, q = sc.q, f = sc.f, o = sc.o;
    // flags: severity 2 shown as warnings (▼ bold), 1 as cautions
    var flags = (f.l || []).map(function (x) { return [-1, x[1], x[2], x[0]]; });
    var fl = "<ul>" + flags.map(function (x) {
      var text = esc(x[1]) + (x[2] ? " <a href='" + esc(x[2]) + "' target='_blank' rel='noopener noreferrer'>Filing</a>" : "");
      return "<li class='b" + (x[3] > 1 ? " s2" : "") + "'>" + text + "</li>";
    }).join("") + "</ul>";
    if (!flags.length) fl = "<ul><li class='g'>None of the warning signs we check for.</li></ul>";
    if ((f.ok || []).length) fl += "<div class='sc-ok'>Checked and fine: " + esc(f.ok.join(" · ")) + ".</div>";
    if (f.note) fl += "<div class='sc-ok'>" + esc(f.note) + "</div>";
    var yr = String(sc.fy || "").slice(0, 4);
    return "<section class='panel sc-panel'><h2>What it's worth, and is it a good business?</h2>" +
      "<p class='hint'>The questions Buffett, Munger, Graham, Burry and other long-term investors ask first, answered from its SEC filings " +
      "(latest fiscal year " + esc(yr) + ") and today's price. Rough screens with every assumption shown, not advice." +
      " <a href='" + esc(BASE + "value.html") + "'>Compare every stock →</a></p><div class='sc-grid'>" +
      card("Value", v, ok(v.iv) ? px(v.iv) : "—", ok(v.iv) ? "a share" + (ok(v.mos) ? " · " + (v.mos >= 0 ? pct(v.mos) + " below" : pct(-v.mos) + " above") : "") : "", lines(v.l)) +
      card("Business quality", q, ok(q.s) ? q.s + "<small>/100</small>" : "—", "", lines(q.l)) +
      card("Red flags (short-seller lens)", f, flags.length ? flags.length : "0", flags.length === 1 ? "warning sign" : "warning signs", fl) +
      card("Run by owners?", o, ok(o.s) ? o.s + "<small>/100</small>" : "—", "", lines(o.l)) +
      "</div></section>";
  }

  // ------------------------------------------------------------ the screen
  var COLS = [  // [key, label, format, default sort direction, hide on phones]
    ["t", "Stock", null, 1], ["p", "Price", px, -1, 1], ["iv", "Value", px, -1, 1],
    ["mos", "Margin of safety", function (v) { return "<span class='" + (v >= 0.2 ? "up" : v < 0 ? "down" : "") + "'>" + pct(v) + "</span>"; }, -1],
    ["ig", "Priced for", function (v) { return ok(v) ? pct(v, 1) + "/yr" : "—"; }, 1, 1],
    ["ey", "Yield", function (v) { return pct(v, 1); }, -1, 1],
    ["q", "Quality", function (v) { return ok(v) ? "<span class='vs-pill " + band(v) + "'>" + v + "</span>" : "—"; }, -1],
    ["roic", "ROIC", function (v) { return pct(v); }, -1, 1],
    ["shc", "Shares /yr", function (v) { return "<span class='" + (v < 0 ? "up" : v > 0.02 ? "down" : "") + "'>" + pct(v, 1, true) + "</span>"; }, 1, 1],
    ["flags", "Flags", function (v) { return ok(v) ? "<span class='vs-pill " + (v === 0 ? "hi" : v === 1 ? "mid" : "lo") + "'>" + v + "</span>" : "—"; }, 1],
    ["own", "Owners", function (v) { return ok(v) ? "<span class='vs-pill " + band(v) + "'>" + v + "</span>" : "—"; }, -1],
    ["cap", "Size", money, -1, 1]
  ];
  var PRESETS = {
    cheap: { sort: "mos", desc: "Graham's margin of safety plus Buffett's wonderful business: priced at least 20% below the estimate, quality 60 or better, at most one red flag.",
             f: function (r) { return r.mos >= 0.2 && r.q >= 60 && r.flags <= 1; } },
    quality: { sort: "q", desc: "Businesses that earn high returns on their capital year after year, make cash, carry little debt and don't dilute you. Price not considered.",
               f: function (r) { return r.q >= 70; } },
    owners: { sort: "own", desc: "Management that grows free cash flow per share, reinvests at high returns, shrinks the share count and is paid in step with shareholders.",
              f: function (r) { return r.own >= 70; } },
    flags: { sort: "flags", desc: "Burry's list: two or more warning signs (profits ahead of cash, slow-paying customers, heavy dilution, auditor changes…). Check before you buy, or if you own one.",
             f: function (r) { return r.flags >= 2; } },
    all: { sort: "cap", desc: "Every scored company, biggest first.", f: function () { return true; } }
  };

  function screen(root) {
    var tb = root.querySelector("tbody"), th = root.querySelector("thead"), q = root.querySelector("#vs-q"),
        capSel = root.querySelector("#vs-cap"), finBox = root.querySelector("#vs-fin"), count = root.querySelector("#vs-count"),
        desc = root.querySelector(".vs-desc"), rows = [], pre = "cheap", key = "mos", dir = -1;
    th.innerHTML = "<tr>" + COLS.map(function (c) {
      return "<th data-k='" + c[0] + "' class='" + (c[4] ? "c-x" : "") + (c[0] !== "t" ? " num" : "") + "'>" + c[1] + "</th>";
    }).join("") + "</tr>";
    function draw() {
      var text = q.value.trim().toLowerCase(), minCap = +capSel.value, P = PRESETS[pre];
      var hit = rows.filter(function (r) {
        return P.f(r) && (finBox.checked || !r.fin) && (!minCap || r.cap >= minCap) &&
          (!text || (r.t + " " + r.n).toLowerCase().indexOf(text) >= 0);
      });
      hit.sort(function (a, b) {
        var x = a[key], y = b[key];
        if (key === "t") return dir * String(x).localeCompare(String(y));
        if (!ok(x)) return ok(y) ? 1 : 0;
        if (!ok(y)) return -1;
        return dir * (x - y);
      });
      count.textContent = hit.length.toLocaleString("en-US") + " of " + rows.length.toLocaleString("en-US");
      desc.textContent = P.desc;
      th.querySelectorAll("th").forEach(function (h) {
        h.classList.toggle("on", h.dataset.k === key);
        var c = COLS.filter(function (c) { return c[0] === h.dataset.k; })[0];
        h.textContent = c[1] + (h.dataset.k === key ? (dir > 0 ? " ▲" : " ▼") : "");
      });
      root.querySelectorAll(".vs-pre button").forEach(function (b) { b.classList.toggle("on", b.dataset.pre === pre); });
      tb.innerHTML = hit.slice(0, 300).map(function (r) {
        var href = r.page ? BASE + "s/" + slug(r.t) + ".html" : BASE + "look.html?s=" + encodeURIComponent(r.t);
        return "<tr data-href='" + esc(href) + "'>" + COLS.map(function (c) {
          if (c[0] === "t") return "<td><a href='" + esc(href) + "'><b>" + esc(r.t) + "</b></a><small>" + esc(r.n) + "</small></td>";
          return "<td class='num" + (c[4] ? " c-x" : "") + "'>" + c[2](r[c[0]]) + "</td>";
        }).join("") + "</tr>";
      }).join("") || "<tr><td colspan='" + COLS.length + "' class='mut'>Nothing matches. Try another list or a smaller company size.</td></tr>";
      if (hit.length > 300) tb.innerHTML += "<tr><td colspan='" + COLS.length + "' class='mut'>Showing the first 300. Filter or sort to see others.</td></tr>";
    }
    th.addEventListener("click", function (e) {
      var h = e.target.closest("th[data-k]");
      if (!h) return;
      var k = h.dataset.k;
      if (k === key) dir = -dir;
      else { key = k; dir = COLS.filter(function (c) { return c[0] === k; })[0][3]; }
      draw();
    });
    root.querySelectorAll(".vs-pre button").forEach(function (b) {
      b.addEventListener("click", function () {
        pre = b.dataset.pre; key = PRESETS[pre].sort;
        dir = COLS.filter(function (c) { return c[0] === key; })[0][3];
        try { history.replaceState(null, "", "#" + pre); } catch (e) { /* file:// */ }
        draw();
      });
    });
    [q, capSel, finBox].forEach(function (el) { el.addEventListener("input", draw); });
    tb.addEventListener("click", function (e) {
      var tr = e.target.closest("tr[data-href]");
      if (tr && !e.target.closest("a")) location.href = tr.dataset.href;
    });
    var h = location.hash.slice(1);
    if (PRESETS[h]) { pre = h; key = PRESETS[h].sort; dir = COLS.filter(function (c) { return c[0] === key; })[0][3]; }
    fetch(BASE + "look/screen.json").then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
      if (!d || !d.r.length) {
        tb.innerHTML = "<tr><td class='mut'>Scores appear after the next morning update reads the SEC's financial data.</td></tr>";
        return;
      }
      rows = d.r.map(function (a) { var o = {}; d.cols.forEach(function (c, i) { o[c] = a[i]; }); return o; });
      draw();
    }).catch(function () { tb.innerHTML = "<tr><td class='mut'>Couldn't load the scores. Try again in a minute.</td></tr>"; });
  }

  window.QTValue = { panel: panel };
  function boot() { var el = document.getElementById("vs"); if (el) screen(el); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})();
