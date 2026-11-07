/* Kitchen and bar screen (PQ-048, stage 3): a shared tablet without Telegram.
   The screen's secret comes after "#" in its link (not sent to the server,
   not in its logs), is kept in this browser and sent in a header. The board
   refreshes itself every 8 s while it is on screen; a short answer means
   nothing changed. Data is put into the page as text nodes only. */
(function () {
  "use strict";

  var root = document.getElementById("app");
  var API = root.dataset.api;
  var REFRESH_MS = 8000;
  var LATE_MINUTES = 15;
  var STORE = "botera-kitchen-screen";

  var T = {};
  var data = null;
  var version = "";
  var seen = null;
  var fresh = {};
  var busy = false;
  var offline = false;
  var sound = false;
  var audio = null;
  var clockOffset = 0;
  var token = "";
  var wakeLock = null;

  // --- the screen's secret ----------------------------------------------------

  function stored() {
    try { return JSON.parse(window.localStorage.getItem(STORE) || "{}"); }
    catch (ignored) { return {}; }
  }
  function keep(values) {
    try {
      var all = stored();
      Object.keys(values).forEach(function (key) { all[key] = values[key]; });
      window.localStorage.setItem(STORE, JSON.stringify(all));
    } catch (ignored) { /* storage may be unavailable */ }
  }
  var fromLink = (window.location.hash || "").replace(/^#/, "");
  if (fromLink) {
    token = fromLink;
    keep({ token: token });
    try { window.history.replaceState(null, "", window.location.pathname); }
    catch (ignored) { /* old browser */ }
  } else {
    token = stored().token || "";
  }
  sound = !!stored().sound;

  // --- helpers ---------------------------------------------------------------

  function el(tag, attrs) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (key) {
      var value = attrs[key];
      if (value === undefined || value === null || value === false) return;
      if (key === "text") node.textContent = value;
      else if (key === "class") node.className = value;
      else if (key.slice(0, 2) === "on") node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : value);
    });
    for (var i = 2; i < arguments.length; i++) {
      var child = arguments[i];
      if (child === null || child === undefined || child === false) continue;
      node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    }
    return node;
  }

  function t(key, params) {
    var text = T[key] || key;
    Object.keys(params || {}).forEach(function (name) {
      text = text.split("{" + name + "}").join(String(params[name]));
    });
    return text;
  }

  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

  function toast(message) {
    var box = document.getElementById("toast");
    box.textContent = message;
    box.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(function () { box.hidden = true; }, 3500);
  }

  function minutesSince(iso) {
    var moment = Date.parse(iso);
    if (isNaN(moment)) return 0;
    return Math.max(0, Math.floor((Date.now() - clockOffset - moment) / 60000));
  }

  function clockText() {
    var now = new Date(Date.now() - clockOffset);
    return String(now.getHours()).padStart(2, "0") + ":" + String(now.getMinutes()).padStart(2, "0");
  }

  function beep() {
    if (!sound || !audio) return;
    try {
      [0, 0.25].forEach(function (delay) {
        var oscillator = audio.createOscillator();
        var gain = audio.createGain();
        oscillator.frequency.value = 880;
        gain.gain.setValueAtTime(0.3, audio.currentTime + delay);
        gain.gain.exponentialRampToValueAtTime(0.001, audio.currentTime + delay + 0.4);
        oscillator.connect(gain);
        gain.connect(audio.destination);
        oscillator.start(audio.currentTime + delay);
        oscillator.stop(audio.currentTime + delay + 0.4);
      });
    } catch (ignored) { /* no audio */ }
  }

  function api(path, body, params) {
    var query = new URLSearchParams(params || {});
    var url = API + path + (query.toString() ? "?" + query.toString() : "");
    var options = { headers: { "X-Screen-Token": token } };
    var controller = window.AbortController ? new AbortController() : null;
    var stop = controller ? setTimeout(function () { controller.abort(); }, 20000) : null;
    if (controller) options.signal = controller.signal;
    if (body !== undefined) {
      options.method = "POST";
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    return fetch(url, options).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (json) {
        clearTimeout(stop);
        return { ok: response.ok, status: response.status, data: json };
      });
    }, function () {
      clearTimeout(stop);
      return { ok: false, status: 0, data: {} };
    });
  }

  // --- keeping the tablet awake ------------------------------------------------

  function awake() {
    if (!("wakeLock" in navigator) || document.visibilityState !== "visible") return;
    navigator.wakeLock.request("screen").then(function (lock) { wakeLock = lock; },
      function () { /* not allowed here */ });
  }

  // --- loading -----------------------------------------------------------------

  function message(text, retry) {
    clear(root);
    root.appendChild(el("div", { class: "center" }, el("div", null,
      el("p", { text: text }),
      retry ? el("button", { class: "tool", type: "button", text: T.retry || "↻", onclick: function () { load(false); } }) : null)));
  }

  function load(quiet) {
    if (!token) {
      message("Откройте ссылку экрана из Admin на этом планшете. / Open the screen's link from Admin on this tablet.");
      return Promise.resolve();
    }
    busy = true;
    var params = quiet && version ? { since: version } : {};
    return api("state", undefined, params).then(function (result) {
      busy = false;
      if (result.status === 0 || result.status >= 500) {
        if (!data) { message(T.offline || "…", true); return; }
        offline = true;
        render();
        return;
      }
      offline = false;
      if (!result.ok) {
        if (result.data.code === "unknown_screen") {
          token = "";
          keep({ token: "" });
        }
        data = null;
        version = "";
        message(result.data.error || "⚠️", result.data.code !== "unknown_screen");
        return;
      }
      if (result.data.unchanged) {
        render();
        return;
      }
      var next = result.data;
      T = next.texts || T;
      version = next.version || "";
      var serverNow = Date.parse(next.now);
      if (!isNaN(serverNow)) clockOffset = Date.now() - serverNow;
      var ids = {};
      (next.orders || []).forEach(function (order) { ids[order.id] = true; });
      var arrived = false;
      if (seen) {
        Object.keys(ids).forEach(function (id) {
          if (!seen[id]) { fresh[id] = true; arrived = true; }
        });
      }
      seen = ids;
      data = next;
      if (arrived) beep();
      render();
    });
  }

  // --- the board ---------------------------------------------------------------

  function card(order) {
    var waited = minutesSince(order.since);
    var classes = ["card", order.status];
    if (waited >= LATE_MINUTES) classes.push("late");
    if (fresh[order.id]) { classes.push("fresh"); delete fresh[order.id]; }
    var items = el("ul", { class: "items" });
    order.items.forEach(function (item) {
      items.appendChild(el("li", { class: item.done ? "ok" : null },
        el("span", { class: "q", text: item.done ? "✓" : item.quantity + " ×" }),
        item.done ? item.name + " ×" + item.quantity : item.name,
        item.comment ? el("span", { class: "c", text: item.comment }) : null));
    });
    var elsewhere = null;
    if (order.elsewhere.length) {
      elsewhere = el("div", { class: "elsewhere" }, t("elsewhere") + ": ");
      order.elsewhere.forEach(function (item, index) {
        if (index) elsewhere.appendChild(document.createTextNode(", "));
        var station = item.station ? " (" + t(item.station) + (item.done ? ", " + t("part_done") : "") + ")" : "";
        elsewhere.appendChild(el("span", { class: item.done ? "ok" : null, text: item.name + " ×" + item.quantity + station }));
      });
    }
    var acts = el("div", { class: "acts" });
    if (order.status === "accepted") {
      acts.appendChild(el("button", { class: "act go", type: "button", text: t("start"), onclick: function (event) { act(order, "start", event.currentTarget); } }));
    }
    acts.appendChild(el("button", { class: "act ok", type: "button", text: t("ready"), onclick: function (event) { act(order, "ready", event.currentTarget); } }));
    return el("article", { class: classes.join(" ") },
      el("div", { class: "top" },
        el("span", { class: "num", text: "#" + order.id }),
        el("span", { class: "wait", text: t("minutes", { minutes: waited }) })),
      el("div", null, el("span", { class: "place", text: order.place }),
        order.when ? el("span", { class: "when", text: " · " + order.when }) : null),
      items,
      order.comment ? el("div", { class: "comment", text: "💬 " + order.comment }) : null,
      elsewhere,
      acts);
  }

  function column(title, nodes, emptyText) {
    var list = el("div", { class: "cards" });
    nodes.forEach(function (node) { list.appendChild(node); });
    if (!nodes.length && emptyText) list.appendChild(el("div", { class: "empty", text: emptyText }));
    return el("section", { class: "column" },
      el("h2", null, title, el("span", { class: "n", text: String(nodes.length) })), list);
  }

  function render() {
    if (!data) return;
    clear(root);
    var screen = data.screen;
    root.appendChild(el("header", { class: "head" },
      el("h1", null, screen.venue + " · " + screen.name + " ",
        el("span", { class: "shows", text: "(" + t("shows_" + screen.shows) + ")" })),
      el("span", { class: "clock", text: clockText() }),
      el("button", { class: "tool", type: "button", text: sound ? t("sound_on") : t("sound_off"), onclick: toggleSound }),
      document.documentElement.requestFullscreen
        ? el("button", { class: "tool", type: "button", text: t("fullscreen"), onclick: fullscreen })
        : null));
    if (offline) root.appendChild(el("div", { class: "offline", text: t("offline") }));
    var waiting = data.orders.filter(function (order) { return order.status === "accepted"; });
    var making = data.orders.filter(function (order) { return order.status === "preparing"; });
    var limit = Date.now() - clockOffset - 15 * 60000;
    var done = data.done.filter(function (mark) { return Date.parse(mark.at) >= limit; }).map(function (mark) {
      return el("div", { class: "done" }, el("b", { text: "#" + mark.id }), el("span", { text: mark.place }));
    });
    root.appendChild(el("div", { class: "board" },
      column(t("new"), waiting.map(card), data.orders.length ? "" : t("nothing")),
      column(t("preparing"), making.map(card), ""),
      column(t("done"), done, "")));
  }

  function act(order, action, button) {
    if (button) button.disabled = true;
    api("action", { order: order.id, action: action }).then(function (result) {
      if (!result.ok) toast(result.data.error || t("error"));
      else if (result.data.stale) toast(t("gone"));
      return load(false);
    });
  }

  function toggleSound() {
    sound = !sound;
    keep({ sound: sound });
    if (sound && !audio) {
      try { audio = new (window.AudioContext || window.webkitAudioContext)(); } catch (ignored) { audio = null; }
    }
    if (audio && audio.state === "suspended") audio.resume();
    beep();
    render();
  }

  function fullscreen() {
    try { document.documentElement.requestFullscreen(); } catch (ignored) { /* not allowed */ }
    awake();
  }

  // A browser lets sound start only after a tap on the page.
  document.addEventListener("pointerdown", function () {
    if (sound && !audio) {
      try { audio = new (window.AudioContext || window.webkitAudioContext)(); } catch (ignored) { audio = null; }
    }
    if (audio && audio.state === "suspended") audio.resume();
  });

  setInterval(function () {
    if (document.visibilityState === "visible" && !busy) load(true);
  }, REFRESH_MS);
  setInterval(function () { if (!busy) render(); }, 30000); // minutes and the clock
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") { load(true); awake(); }
  });
  awake();
  load(false);
})();
