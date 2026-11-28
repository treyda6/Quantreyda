// Quantreyda privacy lock: hide your dollar amounts with one tap, and keep Mine
// behind a passcode (or Face ID / fingerprint) when you hand someone your phone.
//
// All of it stays on this device. The passcode is never stored, only a salted
// PBKDF2 hash of it, under its own key ("qt-lock") so it isn't copied into
// backup files and every device sets its own. "Unlocked" lives in
// sessionStorage, so closing the app locks it again, and it re-locks after the
// idle time you pick. This keeps out people looking at your screen; it doesn't
// encrypt the browser's storage (use an encrypted backup for files you share).
//
// Loaded in <head> right after personal.js so amounts are hidden before the
// page draws.
(function () {
  "use strict";
  var QT = window.QT;
  if (!QT) return;
  var LKEY = "qt-lock", SKEY = "qt-unlocked", ITER = 150000;
  var root = document.documentElement;

  // ---------------------------------------------------------------- stored settings
  function cfg() {
    try {
      var c = JSON.parse(localStorage.getItem(LKEY) || "null");
      return c && typeof c === "object" ? c : {};
    } catch (e) { return {}; }
  }
  function put(c) { try { localStorage.setItem(LKEY, JSON.stringify(c)); return true; } catch (e) { return false; } }
  function isSet() { return !!cfg().hash; }
  function autoMin() { var a = cfg().auto; return a == null ? 5 : +a; }

  function lastActive() { try { return +sessionStorage.getItem(SKEY) || 0; } catch (e) { return 0; } }
  function touch() { try { sessionStorage.setItem(SKEY, String(Date.now())); } catch (e) {} }
  function forget() { try { sessionStorage.removeItem(SKEY); } catch (e) {} }

  function locked() {
    if (!isSet()) return false;
    var t = lastActive(), a = autoMin();
    return !t || (a > 0 && Date.now() - t > a * 6e4);
  }
  function hidden() { return !!QT.state.prefs.privacy || locked(); }

  // ---------------------------------------------------------------- hashing
  function b64(buf) { var s = "", b = new Uint8Array(buf); for (var i = 0; i < b.length; i++) s += String.fromCharCode(b[i]); return btoa(s); }
  function unb64(s) { var b = atob(s), u = new Uint8Array(b.length); for (var i = 0; i < b.length; i++) u[i] = b.charCodeAt(i); return u; }
  function canHash() { return !!(window.crypto && crypto.subtle && window.TextEncoder); }
  function derive(code, salt, iter) {
    return crypto.subtle.importKey("raw", new TextEncoder().encode(String(code)), "PBKDF2", false, ["deriveBits"])
      .then(function (k) { return crypto.subtle.deriveBits({ name: "PBKDF2", salt: salt, iterations: iter, hash: "SHA-256" }, k, 256); })
      .then(b64);
  }
  function setPasscode(code) {
    if (!canHash()) return Promise.reject(new Error("this browser can't set a passcode here"));
    var salt = crypto.getRandomValues(new Uint8Array(16));
    return derive(code, salt, ITER).then(function (h) {
      var c = cfg();
      c.v = 1; c.salt = b64(salt); c.iter = ITER; c.hash = h; c.fails = 0; c.until = 0;
      if (c.auto == null) c.auto = 5;
      if (!put(c)) throw new Error("this browser is blocking storage");
      touch(); apply();
    });
  }
  function wait() { var u = cfg().until || 0; return u > Date.now() ? Math.ceil((u - Date.now()) / 1000) : 0; }
  function check(code) {  // resolves true/false; repeated misses make you wait
    var c = cfg();
    if (!c.hash) return Promise.resolve(true);
    if (wait()) return Promise.resolve(false);
    return derive(code, unb64(c.salt), c.iter || ITER).then(function (h) {
      var c2 = cfg(), ok = h === c2.hash;
      if (ok) { c2.fails = 0; c2.until = 0; }
      else {
        c2.fails = (c2.fails || 0) + 1;
        if (c2.fails >= 5) c2.until = Date.now() + 30000 * Math.pow(2, Math.min(c2.fails - 5, 5));
      }
      put(c2);
      return ok;
    });
  }
  function remove() { try { localStorage.removeItem(LKEY); } catch (e) {} forget(); apply(); }
  function setAuto(min) { var c = cfg(); c.auto = +min || 0; put(c); touch(); apply(); }

  // ---------------------------------------------------------------- Face ID / fingerprint (WebAuthn, checked on this device)
  function bioAvailable() {
    var P = window.PublicKeyCredential;
    if (!P || !P.isUserVerifyingPlatformAuthenticatorAvailable || !window.isSecureContext) return Promise.resolve(false);
    return P.isUserVerifyingPlatformAuthenticatorAvailable().catch(function () { return false; });
  }
  function enrollBio() {
    return navigator.credentials.create({ publicKey: {
      challenge: crypto.getRandomValues(new Uint8Array(32)),
      rp: { name: "Quantreyda" },
      user: { id: crypto.getRandomValues(new Uint8Array(16)), name: "Quantreyda Mine", displayName: "Quantreyda Mine" },
      pubKeyCredParams: [{ type: "public-key", alg: -7 }, { type: "public-key", alg: -257 }],
      authenticatorSelection: { authenticatorAttachment: "platform", userVerification: "required", residentKey: "discouraged" },
      timeout: 60000, attestation: "none"
    } }).then(function (cred) {
      var c = cfg(); c.bio = b64(cred.rawId); put(c);
    });
  }
  function dropBio() { var c = cfg(); delete c.bio; put(c); }
  function bioUnlock() {
    var id = cfg().bio;
    if (!id || !navigator.credentials) return Promise.resolve(false);
    return navigator.credentials.get({ publicKey: {
      challenge: crypto.getRandomValues(new Uint8Array(32)),
      allowCredentials: [{ type: "public-key", id: unb64(id) }],
      userVerification: "required", timeout: 60000
    } }).then(function (a) {
      var ad = a && a.response && new Uint8Array(a.response.authenticatorData);
      return !!(ad && ad.length > 32 && ad[32] & 4);  // 4 = the device verified you (face, finger or device PIN)
    }).catch(function () { return false; });
  }

  // ---------------------------------------------------------------- applying it
  var CSS = [
    ".qt-private .qt-money{filter:blur(.34em);-webkit-user-select:none;user-select:none}",
    ".qt-private input.qt-money:focus{filter:none}",
    ".qt-eye{display:inline-flex;align-items:center;justify-content:center;margin-right:8px;padding:6px 9px}",
    ".qt-eye span{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}",
    ".qt-eye svg{width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}",
    ".qt-eye.on{border-color:var(--acc);color:var(--acc)}",
    ".qt-lockdlg{position:fixed;inset:0;z-index:100;display:flex;align-items:center;justify-content:center;padding:16px;",
    "background:color-mix(in srgb,var(--bg,#0a0c10) 70%,transparent);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px)}",
    ".qt-lockdlg form{width:min(360px,100%);background:var(--card,#141821);color:var(--fg,#e8eaf0);border:1px solid var(--line,#2a2f3a);",
    "border-radius:18px;padding:22px 20px;box-shadow:0 20px 60px rgba(0,0,0,.35);text-align:center;font-family:Inter,system-ui,sans-serif}",
    ".qt-lockdlg h3{margin:8px 0 4px;font-size:19px}.qt-lockdlg p{margin:4px 0;font-size:13.5px;color:var(--mut,#9aa3b2)}",
    ".qt-lockdlg .ic{width:34px;height:34px;fill:none;stroke:var(--acc,#4f8cff);stroke-width:2;stroke-linecap:round;stroke-linejoin:round}",
    ".qt-lockdlg input{width:100%;box-sizing:border-box;margin:12px 0 6px;font:600 22px/1.2 Inter,system-ui,sans-serif;letter-spacing:.3em;",
    "text-align:center;padding:10px;border-radius:12px;border:1px solid var(--line,#2a2f3a);background:var(--bg,#0a0c10);color:var(--fg,#e8eaf0)}",
    ".qt-lockdlg .err{color:var(--down,#ff5c63);min-height:18px}",
    ".qt-lockdlg .row{display:flex;flex-direction:column;gap:8px;margin-top:10px}",
    ".qt-lockdlg button{font:600 15px Inter,system-ui,sans-serif;padding:10px 14px;border-radius:12px;cursor:pointer;",
    "border:1px solid var(--line,#2a2f3a);background:var(--card2,#1b2030);color:var(--fg,#e8eaf0)}",
    ".qt-lockdlg button.pri{background:var(--acc,#4f8cff);border-color:var(--acc,#4f8cff);color:var(--acc-ink,#fff)}",
    ".qt-lockdlg button.ln{border:0;background:none;color:var(--mut,#9aa3b2);font-size:13px;padding:4px}"
  ].join("");
  var st = document.createElement("style");
  st.id = "qt-lock-style"; st.textContent = CSS;
  (document.head || root).appendChild(st);

  var EYE = "<path d='M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z'/><circle cx='12' cy='12' r='3'/>";
  var EYE_OFF = "<path d='M3 3l18 18'/><path d='M10.6 5.1A10.7 10.7 0 0 1 12 5c6.4 0 10 7 10 7a17 17 0 0 1-3.2 4.1M6.6 6.6A17.4 17.4 0 0 0 2 12s3.6 7 10 7a10 10 0 0 0 5.4-1.6'/><path d='M9.9 9.9a3 3 0 0 0 4.2 4.2'/>";
  var LOCK = "<rect x='5' y='11' width='14' height='10' rx='2'/><path d='M8 11V7a4 4 0 0 1 8 0v4'/>";

  var was = null;
  function apply() {
    var h = hidden(), l = locked(), key = h + "|" + l;
    root.classList.toggle("qt-private", h);
    root.classList.toggle("qt-locked", l);
    button();
    if (key !== was) {
      var first = was === null;
      was = key;
      if (!first) document.dispatchEvent(new CustomEvent("qt-privacy", { detail: { hidden: h, locked: l } }));
    }
  }

  function showButton() {
    var s = QT.state;
    return isSet() || s.prefs.privacy || s.accounts.length > 0 || !!document.getElementById("mine-app");
  }
  function button() {
    var theme = document.getElementById("theme");
    if (!theme || !document.body) return;
    var b = document.getElementById("qt-eye");
    if (!showButton()) { if (b) b.remove(); return; }
    if (!b) {
      b = document.createElement("button");
      b.type = "button"; b.id = "qt-eye"; b.className = "toggle qt-eye";
      b.onclick = toggle;
      theme.parentNode.insertBefore(b, theme);
    }
    var h = hidden(), l = locked(), set = isSet();
    b.classList.toggle("on", h);
    b.setAttribute("aria-pressed", h ? "true" : "false");
    b.innerHTML = "<svg viewBox='0 0 24 24' aria-hidden='true'>" + (l ? LOCK : h ? EYE_OFF : EYE) + "</svg><span>" +
      (l ? "Locked" : h ? "Hidden" : set ? "Lock" : "Hide $") + "</span>";
    var t = l ? "Your portfolio is locked. Tap and enter your passcode to show it."
      : h ? "Dollar amounts are hidden. Tap to show them."
      : set ? "Tap to hide your dollar amounts and lock Mine (handy before you hand someone your phone)."
      : "Tap to hide your dollar amounts (handy when showing someone the app).";
    b.title = t; b.setAttribute("aria-label", t);
  }

  function setPrivacy(on) { QT.state.prefs.privacy = !!on; QT.save(QT.state); apply(); }
  function lockNow() { forget(); apply(); }
  function unlock() { touch(); apply(); }

  function toggle() {
    if (!hidden()) {
      if (isSet()) lockNow(); else setPrivacy(true);
      return;
    }
    if (locked()) ask({ auto: true }).then(function (ok) { if (ok) setPrivacy(false); });
    else setPrivacy(false);
  }

  // ---------------------------------------------------------------- the passcode prompt
  var open = null;
  function ask(opts) {
    if (!locked()) return Promise.resolve(true);
    if (open) return open;
    opts = opts || {};
    var c = cfg();
    open = new Promise(function (resolve) {
      var d = document.createElement("div");
      d.className = "qt-lockdlg";
      d.setAttribute("role", "dialog"); d.setAttribute("aria-modal", "true"); d.setAttribute("aria-labelledby", "qt-lock-h");
      d.innerHTML = "<form autocomplete='off'><svg class='ic' viewBox='0 0 24 24' aria-hidden='true'>" + LOCK + "</svg>" +
        "<h3 id='qt-lock-h'>Enter your passcode</h3><p>Your accounts, holdings and journal are locked on this device.</p>" +
        "<input type='password' inputmode='numeric' autocomplete='off' aria-label='Passcode' required>" +
        "<p class='err' aria-live='polite'></p><div class='row'><button class='pri'>Unlock</button>" +
        (c.bio ? "<button type='button' data-bio>Use Face ID or fingerprint</button>" : "") +
        "<button type='button' data-x>Cancel</button><button type='button' class='ln' data-forgot>Forgot it?</button></div></form>";
      document.body.appendChild(d);
      var f = d.querySelector("form"), inp = f.querySelector("input"), err = f.querySelector(".err"), busy = false;
      function done(ok) {
        d.remove(); open = null;
        document.removeEventListener("keydown", esc);
        if (ok) unlock();
        resolve(ok);
      }
      function esc(e) { if (e.key === "Escape") done(false); }
      document.addEventListener("keydown", esc);
      function waiting() {
        var s = wait();
        if (s) err.textContent = "Too many tries. Wait " + s + " seconds.";
        return s;
      }
      function bio() {
        bioUnlock().then(function (ok) { if (ok) done(true); else { err.textContent = "Not recognized. Use your passcode."; inp.focus(); } });
      }
      f.onsubmit = function (e) {
        e.preventDefault();
        if (busy || waiting()) return;
        busy = true; err.textContent = "Checking…";
        check(inp.value).then(function (ok) {
          busy = false;
          if (ok) return done(true);
          inp.value = ""; inp.focus();
          if (!waiting()) err.textContent = "That's not it. Try again.";
        });
      };
      f.querySelector("[data-x]").onclick = function () { done(false); };
      var bb = f.querySelector("[data-bio]");
      if (bb) bb.onclick = bio;
      f.querySelector("[data-forgot]").onclick = function () {
        if (!confirm("There's no way to recover the passcode, because it's never stored.\n\nTo get back in, erase Mine on this device " +
            "(accounts, watchlist, journal, settings and the lock), then restore from a backup file under Mine → Backup.\n\nErase now?")) return;
        try { localStorage.removeItem(QT.KEY); localStorage.removeItem(LKEY); } catch (e) {}
        forget(); location.reload();
      };
      d.addEventListener("click", function (e) { if (e.target === d) done(false); });
      waiting();
      if (opts.auto && c.bio) bio(); else inp.focus();
    });
    return open;
  }

  // ---------------------------------------------------------------- staying unlocked while you use it
  var lastTouch = 0;
  function activity() {
    if (!isSet() || locked()) return;
    var now = Date.now();
    if (now - lastTouch > 10000) { lastTouch = now; touch(); }
  }
  ["pointerdown", "keydown", "scroll", "touchstart"].forEach(function (ev) {
    addEventListener(ev, activity, { passive: true, capture: true });
  });
  setInterval(function () { if (isSet()) apply(); }, 15000);
  document.addEventListener("visibilitychange", function () { if (!document.hidden) apply(); });
  addEventListener("pageshow", apply);  // coming back with the Back button
  addEventListener("storage", function (e) {  // another tab changed the lock or the hide setting
    if (e.key === LKEY || e.key === QT.KEY) { if (e.key === QT.KEY) QT.state = QT.load(); apply(); }
  });

  apply();
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", apply); else apply();

  QT.lock = {
    isSet: isSet, locked: locked, hidden: hidden, ask: ask, lockNow: lockNow, setPrivacy: setPrivacy, apply: apply,
    setPasscode: setPasscode, check: check, remove: remove, autoMin: autoMin, setAuto: setAuto, canHash: canHash,
    bioAvailable: bioAvailable, enrollBio: enrollBio, dropBio: dropBio, hasBio: function () { return !!cfg().bio; }
  };
})();
