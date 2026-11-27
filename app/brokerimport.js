// Reads a broker's positions export (Fidelity, Schwab, Vanguard, E*TRADE or
// any CSV with a symbol and a quantity column) or text pasted from one.
// Runs only in your browser: the file is read here, turned into accounts and
// holdings for you to review, and never uploaded. Full account numbers are
// dropped as soon as they're read; only the last four digits are kept, so a
// later import of the same account can update it.
//
//   QTImport.parse(text, fileName) -> {broker, accounts: [...], notes: [...]}
(function (root) {
  "use strict";

  // ------------------------------------------------------------ CSV / TSV
  function rowsOf(text) {
    text = String(text || "").replace(/^﻿/, "").replace(/\r\n?/g, "\n");
    var lines = text.split("\n"), tabs = 0, commas = 0;
    lines.slice(0, 40).forEach(function (l) {
      tabs += (l.match(/\t/g) || []).length; commas += (l.match(/,/g) || []).length;
    });
    var d = tabs > commas / 2 && tabs > 0 ? "\t" : ",";
    var out = [], row = [], cell = "", q = false;
    for (var i = 0; i < text.length; i++) {
      var c = text[i];
      if (q) {
        if (c === '"') { if (text[i + 1] === '"') { cell += '"'; i++; } else q = false; }
        else cell += c;
      } else if (c === '"' && cell.trim() === "") { q = true; cell = ""; }
      else if (c === d) { row.push(cell.trim()); cell = ""; }
      else if (c === "\n") { row.push(cell.trim()); out.push(row); row = []; cell = ""; }
      else cell += c;
    }
    if (cell || row.length) { row.push(cell.trim()); out.push(row); }
    return out;
  }

  function key(h) { return String(h || "").toLowerCase().replace(/[^a-z0-9]/g, ""); }

  // Column names seen in real exports, normalized (lowercase, letters and digits only).
  var COLS = {
    sym: /^(symbol|ticker|sym|symbolcusip|tickersymbol|securitysymbol)$/,
    qty: /^(quantity|qty|qtyquantity|shares|sharesquantity|units|shareqty|quantityshares|shareholdings|shareshares)$/,
    price: /^(lastprice|price|shareprice|currentprice|marketprice|lastpricedollar|pricedollar|closingprice|lastpriceusd|priceusd)$/,
    value: /^(currentvalue|marketvalue|mktvalmarketvalue|mktval|totalvalue|value|valuedollar|marketvaluedollar|endingvalue|currentvalueusd|valueusd)$/,
    costAvg: /^(averagecostbasis|avgcostbasis|averagecost|avgcost|costpershare|costbasispershare|pricepaid|unitcost|avgprice|averageprice|avgcostpershare|purchaseprice)/,
    costTotal: /^(costbasistotal|costbasis|totalcost|totalcostbasis|costbasisdollar|cost|costbasisusd)$/,
    acctNum: /^(accountnumber|account|accountno|acctnumber|acct|accountid|acctno)$/,
    acctName: /^(accountname|accountnickname|accounttype|accountdescription|nickname|accounttitle)$/,
    desc: /^(description|investmentname|name|securityname|securitydescription|fundname|investment)$/,
    type: /^(type|securitytype|assettype|assetclass|producttype)$/
  };

  function headerOf(row) {
    var cols = {}, n = 0;
    row.forEach(function (h, i) {
      var k = key(h);
      Object.keys(COLS).forEach(function (c) { if (!(c in cols) && COLS[c].test(k)) { cols[c] = i; n++; } });
    });
    if (!("sym" in cols) || !(("qty" in cols) || ("value" in cols))) return null;
    if (row.some(function (h) { return /trade ?date|transaction|settlement ?date/i.test(h); })) return { skip: true };
    cols.raw = row;
    return cols;
  }

  function num(v) {
    var s = String(v == null ? "" : v).trim();
    if (!s || /^(--|n\/?a|-|—)$/i.test(s)) return null;
    var neg = /^\(.*\)$/.test(s) || /^-/.test(s.replace(/^[+$\s]+/, ""));
    s = s.replace(/[()$,%+\s]/g, "").replace(/^-/, "");
    var n = parseFloat(s);
    return isFinite(n) ? (neg ? -n : n) : null;
  }

  // ------------------------------------------------------------ what each row is
  var MMF = /^(SPAXX|FDRXX|FZFXX|FCASH|SPRXX|FZDXX|VMFXX|VMRXX|VUSXX|SWVXX|SNVXX|SNSXX|SNOXX|SWGXX|TTTXX|CORE)$/;
  function isCash(sym, desc) {
    return /\*\*$/.test(sym) || MMF.test(sym.replace(/\*+$/, "")) ||
      /^(cash|cash & cash investments|cash and cash investments|pending activity|money market|sweep|core position)/i.test(sym) ||
      (!sym && /^(cash|money market|held in money market|sweep|fdic)/i.test(desc));
  }
  function isTotal(sym, desc) { return /total/i.test(sym) || /^(account|grand) total/i.test(desc); }
  var TICKER = /^[A-Z]{1,5}([.\/-][A-Z]{1,3})?$/;

  function last4(s) {
    var d = String(s || "").replace(/[^A-Za-z0-9]/g, "");
    return d.length >= 3 ? d.slice(-4) : "";
  }

  // "Positions for account Roth Contributory IRA ...456 as of 10:32 AM ET, 2026/11/27"
  // "Individual ...123", "ROTH IRA (X12345678)"
  function sectionOf(text) {
    var t = String(text).trim();
    if (!t || t.length > 90 || /all[- ]accounts|disclaimer|date downloaded|brokerage services|^the data|^\d+\/\d+\/\d+/i.test(t)) return null;
    t = t.replace(/^positions for (account )?/i, "").replace(/\s+as of .*$/i, "").trim();
    var m = t.match(/(?:\.{2,}|x{2,}|\*{2,}|#|\(|\s)\s*([A-Z]?\d{3,}[\dA-Z-]*)\)?\s*$/i);
    var digits = m ? last4(m[1]) : "";
    var name = m ? t.slice(0, m.index).replace(/[\s(.#*-]+$/, "").trim() : t;
    if (!name && !digits) return null;
    return { name: name, last4: digits };
  }

  function detect(headers, fileName, firstLines) {
    var hk = headers.map(key).join(" "), f = String(fileName || "").toLowerCase();
    if (/averagecostbasis/.test(hk) && /accountname/.test(hk) || /portfolio_positions|fidelity/.test(f)) return "Fidelity";
    if (/mktval|reinvestdividends|qtyquantity/.test(hk) || /positions for /i.test(firstLines) || /schwab/.test(f)) return "Schwab";
    if (/investmentname/.test(hk) && /shareprice/.test(hk) || /vanguard|ofxdownload/.test(f)) return "Vanguard";
    if (/pricepaid/.test(hk) || /etrade|e\*trade|portfoliodownload/.test(f)) return "E*TRADE";
    if (/robinhood/.test(f)) return "Robinhood";
    return "CSV";
  }

  // ------------------------------------------------------------ account type
  function guessKind(name) {
    var n = String(name || "").toLowerCase();
    if (/roth/.test(n) && /custod|minor|utma|ugma|kid|child/.test(n)) return "custodial_roth";
    if (/roth/.test(n)) return "roth";
    if (/utma|ugma|custodial|custodian|minor/.test(n)) return "utma";
    if (/\bhsa\b|health ?savings/.test(n)) return "hsa";
    if (/529|education|college|unique|bright ?start/.test(n)) return "college529";
    if (/401|403|457|brokeragelink|thrift|\btsp\b|retirement plan|\bplan\b/.test(n)) return "k401";
    if (/\bira\b|rollover|traditional|\bsep\b|simple ira|contributory/.test(n)) return "trad";
    if (/individual|joint|brokerage|taxable|\btod\b|trust|cash management|investment account/.test(n)) return "brokerage";
    return "";
  }

  // ------------------------------------------------------------ main
  function parse(text, fileName) {
    var rows = rowsOf(text), hdr = null, sec = null, accts = {}, order = [], notes = [], headers = [];
    var firstLines = rows.slice(0, 3).map(function (r) { return r.join(" "); }).join(" ");

    function acct(name, l4) {
      var k = (l4 || "") + "|" + (l4 ? "" : (name || "").toLowerCase());
      if (!accts[k]) {
        accts[k] = { key: k, name: name || (l4 ? "Account …" + l4 : "Imported account"), last4: l4 || "",
                     kind: guessKind(name), holdings: [], cash: 0, value: 0, skipped: [] };
        order.push(k);
      } else if (name && /^Account …|^Imported account$/.test(accts[k].name)) {
        accts[k].name = name; accts[k].kind = accts[k].kind || guessKind(name);
      }
      return accts[k];
    }

    rows.forEach(function (r) {
      var filled = r.filter(function (c) { return c !== ""; });
      if (!filled.length) return;
      var h = headerOf(r);
      if (h) { hdr = h.skip ? null : h; if (!h.skip) headers = headers.concat(r); return; }
      if (filled.length === 1 || !hdr) {  // a title line: which account the next rows belong to
        var s = sectionOf(filled[0]);
        if (s && !(hdr && filled.length > 1)) sec = s;
        return;
      }
      var get = function (c) { return c in hdr ? (r[hdr[c]] || "") : ""; };
      var sym = get("sym").trim(), desc = get("desc").trim();
      if (!sym && !desc) return;
      var numRaw = get("acctNum"), name = get("acctName");
      var a = acct(name || (sec && sec.name) || "", last4(numRaw) || (sec && sec.last4) || "");
      if (isTotal(sym, desc)) return;
      var qty = num(get("qty")), price = num(get("price")), value = num(get("value"));
      if (value == null && qty != null && price != null) value = qty * price;
      var bare = sym.toUpperCase().replace(/\*+$/, "");
      if (isCash(sym, desc)) {
        var cash = value != null ? value : qty != null ? qty * (price || 1) : 0;
        a.cash += cash; a.value += cash;
        return;
      }
      var t = bare.replace(/[\/-]/g, ".");
      if (!TICKER.test(t)) {
        a.skipped.push((sym || desc).slice(0, 40) + (value != null ? " (" + Math.round(value).toLocaleString("en-US") + ")" : ""));
        if (value != null) a.value += value;
        return;
      }
      if (qty == null && value != null && price) qty = value / price;
      if (qty == null || !(qty > 0)) {
        a.skipped.push(t + " (no share count)");
        return;
      }
      var avg = num(get("costAvg")), tot = num(get("costTotal"));
      var cost = avg != null && avg > 0 ? avg : tot != null && tot > 0 ? tot / qty : 0;
      var x = a.holdings.filter(function (y) { return y.t === t; })[0];
      if (x) {  // several lots of the same fund: one holding, blended cost
        var all = x.sh + qty;
        x.cost = x.cost && cost ? (x.cost * x.sh + cost * qty) / all : x.cost || cost;
        x.sh = all;
      } else {
        a.holdings.push({ t: t, sh: qty, cost: cost, price: price || (value && qty ? value / qty : 0), desc: desc.slice(0, 60) });
      }
      if (value != null) a.value += value;
    });

    var list = order.map(function (k) { return accts[k]; })
      .filter(function (a) { return a.holdings.length || a.cash || a.skipped.length; });
    list.forEach(function (a) {
      a.holdings.forEach(function (h) {
        h.sh = Math.round(h.sh * 1e6) / 1e6; h.cost = Math.round(h.cost * 1e4) / 1e4;
        h.price = Math.round(h.price * 1e4) / 1e4;
      });
      a.cash = Math.round(a.cash * 100) / 100; a.value = Math.round(a.value * 100) / 100;
    });
    var broker = detect(headers, fileName, firstLines);

    if (!list.length) {  // no header row: plain "TICKER shares [cost]" lines
      var plain = { key: "|pasted", name: "Imported account", last4: "", kind: "", holdings: [], cash: 0, value: 0, skipped: [] };
      String(text || "").split(/\r?\n/).forEach(function (line) {
        var p = line.trim().split(/[\s,;\t]+/);
        var t = (p[0] || "").toUpperCase().replace(/[\/-]/g, "."), sh = num(p[1]);
        if (TICKER.test(t) && sh > 0) plain.holdings.push({ t: t, sh: sh, cost: num(p[2]) || 0, price: 0, desc: "" });
      });
      if (plain.holdings.length) { list = [plain]; broker = "List"; }
      else notes.push("Couldn't find a table with a symbol and a quantity (or shares) column.");
    }
    return { broker: broker, accounts: list, notes: notes };
  }

  var api = { parse: parse, guessKind: guessKind, rowsOf: rowsOf, sectionOf: sectionOf };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.QTImport = api;
})(typeof window !== "undefined" ? window : this);
