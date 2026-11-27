// The "Mine" page: accounts, watchlist, alerts, customizing and backup.
// Runs entirely in the browser. Data lives in localStorage via personal.js
// (window.QT); the only thing fetched is the public mine.json price file.
(function () {
  "use strict";
  var QT = window.QT, view = document.getElementById("mi-view");
  if (!QT || !view) return;
  var esc = QT.esc, money = QT.money, pct = QT.pct, KINDS = QT.KINDS;
  var S = QT.state, D = null, editing = {}, pending = null;
  var TABS = ["accounts", "watch", "alerts", "settings", "backup"];
  var IRA = { roth: 1, trad: 1, custodial_roth: 1 };

  var NOTES = {
    roth: "Grows and comes out tax-free in retirement. What you put in (not the gains) can come out any time.",
    custodial_roth: "A Roth IRA for a minor who has a job. They can put in up to what they earned this year, capped at the IRA limit. Becomes theirs at 18 or 21.",
    trad: "Tax-deductible now, taxed when it comes out. Shares one yearly limit with Roth IRAs.",
    utma: "Belongs to the child; you manage it until they come of age (18 to 21 depending on the state). Investment income above a yearly amount can be taxed at the parent's rate (the kiddie tax), and it counts more against college aid than a 529.",
    brokerage: "No limits and no tax break: dividends are taxed each year and gains when you sell. Holding over a year gets the lower long-term rate.",
    k401: "Workplace plan. Plan-only funds often have no public price; type one in under Edit.",
    hsa: "Tax-free going in, growing and coming out for medical costs. Can be invested like an IRA.",
    college529: "Tax-free for school costs. Plan portfolios often have no ticker; type a price in under Edit.",
    other: ""
  };

  function save() {
    QT.state = S;
    if (!QT.save(S)) flash("Couldn't save: this browser is blocking storage (private window?). Changes will be lost when you close it.", true);
  }
  function uid() { return Math.random().toString(36).slice(2, 10); }
  function num(v) { var n = parseFloat(String(v == null ? "" : v).replace(/[$,\s]/g, "")); return isFinite(n) ? n : 0; }
  function cls(x) { return x == null || isNaN(x) ? "" : x >= 0 ? "up" : "down"; }
  function cpct(x) { return "<span class='" + cls(x) + "'>" + pct(x) + "</span>"; }
  function flash(msg, bad) {
    var el = document.getElementById("mi-flash");
    if (!el) { el = document.createElement("p"); el.id = "mi-flash"; el.className = "mi-msg"; view.parentNode.insertBefore(el, view); }
    el.className = "mi-msg " + (bad ? "down" : "up");
    el.textContent = msg;
    clearTimeout(flash.t); flash.t = setTimeout(function () { el.textContent = ""; }, bad ? 9000 : 4000);
  }

  // ---------------------------------------------------------------- stock bits
  function stockLink(t, inner) {
    var p = D.pages[t];
    return p ? "<a href='s/" + esc(p) + ".html'>" + inner + "</a>" : inner;
  }
  function logo(t) {
    var src = D.logos && D.logos[t];
    return src ? "<span class='lg'><img src='" + esc(src) + "' alt='' loading='lazy'></span>"
               : "<span class='lg mono'>" + esc(t.charAt(0) || "?") + "</span>";
  }
  function stockCell(t, sub) {
    var name = D.names[t] || "";
    return "<div class='mi-stock'>" + logo(t) + "<div>" + stockLink(t, "<b>" + esc(t) + "</b>") +
      (name || sub ? "<small>" + esc(sub || name) + "</small>" : "") + "</div></div>";
  }

  function recent(t, days) {
    return D.events.filter(function (e) {
      return e.t === t && (Date.parse(D.asof) - Date.parse(e.d)) / 864e5 <= (days || 30);
    });
  }

  function chips(t) {
    var ev = recent(t, 30), out = [], n = {};
    ev.forEach(function (e) { var k = e.k + ":" + (e.s || ""); n[k] = (n[k] || 0) + 1; });
    if (D.ideas[t]) out.push("<span class='mi-chip idea' title='On the Today page ideas list'>Idea #" + D.ideas[t] + "</span>");
    [["ins:buy", "Insider buy", "buy"], ["pol:buy", "Congress / officials buy", "buy"],
     ["ins:sell", "Insider sell", "sell"], ["pol:sell", "Congress / officials sell", "sell"]].forEach(function (c) {
      if (n[c[0]]) {
        var who = ev.filter(function (e) { return e.k + ":" + e.s === c[0]; }).slice(0, 4)
          .map(function (e) { return e.d + " " + (e.w || "") + (e.a ? " " + e.a : ""); }).join("\n");
        out.push("<span class='mi-chip " + c[2] + "' title='" + esc(who) + "'>" + c[1] + (n[c[0]] > 1 ? " ×" + n[c[0]] : "") + "</span>");
      }
    });
    var nw = ev.filter(function (e) { return e.k === "news"; })[0];
    if (nw) out.push("<a class='mi-chip news' href='" + esc(nw.u) + "' target='_blank' rel='noopener' title='" +
      esc(nw.d + " " + nw.f) + "'>News: " + esc(String(nw.x).slice(0, 34)) + "</a>");
    var h = D.health[t];
    if (h) out.push("<span class='mi-chip" + (h === "Shaky" ? " sell" : "") + "' title='Company health from its SEC filings'>" + esc(h) + "</span>");
    return out.join("");
  }

  // ---------------------------------------------------------------- accounts
  function iraUsed(owner) {
    return S.accounts.filter(function (a) { return IRA[a.kind] && (a.owner || "") === (owner || ""); })
      .reduce(function (s, a) { return s + num(a.contrib); }, 0);
  }

  function holdingRows(a) {
    var rows = a.holdings.map(function (h, i) {
      var p = QT.priceOf(h, D), sh = num(h.sh), val = p ? p.c * sh : null;
      return { h: h, i: i, p: p, val: val, gain: p && h.cost > 0 ? val - h.cost * sh : null,
               gp: p && h.cost > 0 ? p.c / h.cost - 1 : null };
    });
    rows.sort(function (x, y) { return (y.val || 0) - (x.val || 0); });
    return rows;
  }

  function accountCard(a, total) {
    var t = QT.totals([a], D), ed = editing[a.id], rows = holdingRows(a);
    var h = "<div class='mi-card' data-acct='" + a.id + "'><div class='mi-head'><h3>" + esc(a.name) + "</h3>" +
      "<span class='mi-kind " + esc(a.kind) + "'>" + esc(KINDS[a.kind] || "Other") + "</span>" +
      (a.owner ? "<span class='mut'>for " + esc(a.owner) + "</span>" : "") + "<span class='sp'></span>" +
      "<button class='mi-btn sm' data-act='edit'>" + (ed ? "Done" : "Edit") + "</button></div>";
    h += "<div class='mi-stats'><div><span>Value</span><b class='qt-money'>" + money(t.value) + "</b></div>" +
      "<div><span>Today</span><b class='" + cls(t.day) + "'><span class='qt-money'>" + money(t.day) + "</span> " + pct(t.dayPct) + "</b></div>" +
      (t.gain != null ? "<div><span>Gain on cost</span><b class='" + cls(t.gain) + "'><span class='qt-money'>" + money(t.gain) + "</span> " +
        pct(t.cost ? t.gain / t.cost : null) + "</b></div>" : "") +
      (num(a.cash) ? "<div><span>Cash</span><b class='qt-money'>" + money(num(a.cash)) + "</b></div>" : "") +
      (total ? "<div><span>Of all accounts</span><b>" + pct(t.value / total, false) + "</b></div>" : "") + "</div>";
    if (NOTES[a.kind]) h += "<p class='mi-note'>" + esc(NOTES[a.kind]) + "</p>";
    if (IRA[a.kind]) {
      var L = QT.IRA_LIMIT, used = iraUsed(a.owner), left = Math.max(0, L.base - used);
      h += "<p class='mi-note'>" + L.year + " IRA contributions" + (a.owner ? " for " + esc(a.owner) : "") + ": <b class='qt-money'>" +
        money(used) + "</b> of " + money(L.base) + " · <b>" + money(left) + " left</b> (Roth and traditional share one limit; +" +
        money(L.catchup) + " if 50 or older)</p><div class='mi-room'><i style='width:" + Math.min(100, used / L.base * 100) + "%'></i></div>";
    }
    if (ed) {
      h += "<div class='mi-form'><label>Name<input data-f='name' value='" + esc(a.name) + "'></label>" +
        "<label>Type<select data-f='kind'>" + Object.keys(KINDS).map(function (k) {
          return "<option value='" + k + "'" + (k === a.kind ? " selected" : "") + ">" + esc(KINDS[k]) + "</option>";
        }).join("") + "</select></label>" +
        "<label>For (optional)<input data-f='owner' placeholder='e.g. Emma' value='" + esc(a.owner || "") + "'></label>" +
        "<label>Cash<input data-f='cash' inputmode='decimal' value='" + esc(a.cash || "") + "'></label>" +
        (IRA[a.kind] ? "<label>Put in this year<input data-f='contrib' inputmode='decimal' value='" + esc(a.contrib || "") + "'></label>" : "") +
        "</div>";
    }
    if (rows.length) {
      h += "<div class='wrap'><table class='mi-t'><thead><tr><th>Stock</th>";
      h += ed ? "<th>Shares</th><th>Cost / share</th><th title='Only if there is no public price'>Price (manual)</th><th></th>"
              : "<th class='num'>Value</th><th class='num'>Gain</th><th class='num mi-hide-sm'>Today</th>";
      h += "</tr></thead><tbody>";
      rows.forEach(function (r) {
        var hh = r.h;
        if (ed) {
          h += "<tr data-i='" + r.i + "'><td>" + stockCell(hh.t) + "</td>" +
            "<td><input data-h='sh' inputmode='decimal' value='" + esc(hh.sh) + "'></td>" +
            "<td><input data-h='cost' inputmode='decimal' value='" + esc(hh.cost || "") + "' placeholder='optional'></td>" +
            "<td><input data-h='px' inputmode='decimal' value='" + esc(hh.px || "") + "' placeholder='" +
              (D.quotes[hh.t] ? "auto " + D.quotes[hh.t][0] : "needed") + "'></td>" +
            "<td><button class='mi-x' data-act='rm' title='Remove'>×</button></td></tr>";
        } else {
          var sub = num(hh.sh).toLocaleString("en-US", { maximumFractionDigits: 4 }) + " sh" +
            (r.p ? " @ " + money(r.p.c, true) + (r.p.manual ? " (manual)" : "") : "");
          h += "<tr><td>" + stockCell(hh.t, sub) + "<div>" + chips(hh.t) + "</div></td>" +
            "<td class='num'><span class='qt-money'>" + (r.val == null ? "<span class='mut'>no price</span>" : money(r.val)) + "</span></td>" +
            "<td class='num'>" + (r.gain == null ? "<span class='mut'>—</span>" : "<span class='qt-money " + cls(r.gain) + "'>" +
              money(r.gain) + "</span><br><small class='" + cls(r.gp) + "'>" + pct(r.gp) + "</small>") + "</td>" +
            "<td class='num mi-hide-sm'>" + (r.p ? cpct(r.p.r1) : "—") + "</td></tr>";
        }
      });
      h += "</tbody></table></div>";
      if (t.missing && !ed) h += "<p class='mi-note'>" + t.missing + " holding" + (t.missing > 1 ? "s have" : " has") +
        " no public price here yet. Tap Edit to type one in, or add the ticker to universe.txt so the site prices it daily.</p>";
    }
    h += "<form class='mi-form' data-act='add'><label>Ticker<input name='t' placeholder='VTI' autocapitalize='characters' required size='7'></label>" +
      "<label>Shares<input name='sh' inputmode='decimal' required size='7'></label>" +
      "<label>Cost / share<input name='cost' inputmode='decimal' placeholder='optional' size='8'></label>" +
      "<button class='mi-btn pri'>Add</button></form>";
    if (ed) {
      h += "<details style='margin-top:10px'><summary class='mut'>Paste many at once</summary><form class='mi-form' data-act='paste'>" +
        "<textarea name='list' placeholder='One per line: ticker, shares, cost per share (optional)&#10;VTI 25 231.40&#10;AAPL, 10, 172.15&#10;SCHD 40'></textarea>" +
        "<button class='mi-btn'>Add these</button><span class='mut'>A ticker already in this account is replaced.</span></form></details>" +
        "<p style='margin:14px 0 0'><button class='mi-btn dan sm' data-act='del'>Delete this account</button></p>";
    }
    return h + "</div>";
  }

  function renderAccounts() {
    var accs = S.accounts, h = "";
    if (accs.length) {
      var t = QT.totals(accs, D), byKind = {};
      accs.forEach(function (a) { byKind[a.kind] = (byKind[a.kind] || 0) + QT.totals([a], D).value; });
      h += "<div class='mi-card'><div class='mi-head'><h3>All accounts</h3><span class='sp'></span>" +
        "<span class='mut'>Prices as of " + esc(D.asof) + "</span></div>" +
        "<div class='mi-stats mi-big'><div><span>Total value</span><b class='qt-money'>" + money(t.value) + "</b></div>" +
        "<div><span>Today</span><b class='" + cls(t.day) + "'><span class='qt-money'>" + money(t.day) + "</span> " + pct(t.dayPct) + "</b></div>" +
        (t.gain != null ? "<div><span>Gain on cost</span><b class='" + cls(t.gain) + "'><span class='qt-money'>" + money(t.gain) + "</span></b></div>" : "") +
        "</div><div>" + Object.keys(byKind).map(function (k) {
          return "<span class='mi-chip'>" + esc(KINDS[k] || k) + " · " + pct(t.value ? byKind[k] / t.value : 0, false) + "</span>";
        }).join("") + "</div>";
      var held = {};
      accs.forEach(function (a) { a.holdings.forEach(function (x) { held[x.t] = 1; }); });
      var touch = D.events.filter(function (e) { return held[e.t] && (Date.parse(D.asof) - Date.parse(e.d)) / 864e5 <= 30; }).slice(0, 8);
      var ideas = Object.keys(held).filter(function (x) { return D.ideas[x]; });
      if (touch.length || ideas.length) {
        h += "<h3 style='margin:18px 0 4px;font-size:15px'>What touches your holdings</h3>";
        if (ideas.length) h += "<p class='mi-note'>On today's ideas list: " + ideas.map(function (x) { return stockLink(x, "<b>" + esc(x) + "</b>"); }).join(", ") + "</p>";
        h += touch.map(function (e) {
          return "<div class='mi-al'><div class='body'>" + stockLink(e.t, "<b>" + esc(e.t) + "</b>") + " " +
            esc(QT.alertText({ k: e.k, e: e })) + "<br><small>" + esc(e.d) + "</small></div></div>";
        }).join("");
      }
      h += "</div>";
    }
    accs.forEach(function (a) { h += accountCard(a, accs.length > 1 ? QT.totals(accs, D).value : 0); });
    h += "<div class='mi-card'><h3>" + (accs.length ? "Add another account" : "Add your first account") + "</h3>" +
      "<p class='mi-note'>One per real account: your Roth, a child's UTMA, a brokerage account. Shares and cost are optional details; " +
      "only you see them.</p><form class='mi-form' data-act='newacct'>" +
      "<label>Name<input name='name' placeholder='My Roth IRA' required></label>" +
      "<label>Type<select name='kind'>" + Object.keys(KINDS).map(function (k) {
        return "<option value='" + k + "'>" + esc(KINDS[k]) + "</option>"; }).join("") + "</select></label>" +
      "<label>For (optional)<input name='owner' placeholder='e.g. Emma'></label>" +
      "<button class='mi-btn pri'>Create</button></form></div>";
    return h;
  }

  function addHolding(a, t, sh, cost, replace) {
    t = QT.norm(t);
    if (!t || !(sh > 0)) return false;
    var x = a.holdings.filter(function (h) { return h.t === t; })[0];
    if (x && !replace) {  // bought more: add shares, blend the cost
      var tot = num(x.sh) + sh;
      x.cost = x.cost > 0 && cost > 0 ? (x.cost * num(x.sh) + cost * sh) / tot : x.cost || cost || 0;
      x.sh = +tot.toFixed(6);
    } else if (x) { x.sh = sh; x.cost = cost || 0; }
    else a.holdings.push({ t: t, sh: sh, cost: cost || 0 });
    return true;
  }

  function wireAccounts() {
    view.querySelectorAll("form[data-act=newacct]").forEach(function (f) {
      f.onsubmit = function (ev) {
        ev.preventDefault();
        var a = { id: uid(), name: f.name.value.trim() || "Account", kind: f.kind.value, owner: f.owner.value.trim(), cash: 0, contrib: 0, holdings: [] };
        S.accounts.push(a); save(); draw();
        flash("Added " + a.name + ". Now add what it holds.");
      };
    });
    view.querySelectorAll("[data-acct]").forEach(function (card) {
      var a = S.accounts.filter(function (x) { return x.id === card.dataset.acct; })[0];
      if (!a) return;
      card.querySelector("[data-act=edit]").onclick = function () { editing[a.id] = !editing[a.id]; draw(); };
      var add = card.querySelector("form[data-act=add]");
      add.onsubmit = function (ev) {
        ev.preventDefault();
        var t = QT.norm(add.t.value);
        if (!addHolding(a, t, num(add.sh.value), num(add.cost.value))) return flash("Enter a ticker and a number of shares.", true);
        save(); draw();
        if (!D.quotes[t]) flash(t + " added, but there's no public price for it here yet. Tap Edit to type one in.", true);
      };
      var paste = card.querySelector("form[data-act=paste]");
      if (paste) paste.onsubmit = function (ev) {
        ev.preventDefault();
        var n = 0;
        paste.list.value.split(/\n/).forEach(function (line) {
          var p = line.trim().split(/[\s,;\t]+/);
          if (p.length >= 2 && addHolding(a, p[0], num(p[1]), num(p[2]), true)) n++;
        });
        save(); draw(); flash(n ? "Added " + n + " holding" + (n > 1 ? "s" : "") + "." : "Nothing recognized. Use: ticker shares cost", !n);
      };
      card.querySelectorAll("[data-f]").forEach(function (inp) {
        inp.onchange = function () {
          var f = inp.dataset.f;
          a[f] = f === "cash" || f === "contrib" ? num(inp.value) : inp.value.trim();
          if (f === "name" && !a.name) a.name = "Account";
          save(); draw();
        };
      });
      card.querySelectorAll("tr[data-i]").forEach(function (tr) {
        var h = a.holdings[+tr.dataset.i];
        tr.querySelectorAll("[data-h]").forEach(function (inp) {
          inp.onchange = function () { h[inp.dataset.h] = num(inp.value); save(); };
        });
        tr.querySelector("[data-act=rm]").onclick = function () { a.holdings.splice(+tr.dataset.i, 1); save(); draw(); };
      });
      var del = card.querySelector("[data-act=del]");
      if (del) del.onclick = function () {
        if (!confirm("Delete " + a.name + " and its holdings from this device?")) return;
        S.accounts = S.accounts.filter(function (x) { return x !== a; }); save(); draw();
      };
    });
  }

  // ---------------------------------------------------------------- watchlist
  function renderWatch() {
    var h = "<div class='mi-card'><h3>Watchlist</h3><p class='mi-note'>Stocks you're following. You get an alert when an insider, " +
      "a member of Congress or an official trades one, when the company files news with the SEC, or when it makes a sharp move.</p>" +
      "<form class='mi-form' data-act='watch'><label style='flex:1;min-width:180px'>Tickers<input name='t' placeholder='NVDA, COST, SCHD' autocapitalize='characters' required></label>" +
      "<label style='flex:1;min-width:160px'>Note (optional)<input name='note' placeholder='Why you care'></label><button class='mi-btn pri'>Add</button></form>";
    if (!S.watch.length) return h + "<p class='mi-empty' style='margin-top:14px'>Nothing yet. Add a few tickers above.</p></div>";
    h += "<div class='wrap'><table class='mi-t'><thead><tr><th>Stock</th><th class='num'>Price</th><th class='num'>Today</th>" +
      "<th class='num mi-hide-sm'>1 mo</th><th class='num mi-hide-sm'>1 yr</th><th title='Alerts on or off'>Alerts</th><th></th></tr></thead><tbody>";
    S.watch.forEach(function (w, i) {
      var q = D.quotes[w.t];
      h += "<tr data-w='" + i + "'><td>" + stockCell(w.t, w.note) + "<div>" + chips(w.t) + "</div></td>" +
        "<td class='num'>" + (q ? money(q[0], true) : "<span class='mut'>—</span>") + "</td>" +
        "<td class='num'>" + (q ? cpct(q[1]) : "—") + "</td><td class='num mi-hide-sm'>" + (q ? cpct(q[3]) : "—") + "</td>" +
        "<td class='num mi-hide-sm'>" + (q ? cpct(q[4]) : "—") + "</td>" +
        "<td><input type='checkbox' data-act='mute' " + (w.mute ? "" : "checked") + " aria-label='Alerts for " + esc(w.t) + "'></td>" +
        "<td><button class='mi-x' data-act='rm' title='Remove'>×</button></td></tr>";
    });
    return h + "</tbody></table></div></div>";
  }

  function wireWatch() {
    var f = view.querySelector("form[data-act=watch]");
    f.onsubmit = function (ev) {
      ev.preventDefault();
      var have = {}, added = [];
      S.watch.forEach(function (w) { have[w.t] = 1; });
      f.t.value.split(/[\s,;]+/).map(QT.norm).filter(Boolean).forEach(function (t) {
        if (!have[t]) { S.watch.push({ t: t, note: f.note.value.trim(), mute: false }); have[t] = 1; added.push(t); }
      });
      save(); draw();
      var none = added.filter(function (t) { return !D.quotes[t] && !recent(t, 45).length; });
      if (none.length) flash("Added. No recent price or activity on file for " + none.join(", ") + " yet; alerts still work when something happens.");
    };
    view.querySelectorAll("tr[data-w]").forEach(function (tr) {
      var w = S.watch[+tr.dataset.w];
      tr.querySelector("[data-act=mute]").onchange = function (ev) { w.mute = !ev.target.checked; save(); };
      tr.querySelector("[data-act=rm]").onclick = function () { S.watch.splice(+tr.dataset.w, 1); save(); draw(); };
    });
  }

  // ---------------------------------------------------------------- alerts
  var KIND_LABEL = { ins: "Insider", pol: "Politician", news: "News", move: "Price move" };
  function renderAlerts(list) {
    var unread = list.filter(function (a) { return a.unread; }).length;
    var h = "<div class='mi-card'><div class='mi-head'><h3>Alerts</h3><span class='mut'>" + (unread ? unread + " new" : "all caught up") +
      "</span><span class='sp'></span><button class='mi-btn sm' data-act='settings'>Alert rules</button></div>" +
      "<p class='mi-note'>Checked against the site's daily update (" + esc(D.asof) + ") whenever you open the app, for your watchlist" +
      (S.rules.holdings ? " and everything you own" : "") + ". The last 30 days are kept.</p>";
    if ("Notification" in window && !S.prefs.notify)
      h += "<p class='mi-note'><button class='mi-btn sm' data-act='notify'>Show new alerts as notifications</button> on this device when you open the app.</p>";
    if (!list.length) {
      var any = S.watch.length || S.accounts.some(function (a) { return a.holdings.length; });
      return h + "<p class='mi-empty'>" + (any ? "Nothing in the last 30 days for the stocks you follow." :
        "Add stocks to your watchlist or an account and alerts show up here.") + "</p></div>";
    }
    h += list.map(function (a) {
      var e = a.e, link = e.u ? " · <a href='" + esc(e.u) + "' target='_blank' rel='noopener'>filing</a>" : "";
      return "<div class='mi-al" + (a.unread ? " new" : "") + "'><span class='dot'></span><div class='body'>" +
        stockLink(a.t, "<b>" + esc(a.t) + "</b>") + " " + esc(QT.alertText(a)) + "<br><small>" + esc(a.d) + " · " +
        KIND_LABEL[a.k] + " · " + (a.why === "own" ? "you own this" : "on your watchlist") + link + "</small></div></div>";
    }).join("");
    return h + "</div>";
  }

  function askNotify() {
    if (!("Notification" in window)) return flash("This browser can't show notifications. On iPhone, add the site to your Home Screen first.", true);
    Notification.requestPermission().then(function (p) {
      S.prefs.notify = p === "granted"; save(); draw();
      flash(p === "granted" ? "Notifications on for this device." : "Notifications were blocked. You can allow them in the browser's site settings.", p !== "granted");
    });
  }

  function wireAlerts(list) {
    var b = view.querySelector("[data-act=settings]");
    if (b) b.onclick = function () { go("settings"); };
    var n = view.querySelector("[data-act=notify]");
    if (n) n.onclick = askNotify;
    // seeing them counts as reading them
    QT.markSeen(S, list); save(); QT.badge(0); tabBadge(0);
  }

  // ---------------------------------------------------------------- customize
  var PAGES = [["index.html", "Today"], ["congress.html", "Congress"], ["insiders.html", "Insiders"], ["funds.html", "Funds"],
    ["trades.html", "All trades"], ["markets.html", "Odds"], ["leaders.html", "Leaderboard"], ["backtest.html", "Did it work?"]];

  function renderSettings() {
    var r = S.rules, p = S.prefs;
    function box(id, on, label) {
      return "<label class='row'><input type='checkbox' data-set='" + id + "'" + (on ? " checked" : "") + "> " + label + "</label>";
    }
    var h = "<div class='mi-card mi-set'><h3>Alert me about</h3><div class='mi-grid'>" +
      box("rules.insBuy", r.insBuy, "Insider buys") + box("rules.insSell", r.insSell, "Insider sells") +
      box("rules.polBuy", r.polBuy, "Congress and official buys") + box("rules.polSell", r.polSell, "Congress and official sells") +
      box("rules.news", r.news, "Company news (SEC filings)") + box("rules.holdings", r.holdings, "Stocks I own, not just my watchlist") +
      "</div><label class='row'>Sharp moves: <select data-set='rules.move'>" + [[0, "off"], [3, "3% in a day"], [5, "5% in a day"],
        [8, "8% in a day"], [10, "10% in a day"]].map(function (o) {
        return "<option value='" + o[0] + "'" + (+r.move === o[0] ? " selected" : "") + ">" + o[1] + "</option>"; }).join("") +
      "</select> <span class='mut'>(or twice that over a week)</span></label>" +
      box("prefs.notify", p.notify, "Show new alerts as notifications on this device") +
      "<p class='mi-note'>Alerts are checked each time you open the app. For an email while you're away, the tracker's " +
      "daily email (the private WATCHLIST setting on GitHub) still works.</p></div>";
    var navLinks = Array.prototype.slice.call(document.querySelectorAll("header .bar a.nav"))
      .filter(function (a) { return !/mine\.html$/.test(a.getAttribute("href")); });
    h += "<div class='mi-card mi-set'><h3>Menu</h3><p class='mi-note'>Untick pages you don't use. They stay one search away.</p><div class='mi-grid'>" +
      navLinks.map(function (a) {
        var f = a.getAttribute("href").split("/").pop();
        return "<label class='row'><input type='checkbox' data-nav='" + esc(f) + "'" + (p.navHidden.indexOf(f) < 0 ? " checked" : "") + "> " + esc(a.textContent) + "</label>";
      }).join("") + "</div></div>";
    var stock = Object.keys(D.pages)[0];
    var list = PAGES.slice();
    if (stock) list.push(["s/" + D.pages[stock] + ".html", "Stock pages (one layout for all)", "s/stock"]);
    h += "<div class='mi-card mi-set'><h3>Pages</h3><p class='mi-note'>Open a page in customize mode and hide the sections you skip.</p>" +
      list.map(function (pg) {
        var key = pg[2] || pg[0], n = (p.hide[key] || []).length;
        return "<div class='mi-head' style='padding:6px 0'><b>" + esc(pg[1]) + "</b><span class='mut'>" + (n ? n + " hidden" : "") + "</span>" +
          "<span class='sp'></span>" + (n ? "<button class='mi-btn sm' data-reset='" + esc(key) + "'>Show all</button>" : "") +
          "<a class='mi-btn sm' style='text-decoration:none' href='" + esc(pg[0]) + "?customize'>Customize</a></div>";
      }).join("") + "</div>";
    h += "<div class='mi-card mi-set'><h3>Display</h3>" +
      box("prefs.homeCard", p.homeCard, "Show my accounts and alerts at the top of Today") +
      box("prefs.privacy", p.privacy, "Blur dollar amounts (handy when showing someone your screen)") + "</div>";
    return h;
  }

  function wireSettings() {
    view.querySelectorAll("[data-set]").forEach(function (el) {
      el.onchange = function () {
        var k = el.dataset.set.split("."), v = el.type === "checkbox" ? el.checked : +el.value;
        if (k[1] === "notify" && v) { el.checked = false; return askNotify(); }
        S[k[0]][k[1]] = v; save(); QT.navStyle();
      };
    });
    view.querySelectorAll("[data-nav]").forEach(function (el) {
      el.onchange = function () {
        var f = el.dataset.nav, l = S.prefs.navHidden.filter(function (x) { return x !== f; });
        if (!el.checked) l.push(f);
        S.prefs.navHidden = l; save(); QT.navStyle();
      };
    });
    view.querySelectorAll("[data-reset]").forEach(function (el) {
      el.onclick = function () { delete S.prefs.hide[el.dataset.reset]; save(); draw(); };
    });
  }

  // ---------------------------------------------------------------- backup
  function b64(buf) { var s = "", b = new Uint8Array(buf); for (var i = 0; i < b.length; i++) s += String.fromCharCode(b[i]); return btoa(s); }
  function unb64(s) { var b = atob(s), u = new Uint8Array(b.length); for (var i = 0; i < b.length; i++) u[i] = b.charCodeAt(i); return u; }
  var ITER = 310000;
  function keyFrom(pw, salt, iter) {
    return crypto.subtle.importKey("raw", new TextEncoder().encode(pw), "PBKDF2", false, ["deriveKey"]).then(function (base) {
      return crypto.subtle.deriveKey({ name: "PBKDF2", salt: salt, iterations: iter, hash: "SHA-256" }, base,
        { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
    });
  }
  function encrypt(obj, pw) {
    var salt = crypto.getRandomValues(new Uint8Array(16)), iv = crypto.getRandomValues(new Uint8Array(12));
    return keyFrom(pw, salt, ITER).then(function (k) {
      return crypto.subtle.encrypt({ name: "AES-GCM", iv: iv }, k, new TextEncoder().encode(JSON.stringify(obj)));
    }).then(function (ct) {
      return { quantreyda: "backup", v: 1, enc: "AES-256-GCM, PBKDF2-SHA256", iter: ITER, salt: b64(salt), iv: b64(iv), data: b64(ct) };
    });
  }
  function decrypt(file, pw) {
    return keyFrom(pw, unb64(file.salt), file.iter || ITER).then(function (k) {
      return crypto.subtle.decrypt({ name: "AES-GCM", iv: unb64(file.iv) }, k, unb64(file.data));
    }).then(function (pt) { return JSON.parse(new TextDecoder().decode(pt)); });
  }
  function download(obj, name) {
    var url = URL.createObjectURL(new Blob([JSON.stringify(obj, null, 1)], { type: "application/json" }));
    var a = document.createElement("a"); a.href = url; a.download = name; document.body.appendChild(a); a.click();
    setTimeout(function () { URL.revokeObjectURL(url); a.remove(); }, 1000);
  }
  function backupObj() {
    var c = JSON.parse(JSON.stringify(S)); delete c.seen; delete c.notified;
    return { quantreyda: "backup", v: 1, saved: new Date().toISOString(), data: c };
  }

  function renderBackup() {
    var n = S.accounts.length, h = S.accounts.reduce(function (s, a) { return s + a.holdings.length; }, 0);
    var html = "<div class='mi-card'><h3>Where your data lives</h3><p class='mi-note'>Right now: <b>" + n + " account" + (n === 1 ? "" : "s") +
      ", " + h + " holding" + (h === 1 ? "" : "s") + ", " + S.watch.length + " watched</b>, saved in this browser on this device only. " +
      "It isn't on the website, in the GitHub repo or on any server, so people you send the link to see an empty page. " +
      "The flip side: clearing this browser's site data deletes it, and your phone and computer each keep their own copy. " +
      "Keep a backup file and use it to move between devices.</p></div>";
    html += "<div class='mi-card'><h3>Back up</h3><form class='mi-form' data-act='enc'>" +
      "<label>Password<input name='pw' type='password' autocomplete='new-password' required minlength='6'></label>" +
      "<label>Again<input name='pw2' type='password' autocomplete='new-password' required minlength='6'></label>" +
      "<button class='mi-btn pri'>Download encrypted backup</button></form>" +
      "<p class='mi-note'>Encrypted on this device before it's saved; safe to keep in iCloud Drive, Google Drive or email. " +
      "There's no password reset, so don't lose it.</p>" +
      "<p style='margin:12px 0 0'><button class='mi-btn sm' data-act='plain'>Download without a password</button> " +
      "<span class='mut'>readable by anyone who gets the file</span></p></div>";
    html += "<div class='mi-card'><h3>Restore or move to another device</h3><p class='mi-note'>On the other device, open this site, go to Mine → Backup, " +
      "and pick the file (AirDrop, Files or an email to yourself all work).</p><form class='mi-form' data-act='restore'>" +
      "<label>Backup file<input type='file' name='f' accept='.json,application/json' required></label>" +
      "<button class='mi-btn pri'>Open</button></form><div id='mi-restore'></div></div>";
    html += "<div class='mi-card'><h3>Start over</h3><p class='mi-note'>Erase every account, watchlist item and setting from this browser.</p>" +
      "<button class='mi-btn dan' data-act='erase'>Erase my data from this device</button></div>";
    return html;
  }

  function stamp() { return new Date().toISOString().slice(0, 10); }

  function wireBackup() {
    var enc = view.querySelector("form[data-act=enc]");
    enc.onsubmit = function (ev) {
      ev.preventDefault();
      if (enc.pw.value !== enc.pw2.value) return flash("The two passwords don't match.", true);
      if (!window.crypto || !crypto.subtle) return flash("This browser can't encrypt here. Use the plain download instead.", true);
      encrypt(backupObj(), enc.pw.value).then(function (o) {
        download(o, "quantreyda-backup-" + stamp() + ".json"); enc.reset(); flash("Encrypted backup downloaded.");
      });
    };
    view.querySelector("[data-act=plain]").onclick = function () {
      download(backupObj(), "quantreyda-backup-" + stamp() + "-plain.json"); flash("Backup downloaded.");
    };
    var rf = view.querySelector("form[data-act=restore]");
    rf.onsubmit = function (ev) {
      ev.preventDefault();
      var file = rf.f.files[0];
      if (!file) return;
      file.text().then(function (txt) {
        var o = JSON.parse(txt);
        if (!o || o.quantreyda !== "backup") throw new Error("not a Quantreyda backup");
        if (o.data && typeof o.data === "string") {
          var pw = prompt("This backup is encrypted. Password:");
          if (pw == null) return null;
          return decrypt(o, pw).catch(function () { throw new Error("wrong password, or the file is damaged"); });
        }
        return o;
      }).then(function (o) {
        if (!o) return;
        pending = QT.fill(o.data);
        var n = pending.accounts.length, w = pending.watch.length;
        document.getElementById("mi-restore").innerHTML = "<p class='mi-note'>Found <b>" + n + " account" + (n === 1 ? "" : "s") +
          " and " + w + " watched stock" + (w === 1 ? "" : "s") + "</b>. What should happen to what's on this device now?</p>" +
          "<p><button class='mi-btn pri' data-act='replace'>Replace it with the backup</button> " +
          "<button class='mi-btn' data-act='merge'>Add the backup to it</button></p>";
        view.querySelector("[data-act=replace]").onclick = function () { S = QT.fill(pending); finish("Restored."); };
        view.querySelector("[data-act=merge]").onclick = function () {
          var ids = {}, have = {};
          S.accounts.forEach(function (a) { ids[a.id] = 1; });
          pending.accounts.forEach(function (a) { if (ids[a.id]) a.id = uid(); S.accounts.push(a); });
          S.watch.forEach(function (x) { have[x.t] = 1; });
          pending.watch.forEach(function (x) { if (!have[x.t]) S.watch.push(x); });
          finish("Added from the backup.");
        };
      }).catch(function (e) { flash("Couldn't open that file: " + e.message + ".", true); });
    };
    view.querySelector("[data-act=erase]").onclick = function () {
      if (!confirm("Erase all your accounts, watchlist and settings from this browser? This can't be undone without a backup.")) return;
      S = QT.blank(); finish("Erased from this device.");
    };
  }
  function finish(msg) {
    pending = null; save(); QT.navStyle(); draw(); flash(msg);
  }

  // ---------------------------------------------------------------- shell
  function tabBadge(n) {
    var b = document.querySelector(".mi-tabs [data-tab=alerts]");
    b.innerHTML = "Alerts" + (n ? " <span class='qt-badge'>" + n + "</span>" : "");
  }

  function current() { var t = location.hash.slice(1); return TABS.indexOf(t) >= 0 ? t : "accounts"; }
  function go(t) { if (location.hash.slice(1) !== t) location.hash = t; else draw(); }

  function draw() {
    var tab = current(), list = QT.alerts(S, D), unread = list.filter(function (a) { return a.unread; }).length;
    document.querySelectorAll(".mi-tabs button").forEach(function (b) { b.classList.toggle("on", b.dataset.tab === tab); });
    tabBadge(tab === "alerts" ? 0 : unread);
    QT.badge(tab === "alerts" ? 0 : unread);
    if (tab === "accounts") { view.innerHTML = renderAccounts(); wireAccounts(); }
    else if (tab === "watch") { view.innerHTML = renderWatch(); wireWatch(); }
    else if (tab === "alerts") { view.innerHTML = renderAlerts(list); wireAlerts(list); }
    else if (tab === "settings") { view.innerHTML = renderSettings(); wireSettings(); }
    else { view.innerHTML = renderBackup(); wireBackup(); }
    if (tab !== "alerts") QT.notify(list);
  }

  document.querySelectorAll(".mi-tabs button").forEach(function (b) { b.onclick = function () { go(b.dataset.tab); }; });
  addEventListener("hashchange", function () { if (TABS.indexOf(location.hash.slice(1)) >= 0) { draw(); scrollTo(0, 0); } });
  addEventListener("storage", function (e) { if (e.key === QT.KEY) { S = QT.load(); QT.state = S; draw(); } });  // another tab changed it
  QT.getData().then(function (d) {
    D = d;
    ["quotes", "ideas", "health", "names", "pages", "logos"].forEach(function (k) { if (!D[k]) D[k] = {}; });
    if (!D.events) D.events = [];
    draw();
  });
})();
