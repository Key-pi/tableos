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
  var freshTabs = {};    // tabs with something new: flash once
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

  function amount(value) {
    var number = Number(value);
    return number % 1 === 0 ? String(number) : number.toFixed(2);
  }

  function money(value) {
    return amount(value) + " " + (T.currency || "");
  }

  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

  // The buttons of a card (design pass, 2026-11-19): one main action — the
  // primary ones (cash and card together are one choice) or else the first
  // ordinary one — over the whole width; the rest in a row; a destructive
  // one is a quiet line at the end. ``kind``: "primary", "danger",
  // "secondary" (never the main one) or "".
  function actionsBox(list) {
    var main = list.filter(function (item) { return item.kind === "primary"; });
    if (!main.length) {
      var first = list.filter(function (item) { return !item.kind; })[0];
      if (first) main = [first];
    }
    var rest = list.filter(function (item) { return main.indexOf(item) < 0 && item.kind !== "danger"; });
    var quiet = list.filter(function (item) { return item.kind === "danger"; });
    var box = el("div", { class: "acts stack" });
    [[main, "act primary"], [rest, "act"], [quiet, "act quiet"]].forEach(function (group) {
      if (!group[0].length) return;
      var row = el("div", { class: group[0] === quiet ? "quiet-row" : null });
      group[0].forEach(function (item) {
        row.appendChild(el("button", { type: "button", class: group[1], text: item.label, disabled: item.disabled,
                                       onclick: item.onclick }));
      });
      box.appendChild(row);
    });
    return box;
  }

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

  function api(path, body, params) {
    var query = new URLSearchParams(params || {});
    if (point) query.set("point", point);
    var url = API + path + (query.toString() ? "?" + query.toString() : "");
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

  // Minutes since an order came, counted here: the server sends its clock once.
  var clockOffset = 0;

  function minutesSince(created, fallback) {
    var moment = Date.parse(created || "");
    if (isNaN(moment)) return fallback || 0;
    return Math.max(0, Math.floor((Date.now() - clockOffset - moment) / 60000));
  }

  function load(quiet) {
    // While nothing changed the server answers {"unchanged": true} only.
    var params = quiet && data && data.version ? { since: data.version } : undefined;
    return api("state", undefined, params).then(function (result) {
      if (!result.ok) {
        if (!quiet || !data) fatal(result);
        return;
      }
      if (result.data.unchanged) {
        if (quiet && (tab === "bills" || tab === "tables")) {
          return loadTab().then(function () { if (!busy) render(); });
        }
        if (!busy && tab === "orders") render();  // the minutes move on
        return;
      }
      var previous = data;
      data = result.data;
      var serverNow = Date.parse(data.now || "");
      if (!isNaN(serverNow)) clockOffset = Date.now() - serverNow;
      T = data.texts || T;
      var ids = {};
      var arrived = false;
      data.orders.forEach(function (order) {
        ids[order.id] = true;
        if (seen && !seen[order.id]) { fresh[order.id] = true; arrived = true; freshTabs.orders = true; }
      });
      if (seen && previous) {
        var oldCalls = {};
        previous.calls.forEach(function (call) { oldCalls[call.id] = true; });
        data.calls.forEach(function (call) { if (!oldCalls[call.id]) arrived = true; });
        var oldRequests = {};
        (previous.bill_requests || []).forEach(function (id) { oldRequests[id] = true; });
        data.bill_requests.forEach(function (id) {
          if (!oldRequests[id]) { arrived = true; freshTabs.bills = true; }
        });
        // A guest asking for the bill: the open list of bills shows it at once.
        if (freshTabs.bills && tab === "bills") tabData.bills = null;
        // A table where a guest calls or asks for the bill flashes «Столы»
        // (owner 2026-11-11: it was seen only after opening the tab).
        var oldAlerts = {};
        (previous.table_alerts || []).forEach(function (alert) { oldAlerts[alert.table + alert.kind] = true; });
        (data.table_alerts || []).forEach(function (alert) {
          if (!oldAlerts[alert.table + alert.kind]) freshTabs.tables = true;
        });
      }
      seen = ids;
      if (arrived) { haptic("warning"); beep(); }
      // Bills and tables refresh with the list; the sale and the report do not
      // (the report is built again each time the tab opens).
      if (quiet && (tab === "bills" || tab === "tables")) {
        return loadTab().then(function () { if (!busy) render(); });
      }
      // The sale and the report keep what is typed: only the tabs (red counts)
      // and the waiter calls are redrawn there (owner 2026-11-10).
      if (tab === "sale" || tab === "report" || tab === "table_order") { refreshChrome(); return; }
      if (!previous && settleVisit && data.rights.tables) { switchTab("tables"); return; }
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

  function sheet(text, onConfirm, withInput, placeholder) {
    var input = withInput ? el("input", { type: "text", autocapitalize: "characters",
                                          placeholder: placeholder || T.tracking_prompt,
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

  // Pick one option (a reason, a way of refund…), maybe with a comment.
  function chooser(title, options, withComment, onDone) {
    var picked = "";
    var back = el("div", { class: "sheet-back", style: "z-index:40", onclick: function (event) {
      if (event.target === back) back.remove();
    } });
    var chips = el("div", { class: "options" });
    options.forEach(function (option) {
      chips.appendChild(el("button", { type: "button", class: "chip", text: option.label, "aria-pressed": "false",
        onclick: function (event) {
          picked = option.code;
          chips.querySelectorAll(".chip").forEach(function (chip) { chip.setAttribute("aria-pressed", "false"); });
          event.currentTarget.setAttribute("aria-pressed", "true");
        } }));
    });
    var comment = withComment ? el("input", { type: "text", placeholder: T.comment,
      style: "width:100%;padding:10px 12px;border-radius:10px;border:1px solid var(--separator);background:var(--bg)" }) : null;
    back.appendChild(el("div", { class: "sheet", role: "dialog" },
      el("p", { text: title }), chips, comment,
      el("div", { class: "acts" },
        el("button", { type: "button", class: "act", text: T.back, onclick: function () { back.remove(); } }),
        el("button", { type: "button", class: "act primary", text: T.confirm, onclick: function () {
          if (!picked) { toast(T.pick_reason); return; }
          back.remove();
          onDone(picked, comment ? comment.value : "");
        } }))));
    document.body.appendChild(back);
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

  // --- refunds of a paid order (PQ-037) ------------------------------------------

  function openRefund(order) {
    api("refund", undefined, { order: order.id }).then(function (result) {
      if (!result.ok) { toast(result.data.error || T.error); return; }
      var info = result.data;
      var chosen = {};
      var back = el("div", { class: "sheet-back", onclick: function (event) { if (event.target === back) back.remove(); } });
      var amounts = el("div", { class: "note" });
      // «Вернуть на склад» (catalog step 3): off until ticked, shown only
      // when the chosen lines can put goods back.
      var restockBox = el("input", { type: "checkbox" });
      var restock = el("label", { class: "switch", hidden: true }, el("span", { text: T.restock }), restockBox);
      var list = el("div");
      info.lines.forEach(function (line) {
        var box = el("input", { type: "checkbox", disabled: line.refunded, onchange: function (event) {
          if (event.target.checked) chosen[line.id] = true; else delete chosen[line.id];
          preview();
        } });
        list.appendChild(el("label", { class: "switch" },
          el("span", { text: line.name + " × " + line.quantity + " — " + money(line.total) + (line.refunded ? " · " + T.refunded : "") }), box));
      });
      function ids() { return Object.keys(chosen); }
      function preview() {
        if (!ids().length) { amounts.textContent = ""; restock.hidden = true; return; }
        api("refund_preview", { order: info.order, lines: ids() }).then(function (answer) {
          if (!answer.ok) return;
          amounts.textContent = t("refund_amounts", { money: answer.data.money, bonus: answer.data.bonus });
          restock.hidden = !answer.data.can_restock;
          if (restock.hidden) restockBox.checked = false;
        });
      }
      back.appendChild(el("div", { class: "sheet", role: "dialog", style: "max-height:85vh;overflow:auto" },
        el("p", null, el("b", { text: T.refund + " · #" + info.order })), list, amounts, restock,
        el("div", { class: "acts" },
          el("button", { type: "button", class: "act", text: T.back, onclick: function () { back.remove(); } }),
          el("button", { type: "button", class: "act primary", text: T.next, onclick: function () {
            if (!ids().length) { toast(T.pick_lines); return; }
            chooser(T.refund_reason, info.reasons, true, function (reason, comment) {
              chooser(T.refund_method, info.methods, false, function (method) {
                api("refund_do", { order: info.order, lines: ids(), reason: reason, method: method, comment: comment,
                                   restock: !restock.hidden && restockBox.checked })
                  .then(function (answer) { back.remove(); after(answer, function () { return load(true); }); });
              });
            });
          } }))));
      document.body.appendChild(back);
    });
  }

  function findOrder() {
    sheet(T.find_order, function (value) {
      api("order", undefined, { id: value }).then(function (result) {
        if (!result.ok) { toast(result.data.error || T.error); return; }
        var back = el("div", { class: "sheet-back", onclick: function (event) { if (event.target === back) back.remove(); } });
        var box = el("div", { class: "sheet", style: "max-height:85vh;overflow:auto;background:var(--bg2)" }, orderCard(result.data.order));
        box.appendChild(el("div", { class: "acts" }, el("button", { type: "button", class: "act", text: T.back,
                                                                   onclick: function () { back.remove(); } })));
        back.appendChild(box);
        document.body.appendChild(back);
      });
    }, true, T.order_number);
  }

  // --- inviting a staff member (PQ-046) ----------------------------------------------

  function invite() {
    api("invite_roles").then(function (result) {
      if (!result.ok) { toast(result.data.error || T.error); return; }
      chooser(T.invite + ". " + T.invite_role, result.data.roles, false, function (role) {
        api("invite", { role: role }).then(function (answer) {
          if (!answer.ok) { toast(answer.data.error || T.error); return; }
          var link = answer.data.link;
          var back = el("div", { class: "sheet-back", onclick: function (event) { if (event.target === back) back.remove(); } });
          back.appendChild(el("div", { class: "sheet", role: "dialog" },
            el("p", { text: T.invite_ready }),
            el("div", { class: "details", text: link }),
            el("div", { class: "acts" },
              el("button", { type: "button", class: "act", text: T.copy, onclick: function () {
                try { navigator.clipboard.writeText(link).then(function () { toast(T.copied); }); } catch (ignored) { /* no clipboard */ }
              } }),
              el("button", { type: "button", class: "act primary", text: T.share, onclick: function () {
                if (tg && tg.openTelegramLink) tg.openTelegramLink(answer.data.share); else window.open(answer.data.share, "_blank");
              } }),
              el("button", { type: "button", class: "act", text: T.back, onclick: function () { back.remove(); } }))));
          document.body.appendChild(back);
        });
      });
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

  // An order (design pass, 2026-11-19): where it goes first — a table,
  // pickup, delivery — then the number and the minutes, what to make with
  // the count first, the amount large at the bottom, one main button.
  function orderCard(order) {
    var card = el("article", { class: "order" + (order.status === "new" ? " new" : "") +
                                      (fresh[order.id] ? " fresh" : "") });
    card.appendChild(el("div", { class: "order-head" },
      el("span", { class: "title", text: order.place }),
      el("span", { class: "status", text: order.status_label })));
    card.appendChild(el("div", { class: "sub", text: [
      "#" + order.id, t("minutes", { minutes: minutesSince(order.created, order.minutes) }), order.when, order.guest
    ].filter(Boolean).join(" · ") }));
    var list = el("div", { class: "items" });
    order.items.forEach(function (item) {
      list.appendChild(el("div", null, el("span", { class: "q", text: item.quantity + " ×" }), el("span", { text: item.name })));
    });
    card.appendChild(list);
    function line(label, value, link, kind) {
      if (!value) return;
      card.appendChild(el("div", { class: "note" + (kind ? " " + kind : "") }, label ? label + ": " : "",
        link ? el("a", { href: link, target: "_blank", rel: "noopener", text: value }) : value));
    }
    line(T.comment, order.comment, null, "comment");
    line(T.phone, order.phone, order.phone ? "tel:" + order.phone : null);
    line(T.address, order.address);
    if (order.map) line("", T.map, order.map);
    if (order.carrier) line(order.carrier, order.recipient);
    line(T.shipping_cost, order.shipping_cost);
    line(T.tracking, order.tracking, order.tracking_link);
    line(T.assignee, order.assignee);
    if (order.received) card.appendChild(el("div", { class: "note", text: T.received }));
    card.appendChild(el("div", { class: "order-total" },
      el("span", { class: "meta", text: order.payment }),
      el("b", { text: money(order.total) })));
    var buttons = order.actions.map(function (action) {
      return { label: action.label, kind: action.style || "", disabled: busy, onclick: function () { act(order, action); } };
    });
    if (order.can_refund) buttons.push({ label: T.refund, kind: "secondary", onclick: function () { openRefund(order); } });
    if (buttons.length) card.appendChild(actionsBox(buttons));
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
    var box = el("div", { class: "filters sub", role: "group" });
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
      type: "button", class: "chip push", "aria-pressed": String(sound), text: T.sound_on,
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

  // --- tabs: orders, bills, tables ---------------------------------------------

  var tab = "orders";
  var tabData = { bills: null, tables: null, sale: null, report: null, table_order: null };
  // The sale's lines in order; a line is an item with its variant and add-ons
  // (catalog step 2), ``key`` tells two choices apart.
  function newSale(category) {
    return { code: "", lines: [], redeem: false, comment: "", preview: null, category: category || "", picking: null };
  }
  var sale = newSale("");
  // An order to a table from the tablet (PQ-052, step 2): the same screen as
  // the sale, with its table and company.
  var tableOrder = null;
  function C() { return tab === "table_order" && tableOrder ? tableOrder : sale; }
  var openBillId = "";
  // «🧾 Рассчитать в Mini App» from the bot's table card opens this visit's
  // settlement (PQ-009 in the bot, step 2): ``?settle=<visit>``, once.
  var settleVisit = "";
  try { settleVisit = new URLSearchParams(window.location.search).get("settle") || ""; } catch (ignored) { /* old webview */ }

  function openSettleVisit() {
    var wanted = settleVisit;
    settleVisit = "";
    ((tabData.tables || {}).tables || []).forEach(function (table) {
      table.visits.forEach(function (visit) {
        if (visit.id === wanted && visit.can_settle) openSettlement(table, visit);
      });
    });
  }

  function loadTab() {
    var name = tab;  // the answer belongs to this tab, even if another is open by then
    if (name === "orders") return Promise.resolve();
    if ((name === "sale" || name === "table_order") && tabData[name]) return Promise.resolve();  // the menu: once
    if (name === "tables" && tablesMode === "bookings" && data.rights.reservations) return loadBookings();
    var path = { sale: "sale_menu", table_order: "table_order_menu" }[name] || name;
    return api(path).then(function (result) {
      if (!result.ok) { toast(result.data.error || T.error); if (tab === name) tab = "orders"; return; }
      tabData[name] = result.data;
      if (name === "tables") refreshOpenTable();
      if (name === "tables" && settleVisit) openSettleVisit();
    });
  }

  function switchTab(name) {
    tab = name;
    if (name === "report") tabData.report = null;
    // Bills and tables change all the time: asked again on every opening, the
    // last view is shown meanwhile (owner 2026-11-11: it was up to 8 s old).
    var live = name === "bills" || name === "tables";
    if (name !== "orders" && (live || !tabData[name])) {
      loadTab().then(function () { if (tab === name && !busy) render(); });
    }
    render();
  }

  // The tabs read as tabs (design pass, 2026-11-19): a line under the open
  // one, how many in grey, what waits for someone in red.
  function tabsBar() {
    var newOrders = data.orders.filter(function (order) { return order.status === "new"; }).length;
    // [tab, title, how many, red count]: new orders, guests asking for the bill.
    var names = [["orders", T.orders_tab, data.orders.length, newOrders]];
    if (data.rights.billing) names.push(["bills", T.bills, 0, data.bill_requests.length]);
    if (data.rights.tables || data.rights.table_orders || data.rights.reservations) {
      var needing = {};
      (data.table_alerts || []).forEach(function (alert) { needing[alert.table] = true; });
      names.push(["tables", T.tables, 0, Object.keys(needing).length]);
    }
    if (data.rights.sale) names.push(["sale", T.sale]);
    if (data.rights.report) names.push(["report", T.report]);
    if (names.length < 2) return null;
    var bar = el("div", { class: "tabs-main", role: "tablist", "data-part": "tabs" });
    names.forEach(function (item) {
      bar.appendChild(el("button", {
        type: "button", class: "tab-main" + (item[3] && freshTabs[item[0]] ? " fresh" : ""), role: "tab",
        "aria-pressed": String(tab === item[0] || (item[0] === "tables" && tab === "table_order")),
        onclick: function () { if (item[0] !== "table_order") tableOrder = null; switchTab(item[0]); }
      }, item[1], item[2] ? el("span", { class: "n", text: String(item[2]) }) : null,
         item[3] ? el("span", { class: "count", text: String(item[3]) }) : null));
    });
    freshTabs = {};
    return bar;
  }

  // On a phone the tabs scroll sideways: the open one stays in sight.
  function keepTabInView() {
    var bar = root.querySelector(".tabs-main");
    var open = bar && bar.querySelector('[aria-pressed="true"]');
    if (open && open.offsetLeft + open.offsetWidth > bar.clientWidth) bar.scrollLeft = open.offsetLeft - 8;
  }

  function moneyLine(label, value, strong) {
    return el("div", { class: "row" }, el(strong ? "b" : "span", { text: label }),
              el(strong ? "b" : "span", { text: money(value) }));
  }

  // Bills (design pass, 2026-11-19): who asks for the bill first, then the
  // open bills by guest with what is left to pay; a tap opens the bill.
  function renderBills(page) {
    var info = tabData.bills;
    if (!info) { page.appendChild(el("div", { class: "center" }, el("div", { class: "spinner" }))); return; }
    var board = el("div", { class: "board" });
    // Orders at tables nobody asked a bill for yet: one line over the whole
    // row (owner 2026-11-11: the small line hung beside the cards).
    if (info.unbilled) {
      board.appendChild(el("div", { class: "banner" },
        el("div", { class: "grow" }, el("b", { text: "🍽 " + t("unbilled", { count: info.unbilled }) }),
           el("div", { class: "sub", text: T.unbilled_hint })),
        data.rights.tables ? el("button", { type: "button", class: "act small", text: T.to_tables,
                                            onclick: function () { switchTab("tables"); } }) : null));
    }
    if (info.requests.length) board.appendChild(el("div", { class: "board-title", text: T.bill_requests }));
    info.requests.forEach(function (item) {
      var card = el("article", { class: "order new" },
        el("div", { class: "order-head" }, el("span", { class: "title", text: "🧾 " + t("table", { number: item.table }) }),
           el("span", { class: "status", text: item.status_label })),
        el("div", { class: "sub", text: item.type + " · " + item.guest }));
      if (item.note) card.appendChild(el("div", { class: "note comment", text: item.note }));
      var buttons = [];
      if (item.bill) buttons.push({ label: T.open_bill, kind: "primary", onclick: function () { openBill(item.bill); } });
      if (item.can_process) buttons.push({ label: T.process, onclick: function () { requestAction(item, "process"); } });
      buttons.push({ label: T.cancel_request, kind: "danger", onclick: function () { requestAction(item, "cancel"); } });
      card.appendChild(actionsBox(buttons));
      board.appendChild(card);
    });
    if (info.bills.length) board.appendChild(el("div", { class: "board-title", text: T.open_bills }));
    info.bills.forEach(function (bill) {
      board.appendChild(el("article", { class: "order link", role: "button", tabindex: "0", onclick: function () { openBill(bill.id); } },
        el("div", { class: "order-head" }, el("span", { class: "title", text: bill.place }),
           el("span", { class: "status", text: bill.status_label })),
        el("div", { class: "sub", text: bill.guest + " · #" + bill.id }),
        el("div", { class: "order-total" },
          el("span", { class: "meta", text: Number(bill.paid) ? T.paid + " " + money(bill.paid) + " / " + money(bill.total) : T.remaining }),
          el("b", { text: money(bill.remaining) }))));
    });
    if (!info.requests.length && !info.bills.length) board.appendChild(el("div", { class: "empty", text: T.no_bills }));
    page.appendChild(board);
  }

  // One visit of a table: guests, what is not billed, bills, the actions.
  function visitCard(table, visit) {
        var card = el("article", { class: "order" },
          el("div", { class: "top" }, el("span", { class: "num", text: t("table", { number: table.number }) }),
             el("span", { class: "status", text: t("visit", { since: visit.since }) })));
        // How many sit at the table and who they are (PQ-009, step 2): a tap
        // on a guest — an order for them.
        var count = el("div", { class: "row" }, el("span", { text: T.headcount }));
        if (visit.can_count) {
          count.appendChild(el("span", { class: "stepper" },
            el("button", { type: "button", text: "−", disabled: visit.headcount <= 1,
                           onclick: function () { setHeadcount(visit, visit.headcount - 1); } }),
            el("span", { text: String(visit.headcount) }),
            el("button", { type: "button", text: "+", onclick: function () { setHeadcount(visit, visit.headcount + 1); } })));
        } else count.appendChild(el("b", { text: String(visit.headcount) }));
        card.appendChild(count);
        visit.guests.forEach(function (guest) {
          var owes = el("span", { class: "owes" });
          if (Number(guest.unbilled) > 0) owes.appendChild(el("span", { class: "tag", text: t("guest_unbilled", { amount: money(guest.unbilled) }) }));
          if (Number(guest.to_pay) > 0) owes.appendChild(el("span", { class: "tag due", text: t("guest_to_pay", { amount: money(guest.to_pay) }) }));
          var line = el("div", { class: "row guest" + (guest.here ? "" : " gone") },
            el("span", { text: (guest.bot ? "👤 " : "") + guest.label }), owes);
          if (guest.here && data.rights.table_orders) {
            line.style.cursor = "pointer";
            line.addEventListener("click", function () { startTableOrder(table, visit, guest); });
          }
          card.appendChild(line);
        });
        if (visit.unbilled) card.appendChild(el("div", { class: "row" },
          el("b", { text: t("not_billed", { count: visit.unbilled, total: amount(visit.unbilled_total) }) })));
        visit.bills.forEach(function (bill) {
          card.appendChild(el("button", { type: "button", class: "act bill-line", onclick: function () { openBill(bill.id); } },
            el("span", { class: "who", text: "🧾 " + bill.who }),
            el("span", { class: "meta", text: bill.status_label + " · #" + bill.id }),
            el("b", { text: money(bill.remaining) })));
        });
        // One main action (design pass, 2026-11-19): «Рассчитать» for who
        // takes money, «＋ Заказ» for a waiter; the quick bills stay beside.
        var buttons = [];
        if (visit.can_settle) buttons.push({ label: T.settle, kind: "primary", onclick: function () { openSettlement(table, visit); } });
        if (data.rights.table_orders) buttons.push({ label: T.add_order, onclick: function () { startTableOrder(table, visit); } });
        if (visit.can_bill) {
          // "A bill to each" only when two or more guests ordered: otherwise
          // it is the same single bill.
          buttons.push({ label: visit.can_split ? T.shared_bill : T.issue_bill, kind: "secondary",
                         onclick: function () { visitAction(visit, "shared"); } });
          if (visit.can_split) buttons.push({ label: T.personal_bills, kind: "secondary",
                                              onclick: function () { visitAction(visit, "personal"); } });
        }
        if (visit.can_close !== false) {
          buttons.push({ label: T.close_visit, kind: "danger",
                         onclick: function () { sheet(T.close_visit_confirm, function () { visitAction(visit, "close"); }); } });
        }
        var acts = buttons.length ? actionsBox(buttons) : null;
        if (acts) card.appendChild(acts);
        return card;
  }

  // «Рассчитать» (PQ-009, step 3) — a screen of its own (design pass,
  // 2026-11-19): a column for each guest and one for «Общее». Tap lines (one
  // or several), then the column that pays for them; lines already paid for
  // are folded below and count nowhere. Then each guest pays their bill and
  // their part of «Общее» with one tap.
  function openSettlement(table, visit) {
    var old = document.querySelector(".screen.settle");
    if (old) old.remove();
    var screen = el("div", { class: "screen settle", role: "dialog" });
    document.body.appendChild(screen);
    document.body.classList.add("covered");
    var state = null, places = {}, sharers = {}, picked = {}, mode = "layout", paidOpen = false;
    var close = function () {
      screen.remove();
      document.body.classList.remove("covered");
      load(true).then(render);
    };
    var cents = function (value) { return Math.round(value * 100) / 100; };
    var guestOf = function (id) { return state.guests.filter(function (guest) { return guest.id === id; })[0]; };
    var nameOf = function (id) {
      if (id === "shared") return T.settle_shared;
      var guest = guestOf(id);
      return guest ? guest.label : "?";
    };
    var free = function () { return state.lines.filter(function (line) { return !line.fixed; }); };
    var sum = function (lines) { return cents(lines.reduce(function (total, line) { return total + Number(line.total); }, 0)); };
    var sharing = function () { return state.guests.filter(function (guest) { return sharers[guest.id]; }).map(function (guest) { return guest.id; }); };
    var pickedIds = function () { return Object.keys(picked).filter(function (id) { return picked[id]; }); };
    var show = function (settlement) {
      state = settlement;
      places = {}; sharers = {}; picked = {};
      state.lines.forEach(function (line) { places[line.id] = line.place; });
      (state.sharers.length ? state.sharers : state.guests.filter(function (guest) { return guest.here; })
        .map(function (guest) { return guest.id; })).forEach(function (id) { sharers[id] = true; });
      // Laid out and nothing new — the money; new lines — lay them out first.
      var fresh = state.guests.some(function (guest) { return Number(guest.unbilled) > 0; });
      mode = state.laid_out && !fresh ? "pay" : "layout";
      if (state.laid_out && fresh) toast(T.settle_new_lines);
      draw();
    };
    var send = function (path, body) {
      if (busy) return;
      busy = true;
      api(path, body).then(function (result) {
        busy = false;
        if (!result.ok) { haptic("error"); toast(result.data.error || T.error); return; }
        haptic("success");
        if (result.data.notice) toast(result.data.notice);
        show(result.data.settlement);
      });
    };
    var head = function () {
      var steps = el("div", { class: "steps" },
        el("span", { class: mode === "layout" ? "on" : "", text: T.settle_step_layout }),
        el("span", { class: mode === "pay" ? "on" : "", text: T.settle_step_pay }));
      return el("div", { class: "screen-head" },
        el("button", { type: "button", class: "act small", "aria-label": T.back, text: "←", onclick: close }),
        el("h2", { text: t("settle_title", { number: table.number }) }), steps);
    };
    var bar = function (amount, buttons) {
      var box = el("div", { class: "screen-bar" },
        el("div", { class: "total" }, T.settle_table_total, el("b", { text: money(amount) })));
      buttons.forEach(function (button) { box.appendChild(button); });
      return box;
    };
    var button = function (text, kind, onclick) {
      return el("button", { type: "button", class: "act" + (kind ? " " + kind : ""), text: text, onclick: onclick });
    };
    var draw = function () {
      clear(screen);
      screen.appendChild(head());
      var body = el("div", { class: "screen-body" });
      screen.appendChild(body);
      if (mode === "pay") return drawPay(body);
      var chosen = pickedIds();
      body.appendChild(el("div", { class: "screen-hint", text: chosen.length ? t("settle_picked", { count: chosen.length }) : T.settle_pick_hint }));
      var columns = el("div", { class: "columns" });
      var shared = free().filter(function (line) { return places[line.id] === "shared"; });
      var who = sharing();
      var part = who.length ? cents(sum(shared) / who.length) : 0;
      var column = function (id) {
        var lines = free().filter(function (line) { return places[line.id] === id; });
        var guest = guestOf(id);
        var total = id === "shared" ? sum(lines) : cents(sum(lines) + (sharers[id] ? part : 0));
        var here = chosen.length && chosen.some(function (lineId) { return places[lineId] !== id; });
        var card = el("div", { class: "col" + (here ? " target" : "") + (id === "shared" ? " shared" : "") });
        if (here) card.addEventListener("click", function () {
          chosen.forEach(function (lineId) { places[lineId] = id; });
          picked = {};
          draw();
        });
        var top = el("div", { class: "col-head" },
          el("span", { class: "name", text: (guest && guest.bot ? "👤 " : id === "shared" ? "👥 " : "") + nameOf(id) }),
          el("span", { class: "sum", text: money(total) }));
        card.appendChild(top);
        card.appendChild(el("div", { class: "sub", text: id === "shared"
          ? T.settle_shared_hint + (who.length ? " · " + t("settle_per_guest", { amount: money(part) }) : "")
          : (guest && guest.bot ? T.settle_with_bot : T.settle_without_bot)
            + (sharers[id] && part ? " · " + t("settle_with_share", { amount: money(part) }) : "") }));
        if (id === "shared") {
          var chips = el("div", { class: "chips" });
          state.guests.forEach(function (other) {
            chips.appendChild(el("button", { type: "button", class: "chip", text: other.label, "aria-pressed": String(!!sharers[other.id]),
              onclick: function (event) { event.stopPropagation(); sharers[other.id] = !sharers[other.id]; draw(); } }));
          });
          card.appendChild(chips);
        }
        lines.forEach(function (line) {
          card.appendChild(el("div", { class: "line" + (picked[line.id] ? " picked" : ""),
            onclick: function (event) { event.stopPropagation(); picked[line.id] = !picked[line.id]; draw(); } },
            el("span", { text: line.name + (line.quantity > 1 ? " × " + line.quantity : "") }),
            el("span", { text: money(line.total) })));
        });
        if (lines.length > 1 && !chosen.length) {
          card.appendChild(el("button", { type: "button", class: "act small ghost", text: T.settle_pick_all,
            onclick: function (event) { event.stopPropagation(); lines.forEach(function (line) { picked[line.id] = true; }); draw(); } }));
        }
        if (!lines.length) card.appendChild(el("div", { class: "drop", text: T.settle_drop }));
        columns.appendChild(card);
      };
      state.guests.forEach(function (guest) { column(guest.id); });
      column("shared");
      body.appendChild(columns);
      var paid = state.lines.filter(function (line) { return line.fixed; });
      if (paid.length) {
        var fold = el("div", { class: "paid-fold" });
        fold.appendChild(el("button", { type: "button", class: "paid-head", onclick: function () { paidOpen = !paidOpen; draw(); } },
          el("span", { text: "🔒 " + t("settle_paid_fold", { count: paid.length }) }),
          el("span", { text: money(sum(paid)) + (paidOpen ? " ▴" : " ▾") })));
        if (paidOpen) paid.forEach(function (line) {
          fold.appendChild(el("div", { class: "row" },
            el("span", { text: line.name + (line.quantity > 1 ? " × " + line.quantity : "") + " · " + nameOf(line.place) }),
            el("span", { text: money(line.total) })));
        });
        body.appendChild(fold);
      }
      screen.appendChild(bar(sum(free()), [
        chosen.length ? button(T.settle_unpick, "", function () { picked = {}; draw(); }) : button(T.back, "", close),
        button(T.settle_done, "primary", function () {
          var lines = {};
          free().forEach(function (line) { lines[line.id] = places[line.id]; });
          send("settlement_save", { visit: visit.id, places: lines, sharers: sharing() });
        }),
      ]));
    };
    var drawPay = function (body) {
      var columns = el("div", { class: "columns" });
      var left = 0;
      state.guests.forEach(function (guest) {
        var owes = Number(guest.to_pay);
        left += owes;
        var card = el("div", { class: "col pay" + (owes > 0 ? "" : " done") },
          el("div", { class: "col-head" },
            el("span", { class: "name", text: (guest.bot ? "👤 " : "") + guest.label }),
            el("span", { class: "sum", text: owes > 0 ? money(owes) : T.settle_paid })));
        if (owes > 0) {
          card.appendChild(el("div", { class: "acts" },
            button(T.sale_cash, "primary", function () { send("settlement_pay", { visit: visit.id, guest: guest.id, method: "cash", amount: guest.to_pay }); }),
            button(T.sale_card, "primary", function () { send("settlement_pay", { visit: visit.id, guest: guest.id, method: "terminal", amount: guest.to_pay }); })));
        }
        columns.appendChild(card);
      });
      body.appendChild(columns);
      var buttons = [button(T.back, "", close)];
      if (free().length) buttons.push(button(T.settle_relayout, "", function () { mode = "layout"; picked = {}; draw(); }));
      screen.appendChild(bar(cents(left), buttons));
    };
    screen.appendChild(head());
    screen.appendChild(el("div", { class: "center" }, el("div", { class: "spinner", role: "status" })));
    api("settlement", undefined, { visit: visit.id }).then(function (result) {
      if (!result.ok) { close(); toast(result.data.error || T.error); return; }
      show(result.data.settlement);
    });
  }

  function setHeadcount(visit, count) {
    if (busy || count < 1) return;
    busy = true;
    api("visit_guests", { visit: visit.id, count: count }).then(function (result) {
      after(result, function () { return load(true); });
    });
  }

  function renderTables(page) {
    var info = tabData.tables;
    if (tablesMode === "bookings" && !data.rights.reservations) tablesMode = "plan";
    if (tablesMode === "bookings") { renderBookings(page); return; }
    if (!info) { page.appendChild(el("div", { class: "center" }, el("div", { class: "spinner" }))); return; }
    if (info.plan) { renderPlan(page, info); return; }
    var board = el("div", { class: "board" });
    info.tables.forEach(function (table) {
      table.visits.forEach(function (visit) { board.appendChild(visitCard(table, visit)); });
    });
    if (!info.tables.length) board.appendChild(el("div", { class: "empty", text: T.no_tables }));
    page.appendChild(board);
  }

  // --- the floor plan (PQ-052): halls, tables coloured by their state -------
  var planHall = "";

  var openTableId = "", openTableShown = "";

  // The open table card is built again when its table changed meanwhile.
  function refreshOpenTable() {
    var open = document.querySelector(".sheet-back.table");
    var info = tabData.tables;
    if (!open || !openTableId || !info || !info.plan) return;
    var entry = null;
    info.plan.halls.concat([{ tables: info.plan.unplaced }]).forEach(function (hall) {
      hall.tables.forEach(function (item) { if (item.id === openTableId) entry = item; });
    });
    var table = info.tables.filter(function (item) { return item.id === openTableId; })[0];
    if (!entry) { open.remove(); openTableId = ""; return; }
    if (JSON.stringify([entry, table]) === openTableShown) return;
    openTable(info, entry, true);  // the company left: the sheet closes or shows the free table
  }

  // A tap on a table: its sheet — the company sitting there, the booking
  // the plan shows, «Новый заказ» and «＋ Забронировать»; a free table with
  // nothing to show goes straight to the order (or says it is free).
  function openTable(info, entry, refreshing) {
    var old = document.querySelector(".sheet-back.table");
    var table = info.tables.filter(function (item) { return item.id === entry.id; })[0];
    if (!table && !entry.reservation && !data.rights.reservations) {
      if (old) old.remove();
      openTableId = "";
      if (refreshing) return;
      if (data.rights.table_orders) { startTableOrder({ id: entry.id, number: entry.number }, null); return; }
      toast(t("table_free", { number: entry.number }));
      return;
    }
    if (old) old.remove();
    openTableId = entry.id;
    openTableShown = JSON.stringify([entry, table]);
    var back = el("div", { class: "sheet-back table", onclick: function (event) {
      if (event.target === back) { back.remove(); openTableId = ""; }
    } });
    var body = el("div", { class: "sheet", role: "dialog", style: "max-height:85vh;overflow:auto" },
      el("p", null, el("b", { text: t("table", { number: entry.number }) + " · " + T["plan_" + entry.state] })));
    if (entry.reservation) body.appendChild(bookingCard(entry.reservation, table));
    (table ? table.visits : []).forEach(function (visit) { body.appendChild(visitCard(table, visit)); });
    var acts = el("div", { class: "acts" },
      el("button", { type: "button", class: "act", text: T.back, onclick: function () { back.remove(); openTableId = ""; } }));
    if (!table && data.rights.table_orders) {
      acts.appendChild(el("button", { type: "button", class: "act primary", text: T.new_order, onclick: function () {
        var booking = entry.reservation;
        var go = function () { startTableOrder({ id: entry.id, number: entry.number }, null); };
        // Seating others at a table booked soon is allowed — with a warning.
        if (booking) sheet(t("booked_soon_q", { number: entry.number, time: booking.time, name: booking.name, guests: booking.guests }), go);
        else go();
      } }));
    }
    if (data.rights.reservations) {
      acts.appendChild(el("button", { type: "button", class: "act", text: T.book, onclick: function () {
        bookingForm(null, { table: entry.id, date: todayIso() });
      } }));
    }
    body.appendChild(acts);
    back.appendChild(body);
    document.body.appendChild(back);
  }

  function planTile(info, hall, entry) {
    var booking = entry.reservation;
    // The number, then two short lines (design pass, 2026-11-19): since when
    // and how many, or the booking's time and name.
    var lines = entry.state === "free" ? [T.plan_free]
      : entry.state === "reserved" || entry.state === "late" ? [booking.time, booking.name]
      : [entry.since, entry.guests ? t("guests", { count: entry.guests }) : ""];
    // State classes are prefixed: «.call» is the calls board's own style.
    var tile = el("button", { type: "button", class: "ptable st-" + entry.state + (entry.shape === "round" ? " round" : ""),
                              onclick: function () { openTable(info, entry); } },
      el("span", null, el("b", { text: String(entry.number) }),
         lines.filter(Boolean).length ? el("small", { text: lines.filter(Boolean)[0] }) : null,
         lines.filter(Boolean).length > 1 ? el("small", { text: lines.filter(Boolean)[1] }) : null));
    // Guests sit there and a booking comes soon: its time in the corner.
    if (booking && entry.state !== "reserved" && entry.state !== "late") {
      tile.setAttribute("data-booking", "📅 " + booking.time);
      if (booking.late) tile.classList.add("booking-late");
    }
    if (hall) {
      tile.style.left = (entry.x / hall.width * 100) + "%";
      tile.style.top = (entry.y / hall.height * 100) + "%";
      tile.style.width = (entry.w / hall.width * 100) + "%";
      tile.style.height = (entry.h / hall.height * 100) + "%";
    }
    return tile;
  }

  function renderPlan(page, info) {
    var plan = info.plan;
    if (!plan.halls.some(function (hall) { return hall.id === planHall; })) {
      planHall = plan.halls.length ? plan.halls[0].id : "";
    }
    if (plan.halls.length > 1 || data.rights.reservations) {
      var chips = el("div", { class: "filters sub", role: "tablist" });
      if (plan.halls.length > 1) {
        plan.halls.forEach(function (hall) {
          var busyHere = hall.tables.filter(function (item) {
            return item.state === "call" || item.state === "bill" || item.state === "late";
          }).length;
          chips.appendChild(el("button", { type: "button", class: "chip", "aria-pressed": String(hall.id === planHall),
                                           onclick: function () { planHall = hall.id; render(); } },
            hall.name, busyHere ? el("span", { class: "count", text: String(busyHere) }) : null));
        });
      } else {
        chips.appendChild(el("button", { type: "button", class: "chip", "aria-pressed": "true", text: T.plan_view }));
      }
      if (data.rights.reservations) chips.appendChild(bookingsChip(false));
      page.appendChild(chips);
    }
    var wrap = el("div", { class: "plan-wrap" });
    var legend = el("div", { class: "plan-legend" },
      el("span", null, el("i", { class: "st-free" }), T.plan_free),
      el("span", null, el("i", { class: "st-busy" }), T.plan_busy),
      el("span", null, el("i", { class: "st-bill" }), T.plan_bill),
      el("span", null, el("i", { class: "st-call" }), T.plan_call));
    if (data.rights.reservations) {
      legend.appendChild(el("span", null, el("i", { class: "st-reserved" }), T.plan_reserved));
      legend.appendChild(el("span", null, el("i", { class: "st-late" }), T.plan_late));
    }
    wrap.appendChild(legend);
    var hall = plan.halls.filter(function (item) { return item.id === planHall; })[0];
    if (hall) {
      var area = el("div", { class: "plan", style: "aspect-ratio:" + hall.width + " / " + hall.height });
      hall.tables.forEach(function (entry) { area.appendChild(planTile(info, hall, entry)); });
      wrap.appendChild(area);
    }
    if (plan.unplaced.length) {
      wrap.appendChild(el("div", { class: "plan-rest-title", text: T.plan_unplaced }));
      var rest = el("div", { class: "plan-rest" });
      plan.unplaced.forEach(function (entry) { rest.appendChild(planTile(info, null, entry)); });
      wrap.appendChild(rest);
    }
    if (!hall && !plan.unplaced.length) wrap.appendChild(el("div", { class: "empty", text: T.no_tables }));
    page.appendChild(wrap);
  }

  // --- reservations (PQ-052, step 3): the day's list, the form, the marks ---
  var tablesMode = "plan", bookingDay = "";

  function todayIso() {
    var now = new Date(Date.now() - clockOffset);
    return now.getFullYear() + "-" + ("0" + (now.getMonth() + 1)).slice(-2) + "-" + ("0" + now.getDate()).slice(-2);
  }

  function shiftDay(iso, days) {
    var moment = new Date(iso + "T12:00:00");
    moment.setDate(moment.getDate() + days);
    return moment.getFullYear() + "-" + ("0" + (moment.getMonth() + 1)).slice(-2) + "-" + ("0" + moment.getDate()).slice(-2);
  }

  function dayLabel(iso, today) {
    var moment = new Date(iso + "T12:00:00"), text;
    try {
      text = moment.toLocaleDateString(data.language || undefined, { weekday: "short", day: "numeric", month: "long" });
    } catch (ignored) {
      text = iso.split("-").reverse().join(".");
    }
    return iso === today ? T.today + " · " + text : text;
  }

  function loadBookings() {
    return api("reservations", undefined, bookingDay ? { date: bookingDay } : undefined).then(function (result) {
      if (!result.ok) { toast(result.data.error || T.error); tablesMode = "plan"; return; }
      tabData.bookings = result.data;
      bookingDay = result.data.date;
    });
  }

  function setTablesMode(mode) {
    tablesMode = mode;
    loadTab().then(function () { if (tab === "tables" && !busy) render(); });
    render();
  }

  function bookingsChip(pressed) {
    return el("button", { type: "button", class: "chip", "aria-pressed": String(pressed),
                          onclick: function () { setTablesMode(pressed ? "plan" : "bookings"); } }, "📅 " + T.bookings);
  }

  // The tables a booking may take: from the day's list or from the plan.
  function bookingTables() {
    if (tabData.bookings && tabData.bookings.tables) return tabData.bookings.tables;
    var plan = tabData.tables && tabData.tables.plan;
    if (!plan) return [];
    var all = [];
    plan.halls.concat([{ tables: plan.unplaced }]).forEach(function (hall) {
      hall.tables.forEach(function (item) { all.push({ id: item.id, number: item.number, seats: item.seats }); });
    });
    return all.sort(function (a, b) { return a.number - b.number; });
  }

  // One booking: when, where, who, and what can be done with it now; one
  // main button — «Пришли» (design pass, 2026-11-19).
  function bookingCard(booking, table) {
    var card = el("article", { class: "order booking b-" + booking.status + (booking.late ? " new" : "") },
      el("div", { class: "order-head" }, el("span", { class: "title time", text: booking.time + "–" + booking.until }),
         el("span", { class: "status", text: booking.late ? T.plan_late : booking.status_label })),
      el("div", { class: "place", text: t("table", { number: booking.table }) + " · " + t("guests", { count: booking.guests }) }),
      el("div", null, el("b", { text: booking.name }), " · ", el("a", { href: "tel:" + booking.phone, text: booking.phone }),
         booking.customer_code ? el("span", { class: "meta", text: " · 👤 " + booking.customer_code }) : null));
    if (booking.comment) card.appendChild(el("div", { class: "note comment", text: booking.comment }));
    if (booking.status !== "booked") return card;
    var buttons = [{ label: T.seat, kind: "primary", onclick: function () { seatBooking(booking, table); } }];
    if (booking.started) {
      buttons.push({ label: T.no_show, onclick: function () {
        sheet(t("no_show_q", { name: booking.name }), function () { bookingAction(booking, "no_show"); });
      } });
    }
    buttons.push({ label: T.edit_booking, onclick: function () { bookingForm(booking, {}); } });
    buttons.push({ label: T.cancel_booking, kind: "danger", onclick: function () {
      sheet(t("cancel_booking_q", { name: booking.name, time: booking.time }), function () { bookingAction(booking, "cancel"); });
    } });
    card.appendChild(actionsBox(buttons));
    return card;
  }

  // «Пришли»: a free table opens its visit; where a company already sits,
  // staff confirm these are the booking's guests (they were seated first).
  function seatBooking(booking, table) {
    if (!table && tabData.tables) {
      table = tabData.tables.tables.filter(function (item) { return item.id === booking.table_id; })[0];
    }
    if (table && table.visits.length === 1) {
      sheet(t("seat_here_q", { number: booking.table, name: booking.name }), function () {
        bookingAction(booking, "seat", table.visits[0].id);
      });
      return;
    }
    bookingAction(booking, "seat");
  }

  function bookingAction(booking, action, visit) {
    if (busy) return;
    busy = true;
    var payload = { id: booking.id, action: action };
    if (visit) payload.visit = visit;
    api("reservation_action", payload).then(function (result) {
      after(result, function () { return load(true); });
    });
  }

  function field(label, input) {
    return el("label", { class: "field" }, el("span", { text: label }), input);
  }

  function nextHalfHour() {
    var now = new Date(Date.now() - clockOffset + 30 * 60000);
    var minutes = now.getMinutes() < 30 ? "30" : "00";
    if (minutes === "00") now.setHours(now.getHours() + 1);
    return ("0" + now.getHours()).slice(-2) + ":" + minutes;
  }

  // The form: a new booking (``defaults`` — table and day) or another time
  // for one still «Ждём».
  function bookingForm(booking, defaults) {
    var tables = bookingTables();
    var value = booking || {};
    var day = value.date || defaults.date || bookingDay || todayIso();
    var input = function (attrs) {
      return el("input", Object.assign({ style: "width:100%" }, attrs));
    };
    var table = el("select", { style: "width:100%;padding:10px 12px;border-radius:10px;border:1px solid var(--separator);background:var(--bg);color:var(--text)" });
    tables.forEach(function (item) {
      table.appendChild(el("option", { value: item.id, text: t("table", { number: item.number }),
                                        selected: item.id === (value.table_id || defaults.table) ? true : undefined }));
    });
    var date = input({ type: "date", value: day });
    var time = input({ type: "time", value: value.time || (day === todayIso() ? nextHalfHour() : "19:00"), step: 300 });
    var minutes = el("select", { style: table.getAttribute("style") });
    [60, 90, 120, 150, 180, 240, 300].forEach(function (count) {
      minutes.appendChild(el("option", { value: String(count), text: t("hours", { hours: String(count / 60).replace(".", ",") }),
                                          selected: count === (value.minutes || 120) ? true : undefined }));
    });
    var guests = input({ type: "number", min: 1, max: 99, inputmode: "numeric", value: String(value.guests || 2) });
    // The guest's customer code (PQ-056): the name and phone come from their profile.
    var code = input({ type: "text", maxlength: 32, autocapitalize: "characters", value: value.customer_code || "",
                       placeholder: T.customer_code, autocomplete: "off" });
    var name = input({ type: "text", maxlength: 80, value: value.name || "", autocomplete: "off" });
    var phone = input({ type: "tel", maxlength: 32, value: value.phone || "", autocomplete: "off" });
    var comment = input({ type: "text", maxlength: 300, value: value.comment || "" });
    // Whom the code names, as soon as it is typed (as on «Продажа»): the guest,
    // or «not found»; their name and phone go into the fields still empty
    // (or still holding what the previous code put there).
    var who = el("div", { class: "note", hidden: true });
    var filled = { name: "", phone: "" };
    var findGuest = function () {
      var typed = code.value.trim();
      who.hidden = true;
      if (!typed) return;
      api("reservation_guest", { customer_code: typed }).then(function (result) {
        if (code.value.trim() !== typed) return;  // another code was typed meanwhile
        who.hidden = false;
        if (!result.ok) {
          who.className = "form-error";
          who.textContent = result.data.error || T.error;
          return;
        }
        who.className = "note";
        who.textContent = "👤 " + result.data.guest;
        [[name, "name"], [phone, "phone"]].forEach(function (pair) {
          var box = pair[0], key = pair[1], found = result.data[key];
          if (found && (!box.value || box.value === filled[key])) { box.value = found; filled[key] = found; }
        });
      });
    };
    code.addEventListener("change", findGuest);
    var back = el("div", { class: "sheet-back booking-form", style: "z-index:40", onclick: function (event) {
      if (event.target === back) back.remove();
    } });
    var problem = el("div", { class: "form-error", role: "alert", hidden: true });
    var save = el("button", { type: "button", class: "act primary", text: T.save, onclick: function () {
      if (busy) return;
      problem.hidden = true;
      busy = true;
      save.disabled = true;
      var body = { table: table.value, date: date.value, time: time.value, minutes: Number(minutes.value),
                   guests: Number(guests.value), customer_code: code.value.trim(), name: name.value,
                   phone: phone.value, comment: comment.value };
      if (booking) body.id = booking.id;
      api("reservation_save", body).then(function (result) {
        busy = false;
        save.disabled = false;
        if (!result.ok) {  // the form stays, the reason over its buttons
          haptic("error");
          problem.textContent = result.data.error || T.error;
          problem.hidden = false;
          return;
        }
        back.remove();
        var open = document.querySelector(".sheet-back.table");
        if (open) { open.remove(); openTableId = ""; }
        bookingDay = date.value;
        after(result);
      });
    } });
    back.appendChild(el("div", { class: "sheet", role: "dialog", style: "max-height:90vh;overflow:auto" },
      el("p", null, el("b", { text: booking ? T.edit_booking : T.new_booking })),
      el("div", { class: "form-grid" }, field(T.f_table, table), field(T.f_guests, guests),
         field(T.f_date, date), field(T.f_time, time), field(T.f_minutes, minutes)),
      field(T.booking_code_hint, code), who,
      el("div", { class: "form-grid" }, field(T.f_name, name), field(T.phone, phone)),
      field(T.comment, comment),
      problem,
      el("div", { class: "acts" },
        el("button", { type: "button", class: "act", text: T.back, onclick: function () { back.remove(); } }), save)));
    document.body.appendChild(back);
    if (value.customer_code) findGuest();
    if (!booking) name.focus();
  }

  function renderBookings(page) {
    var chips = el("div", { class: "filters sub", role: "tablist" },
      el("button", { type: "button", class: "chip", "aria-pressed": "false", text: T.plan_view,
                     onclick: function () { setTablesMode("plan"); } }),
      bookingsChip(true));
    page.appendChild(chips);
    var info = tabData.bookings;
    if (!info) { page.appendChild(el("div", { class: "center" }, el("div", { class: "spinner" }))); return; }
    var go = function (day) { bookingDay = day; tabData.bookings = null; setTablesMode("bookings"); };
    page.appendChild(el("div", { class: "booking-day" },
      el("div", { class: "day-nav" },
        el("button", { type: "button", text: "‹", "aria-label": "-1", onclick: function () { go(shiftDay(info.date, -1)); } }),
        el("b", { text: dayLabel(info.date, info.today) }),
        el("button", { type: "button", text: "›", "aria-label": "+1", onclick: function () { go(shiftDay(info.date, 1)); } })),
      info.date !== info.today ? el("button", { type: "button", class: "act small", text: T.today,
                                                onclick: function () { go(info.today); } }) : null,
      el("button", { type: "button", class: "act primary", text: T.book,
                     onclick: function () { bookingForm(null, { date: info.date }); } })));
    var board = el("div", { class: "board" });
    info.reservations.forEach(function (booking) { board.appendChild(bookingCard(booking, null)); });
    if (!info.reservations.length) board.appendChild(el("div", { class: "empty", text: T.no_bookings }));
    page.appendChild(board);
  }

  function after(result, refresh) {
    busy = false;
    if (!result.ok) { haptic("error"); toast(result.data.error || T.error); }
    else if (result.data.stale) toast(T.stale);
    else { haptic("success"); if (result.data.notice) toast(result.data.notice); }
    return (refresh ? refresh() : Promise.resolve()).then(function () { return loadTab(); }).then(render);
  }

  function requestAction(item, action) {
    if (busy) return;
    busy = true;
    api("request_action", { request: item.id, action: action }).then(function (result) { after(result); });
  }

  function visitAction(visit, action) {
    if (busy) return;
    busy = true;
    var open = document.querySelector(".sheet-back.table");
    if (open) open.remove();
    api("visit_action", { visit: visit.id, action: action }).then(function (result) { after(result); });
  }

  function openBill(id) {
    openBillId = id;
    api("bill", undefined, { id: id }).then(function (result) {
      if (!result.ok) { toast(result.data.error || T.error); return; }
      showBill(result.data.bill);
    });
  }

  // A screen over the page (design pass, 2026-11-19): a head with the title
  // and «Назад», a body, a bar with the total and the main buttons.
  function openScreen(kind, title, onClose) {
    var old = document.querySelector(".screen." + kind);
    if (old) old.remove();
    var screen = el("div", { class: "screen " + kind, role: "dialog" });
    var close = function () { screen.remove(); if (!document.querySelector(".screen")) document.body.classList.remove("covered"); if (onClose) onClose(); };
    var head = el("div", { class: "screen-head" },
      el("button", { type: "button", class: "act small", text: "← " + T.back, onclick: close }),
      el("h2", { text: title }));
    var body = el("div", { class: "screen-body" });
    var bar = el("div", { class: "screen-bar" });
    screen.appendChild(head); screen.appendChild(body); screen.appendChild(bar);
    document.body.appendChild(screen);
    document.body.classList.add("covered");
    return { screen: screen, head: head, body: body, bar: bar, close: close };
  }

  // The bill (design pass, 2026-11-19): its lines with ✕ where they may be
  // taken off, the payment beside — bonuses, equal parts — and «К оплате»
  // with cash and card in the bar.
  function showBill(bill) {
    var view = openScreen("bill-screen", "🧾 " + bill.place, function () { openBillId = ""; });
    view.head.appendChild(el("span", { class: "status", text: bill.status_label }));
    var lines = el("div", { class: "col" },
      el("div", { class: "col-head" }, el("span", { class: "name", text: bill.guest }), el("span", { class: "sum", text: money(bill.total) })),
      el("div", { class: "sub", text: bill.kind + " · #" + bill.id }));
    bill.lines.forEach(function (line) {
      lines.appendChild(el("div", { class: "bill-row" },
        el("span", { class: "grow", text: line.name + " × " + line.quantity }),
        el("b", { text: money(line.total) }),
        bill.can_edit ? el("button", { type: "button", class: "x", text: "✕", title: t("remove_line_q", { name: line.name }),
          "aria-label": t("remove_line_q", { name: line.name }),
          onclick: function () { billChange(bill, { action: "remove_line", line: line.id }, t("remove_line_q", { name: line.name })); } }) : null));
    });
    if (bill.removed) lines.appendChild(el("div", { class: "sub", text: t("removed", { count: bill.removed }) }));
    if (bill.can_edit && bill.orders.length) {
      var cancel = el("div", { class: "chips" });
      bill.orders.forEach(function (publicId) {
        cancel.appendChild(el("button", { type: "button", class: "act quiet", text: t("cancel_order", { id: publicId }),
          onclick: function () { billChange(bill, { action: "cancel_order", order: publicId }, t("cancel_order", { id: publicId }) + "?"); } }));
      });
      lines.appendChild(cancel);
    }
    var pay = el("div", { class: "col" }, el("div", { class: "col-head" }, el("span", { class: "name", text: T.payment })));
    pay.appendChild(moneyLine(T.total, bill.total));
    if (Number(bill.paid)) pay.appendChild(moneyLine(T.paid, bill.paid));
    if (Number(bill.bonus_spent)) pay.appendChild(moneyLine(T.bonuses, bill.bonus_spent));
    pay.appendChild(moneyLine(T.remaining, bill.remaining, true));
    var main = [];
    bill.actions.forEach(function (action) {
      if (action.code === "pay_bonus") {
        pay.appendChild(el("div", { class: "note", text: t("bonus_offer", { balance: bill.bonus_balance, amount: bill.bonus_offer }) }));
        pay.appendChild(el("button", { type: "button", class: "act", text: action.label, onclick: function () { billAction(bill, action); } }));
        return;
      }
      // A split bill is paid part by part, not all at once.
      if (bill.split.length && (action.code === "pay_cash" || action.code === "pay_terminal")) return;
      main.push(el("button", { type: "button", class: "act" + (action.style ? " " + action.style : ""),
                               text: action.label, onclick: function () { billAction(bill, action); } }));
    });
    // Paid in equal parts (PQ-009): each part by cash or card.
    if (bill.split.length) {
      pay.appendChild(el("div", { class: "sub", text: t("split_title", { parts: bill.split_parts }) }));
      bill.split.forEach(function (part) {
        var row = el("div", { class: "part" },
          el("span", { class: "grow", text: t("split_part", { number: part.number }) }),
          el("b", { text: money(part.amount) }));
        if (part.paid) row.appendChild(el("span", { class: "tag", text: "✓ " + part.method }));
        else if (Number(part.amount) > 0) {
          row.appendChild(el("button", { type: "button", class: "act small", text: T.sale_cash,
                                         onclick: function () { payPart(bill, part, "cash"); } }));
          row.appendChild(el("button", { type: "button", class: "act small", text: T.sale_card,
                                         onclick: function () { payPart(bill, part, "terminal"); } }));
        }
        pay.appendChild(row);
      });
    }
    if (bill.can_split) pay.appendChild(el("button", { type: "button", class: "act", text: T.split_equally,
                                                      onclick: function () { splitBill(bill); } }));
    if (bill.can_void) pay.appendChild(el("button", { type: "button", class: "act quiet", text: T.void_bill,
      onclick: function () { billChange(bill, { action: "void" }, T.void_bill + " #" + bill.id + "?"); } }));
    view.body.appendChild(el("div", { class: "columns" }, lines, pay));
    view.bar.appendChild(el("div", { class: "total" }, T.remaining, el("b", { text: money(bill.remaining) })));
    main.forEach(function (button) { view.bar.appendChild(button); });
  }

  function billChange(bill, change, title) {
    chooser(title, bill.reasons, true, function (reason, comment) {
      if (busy) return;
      busy = true;
      change.bill = bill.id; change.reason = reason; change.comment = comment;
      api("bill_change", change).then(function (result) {
        if (result.ok && result.data.bill) showBill(result.data.bill);
        after(result, function () { return load(true); });
      });
    });
  }

  // How many parts: a few quick choices, any other number, or «don't split».
  function splitBill(bill) {
    var back = el("div", { class: "sheet-back", style: "z-index:40", onclick: function (event) {
      if (event.target === back) back.remove();
    } });
    var send = function (parts) {
      back.remove();
      if (busy) return;
      busy = true;
      api("bill_split", { bill: bill.id, parts: parts }).then(function (result) {
        if (result.ok && result.data.bill) showBill(result.data.bill);
        after(result, function () { return load(true); });
      });
    };
    var count = el("input", { type: "number", min: 2, max: 20, inputmode: "numeric", placeholder: "2–20",
                              style: "width:100%;padding:10px 12px;border-radius:10px;border:1px solid var(--separator);background:var(--bg)" });
    var quick = el("div", { class: "acts" });
    [2, 3, 4, 5, 6].forEach(function (parts) {
      quick.appendChild(el("button", { type: "button", class: "act" + (parts === bill.split_parts ? " primary" : ""),
                                       text: String(parts), onclick: function () { send(parts); } }));
    });
    var acts = el("div", { class: "acts" },
      el("button", { type: "button", class: "act", text: T.back, onclick: function () { back.remove(); } }));
    if (bill.split_parts) acts.appendChild(el("button", { type: "button", class: "act danger", text: T.split_undo,
                                                         onclick: function () { send(0); } }));
    acts.appendChild(el("button", { type: "button", class: "act primary", text: T.confirm, onclick: function () {
      if (Number(count.value)) send(Number(count.value));
    } }));
    back.appendChild(el("div", { class: "sheet", role: "dialog" },
      el("p", null, el("b", { text: T.split_how })), quick, count, acts));
    document.body.appendChild(back);
  }

  function payPart(bill, part, method) {
    if (busy) return;
    busy = true;
    api("bill_split_pay", { bill: bill.id, part: part.number, method: method, amount: part.amount }).then(function (result) {
      if (result.ok && result.data.bill) showBill(result.data.bill);
      after(result, function () { return load(true); });
    });
  }

  function billAction(bill, action) {
    if (busy) return;
    busy = true;
    api("bill_action", { bill: bill.id, action: action.code }).then(function (result) {
      if (result.ok && result.data.bill) showBill(result.data.bill);
      after(result, function () { return load(true); });
    });
  }

  // --- quick sale ------------------------------------------------------------------

  // One key per sale: a retry after a slow answer sends the same key and the
  // server returns the sale already saved; any change makes a new sale.
  function saleKey() {
    if (!C().key) {
      if (window.crypto && window.crypto.randomUUID) C().key = window.crypto.randomUUID();
      else {
        var bytes = new Uint8Array(16);
        window.crypto.getRandomValues(bytes);
        C().key = Array.prototype.map.call(bytes, function (b) { return ("0" + b.toString(16)).slice(-2); }).join("");
      }
    }
    return C().key;
  }

  function salePreview() {
    C().key = "";
    var forTable = C() === tableOrder;
    var request = forTable
      ? api("table_order_preview", { customer_code: C().code, items: saleItems() })
      : api("sale_preview", { customer_code: C().code, items: saleItems(), redeem: C().redeem });
    var target = C();
    request.then(function (result) {
      if (!result.ok) { toast(result.data.error || T.error); target.preview = null; }
      else if (forTable) {
        var order = result.data.order;
        target.preview = { guest: order.guest, items: order.items, to_pay: order.total, redeemable: "0", bonus_awarded: "0" };
      } else target.preview = result.data.sale;
      render();
    });
  }

  // «Новый заказ» on a free table, «＋ Заказ» to a company sitting there,
  // a tap on a guest of it — their order. «Для кого» (PQ-009, step 2): a
  // guest at the table or «＋ Новый гость» (with a code — a guest with the
  // bot); by default the company's first guest without the bot.
  function startTableOrder(table, visit, guest) {
    var old = document.querySelector(".sheet-back.table");
    if (old) old.remove();
    tableOrder = newSale(tableOrder && tableOrder.category);
    tableOrder.table = { id: table.id, number: table.number };
    tableOrder.visit = visit ? visit.id : "";
    tableOrder.since = visit ? visit.since : "";
    tableOrder.guests = visit ? visit.guests.filter(function (item) { return item.here; }) : [];
    var plain = tableOrder.guests.filter(function (item) { return !item.bot; })[0];
    tableOrder.who = guest ? guest.id : plain ? plain.id : "new";
    switchTab("table_order");
    window.scrollTo(0, 0);
  }

  function leaveTableOrder() {
    tableOrder = null;
    tabData.tables = null;
    switchTab("tables");
  }

  function saleItems() {
    return C().lines.map(function (line) {
      return { item: line.item, quantity: line.quantity, variant: line.variant, options: line.options };
    });
  }

  function lineKey(item, variant, options) {
    return item + "|" + (variant || "-") + "|" + options.slice().sort().join(",");
  }

  function saleLine(key) {
    return C().lines.filter(function (line) { return line.key === key; })[0];
  }

  function itemCount(itemId) {
    return C().lines.reduce(function (sum, line) { return sum + (line.item === itemId ? line.quantity : 0); }, 0);
  }

  function saleChange(item, variant, options, delta) {
    var key = lineKey(item, variant, options);
    var line = saleLine(key);
    if (!line && delta > 0) {
      line = { key: key, item: item, variant: variant, options: options, quantity: 0 };
      C().lines.push(line);
    }
    if (!line) return;
    line.quantity += delta;
    if (line.quantity <= 0) C().lines = C().lines.filter(function (other) { return other !== line; });
    haptic("success");
    salePreview();
    render();  // the count on the tile at once; the receipt when the server answered
  }

  // --- a variant and add-ons for the sale (as on the guest's page) -------------

  function startingPick(item) {
    var pick = { variant: null, options: {} };
    if (item.variants && item.variants.length) pick.variant = item.variants[0].id;
    (item.groups || []).forEach(function (group) {
      if (group.min > 0 && group.max === 1 && group.options.length) pick.options[group.options[0].id] = true;
    });
    return pick;
  }

  function pickedPrice(item, pick) {
    var price = Number(item.price);
    (item.variants || []).forEach(function (variant) { if (variant.id === pick.variant) price = Number(variant.price); });
    (item.groups || []).forEach(function (group) {
      group.options.forEach(function (option) { if (pick.options[option.id]) price += Number(option.price); });
    });
    return price;
  }

  function pickedIn(group, pick) {
    return group.options.filter(function (option) { return pick.options[option.id]; }).length;
  }

  function renderPicker(page) {
    var item = C().picking.item;
    var pick = C().picking.pick;
    var box = el("div", { class: "section chooser" }, el("div", { class: "what", text: item.name }));
    var addButton = el("button", { type: "button", class: "add-picked" });
    function refresh() { addButton.textContent = t("add_for", { price: money(pickedPrice(item, pick)) }); }
    if (item.variants && item.variants.length) {
      var variants = el("fieldset", { class: "choice-group" }, el("legend", { text: T.variant }));
      item.variants.forEach(function (variant) {
        variants.appendChild(el("label", { class: "choice" },
          el("input", { type: "radio", name: "sale-variant", checked: pick.variant === variant.id,
                        onchange: function () { pick.variant = variant.id; refresh(); } }),
          el("span", { class: "grow", text: variant.name }),
          // How many are left (catalog step 3); the till may sell below zero.
          "left" in variant ? el("span", { class: "muted", text: t("left", { count: variant.left }) + " · " }) : null,
          el("span", { class: "muted", text: money(variant.price) })));
      });
      box.appendChild(variants);
    }
    (item.groups || []).forEach(function (group) {
      var radio = group.min > 0 && group.max === 1;
      var hints = [];
      if (group.min > 0) hints.push(T.required);
      if (group.max === 0) hints.push(T.any_number); else if (group.max > 1) hints.push(t("up_to", { count: group.max }));
      var fieldset = el("fieldset", { class: "choice-group" },
        el("legend", null, group.name, hints.length ? el("span", { class: "muted", text: " · " + hints.join(" · ") }) : null));
      group.options.forEach(function (option) {
        fieldset.appendChild(el("label", { class: "choice" },
          el("input", { type: radio ? "radio" : "checkbox", name: "sale-g-" + group.id, value: option.id,
                        checked: !!pick.options[option.id],
                        onchange: function (event) {
                          if (radio || (group.max === 1 && event.target.checked)) {
                            group.options.forEach(function (other) { delete pick.options[other.id]; });
                          }
                          if (event.target.checked) pick.options[option.id] = true; else delete pick.options[option.id];
                          if (group.max > 1 && pickedIn(group, pick) > group.max) {
                            delete pick.options[option.id];
                            toast(t("at_most", { group: group.name, count: group.max }));
                            haptic("error");
                          }
                          fieldset.querySelectorAll("input").forEach(function (input) { input.checked = !!pick.options[input.value]; });
                          refresh();
                        } }),
          el("span", { class: "grow", text: option.name }),
          Number(option.price) > 0 ? el("span", { class: "muted", text: "+" + money(option.price) }) : null));
      });
      box.appendChild(fieldset);
    });
    addButton.onclick = function () {
      if (item.variants && item.variants.length && !pick.variant) { toast(T.choose_variant); return; }
      var missing = (item.groups || []).filter(function (group) { return pickedIn(group, pick) < group.min; })[0];
      if (missing) { toast(t("choose_group", { group: missing.name })); haptic("error"); return; }
      C().picking = null;
      saleChange(item.id, pick.variant, Object.keys(pick.options), 1);
    };
    refresh();
    box.appendChild(addButton);
    box.appendChild(el("button", { type: "button", class: "act", text: T.back,
                                   onclick: function () { C().picking = null; render(); } }));
    page.appendChild(box);
  }

  // «Продажа» and an order to a table (design pass, 2026-11-19): the menu as
  // tiles — a tap adds one, «−» takes one off — and the receipt beside it
  // (below on a phone, with a bar that leads to it): who, the lines, the
  // total and the main button.
  function renderSale(page) {
    var forTable = tab === "table_order" && tableOrder;
    var info = forTable ? tabData.table_order : tabData.sale;
    if (!info) { page.appendChild(el("div", { class: "center" }, el("div", { class: "spinner" }))); return; }
    if (forTable) {
      page.appendChild(el("div", { class: "order-for" },
        el("button", { type: "button", class: "act small", text: "← " + T.tables, onclick: leaveTableOrder }),
        el("b", { text: t("order_for_table", { number: tableOrder.table.number })
                         + (tableOrder.since ? " · " + t("visit", { since: tableOrder.since }) : "") })));
    }
    var layout = el("div", { class: "sale-layout" });
    var menu = el("div", { class: "sale-menu" });
    if (!C().category && info.categories.length) C().category = info.categories[0].id;
    if (info.categories.length > 1) {
      var chips = el("div", { class: "filters sub" });
      info.categories.forEach(function (category) {
        chips.appendChild(el("button", { type: "button", class: "chip", text: category.name,
          "aria-pressed": String(category.id === C().category),
          onclick: function () { C().category = category.id; render(); } }));
      });
      menu.appendChild(chips);
    }
    if (C().picking) renderPicker(menu);
    var grid = el("div", { class: "menu-grid" });
    info.categories.filter(function (category) { return category.id === C().category; }).forEach(function (category) {
      category.items.forEach(function (item) {
        var quantity = itemCount(item.id);
        var choice = item.choice && item.choice !== "none";
        var price = item.price_from ? t("price_from", { price: money(item.price) }) : money(item.price);
        grid.appendChild(el("div", { class: "item-tile" + (quantity ? " on" : ""), role: "button", tabindex: "0",
          onclick: function () {
            if (choice) { C().picking = { item: item, pick: startingPick(item) }; render(); window.scrollTo(0, 0); }
            else saleChange(item.id, null, [], 1);
          } },
          el("span", { class: "name", text: item.name }),
          el("span", { class: "meta", text: price + ("left" in item ? " · " + t("left", { count: item.left }) : "") }),
          quantity ? el("span", { class: "qty", text: String(quantity) }) : null,
          // An item with a variant or add-ons is taken off in the receipt: its lines differ.
          quantity && !choice ? el("button", { type: "button", class: "minus", text: "−", "aria-label": "−",
            onclick: function (event) { event.stopPropagation(); saleChange(item.id, null, [], -1); } }) : null));
      });
    });
    menu.appendChild(grid);
    layout.appendChild(menu);
    layout.appendChild(saleCart(forTable));
    page.appendChild(layout);
    var preview = C().preview;
    if (preview && preview.items.length && C().lines.length) {
      var count = C().lines.reduce(function (sum, line) { return sum + line.quantity; }, 0);
      page.appendChild(el("div", { class: "cart-bar" },
        el("div", { class: "total" }, t("sale_lines", { count: count }), el("b", { text: money(preview.to_pay) })),
        el("button", { type: "button", class: "act primary", text: T.to_cart, onclick: function () {
          var cart = document.getElementById("cart");
          if (cart) cart.scrollIntoView({ behavior: "smooth", block: "start" });
        } })));
    }
  }

  function saleCart(forTable) {
    var cart = el("aside", { class: "cart", id: "cart" });
    if (!forTable) cart.appendChild(el("h3", { text: T.receipt }));
    if (forTable && tableOrder.guests.length) {
      var whom = el("div", { class: "chips" });
      tableOrder.guests.concat([{ id: "new", label: T.new_guest }]).forEach(function (guest) {
        whom.appendChild(el("button", { type: "button", class: "chip", text: (guest.bot ? "👤 " : "") + guest.label,
          "aria-pressed": String(tableOrder.who === guest.id),
          onclick: function () {
            tableOrder.who = guest.id;
            if (guest.id !== "new" && tableOrder.code) { tableOrder.code = ""; salePreview(); }
            tableOrder.key = "";
            render();
          } }));
      });
      cart.appendChild(el("div", { class: "sub", text: T.order_for }));
      cart.appendChild(whom);
    }
    // The code names a new guest; a guest already at the table is chosen above.
    if (!forTable || tableOrder.who === "new") {
      var code = el("input", { type: "text", autocapitalize: "characters", value: C().code, placeholder: T.customer_code,
        onchange: function (event) { C().code = event.target.value.trim(); salePreview(); } });
      cart.appendChild(el("label", { class: "field" }, el("span", { text: forTable ? T.order_code_hint : T.customer_code_hint }), code));
    }
    var preview = C().preview;
    if (preview && preview.guest) cart.appendChild(el("div", { class: "note found", text: "👤 " + preview.guest }));
    if (!preview || !preview.items.length || !C().lines.length) {
      cart.appendChild(el("div", { class: "cart-empty", text: T.tap_to_add }));
      return cart;
    }
    var lines = el("div", { class: "cart-lines" });
    preview.items.forEach(function (line, index) {
      var own = preview.items.length === C().lines.length ? C().lines[index] : null;
      lines.appendChild(el("div", { class: "cart-line" },
        el("span", { class: "grow", text: line.name + " × " + line.quantity }),
        own ? el("span", { class: "stepper" },
          el("button", { type: "button", text: "−", onclick: function () { saleChange(own.item, own.variant, own.options, -1); } }),
          el("button", { type: "button", text: "+", onclick: function () { saleChange(own.item, own.variant, own.options, 1); } })) : null,
        el("b", { text: money(line.total) })));
    });
    cart.appendChild(lines);
    if (Number(preview.redeemable) > 0) {
      var toggle = el("input", { type: "checkbox", onchange: function (event) { C().redeem = event.target.checked; salePreview(); } });
      toggle.checked = C().redeem;
      cart.appendChild(el("label", { class: "switch" },
        el("span", { text: t("sale_redeem", { amount: preview.redeemable }) }), toggle));
    }
    cart.appendChild(el("div", { class: "cart-total" }, el("span", { text: T.to_pay }), el("b", { text: money(preview.to_pay) })));
    if (Number(preview.bonus_awarded) > 0) cart.appendChild(el("div", { class: "note", text: t("sale_bonus", { bonus: preview.bonus_awarded }) }));
    cart.appendChild(el("input", { type: "text", placeholder: T.comment, value: C().comment,
      onchange: function (event) { C().comment = event.target.value; C().key = ""; } }));
    // A sale at the counter is paid at once (PQ-054): «Наличные» or
    // «Карта»; when bonuses pay it all, one «Провести».
    function confirmSale(payment) {
      if (busy) return;
      busy = true;
      api("sale_confirm", { customer_code: C().code, items: saleItems(), redeem: C().redeem, comment: C().comment,
                            key: saleKey(), payment: payment, to_pay: preview.to_pay })
        .then(function (result) {
          if (result.ok) sale = newSale(sale.category);
          after(result);
        });
    }
    var buttons = [];
    if (forTable) {
      buttons.push({ label: T.send_order, kind: "primary", disabled: busy, onclick: function () {
        if (busy) return;
        busy = true;
        var chosen = tableOrder.who !== "new";
        api("table_order", { table: tableOrder.table.id, visit: tableOrder.visit,
                             guest: chosen ? tableOrder.who : "", new_guest: !chosen && !!tableOrder.visit,
                             customer_code: chosen ? "" : tableOrder.code,
                             items: saleItems(), comment: tableOrder.comment, key: saleKey() })
          .then(function (result) {
            if (result.ok) { tableOrder = null; tabData.tables = null; tab = "tables"; }
            after(result, function () { return load(true); });
          });
      } });
    } else if (Number(preview.to_pay) > 0) {
      buttons.push({ label: T.sale_cash, kind: "primary", disabled: busy, onclick: function () { confirmSale("cash"); } });
      buttons.push({ label: T.sale_card, kind: "primary", disabled: busy, onclick: function () { confirmSale("terminal"); } });
    } else {
      buttons.push({ label: T.sale_confirm, kind: "primary", disabled: busy, onclick: function () { confirmSale(""); } });
    }
    buttons.push({ label: T.clear, kind: "danger", onclick: function () {
      if (forTable) { tableOrder.lines = []; tableOrder.preview = null; tableOrder.key = ""; }
      else sale = newSale(sale.category);
      render();
    } });
    cart.appendChild(actionsBox(buttons));
    return cart;
  }

  // --- the day report ---------------------------------------------------------------

  // The day report (design pass, 2026-11-19): revenue, purchases and the
  // average checks as large tiles; the rest in cards below.
  function renderReport(page) {
    var info = tabData.report;
    if (!info) { page.appendChild(el("div", { class: "center" }, el("div", { class: "spinner" }))); return; }
    var signed = function (value) { return value.charAt(0) === "-" ? "−" + money(value.slice(1)) : money(value); };
    var moneyRows = info.money.map(function (row) { return Object.assign({ money: true }, row); });
    var rows = moneyRows.concat(info.counts);
    var shown = function (row) { return row.money ? signed(String(row.value)) : String(row.value); };
    page.appendChild(el("div", { class: "report-head" }, el("h2", { text: T.report }), el("span", { class: "meta", text: info.date })));
    var tiles = el("div", { class: "tiles" });
    rows.filter(function (row) { return row.tile; }).forEach(function (row, index) {
      tiles.appendChild(el("div", { class: "tile" + (index === 0 ? " main" : "") },
        el("span", { text: row.label }), el("b", { text: shown(row) })));
    });
    page.appendChild(tiles);
    var board = el("div", { class: "board" });
    function block(title, items) {
      if (!items.length) return;
      var card = el("article", { class: "order" }, el("div", { class: "card-title", text: title }));
      items.forEach(function (item) { card.appendChild(item); });
      board.appendChild(card);
    }
    function row(label, value, extra) {
      return el("div", { class: "row" }, el("span", null, label, extra ? el("span", { class: "meta", text: " · " + extra }) : null),
                el("span", { text: value }));
    }
    block(T.report_details, rows.filter(function (item) { return !item.tile; }).map(function (item) {
      return row(item.label, shown(item));
    }));
    block(T.by_channel, info.channels.map(function (item) { return row(item.label, money(item.revenue), item.orders); }));
    block(T.by_method, info.methods.map(function (item) { return row(item.label, money(item.amount), item.count); }));
    block(T.by_staff, info.staff.map(function (item) { return row(item.label, money(item.amount)); }));
    page.appendChild(board);
  }

  function callsBoard() {
    if (!data.calls.length) return null;
    var calls = el("div", { class: "board", "data-part": "calls" });
    data.calls.forEach(function (call) {
      calls.appendChild(el("div", { class: "call" },
        el("div", null,
          el("div", { class: "what", text: "🔔 " + t("table", { number: call.table }) }),
          el("div", { class: "meta", text: t("minutes", { minutes: minutesSince(call.created, call.minutes) }) })),
        el("button", { type: "button", class: "act primary", text: T.go, disabled: busy,
                       style: "flex:none", onclick: function () { takeCall(call); } })));
    });
    return calls;
  }

  // Redraws the tabs and the calls in place, leaving the rest of the page
  // (a sale being typed, the report) as it is.
  function refreshChrome() {
    var oldBar = root.querySelector('[data-part="tabs"]');
    var bar = tabsBar();
    if (oldBar && bar) { oldBar.replaceWith(bar); keepTabInView(); }
    var anchor = bar && bar.isConnected ? bar : root.querySelector(".staff-head");
    var oldCalls = root.querySelector('[data-part="calls"]');
    var calls = callsBoard();
    if (oldCalls && calls) oldCalls.replaceWith(calls);
    else if (oldCalls) oldCalls.remove();
    else if (calls && anchor) anchor.after(calls);
  }

  function render() {
    if (!data) return;
    var page = el("div", { class: "page" });
    var head = el("div", { class: "staff-head" },
      el("div", null, el("h1", { text: data.venue.name }), el("div", { class: "who", text: T.title + " · " + data.employee })));
    if (data.venues.length) {
      var select = el("select", { "aria-label": T.change_point, onchange: function (event) {
        point = event.target.value; remember(); seen = null; data = null;
        tabData = { bills: null, tables: null, sale: null, report: null, table_order: null }; tab = "orders"; tableOrder = null; load();
      } });
      data.venues.forEach(function (venue) {
        var option = el("option", { value: venue.id, text: venue.name });
        if (venue.id === data.venue.id) option.selected = true;
        select.appendChild(option);
      });
      head.appendChild(select);
    }
    var tools = el("div", { class: "tools" });
    if (data.rights.invite) tools.appendChild(el("button", { type: "button", class: "icon-btn", text: "👥",
                                                             "aria-label": T.invite, title: T.invite, onclick: invite }));
    tools.appendChild(el("button", { type: "button", class: "icon-btn", text: "🔎", "aria-label": T.find_order,
                                     title: T.find_order, onclick: findOrder }));
    head.appendChild(tools);
    page.appendChild(head);
    var bar = tabsBar();
    if (bar) page.appendChild(bar);

    var calls = callsBoard();
    if (calls) page.appendChild(calls);

    if (tab === "sale") renderSale(page);
    else if (tab === "report") renderReport(page);
    else if (tab === "bills") renderBills(page);
    else if (tab === "tables") renderTables(page);
    else if (tab === "table_order") renderSale(page);
    else {
      page.appendChild(filters());
      var shown = data.orders.filter(function (order) { return matches(order, filter); });
      // New ones first, then the oldest: what waits longest is on top.
      shown.sort(function (a, b) {
        if ((a.status === "new") !== (b.status === "new")) return a.status === "new" ? -1 : 1;
        return (Date.parse(a.created) || 0) - (Date.parse(b.created) || 0);  // oldest first
      });
      var board = el("div", { class: "board" });
      shown.forEach(function (order) { board.appendChild(orderCard(order)); });
      if (!shown.length) board.appendChild(el("div", { class: "empty", text: T.no_orders }));
      page.appendChild(board);
    }
    var scroll = window.scrollY;
    show(page);
    keepTabInView();
    window.scrollTo(0, scroll);
    fresh = {};
  }

  // --- start -------------------------------------------------------------------

  if (!initData) { fatal({ status: 401, data: {} }); return; }
  if (tg && tg.BackButton) tg.BackButton.hide();
  if (tg && tg.MainButton) tg.MainButton.hide();
  load().then(schedule);
})();
