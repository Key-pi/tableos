/* Staff screen of the Mini App (PQ-048, stage 2): open orders and guest calls.
   The list refreshes itself while the screen is open; every button goes to
   the API (webapp/staff_views.py), which checks the right and the order's
   state again. Data is put into the page as text nodes only. */
(function () {
  "use strict";

  var tg = window.Telegram && window.Telegram.WebApp;
  if (tg) {
    try { tg.ready(); tg.expand(); } catch (ignored) { /* older Telegram */ }
  }
  var root = document.getElementById("app");
  var API = root.dataset.api;
  var initData = tg ? tg.initData : "";
  var REFRESH_MS = 8000;
  var STORE = "botera-staff:" + API;

  var T = {};
  var data = null;
  var filter = "all";
  var seen = null;       // order ids already shown
  var fresh = {};        // ids that just appeared: highlighted once
  var busy = false;
  var timer = null;
  var sound = false;
  var audio = null;
  var point = "";

  try {
    var saved = JSON.parse(window.localStorage.getItem(STORE) || "{}");
    point = saved.point || "";
    sound = !!saved.sound;
  } catch (ignored) { /* storage may be unavailable */ }

  function remember() {
    try { window.localStorage.setItem(STORE, JSON.stringify({ point: point, sound: sound })); }
    catch (ignored) { /* storage may be unavailable */ }
  }

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

  function money(value) {
    var number = Number(value);
    return (number % 1 === 0 ? String(number) : number.toFixed(2)) + " " + (T.currency || "");
  }

  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

  function toast(message) {
    var box = document.getElementById("toast");
    box.textContent = message;
    box.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(function () { box.hidden = true; }, 3500);
  }

  function haptic(kind) {
    try {
      if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred(kind || "success");
    } catch (ignored) { /* older Telegram */ }
  }

  function beep() {
    if (!sound || !audio) return;
    try {
      var oscillator = audio.createOscillator();
      var gain = audio.createGain();
      oscillator.frequency.value = 880;
      gain.gain.setValueAtTime(0.25, audio.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, audio.currentTime + 0.6);
      oscillator.connect(gain);
      gain.connect(audio.destination);
      oscillator.start();
      oscillator.stop(audio.currentTime + 0.6);
    } catch (ignored) { /* no audio */ }
  }

  function api(path, body) {
    var url = API + path + (point ? "?point=" + encodeURIComponent(point) : "");
    var options = { headers: { "X-Telegram-Init-Data": initData } };
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
      return { ok: false, status: 0, data: { error: T.error } };
    });
  }

  function show(node) { clear(root); root.appendChild(node); }

  // --- loading and refreshing --------------------------------------------------

  function fatal(result) {
    var body = result.data || {};
    show(el("div", { class: "center" }, el("div", null,
      el("p", { text: body.error || T.error ||
        "Откройте рабочий экран из бота в Telegram. / Open it from the bot in Telegram." }),
      result.status === 0 || result.status >= 500
        ? el("button", { class: "chip", type: "button", text: T.retry || "↻", onclick: load })
        : null)));
  }

  function load(quiet) {
    return api("state").then(function (result) {
      if (!result.ok) {
        if (!quiet || !data) fatal(result);
        return;
      }
      var previous = data;
      data = result.data;
      T = data.texts || T;
      var ids = {};
      var arrived = false;
      data.orders.forEach(function (order) {
        ids[order.id] = true;
        if (seen && !seen[order.id]) { fresh[order.id] = true; arrived = true; }
      });
      if (seen && previous) {
        var oldCalls = {};
        previous.calls.forEach(function (call) { oldCalls[call.id] = true; });
        data.calls.forEach(function (call) { if (!oldCalls[call.id]) arrived = true; });
      }
      seen = ids;
      if (arrived) { haptic("warning"); beep(); }
      if (!busy) render();
    });
  }

  function schedule() {
    clearInterval(timer);
    timer = setInterval(function () {
      if (document.visibilityState === "visible" && !busy) load(true);
    }, REFRESH_MS);
  }

  // Browsers allow sound only after a tap: the first tap switches it on.
  document.addEventListener("click", function () {
    if (sound && !audio) {
      try { audio = new (window.AudioContext || window.webkitAudioContext)(); } catch (ignored) { /* no audio */ }
    }
  });

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") load(true);
  });

  // --- actions ---------------------------------------------------------------

  function sheet(text, onConfirm, withInput) {
    var input = withInput ? el("input", { type: "text", autocapitalize: "characters",
                                          placeholder: T.tracking_prompt,
                                          style: "width:100%;padding:10px 12px;border-radius:10px;border:1px solid var(--separator);background:var(--bg)" }) : null;
    var back = el("div", { class: "sheet-back", onclick: function (event) {
      if (event.target === back) back.remove();
    } });
    back.appendChild(el("div", { class: "sheet", role: "dialog" },
      el("p", { text: text }),
      input,
      el("div", { class: "acts" },
        el("button", { type: "button", class: "act", text: T.back, onclick: function () { back.remove(); } }),
        el("button", { type: "button", class: "act primary", text: T.confirm, onclick: function () {
          back.remove();
          onConfirm(input ? input.value : "");
        } }))));
    document.body.appendChild(back);
    if (input) input.focus();
  }

  function act(order, action) {
    if (action.confirm) { sheet(action.confirm, function () { send(order, action, ""); }); return; }
    if (action.input === "tracking") {
      sheet(action.label + " · " + T.tracking_prompt, function (value) { send(order, action, value); }, true);
      return;
    }
    send(order, action, "");
  }

  function send(order, action, tracking) {
    if (busy) return;
    busy = true;
    render();
    api("action", { order: order.id, action: action.code, tracking: tracking }).then(function (result) {
      busy = false;
      if (!result.ok) {
        haptic("error");
        toast(result.data.error || T.error);
      } else if (result.data.stale) {
        toast(T.stale);
      } else {
        haptic("success");
        toast(T.updated + ": #" + order.id + " · " + result.data.order.status_label);
      }
      load(true);
    });
  }

  function takeCall(call) {
    if (busy) return;
    busy = true;
    api("call", { call: call.id }).then(function (result) {
      busy = false;
      if (!result.ok) { haptic("error"); toast(result.data.error || T.error); }
      else haptic("success");
      load(true);
    });
  }

  // --- drawing ---------------------------------------------------------------

  function orderCard(order) {
    var card = el("article", { class: "order" + (order.status === "new" ? " new" : "") +
                                      (fresh[order.id] ? " fresh" : "") });
    card.appendChild(el("div", { class: "top" },
      el("span", null, el("span", { class: "num", text: "#" + order.id }), " ",
         el("span", { class: "place", text: order.place })),
      el("span", { class: "status", text: order.status_label })));
    card.appendChild(el("div", { class: "meta", text: [
      t("minutes", { minutes: order.minutes }), order.when, order.guest
    ].filter(Boolean).join(" · ") }));
    var list = el("ul");
    order.items.forEach(function (item) { list.appendChild(el("li", { text: item.name + " × " + item.quantity })); });
    card.appendChild(list);
    function line(label, value, link) {
      if (!value) return;
      card.appendChild(el("div", { class: "note" }, label + ": ",
        link ? el("a", { href: link, target: "_blank", rel: "noopener", text: value }) : value));
    }
    line(T.phone, order.phone);
    line(T.address, order.address);
    if (order.map) card.appendChild(el("div", { class: "note" },
      el("a", { href: order.map, target: "_blank", rel: "noopener", text: T.map })));
    if (order.carrier) line(order.carrier, order.recipient);
    line(T.shipping_cost, order.shipping_cost);
    line(T.tracking, order.tracking, order.tracking_link);
    line(T.comment, order.comment);
    line(T.assignee, order.assignee);
    if (order.received) card.appendChild(el("div", { class: "note", text: T.received }));
    card.appendChild(el("div", { class: "row" },
      el("b", { text: T.total + ": " + money(order.total) }),
      el("span", { class: "meta", text: order.payment })));
    if (order.actions.length) {
      var acts = el("div", { class: "acts" });
      order.actions.forEach(function (action) {
        acts.appendChild(el("button", {
          type: "button", class: "act" + (action.style ? " " + action.style : ""),
          text: action.label, disabled: busy, onclick: function () { act(order, action); }
        }));
      });
      card.appendChild(acts);
    }
    return card;
  }

  function filters() {
    var groups = [["all", T.all], ["new", T.new]];
    var names = { table: null, pickup: null, delivery: null, shipping: null };
    data.orders.forEach(function (order) {
      if (order.channel in names && !names[order.channel]) {
        names[order.channel] = order.channel === "table" ? T.table.replace(" {number}", "") : order.place;
      }
    });
    Object.keys(names).forEach(function (code) { if (names[code]) groups.push([code, names[code]]); });
    var box = el("div", { class: "filters", role: "group" });
    groups.forEach(function (group) {
      var count = data.orders.filter(function (order) { return matches(order, group[0]); }).length;
      var newCount = data.orders.filter(function (order) {
        return order.status === "new" && matches(order, group[0]);
      }).length;
      box.appendChild(el("button", {
        type: "button", class: "chip", "aria-pressed": String(filter === group[0]),
        onclick: function () { filter = group[0]; render(); }
      }, group[1] + " " + count, newCount && group[0] !== "new" ? el("span", { class: "count", text: String(newCount) }) : null));
    });
    box.appendChild(el("button", {
      type: "button", class: "chip", "aria-pressed": String(sound), text: T.sound_on,
      onclick: function () {
        sound = !sound;
        if (sound && !audio) {
          try { audio = new (window.AudioContext || window.webkitAudioContext)(); } catch (ignored) { sound = false; }
        }
        remember();
        if (sound) beep();
        render();
      }
    }));
    return box;
  }

  function matches(order, group) {
    if (group === "all") return true;
    if (group === "new") return order.status === "new";
    return order.channel === group;
  }

  function render() {
    if (!data) return;
    var page = el("div", { class: "page" });
    var head = el("div", { class: "staff-head" },
      el("div", null, el("h1", { text: data.venue.name }), el("div", { class: "who", text: T.title + " · " + data.employee })));
    if (data.venues.length) {
      var select = el("select", { "aria-label": T.change_point, onchange: function (event) {
        point = event.target.value; remember(); seen = null; data = null; load();
      } });
      data.venues.forEach(function (venue) {
        var option = el("option", { value: venue.id, text: venue.name });
        if (venue.id === data.venue.id) option.selected = true;
        select.appendChild(option);
      });
      head.appendChild(select);
    }
    page.appendChild(head);
    page.appendChild(filters());

    if (data.calls.length) {
      var calls = el("div", { class: "board" });
      data.calls.forEach(function (call) {
        calls.appendChild(el("div", { class: "call" },
          el("div", null,
            el("div", { class: "what", text: "🔔 " + t("table", { number: call.table }) }),
            el("div", { class: "meta", text: t("minutes", { minutes: call.minutes }) })),
          el("button", { type: "button", class: "act primary", text: T.go, disabled: busy,
                         style: "flex:none", onclick: function () { takeCall(call); } })));
      });
      page.appendChild(calls);
    }

    var shown = data.orders.filter(function (order) { return matches(order, filter); });
    // New ones first, then the oldest: what waits longest is on top.
    shown.sort(function (a, b) {
      if ((a.status === "new") !== (b.status === "new")) return a.status === "new" ? -1 : 1;
      return b.minutes - a.minutes;
    });
    var board = el("div", { class: "board" });
    shown.forEach(function (order) { board.appendChild(orderCard(order)); });
    if (!shown.length) board.appendChild(el("div", { class: "empty", text: T.no_orders }));
    page.appendChild(board);
    var scroll = window.scrollY;
    show(page);
    window.scrollTo(0, scroll);
    fresh = {};
  }

  // --- start -------------------------------------------------------------------

  if (!initData) { fatal({ status: 401, data: {} }); return; }
  if (tg && tg.BackButton) tg.BackButton.hide();
  if (tg && tg.MainButton) tg.MainButton.hide();
  load().then(schedule);
})();
