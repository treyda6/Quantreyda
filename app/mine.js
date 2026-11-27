// The "Mine" page: accounts, watchlist, decision journal, alerts, customizing and backup.
// Runs entirely in the browser. Data lives in localStorage via personal.js
// (window.QT); the only thing fetched is the public mine.json price file.
(function () {
  "use strict";
  var QT = window.QT, view = document.getElementById("mi-view");
  if (!QT || !view) return;
  var esc = QT.esc, money = QT.money, pct = QT.pct, KINDS = QT.KINDS;
  var S = QT.state, D = null, editing = {}, pending = null, IMP = null, watchOdd = null;
  var TABS = ["accounts", "watch", "journal", "alerts", "settings", "backup"];
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
  function flash(msg, bad, act) {  // act: optional click handler, shown as a button after the message
    var el = document.getElementById("mi-flash");
    if (!el) { el = document.createElement("p"); el.id = "mi-flash"; el.className = "mi-msg"; view.parentNode.insertBefore(el, view); }
    el.className = "mi-msg " + (bad ? "down" : "up");
    el.textContent = msg;
    if (act) {
      var b = document.createElement("button");
      b.type = "button"; b.className = "mi-btn sm"; b.style.marginLeft = "10px"; b.textContent = "Write it down";
      b.onclick = function () { el.textContent = ""; act(); };
      el.appendChild(b);
    }
    clearTimeout(flash.t); flash.t = setTimeout(function () { el.textContent = ""; }, act ? 15000 : bad ? 9000 : 4000);
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
    if (D.ideas[t]) out.push("<span class='mi-chip idea' title='On the Ideas page list'>Idea #" + D.ideas[t] + "</span>");
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
    if (a.link) h += "<p class='mi-note'>Imported from " + esc(a.link.broker) + (a.link.last4 ? " account …" + esc(a.link.last4) : "") +
      " on " + esc(a.link.at || "") + ". Import a new file from the same account to update it.</p>";
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
            (r.p ? " @ " + money(r.p.c, true) + (r.p.manual ? " (manual)" : r.p.file ? " (from import)" : "") : "");
          h += "<tr><td>" + stockCell(hh.t, sub) + "<div>" + journalChip(hh.t) + chips(hh.t) + "</div></td>" +
            "<td class='num'><span class='qt-money'>" + (r.val == null ? "<span class='mut'>no price</span>" : money(r.val)) + "</span></td>" +
            "<td class='num'>" + (r.gain == null ? "<span class='mut'>—</span>" : "<span class='qt-money " + cls(r.gain) + "'>" +
              money(r.gain) + "</span><br><small class='" + cls(r.gp) + "'>" + pct(r.gp) + "</small>") + "</td>" +
            "<td class='num mi-hide-sm'>" + (r.p ? cpct(r.p.r1) : "—") + "</td></tr>";
        }
      });
      h += "</tbody></table></div>";
      if (t.missing && !ed) h += "<p class='mi-note'>" + t.missing + " holding" + (t.missing > 1 ? "s have" : " has") +
        " no public price here yet. Its history may still be filling in, or it may be an OTC or private holding. Tap Edit to type a price in.</p>";
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

  // ---------------------------------------------------------------- ticker check
  // look/index.json is the site's public list of every US stock, ETF, mutual
  // and money market fund (lookup.py). It's fetched whole, so what you type
  // is matched here and never sent anywhere.
  var IDX = null, idxP = null;
  var KIND_NAME = { stock: "Stock", etf: "ETF", fund: "Mutual fund", money: "Money market" };
  function symIndex() {
    if (!idxP) idxP = fetch(QT.ROOT + "look/index.json").then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (!j || !j.s || !j.s.length) { idxP = null; return null; }
        var kinds = j.kinds || ["stock", "etf", "fund", "money"], map = {};
        var rows = j.s.map(function (r) {
          var o = { t: QT.norm(r[0]), n: r[1] || "", k: kinds[r[2]] || "stock", page: !!r[3], has: !!r[4] };
          o.nl = (o.t + " " + o.n).toLowerCase();
          map[o.t] = o;
          return o;
        });
        IDX = { rows: rows, map: map };
        return IDX;
      }).catch(function () { idxP = null; return null; });
    return idxP;
  }
  // Without the list (offline, or a site build that hasn't made it yet) nothing is blocked.
  function knownTicker(t) { return !IDX || !!IDX.map[t] || !!(D && D.quotes[t]); }
  function suggest(q, n) {
    if (!IDX || !String(q || "").trim()) return [];
    if (window.QTLook && QTLook.search) return QTLook.search(IDX.rows, q).slice(0, n || 6);
    var up = QT.norm(q), lo = String(q).trim().toLowerCase();
    return IDX.rows.filter(function (r) { return r.t.indexOf(up) === 0 || r.nl.indexOf(" " + lo) >= 0; }).slice(0, n || 6);
  }
  function nameOf(t) { return (IDX && IDX.map[t] && IDX.map[t].n) || D.names[t] || ""; }

  // Suggestions under a ticker box as you type. multi: comma-separated list.
  function autocomplete(input, multi, picked) {
    if (!input || input.dataset.ac) return;
    input.dataset.ac = "1";
    input.setAttribute("autocomplete", "off");
    var box = document.createElement("div");
    box.className = "lk-sugg mi-ac"; box.hidden = true;
    input.parentNode.appendChild(box);
    var hits = [], on = -1;
    function token() { return multi ? input.value.split(/[\s,;]+/).pop() : input.value; }
    function paint() {
      box.innerHTML = hits.map(function (r, i) {
        return "<a href='#' data-i='" + i + "' class='" + (i === on ? "on" : "") + "'><b>" + esc(r.t) + "</b><span>" + esc(r.n) +
          "</span><em>" + esc(KIND_NAME[r.k] || "") + "</em></a>";
      }).join("");
      box.hidden = !hits.length;
    }
    function pick(r) {
      if (multi) {
        var parts = input.value.split(/([\s,;]+)/); parts[parts.length - 1] = r.t;
        input.value = parts.join("") + ", ";
      } else input.value = r.t;
      hits = []; on = -1; paint();
      if (picked) picked(r); else input.focus();
    }
    function update() {
      symIndex().then(function () { hits = suggest(token()); on = -1; paint(); });
    }
    input.addEventListener("focus", function () { symIndex(); });
    input.addEventListener("input", update);
    input.addEventListener("blur", function () { setTimeout(function () { box.hidden = true; }, 150); });
    input.addEventListener("keydown", function (e) {
      if (box.hidden || !hits.length) return;
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault(); on = (on + (e.key === "ArrowDown" ? 1 : hits.length - 1) + (on < 0 && e.key === "ArrowUp" ? 1 : 0)) % hits.length; paint();
      } else if (e.key === "Enter" && on >= 0) { e.preventDefault(); pick(hits[on]); }
      else if (e.key === "Escape") { box.hidden = true; }
    });
    box.addEventListener("mousedown", function (e) {
      var a = e.target.closest("a[data-i]");
      if (a) { e.preventDefault(); pick(hits[+a.dataset.i]); }
    });
  }

  // "We don't know that ticker": suggestions, and a way to add it anyway
  // (funds only sold inside a 401(k) or 529 plan have no public ticker).
  function nearMiss(t) {  // "VTII" -> VTI, VTV...: shorten until something matches
    for (var k = t.length - 1; k >= 1; k--) {
      var hits = suggest(t.slice(0, k), 4).filter(function (r) { return r.t !== t; });
      if (hits.length) return hits;
    }
    return [];
  }

  function unknownNote(form, list, anyway, input, onFix) {
    var old = form.parentNode.querySelector(".mi-unk");
    if (old) old.remove();
    if (!list.length) return;
    var el = document.createElement("div");
    el.className = "mi-unk";
    var alt = list.length === 1 ? nearMiss(list[0]) : [];
    el.innerHTML = "<b>" + esc(list.join(", ")) + "</b> " + (list.length === 1 ? "isn't a ticker" : "aren't tickers") +
      " we can find among US stocks, ETFs and mutual funds. Check the spelling" +
      (alt.length ? ", or did you mean " + alt.map(function (r) {
        return "<a href='#' data-t='" + esc(r.t) + "' title='" + esc(r.n) + "'>" + esc(r.t) + "</a>"; }).join(", ") + "?" : ".") +
      " <span class='mut'>A fund that's only sold inside a 401(k) or 529 plan won't be listed; add it anyway and type its price.</span> " +
      "<button type='button' class='mi-btn sm'>Add " + (list.length === 1 ? "it" : "them") + " anyway</button>";
    form.parentNode.insertBefore(el, form.nextSibling);
    el.querySelectorAll("a[data-t]").forEach(function (a) {
      a.onclick = function (e) {
        e.preventDefault();
        if (input) input.value = input.value.replace(new RegExp("\\b" + list[0].replace(/\./g, "\\.") + "\\b", "i"), a.dataset.t);
        el.remove();
        if (onFix) onFix();
        if (input) input.focus();
      };
    });
    el.querySelector("button").onclick = function () { el.remove(); anyway(); };
  }

  // ---------------------------------------------------------------- broker import
  // The file is parsed here by brokerimport.js and never leaves this device.
  var HOWTO = [
    ["Fidelity", "On fidelity.com: Accounts → Positions → the download icon (top right of the table). Pick “All accounts” first to get every account in one file."],
    ["Schwab", "On schwab.com: Accounts → Positions → Export (top right). “All accounts” exports each account as its own section."],
    ["Vanguard", "On vanguard.com: My accounts → Download center → choose the accounts, “CSV” and “Holdings”."],
    ["Anyone else", "Any positions or holdings download with a symbol and a quantity (or shares) column works. You can also copy the table from the broker's website or a spreadsheet and paste it below."]
  ];

  function importCard(open) {
    return "<div class='mi-card' id='mi-import'><details" + (open ? " open" : "") + "><summary><b>Import from your broker</b> " +
      "<span class='mut'>Fidelity, Schwab, Vanguard, E*TRADE or any CSV</span></summary>" +
      "<p class='mi-note'>Faster and more exact than typing: shares, cost basis and cash come straight from the broker's download. " +
      "The file is read on this device and never uploaded, and account numbers are cut to their last 4 digits. " +
      "Importing again later updates the same accounts.</p>" +
      "<form class='mi-form' data-act='imp-file'><label>Positions file<input type='file' name='f' multiple " +
      "accept='.csv,.txt,.tsv,text/csv,text/plain,text/tab-separated-values'></label></form>" +
      "<form class='mi-form' data-act='imp-paste'><textarea name='txt' placeholder='…or paste the positions table here (copied from the broker&#39;s site or a spreadsheet)'></textarea>" +
      "<button class='mi-btn'>Read pasted text</button></form>" +
      "<details style='margin-top:10px'><summary class='mut'>Where to find the download</summary>" +
      HOWTO.map(function (x) { return "<p class='mi-note'><b>" + esc(x[0]) + ":</b> " + esc(x[1]) + "</p>"; }).join("") +
      "<p class='mi-note'>Brokers move these buttons now and then; look for “Download” or “Export” on the positions page.</p></details>" +
      "</details></div>";
  }

  function findMatch(pa) {
    var name = (pa.name || "").toLowerCase();
    return S.accounts.filter(function (a) {
      return a.link && a.link.broker === pa.broker && pa.last4 && a.link.last4 === pa.last4;
    })[0] || S.accounts.filter(function (a) {
      return a.link && a.link.broker === pa.broker && !pa.last4 && (a.link.name || "").toLowerCase() === name;
    })[0] || (name && name !== "imported account" ? S.accounts.filter(function (a) { return !a.link && a.name.toLowerCase() === name; })[0] : null);
  }

  function startImport(results) {
    var list = [], notes = [];
    results.forEach(function (r) {
      notes = notes.concat(r.notes.map(function (n) { return (r.file ? r.file + ": " : "") + n; }));
      r.accounts.forEach(function (pa) {
        pa.broker = r.broker;
        var m = findMatch(pa);
        pa.include = true;
        pa.target = m ? m.id : "new";
        pa.newName = m ? m.name : (r.broker !== "CSV" && r.broker !== "List" && !/^Account …/.test(pa.name) ? r.broker + " " : "") + pa.name;
        if (/^(Imported account|Account …\w*)$/.test(pa.name) && r.broker !== "CSV" && r.broker !== "List") pa.newName = r.broker + " " + pa.name.replace("Imported account", "account");
        pa.newKind = m ? m.kind : pa.kind;
        pa.owner = m ? (m.owner || "") : "";
        list.push(pa);
      });
    });
    if (!list.length) return flash(notes[0] || "Nothing to import in that file.", true);
    IMP = { accounts: list, notes: notes };
    symIndex().then(function () { if (IMP) draw(); });  // mark tickers we can't find
    go("accounts"); draw(); scrollTo(0, 0);
  }

  function diffText(pa) {
    if (pa.target === "new") return "";
    var a = S.accounts.filter(function (x) { return x.id === pa.target; })[0];
    if (!a) return "";
    var old = {}, add = 0, chg = 0, rm = 0, seen = {};
    a.holdings.forEach(function (h) { old[h.t] = h; });
    pa.holdings.forEach(function (h) {
      seen[h.t] = 1;
      if (!old[h.t]) add++; else if (Math.abs(num(old[h.t].sh) - h.sh) > 1e-6) chg++;
    });
    a.holdings.forEach(function (h) { if (!seen[h.t]) rm++; });
    var bits = [];
    if (add) bits.push(add + " new");
    if (chg) bits.push(chg + " with new share counts");
    if (rm) bits.push(rm + " no longer held (removed)");
    return "Updates <b>" + esc(a.name) + "</b>: " + (bits.length ? bits.join(", ") : "same holdings") +
      "; cash " + money(num(a.cash)) + " → " + money(pa.cash) + ".";
  }

  function kindOptions(sel) {
    return (sel ? "" : "<option value='' selected>Pick a type…</option>") + Object.keys(KINDS).map(function (k) {
      return "<option value='" + k + "'" + (k === sel ? " selected" : "") + ">" + esc(KINDS[k]) + "</option>";
    }).join("");
  }

  function renderImport() {
    var n = IMP.accounts.length;
    var h = "<div class='mi-card' id='mi-review'><div class='mi-head'><h3>Review your import</h3><span class='sp'></span>" +
      "<span class='mut'>" + n + " account" + (n === 1 ? "" : "s") + " found</span></div>" +
      "<p class='mi-note'>Check the type of each account (it decides the tax notes and the IRA limit) and where it should go. " +
      "Nothing is saved until you press Save.</p>";
    IMP.notes.forEach(function (x) { h += "<p class='mi-note down'>" + esc(x) + "</p>"; });
    IMP.accounts.forEach(function (pa, i) {
      var opts = "<option value='new'" + (pa.target === "new" ? " selected" : "") + ">A new account</option>" +
        S.accounts.map(function (a) {
          return "<option value='" + a.id + "'" + (pa.target === a.id ? " selected" : "") + ">Update: " + esc(a.name) + "</option>";
        }).join("");
      h += "<div class='mi-imp' data-i='" + i + "'><div class='mi-head'><label class='row'><input type='checkbox' data-k='include'" +
        (pa.include ? " checked" : "") + "> <b>" + esc(pa.broker) + " · " + esc(pa.name) + "</b></label>" +
        (pa.last4 ? "<span class='mut'>…" + esc(pa.last4) + "</span>" : "") + "<span class='sp'></span><span class='mut'>" +
        pa.holdings.length + " holding" + (pa.holdings.length === 1 ? "" : "s") +
        (function (k) { return k ? " · <span class='down'>" + k + " ticker" + (k === 1 ? "" : "s") + " not found</span>" : ""; })(
          pa.holdings.filter(function (x) { return !knownTicker(x.t); }).length) + (pa.cash ? " · cash <span class='qt-money'>" + money(pa.cash) + "</span>" : "") +
        (pa.value ? " · <span class='qt-money'>" + money(pa.value) + "</span> in the file" : "") + "</span></div>";
      if (pa.include) {
        h += "<div class='mi-form'><label>Save as<select data-k='target'>" + opts + "</select></label>" +
          "<label>Name<input data-k='newName' value='" + esc(pa.newName) + "'></label>" +
          "<label>Type<select data-k='newKind'" + (pa.newKind ? "" : " class='need'") + ">" + kindOptions(pa.newKind) + "</select></label>" +
          "<label>For (optional)<input data-k='owner' placeholder='e.g. Emma' value='" + esc(pa.owner) + "'></label></div>";
        var d = diffText(pa);
        if (d) h += "<p class='mi-note'>" + d + "</p>";
        h += "<details><summary class='mut'>See the holdings</summary><div class='wrap'><table class='mi-t'><thead><tr><th>Stock</th>" +
          "<th class='num'>Shares</th><th class='num'>Cost / share</th><th class='num'>Price in file</th></tr></thead><tbody>" +
          pa.holdings.map(function (x) {
            return "<tr><td><b>" + esc(x.t) + "</b>" + (knownTicker(x.t) ? "" : " <span class='mi-chip sell' title='Not found among US stocks, ETFs and mutual funds. Fine for a plan-only fund: the price from the file is used.'>not found</span>") +
              "<small class='mut' style='display:block'>" + esc(x.desc) + "</small></td><td class='num'>" +
              x.sh.toLocaleString("en-US", { maximumFractionDigits: 4 }) + "</td><td class='num'>" + (x.cost ? money(x.cost, true) : "—") +
              "</td><td class='num'>" + (x.price ? money(x.price, true) : "—") + "</td></tr>";
          }).join("") + "</tbody></table></div>" +
          (pa.skipped.length ? "<p class='mi-note'>Left out (no stock or fund ticker, such as bonds, CDs or options): " +
            esc(pa.skipped.join("; ")) + "</p>" : "") + "</details>";
      }
      h += "</div>";
    });
    return h + "<p style='margin:16px 0 0'><button class='mi-btn pri' data-act='imp-save'>Save to this device</button> " +
      "<button class='mi-btn' data-act='imp-cancel'>Cancel</button></p></div>";
  }

  function saveImport() {
    var todo = IMP.accounts.filter(function (pa) { return pa.include; });
    if (!todo.length) return flash("Tick at least one account to import.", true);
    var miss = todo.filter(function (pa) { return !pa.newKind; });
    if (miss.length) return flash("Pick a type for " + miss.map(function (pa) { return pa.name; }).join(", ") + ".", true);
    var targets = {};
    for (var i = 0; i < todo.length; i++) {
      var t = todo[i].target;
      if (t !== "new" && targets[t]) return flash("Two imported accounts point at the same account. Pick a different one for one of them.", true);
      targets[t] = 1;
    }
    var today = new Date().toISOString().slice(0, 10), nH = 0, tickers = [];
    todo.forEach(function (pa) {
      var a = pa.target === "new" ? null : S.accounts.filter(function (x) { return x.id === pa.target; })[0];
      if (!a) { a = { id: uid(), contrib: 0, holdings: [] }; S.accounts.push(a); }
      var old = {};
      a.holdings.forEach(function (h) { old[h.t] = h; });
      a.name = pa.newName.trim() || pa.name; a.kind = pa.newKind; a.owner = pa.owner.trim(); a.cash = pa.cash;
      a.holdings = pa.holdings.map(function (x) {
        var o = old[x.t] || {};
        tickers.push(x.t);
        return { t: x.t, sh: x.sh, cost: x.cost || num(o.cost) || 0, px: x.price ? 0 : num(o.px) || 0, fpx: x.price || 0 };
      });
      a.link = { broker: pa.broker, last4: pa.last4, name: pa.name, at: today };
      nH += a.holdings.length;
    });
    IMP = null; save(); draw(); scrollTo(0, 0);
    flash("Imported " + todo.length + " account" + (todo.length === 1 ? "" : "s") + " and " + nH + " holdings. Saved on this device only.");
    QT.fillQuotes(D, tickers).then(draw);
  }

  function readFiles(files) {
    if (!window.QTImport) return flash("The importer didn't load. Reload the page and try again.", true);
    Promise.all(Array.prototype.map.call(files, function (f) {
      return f.text().then(function (txt) { var r = QTImport.parse(txt, f.name); r.file = f.name; return r; });
    })).then(startImport, function () { flash("Couldn't read that file.", true); });
  }

  function wireImport() {
    var ff = view.querySelector("form[data-act=imp-file]");
    if (ff) ff.f.onchange = function () { if (ff.f.files.length) readFiles(ff.f.files); };
    var pf = view.querySelector("form[data-act=imp-paste]");
    if (pf) pf.onsubmit = function (ev) {
      ev.preventDefault();
      if (!window.QTImport) return flash("The importer didn't load. Reload the page and try again.", true);
      var r = QTImport.parse(pf.txt.value, ""); r.file = "";
      startImport([r]);
    };
    if (!IMP) return;
    view.querySelectorAll(".mi-imp").forEach(function (box) {
      var pa = IMP.accounts[+box.dataset.i];
      box.querySelectorAll("[data-k]").forEach(function (el) {
        el.onchange = function () {
          var k = el.dataset.k;
          pa[k] = el.type === "checkbox" ? el.checked : el.value;
          if (k === "target" && el.value !== "new") {  // updating: start from that account's settings
            var a = S.accounts.filter(function (x) { return x.id === el.value; })[0];
            if (a) { pa.newName = a.name; pa.newKind = a.kind; pa.owner = a.owner || ""; }
          }
          if (k === "include" || k === "target" || k === "newKind") draw();
        };
      });
    });
    view.querySelector("[data-act=imp-save]").onclick = saveImport;
    view.querySelector("[data-act=imp-cancel]").onclick = function () { IMP = null; draw(); flash("Import cancelled. Nothing was saved."); };
  }

  function renderAccounts() {
    var accs = S.accounts, h = "";
    if (IMP) return renderImport();
    h += importCard(!accs.length);
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
    wireImport();
    if (IMP) return;
    wireJournalChips();
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
      autocomplete(add.t, false, function () { add.sh.focus(); });
      add.onsubmit = function (ev) {
        ev.preventDefault();
        var t = QT.norm(add.t.value), sh = num(add.sh.value), cost = num(add.cost.value);
        if (!t || !(sh > 0)) return flash("Enter a ticker and a number of shares.", true);
        symIndex().then(function () {
          if (!knownTicker(t)) return unknownNote(add, [t], function () {
            addHolding(a, t, sh, cost); editing[a.id] = true; save(); draw();
            flash(t + " added. Type its price in the Price (manual) box so it counts toward your totals.");
          }, add.t);
          addHolding(a, t, sh, cost);
          save(); draw();
          var bought = { t: t, px: cost };
          QT.fillQuotes(D, [t]).then(function () {
            draw();
            if (!D.quotes[t]) flash(t + " added, but there's no price for it here yet. Tap Edit to type one in.", true);
            else if (!openEntry(t)) flash(t + " added. Write down why you bought it while it's fresh: Journal tab.", false, function () { newEntry(bought); });
          });
        });
      };
      var paste = card.querySelector("form[data-act=paste]");
      if (paste) paste.onsubmit = function (ev) {
        ev.preventDefault();
        var n = 0;
        paste.list.value.split(/\n/).forEach(function (line) {
          var p = line.trim().split(/[\s,;\t]+/);
          if (p.length >= 2 && addHolding(a, p[0], num(p[1]), num(p[2]), true)) n++;
        });
        save(); QT.fillQuotes(D, a.holdings.map(function (h) { return h.t; })).then(draw);
        draw(); flash(n ? "Added " + n + " holding" + (n > 1 ? "s" : "") + "." : "Nothing recognized. Use: ticker shares cost", !n);
        if (n) symIndex().then(function () {
          var odd = a.holdings.map(function (h) { return h.t; }).filter(function (t) { return !knownTicker(t); });
          if (odd.length) flash("Added " + n + ". Not found among US stocks, ETFs and funds: " + odd.join(", ") +
            ". Fix a typo by removing it, or type a price for a plan-only fund.", true);
        });
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
    autocomplete(f.t, true);
    f.onsubmit = function (ev) {
      ev.preventDefault();
      var want = f.t.value.split(/[\s,;]+/).map(QT.norm).filter(Boolean), note = f.note.value.trim();
      symIndex().then(function () {
        var odd = want.filter(function (t) { return !knownTicker(t); });
        watchOdd = odd.length ? { list: odd, note: note } : null;
        if (!addWatch(want.filter(function (t) { return knownTicker(t); }), note)) draw();
      });
    };
    if (watchOdd) {  // kept across redraws until fixed or added anyway
      f.t.value = watchOdd.list.join(", ");
      unknownNote(f, watchOdd.list, function () {
        var o = watchOdd; watchOdd = null; f.t.value = ""; addWatch(o.list, o.note);
      }, f.t, function () { watchOdd = null; });
    }
    function addWatch(list, note) {
      var have = {}, added = [];
      S.watch.forEach(function (w) { have[w.t] = 1; });
      list.forEach(function (t) {
        if (!have[t]) { S.watch.push({ t: t, note: note, mute: false }); have[t] = 1; added.push(t); }
      });
      if (!added.length) return false;
      save(); draw();
      QT.fillQuotes(D, added).then(function () {
        draw();
        var none = added.filter(function (t) { return !D.quotes[t] && !recent(t, 45).length; });
        if (none.length) flash("Added. No recent price or activity on file for " + none.join(", ") + " yet; alerts still work when something happens.");
      });
      return true;
    }
    view.querySelectorAll("tr[data-w]").forEach(function (tr) {
      var w = S.watch[+tr.dataset.w];
      tr.querySelector("[data-act=mute]").onchange = function (ev) { w.mute = !ev.target.checked; save(); };
      tr.querySelector("[data-act=rm]").onclick = function () { S.watch.splice(+tr.dataset.w, 1); save(); draw(); };
    });
  }

  // ---------------------------------------------------------------- decision journal
  // Munger's discipline: write down why you bought, what would make you sell,
  // and when you'll look again. Then actually look again. Saved on this device
  // only, like everything else on this page, and included in the backup.
  var jEdit = null, jPre = null, jReview = null, jFocus = null;
  var VERDICT = { holds: "Still holds", changed: "Changed", broken: "Broken", sold: "Sold" };

  function addMonths(iso, n) {
    var d = new Date(iso + "T12:00:00");
    d.setMonth(d.getMonth() + n);
    return d.toISOString().slice(0, 10);
  }
  function entry(id) { return S.journal.filter(function (j) { return j.id === id; })[0]; }
  function openEntry(t) { return S.journal.filter(function (j) { return j.t === t && !j.closed; })[0]; }
  function isDue(j) { return !j.closed && j.review && j.review <= QT.today(); }
  function newEntry(pre) { jEdit = "new"; jPre = pre || {}; jReview = null; go("journal"); }
  function costOf(t) {  // what you paid, from your accounts, for a new entry's default
    var c = 0;
    S.accounts.forEach(function (a) { a.holdings.forEach(function (h) { if (h.t === t && h.cost > 0) c = h.cost; }); });
    return c;
  }

  function journalChip(t) {
    var j = openEntry(t);
    if (j) return "<button type='button' class='mi-chip" + (isDue(j) ? " idea" : "") + "' data-jgo='" + esc(j.id) + "' title='Your reason for owning it'>" +
      (isDue(j) ? "Review due" : "Your thesis") + "</button>";
    return "<button type='button' class='mi-chip add' data-jnew='" + esc(t) + "' title='Write down why you own it'>+ Why I own it</button>";
  }
  function wireJournalChips() {
    view.querySelectorAll("[data-jgo]").forEach(function (b) { b.onclick = function () { jFocus = b.dataset.jgo; go("journal"); }; });
    view.querySelectorAll("[data-jnew]").forEach(function (b) {
      b.onclick = function () { var t = b.dataset.jnew; newEntry({ t: t, px: costOf(t) }); };
    });
  }

  function reviewOpts(sel) {
    return [[3, "In 3 months"], [6, "In 6 months"], [12, "In a year"], [0, "No reminder"]].map(function (o) {
      return "<option value='" + o[0] + "'" + (o[0] === sel ? " selected" : "") + ">" + o[1] + "</option>";
    }).join("");
  }

  function entryForm(j) {
    var isNew = !j.id;
    return "<div class='mi-card mi-jform'><h3>" + (isNew ? "New journal entry" : "Edit " + esc(j.t)) + "</h3>" +
      "<p class='mi-note'>Write it so future-you can judge it: what you expect to happen, and roughly by when. Be specific; \"it's a great company\" can't be wrong.</p>" +
      "<form class='mi-form' data-act='jsave'>" +
      "<label>Ticker<input name='t' required size='7' autocapitalize='characters' placeholder='COST' value='" + esc(j.t || "") + "'></label>" +
      "<label>Bought on<input name='d' type='date' required value='" + esc(j.d || QT.today()) + "'></label>" +
      "<label>Price paid<input name='px' inputmode='decimal' size='8' placeholder='optional' value='" + esc(j.px || "") + "'></label>" +
      (isNew ? "<label>Review<select name='in'>" + reviewOpts(6) + "</select></label>"
             : "<label>Review on<input name='review' type='date' value='" + esc(j.review || "") + "'></label>") +
      "<label class='wide'>Why I bought it<textarea name='thesis' required placeholder='e.g. Membership fees keep growing about 8% a year and renewals stay above 90%. At about 45 times earnings I&#39;m paying up for that, so I expect it to roughly match the market, with less risk.'>" + esc(j.thesis || "") + "</textarea></label>" +
      "<label class='wide'>What would make me sell<textarea name='sell' required placeholder='e.g. Renewal rate drops below 88% two quarters in a row, the CEO starts large acquisitions, or the price runs past 60 times earnings.'>" + esc(j.sell || "") + "</textarea></label>" +
      "<button class='mi-btn pri'>" + (isNew ? "Save entry" : "Save changes") + "</button>" +
      "<button type='button' class='mi-btn' data-act='jcancel'>Cancel</button></form></div>";
  }

  function reviewForm(j) {
    return "<form class='mi-form mi-jrev' data-act='jrev'>" +
      "<div class='mi-jv' role='radiogroup' aria-label='Is the reason still true?'>" + ["holds", "changed", "broken", "sold"].map(function (v, i) {
        return "<label><input type='radio' name='v' value='" + v + "'" + (i ? "" : " checked") + "> " +
          { holds: "Still holds", changed: "It's changed", broken: "It's broken", sold: "I sold it" }[v] + "</label>";
      }).join("") + "</div>" +
      "<label class='wide'>Notes<textarea name='note' placeholder='What happened since you bought? Did your sell signals show up? If you sold: what did you learn?'></textarea></label>" +
      "<label class='mi-jnext'>Next review<select name='in'>" + reviewOpts(6) + "</select></label>" +
      "<label class='mi-jsold' hidden>Sold at<input name='spx' inputmode='decimal' size='8' placeholder='optional'></label>" +
      "<button class='mi-btn pri'>Save review</button><button type='button' class='mi-btn' data-act='jcancel'>Cancel</button></form>";
  }

  function entryCard(j) {
    var q = D.quotes[j.t], since = q && j.px > 0 ? q[0] / j.px - 1 : null, due = isDue(j);
    var sub = "Bought " + j.d + (j.px > 0 ? " at " + money(j.px, true) : "");
    var status = j.closed ? "<span class='mi-kind'>Sold" + (j.sold && j.sold.d ? " " + esc(j.sold.d) : "") + "</span>"
      : due ? "<span class='mi-kind due'>Review due</span>"
      : j.review ? "<span class='mut'>Review on " + esc(j.review) + "</span>" : "<span class='mut'>No review set</span>";
    var h = "<div class='mi-card mi-j" + (due ? " due" : "") + (j.closed ? " closed" : "") + "' data-j='" + esc(j.id) + "' id='j-" + esc(j.id) + "'>" +
      "<div class='mi-head'>" + stockCell(j.t, sub) + status + "<span class='sp'></span>" +
      (since != null ? "<span title='" + (j.closed && j.sold && j.sold.px ? "Sold at " + money(j.sold.px, true) : "Price change since you bought") + "'>" +
        cpct(j.closed && j.sold && j.sold.px > 0 ? j.sold.px / j.px - 1 : since) + " <small class='mut'>" + (j.closed && j.sold && j.sold.px > 0 ? "when sold" : "since") + "</small></span>" : "") + "</div>" +
      "<div class='mi-jgrid'><div><span>Why I bought it</span><p>" + esc(j.thesis) + "</p></div>" +
      "<div><span>What would make me sell</span><p>" + esc(j.sell) + "</p></div></div>";
    if (j.reviews && j.reviews.length) {
      h += "<details class='mi-jhist'" + (j.reviews.length < 3 ? " open" : "") + "><summary>" + j.reviews.length + " review" + (j.reviews.length > 1 ? "s" : "") + "</summary>" +
        j.reviews.slice().reverse().map(function (r) {
          return "<div class='mi-al'><div class='body'><b class='v-" + esc(r.v) + "'>" + esc(VERDICT[r.v] || r.v) + "</b> <small>" + esc(r.d) + "</small>" +
            (r.note ? "<br>" + esc(r.note) : "") + "</div></div>";
        }).join("") + "</details>";
    }
    if (jReview === j.id) h += reviewForm(j);
    else h += "<p class='mi-jbtns'>" + (j.closed ? "" : "<button class='mi-btn sm" + (due ? " pri" : "") + "' data-act='jreview'>Review now</button>") +
      "<button class='mi-btn sm' data-act='jedit'>Edit</button><button class='mi-btn sm dan' data-act='jdel'>Delete</button></p>";
    return h + "</div>";
  }

  function renderJournal() {
    var open = S.journal.filter(function (j) { return !j.closed; }), shut = S.journal.filter(function (j) { return j.closed; });
    var due = open.filter(isDue).length;
    open.sort(function (a, b) {  // due first, then the soonest review
      var x = isDue(a) - isDue(b);
      return x ? -x : String(a.review || "9999") < String(b.review || "9999") ? -1 : 1;
    });
    var h = "<div class='mi-card'><div class='mi-head'><h3>Decision journal</h3><span class='mut'>" + open.length + " open" +
      (due ? " · <b class='down'>" + due + " due for review</b>" : "") + "</span><span class='sp'></span>" +
      (jEdit ? "" : "<button class='mi-btn pri sm' data-act='jnew'>New entry</button>") + "</div>" +
      "<p class='mi-note'>For each buy: why you bought it, what would make you sell, and a date to look again. " +
      "You get a reminder here, on the Mine badge and as a notification if those are on, when a review comes due. " +
      "Charlie Munger's point: writing it down keeps you from rewriting your reasons after the fact.</p></div>";
    if (jEdit) h += entryForm(jEdit === "new" ? (jPre || {}) : entry(jEdit) || {});
    if (!S.journal.length && !jEdit) return h + "<p class='mi-empty'>Nothing yet. Start with something you already own: why did you buy it?</p>";
    h += open.map(entryCard).join("");
    if (shut.length) h += "<details class='mi-jclosed'><summary>Sold (" + shut.length + ")</summary>" + shut.map(entryCard).join("") + "</details>";
    return h;
  }

  function wireJournal() {
    var nb = view.querySelector("[data-act=jnew]");
    if (nb) nb.onclick = function () { newEntry({}); };
    view.querySelectorAll("[data-act=jcancel]").forEach(function (b) { b.onclick = function () { jEdit = jReview = null; draw(); }; });
    var f = view.querySelector("form[data-act=jsave]");
    if (f) {
      if (jEdit === "new" && !f.t.value) f.t.focus();
      f.t.onchange = function () {  // fill in what you paid if an account knows
        var t = QT.norm(f.t.value), c = costOf(t);
        if (c && !f.px.value) f.px.value = +c.toFixed(4);
      };
      f.onsubmit = function (ev) {
        ev.preventDefault();
        var t = QT.norm(f.t.value);
        if (!t) return flash("Enter a ticker.", true);
        var j = jEdit === "new" ? { id: uid(), made: new Date().toISOString(), reviews: [], closed: false } : entry(jEdit);
        j.t = t; j.d = f.d.value || QT.today(); j.px = num(f.px.value);
        j.thesis = f.thesis.value.trim(); j.sell = f.sell.value.trim();
        if (f["in"]) { var m = +f["in"].value; j.review = m ? addMonths(QT.today(), m) : ""; }
        else j.review = f.review.value || "";
        if (jEdit === "new") S.journal.push(j);
        jEdit = null; jFocus = j.id; save(); draw();
        flash(j.review ? "Saved. You'll get a reminder on " + j.review + "." : "Saved.");
        QT.fillQuotes(D, [t]).then(draw);
      };
    }
    view.querySelectorAll("[data-j]").forEach(function (card) {
      var j = entry(card.dataset.j);
      if (!j) return;
      var on = function (act, fn) { var b = card.querySelector("[data-act=" + act + "]"); if (b) b.onclick = fn; };
      on("jreview", function () { jReview = j.id; jEdit = null; draw(); });
      on("jedit", function () { jEdit = j.id; jReview = null; draw(); scrollTo(0, 0); });
      on("jdel", function () {
        if (!confirm("Delete your " + j.t + " journal entry and its reviews?")) return;
        S.journal = S.journal.filter(function (x) { return x !== j; }); save(); draw();
      });
      var rf = card.querySelector("form[data-act=jrev]");
      if (!rf) return;
      var sync = function () {
        var sold = rf.v.value === "sold";
        rf.querySelector(".mi-jnext").hidden = sold; rf.querySelector(".mi-jsold").hidden = !sold;
      };
      rf.querySelectorAll("input[name=v]").forEach(function (r) { r.onchange = sync; });
      rf.onsubmit = function (ev) {
        ev.preventDefault();
        var v = rf.v.value, today = QT.today();
        j.reviews = j.reviews || [];
        j.reviews.push({ d: today, v: v, note: rf.note.value.trim() });
        if (v === "sold") { j.closed = true; j.review = ""; j.sold = { d: today, px: num(rf.spx.value) }; }
        else { var m = +rf["in"].value; j.review = m ? addMonths(today, m) : ""; }
        jReview = null; save(); draw();
        flash(v === "sold" ? "Closed. It stays under Sold so you can learn from it."
          : v === "broken" ? "Noted. If the reason you bought is gone, ask whether you'd buy it today." : "Review saved.");
      };
    });
    if (jFocus) {
      var el = document.getElementById("j-" + jFocus);
      if (el) { el.scrollIntoView({ block: "center" }); el.classList.add("flash"); }
      jFocus = null;
    }
  }

  // ---------------------------------------------------------------- alerts
  var KIND_LABEL = { ins: "Insider", pol: "Politician", news: "News", move: "Price move", journal: "Decision journal" };
  var WHY = { own: "you own this", watch: "on your watchlist", journal: "<a href='#journal'>open your journal</a>" };
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
        KIND_LABEL[a.k] + " · " + (WHY[a.why] || WHY.watch) + link + "</small></div></div>";
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
  function sitePages() {  // whatever the menu links to, so new pages show up by themselves
    var seen = {}, out = [];
    document.querySelectorAll("header .bar a.nav").forEach(function (a) {
      var f = a.getAttribute("href").split("/").pop();
      if (f && f !== "mine.html" && !seen[f]) { seen[f] = 1; out.push([f, a.textContent.trim()]); }
    });
    return out;
  }

  function renderSettings() {
    var r = S.rules, p = S.prefs;
    function box(id, on, label) {
      return "<label class='row'><input type='checkbox' data-set='" + id + "'" + (on ? " checked" : "") + "> " + label + "</label>";
    }
    var h = "<div class='mi-card mi-set' id='focus'><h3>Focus mode</h3>" +
      box("prefs.focus", p.focus, "<b>Trim the noise</b> <span class='mut'>(on unless you turn it off)</span>") +
      "<p class='mi-note'>What long-term investors like Buffett, Munger and Lynch would skip. You can also flip it with the Focus button at the top of every page.</p>" +
      "<div class='mi-jgrid'><div><span>Hides</span><p>" + QT.FOCUS.hides.map(esc).join(" · ") + "</p></div>" +
      "<div><span>Keeps</span><p>" + QT.FOCUS.keeps.map(esc).join(" · ") + "</p></div></div></div>";
    h += "<div class='mi-card mi-set'><h3>Alert me about</h3><div class='mi-grid'>" +
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
        return "<label class='row'><input type='checkbox' data-nav='" + esc(f) + "'" + (p.navHidden.indexOf(f) < 0 ? " checked" : "") + "> " + esc(a.textContent) +
          (p.focus && QT.FOCUS.pages[f] ? " <span class='mut'>(Focus hides it)</span>" : "") + "</label>";
      }).join("") + "</div></div>";
    var stock = Object.keys(D.pages)[0];
    var list = sitePages();
    if (stock) list.push(["s/" + D.pages[stock] + ".html", "Stock pages (one layout for all)", "s/stock"]);
    h += "<div class='mi-card mi-set'><h3>Pages</h3><p class='mi-note'>Open a page in customize mode and hide the sections you skip.</p>" +
      list.map(function (pg) {
        var key = pg[2] || pg[0], n = (p.hide[key] || []).length;
        return "<div class='mi-head' style='padding:6px 0'><b>" + esc(pg[1]) + "</b><span class='mut'>" + (n ? n + " hidden" : "") + "</span>" +
          "<span class='sp'></span>" + (n ? "<button class='mi-btn sm' data-reset='" + esc(key) + "'>Show all</button>" : "") +
          "<a class='mi-btn sm' style='text-decoration:none' href='" + esc(pg[0]) + "?customize'>Customize</a></div>";
      }).join("") + "</div>";
    h += "<div class='mi-card mi-set'><h3>Display</h3>" +
      box("prefs.homeCard", p.homeCard, "Show my accounts and alerts at the top of Home") +
      box("prefs.privacy", p.privacy, "Blur dollar amounts (handy when showing someone your screen)") + "</div>";
    return h;
  }

  function wireSettings() {
    view.querySelectorAll("[data-set]").forEach(function (el) {
      el.onchange = function () {
        var k = el.dataset.set.split("."), v = el.type === "checkbox" ? el.checked : +el.value;
        if (k[1] === "notify" && v) { el.checked = false; return askNotify(); }
        S[k[0]][k[1]] = v; save(); QT.navStyle();
        if (k[1] === "focus") { syncFocusBtn(); draw(); flash(v ? "Focus on: the noisy sections are hidden across the site." : "Focus off: everything is back."); }
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

  function syncFocusBtn() {  // the header button was drawn before this change
    var b = document.getElementById("qt-focus"), on = QT.focusOn();
    if (!b) return;
    b.classList.toggle("on", on); b.setAttribute("aria-pressed", on ? "true" : "false");
    b.setAttribute("aria-label", "Focus mode " + (on ? "on" : "off"));
    var sp = b.querySelector("span"); if (sp) sp.textContent = on ? " on" : " off";
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
      ", " + h + " holding" + (h === 1 ? "" : "s") + ", " + S.watch.length + " watched, " + S.journal.length + " journal entr" + (S.journal.length === 1 ? "y" : "ies") +
      "</b>, saved in this browser on this device only. " +
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
    html += "<div class='mi-card'><h3>Start over</h3><p class='mi-note'>Erase every account, watchlist item, journal entry and setting from this browser.</p>" +
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
        var n = pending.accounts.length, w = pending.watch.length, jn = pending.journal.length;
        document.getElementById("mi-restore").innerHTML = "<p class='mi-note'>Found <b>" + n + " account" + (n === 1 ? "" : "s") +
          ", " + w + " watched stock" + (w === 1 ? "" : "s") + " and " + jn + " journal entr" + (jn === 1 ? "y" : "ies") +
          "</b>. What should happen to what's on this device now?</p>" +
          "<p><button class='mi-btn pri' data-act='replace'>Replace it with the backup</button> " +
          "<button class='mi-btn' data-act='merge'>Add the backup to it</button></p>";
        view.querySelector("[data-act=replace]").onclick = function () { S = QT.fill(pending); finish("Restored."); };
        view.querySelector("[data-act=merge]").onclick = function () {
          var ids = {}, have = {};
          S.accounts.forEach(function (a) { ids[a.id] = 1; });
          pending.accounts.forEach(function (a) { if (ids[a.id]) a.id = uid(); S.accounts.push(a); });
          S.watch.forEach(function (x) { have[x.t] = 1; });
          pending.watch.forEach(function (x) { if (!have[x.t]) S.watch.push(x); });
          var jids = {};  // the same entry restored twice is skipped, not doubled
          S.journal.forEach(function (j) { jids[j.id] = 1; });
          pending.journal.forEach(function (j) { if (!jids[j.id]) S.journal.push(j); });
          finish("Added from the backup.");
        };
      }).catch(function (e) { flash("Couldn't open that file: " + e.message + ".", true); });
    };
    view.querySelector("[data-act=erase]").onclick = function () {
      if (!confirm("Erase all your accounts, watchlist, journal and settings from this browser? This can't be undone without a backup.")) return;
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
    var j = document.querySelector(".mi-tabs [data-tab=journal]"), due = QT.dueReviews(S).length;
    if (j) j.innerHTML = "Journal" + (due ? " <span class='qt-badge' title='Reviews due'>" + due + "</span>" : "");
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
    else if (tab === "journal") { view.innerHTML = renderJournal(); wireJournal(); }
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
