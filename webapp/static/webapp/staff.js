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
        if (seen && !seen[order.id]) { fresh[order.id] = true; arrived = true; }
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
      }
      seen = ids;
      if (arrived) { haptic("warning"); beep(); }
      // Bills and tables refresh with the list; the sale and the report do not
      // (the report is built again each time the tab opens).
      if (quiet && (tab === "bills" || tab === "tables")) {
        return loadTab().then(function () { if (!busy) render(); });
      }
      if (tab === "sale" || tab === "report") return;
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

  function orderCard(order) {
    var card = el("article", { class: "order" + (order.status === "new" ? " new" : "") +
                                      (fresh[order.id] ? " fresh" : "") });
    card.appendChild(el("div", { class: "top" },
      el("span", null, el("span", { class: "num", text: "#" + order.id }), " ",
         el("span", { class: "place", text: order.place })),
      el("span", { class: "status", text: order.status_label })));
    card.appendChild(el("div", { class: "meta", text: [
      t("minutes", { minutes: minutesSince(order.created, order.minutes) }), order.when, order.guest
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
    if (order.actions.length || order.can_refund) {
      var acts = el("div", { class: "acts" });
      if (order.can_refund) acts.appendChild(el("button", { type: "button", class: "act", text: T.refund,
                                                            onclick: function () { openRefund(order); } }));
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

  // --- tabs: orders, bills, tables ---------------------------------------------

  var tab = "orders";
  var tabData = { bills: null, tables: null, sale: null, report: null };
  // The sale's lines in order; a line is an item with its variant and add-ons
  // (catalog step 2), ``key`` tells two choices apart.
  function newSale(category) {
    return { code: "", lines: [], redeem: false, comment: "", preview: null, category: category || "", picking: null };
  }
  var sale = newSale("");
  var openBillId = "";

  function loadTab() {
    if (tab === "orders") return Promise.resolve();
    if (tab === "sale" && tabData.sale) return Promise.resolve();  // the menu: once
    return api(tab === "sale" ? "sale_menu" : tab).then(function (result) {
      if (!result.ok) { toast(result.data.error || T.error); tab = "orders"; return; }
      tabData[tab] = result.data;
    });
  }

  function switchTab(name) {
    tab = name;
    if (name === "report") tabData.report = null;
    if (name !== "orders" && !tabData[name]) {
      loadTab().then(render);
    }
    render();
  }

  function tabsBar() {
    var newOrders = data.orders.filter(function (order) { return order.status === "new"; }).length;
    // [tab, title, red count]: new orders, guests asking for the bill.
    var names = [["orders", T.orders + " " + data.orders.length, newOrders]];
    if (data.rights.billing) names.push(["bills", T.bills, data.bill_requests.length]);
    if (data.rights.tables) names.push(["tables", T.tables]);
    if (data.rights.sale) names.push(["sale", T.sale]);
    if (data.rights.report) names.push(["report", T.report]);
    if (names.length < 2) return null;
    var bar = el("div", { class: "filters", role: "tablist" });
    names.forEach(function (item) {
      bar.appendChild(el("button", {
        type: "button", class: "chip" + (item[2] && freshTabs[item[0]] ? " fresh" : ""), role: "tab",
        "aria-pressed": String(tab === item[0]), onclick: function () { switchTab(item[0]); }
      }, item[1], item[2] ? el("span", { class: "count", text: String(item[2]) }) : null));
    });
    freshTabs = {};
    return bar;
  }

  function moneyLine(label, value, strong) {
    return el("div", { class: "row" }, el(strong ? "b" : "span", { text: label }),
              el(strong ? "b" : "span", { text: money(value) }));
  }

  function renderBills(page) {
    var info = tabData.bills;
    if (!info) { page.appendChild(el("div", { class: "center" }, el("div", { class: "spinner" }))); return; }
    var board = el("div", { class: "board" });
    info.requests.forEach(function (item) {
      var card = el("article", { class: "order new" },
        el("div", { class: "top" }, el("span", { class: "place", text: "🧾 " + t("table", { number: item.table }) }),
           el("span", { class: "status", text: item.status_label })),
        el("div", { class: "meta", text: item.type + " · " + item.guest }));
      if (item.note) card.appendChild(el("div", { class: "note", text: item.note }));
      var acts = el("div", { class: "acts" });
      if (item.bill) acts.appendChild(el("button", { type: "button", class: "act primary", text: T.open_bill,
                                                    onclick: function () { openBill(item.bill); } }));
      if (item.can_process) acts.appendChild(el("button", { type: "button", class: "act", text: T.process,
                                                           onclick: function () { requestAction(item, "process"); } }));
      acts.appendChild(el("button", { type: "button", class: "act danger", text: T.cancel_request,
                                     onclick: function () { requestAction(item, "cancel"); } }));
      card.appendChild(acts);
      board.appendChild(card);
    });
    info.bills.forEach(function (bill) {
      board.appendChild(el("article", { class: "order", style: "cursor:pointer", onclick: function () { openBill(bill.id); } },
        el("div", { class: "top" }, el("span", null, el("span", { class: "num", text: "#" + bill.id }), " ",
                                       el("span", { class: "place", text: bill.place })),
           el("span", { class: "status", text: bill.status_label })),
        moneyLine(T.total, bill.total), moneyLine(T.remaining, bill.remaining, true)));
    });
    if (info.unbilled) board.appendChild(el("div", { class: "note", text: t("unbilled", { count: info.unbilled }) }));
    if (!info.requests.length && !info.bills.length) board.appendChild(el("div", { class: "empty", text: T.no_bills }));
    page.appendChild(board);
  }

  function renderTables(page) {
    var info = tabData.tables;
    if (!info) { page.appendChild(el("div", { class: "center" }, el("div", { class: "spinner" }))); return; }
    var board = el("div", { class: "board" });
    info.tables.forEach(function (table) {
      table.visits.forEach(function (visit) {
        var card = el("article", { class: "order" },
          el("div", { class: "top" }, el("span", { class: "num", text: t("table", { number: table.number }) }),
             el("span", { class: "status", text: t("visit", { since: visit.since }) })),
          el("div", { class: "meta", text: t("guests", { count: visit.guests.length }) + (visit.guests.length ? " · " + visit.guests.join(", ") : "") }));
        if (visit.unbilled) card.appendChild(el("div", { class: "row" },
          el("b", { text: t("not_billed", { count: visit.unbilled, total: visit.unbilled_total }) })));
        visit.bills.forEach(function (bill) {
          card.appendChild(el("button", { type: "button", class: "act", style: "text-align:left",
                                          onclick: function () { openBill(bill.id); } },
            "🧾 #" + bill.id + " · " + bill.status_label + " · " + T.remaining + " " + money(bill.remaining)));
        });
        var acts = el("div", { class: "acts" });
        if (visit.can_bill) {
          acts.appendChild(el("button", { type: "button", class: "act primary", text: T.shared_bill,
                                         onclick: function () { visitAction(visit, "shared"); } }));
          acts.appendChild(el("button", { type: "button", class: "act", text: T.personal_bills,
                                         onclick: function () { visitAction(visit, "personal"); } }));
        }
        acts.appendChild(el("button", { type: "button", class: "act danger", text: T.close_visit,
                                       onclick: function () { sheet(T.close_visit_confirm, function () { visitAction(visit, "close"); }); } }));
        card.appendChild(acts);
        board.appendChild(card);
      });
    });
    if (!info.tables.length) board.appendChild(el("div", { class: "empty", text: T.no_tables }));
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
    api("visit_action", { visit: visit.id, action: action }).then(function (result) { after(result); });
  }

  function openBill(id) {
    openBillId = id;
    api("bill", undefined, { id: id }).then(function (result) {
      if (!result.ok) { toast(result.data.error || T.error); return; }
      showBill(result.data.bill);
    });
  }

  function showBill(bill) {
    var old = document.querySelector(".sheet-back.bill");
    if (old) old.remove();
    var back = el("div", { class: "sheet-back bill", onclick: function (event) {
      if (event.target === back) { back.remove(); openBillId = ""; }
    } });
    var lines = el("ul");
    bill.lines.forEach(function (line) {
      lines.appendChild(el("li", { text: line.name + " × " + line.quantity + " — " + money(line.total) }));
    });
    var box = el("div", { class: "sheet", role: "dialog", style: "max-height:85vh;overflow:auto" },
      el("div", { class: "top", style: "display:flex;justify-content:space-between" },
        el("b", { text: "🧾 #" + bill.id + " · " + bill.place }), el("span", { class: "status", text: bill.status_label })),
      el("div", { class: "meta", text: bill.kind + " · " + bill.guest }),
      lines,
      bill.removed ? el("div", { class: "note", text: t("removed", { count: bill.removed }) }) : null,
      moneyLine(T.total, bill.total),
      Number(bill.paid) ? moneyLine(T.paid, bill.paid) : null,
      Number(bill.bonus_spent) ? moneyLine(T.bonuses || "Бонуси", bill.bonus_spent) : null,
      moneyLine(T.remaining, bill.remaining, true),
      bill.bonus_offer ? el("div", { class: "note", text: t("bonus_offer", { balance: bill.bonus_balance, amount: bill.bonus_offer }) }) : null);
    if (bill.can_edit) {
      var edit = el("div", { class: "acts" });
      bill.lines.forEach(function (line) {
        edit.appendChild(el("button", { type: "button", class: "act danger", text: "✕ " + line.name + " × " + line.quantity,
          onclick: function () { billChange(bill, { action: "remove_line", line: line.id }, t("remove_line_q", { name: line.name })); } }));
      });
      bill.orders.forEach(function (publicId) {
        edit.appendChild(el("button", { type: "button", class: "act danger", text: t("cancel_order", { id: publicId }),
          onclick: function () { billChange(bill, { action: "cancel_order", order: publicId }, t("cancel_order", { id: publicId }) + "?"); } }));
      });
      box.appendChild(el("div", { class: "note", style: "margin-top:8px", text: T.change_bill }));
      box.appendChild(edit);
    }
    var acts = el("div", { class: "acts" });
    bill.actions.forEach(function (action) {
      acts.appendChild(el("button", { type: "button", class: "act" + (action.style ? " " + action.style : ""),
                                     text: action.label, onclick: function () { billAction(bill, action); } }));
    });
    if (bill.can_void) acts.appendChild(el("button", { type: "button", class: "act danger", text: T.void_bill,
      onclick: function () { billChange(bill, { action: "void" }, T.void_bill + " #" + bill.id + "?"); } }));
    acts.appendChild(el("button", { type: "button", class: "act", text: T.back,
                                   onclick: function () { back.remove(); openBillId = ""; } }));
    box.appendChild(acts);

    back.appendChild(box);
    document.body.appendChild(back);
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
    if (!sale.key) {
      if (window.crypto && window.crypto.randomUUID) sale.key = window.crypto.randomUUID();
      else {
        var bytes = new Uint8Array(16);
        window.crypto.getRandomValues(bytes);
        sale.key = Array.prototype.map.call(bytes, function (b) { return ("0" + b.toString(16)).slice(-2); }).join("");
      }
    }
    return sale.key;
  }

  function salePreview() {
    sale.key = "";
    api("sale_preview", { customer_code: sale.code, items: saleItems(), redeem: sale.redeem }).then(function (result) {
      if (!result.ok) { toast(result.data.error || T.error); sale.preview = null; }
      else sale.preview = result.data.sale;
      render();
    });
  }

  function saleItems() {
    return sale.lines.map(function (line) {
      return { item: line.item, quantity: line.quantity, variant: line.variant, options: line.options };
    });
  }

  function lineKey(item, variant, options) {
    return item + "|" + (variant || "-") + "|" + options.slice().sort().join(",");
  }

  function saleLine(key) {
    return sale.lines.filter(function (line) { return line.key === key; })[0];
  }

  function itemCount(itemId) {
    return sale.lines.reduce(function (sum, line) { return sum + (line.item === itemId ? line.quantity : 0); }, 0);
  }

  function saleChange(item, variant, options, delta) {
    var key = lineKey(item, variant, options);
    var line = saleLine(key);
    if (!line && delta > 0) {
      line = { key: key, item: item, variant: variant, options: options, quantity: 0 };
      sale.lines.push(line);
    }
    if (!line) return;
    line.quantity += delta;
    if (line.quantity <= 0) sale.lines = sale.lines.filter(function (other) { return other !== line; });
    haptic("success");
    salePreview();
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
    var item = sale.picking.item;
    var pick = sale.picking.pick;
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
      sale.picking = null;
      saleChange(item.id, pick.variant, Object.keys(pick.options), 1);
    };
    refresh();
    box.appendChild(addButton);
    box.appendChild(el("button", { type: "button", class: "act", text: T.back,
                                   onclick: function () { sale.picking = null; render(); } }));
    page.appendChild(box);
  }

  function renderSale(page) {
    var info = tabData.sale;
    if (!info) { page.appendChild(el("div", { class: "center" }, el("div", { class: "spinner" }))); return; }
    var section = el("div", { class: "section" });
    var code = el("input", { type: "text", autocapitalize: "characters", value: sale.code, placeholder: T.customer_code,
      style: "width:100%;padding:10px 12px;border-radius:10px;border:1px solid var(--separator);background:var(--bg)",
      onchange: function (event) { sale.code = event.target.value.trim(); salePreview(); } });
    section.appendChild(el("label", { class: "field" }, el("span", { text: T.customer_code_hint }), code));
    if (sale.preview && sale.preview.guest) section.appendChild(el("div", { class: "note", text: "👤 " + sale.preview.guest }));
    page.appendChild(section);
    if (!sale.category && info.categories.length) sale.category = info.categories[0].id;
    var chips = el("div", { class: "filters" });
    info.categories.forEach(function (category) {
      chips.appendChild(el("button", { type: "button", class: "chip", text: category.name,
        "aria-pressed": String(category.id === sale.category),
        onclick: function () { sale.category = category.id; render(); } }));
    });
    page.appendChild(chips);
    if (sale.picking) renderPicker(page);
    var grid = el("div", { class: "board" });
    info.categories.filter(function (category) { return category.id === sale.category; }).forEach(function (category) {
      category.items.forEach(function (item) {
        var quantity = itemCount(item.id);
        var price = item.price_from ? t("price_from", { price: money(item.price) }) : money(item.price);
        var control = item.choice && item.choice !== "none"
          ? el("button", { type: "button", class: "add", text: "+", onclick: function () {
              sale.picking = { item: item, pick: startingPick(item) }; render(); window.scrollTo(0, 0);
            } })
          : el("span", { class: "stepper" },
              el("button", { type: "button", text: "−", onclick: function () { saleChange(item.id, null, [], -1); } }),
              el("span", { text: String(quantity) }),
              el("button", { type: "button", text: "+", onclick: function () { saleChange(item.id, null, [], 1); } }));
        grid.appendChild(el("div", { class: "call", style: "border-left-color:" + (quantity ? "var(--accent)" : "transparent") },
          el("div", null, el("div", { class: "what", text: item.name }),
             el("div", { class: "meta", text: price + ("left" in item ? " · " + t("left", { count: item.left }) : "")
                                              + (quantity && item.choice !== "none" ? " · ×" + quantity : "") })),
          control));
      });
    });
    page.appendChild(grid);
    var preview = sale.preview;
    if (preview && preview.items.length) {
      var summary = el("div", { class: "section" });
      preview.items.forEach(function (line, index) {
        var own = preview.items.length === sale.lines.length ? sale.lines[index] : null;
        summary.appendChild(el("div", { class: "row" }, el("span", { text: line.name + " × " + line.quantity }),
          el("span", null,
            own ? el("span", { class: "stepper" },
              el("button", { type: "button", text: "−", onclick: function () { saleChange(own.item, own.variant, own.options, -1); } }),
              el("button", { type: "button", text: "+", onclick: function () { saleChange(own.item, own.variant, own.options, 1); } })) : null,
            " " + money(line.total))));
      });
      if (Number(preview.redeemable) > 0) {
        var toggle = el("input", { type: "checkbox", onchange: function (event) { sale.redeem = event.target.checked; salePreview(); } });
        toggle.checked = sale.redeem;
        summary.appendChild(el("label", { class: "switch" },
          el("span", { text: t("sale_redeem", { amount: preview.redeemable }) }), toggle));
      }
      summary.appendChild(el("div", { class: "row" }, el("b", { text: T.to_pay }), el("b", { text: money(preview.to_pay) })));
      if (Number(preview.bonus_awarded) > 0) summary.appendChild(el("div", { class: "note", text: t("sale_bonus", { bonus: preview.bonus_awarded }) }));
      var comment = el("input", { type: "text", placeholder: T.comment, value: sale.comment,
        style: "width:100%;padding:10px 12px;margin-top:8px;border-radius:10px;border:1px solid var(--separator);background:var(--bg)",
        onchange: function (event) { sale.comment = event.target.value; sale.key = ""; } });
      summary.appendChild(comment);
      summary.appendChild(el("div", { class: "acts" },
        el("button", { type: "button", class: "act", text: T.clear, onclick: function () {
          sale = newSale(sale.category); render();
        } }),
        el("button", { type: "button", class: "act primary", text: T.sale_confirm, disabled: busy, onclick: function () {
          if (busy) return;
          busy = true;
          api("sale_confirm", { customer_code: sale.code, items: saleItems(), redeem: sale.redeem, comment: sale.comment, key: saleKey() })
            .then(function (result) {
              if (result.ok) sale = newSale(sale.category);
              after(result);
            });
        } })));
      page.appendChild(summary);
    }
  }

  // --- the day report ---------------------------------------------------------------

  function renderReport(page) {
    var info = tabData.report;
    if (!info) { page.appendChild(el("div", { class: "center" }, el("div", { class: "spinner" }))); return; }
    var board = el("div", { class: "board" });
    function block(title, rows) {
      var card = el("article", { class: "order" }, el("div", { class: "place", text: title }));
      rows.forEach(function (row) { card.appendChild(row); });
      board.appendChild(card);
    }
    block(T.report + " · " + info.date, info.money.map(function (row) {
      return el("div", { class: "row" }, el(row.strong ? "b" : "span", { text: row.label }),
                el(row.strong ? "b" : "span", { text: row.value.charAt(0) === "-" ? "−" + money(row.value.slice(1)) : money(row.value) }));
    }).concat(info.counts.map(function (row) {
      return el("div", { class: "row" }, el("span", { text: row.label }), el("span", { text: String(row.value) }));
    })));
    if (info.channels.length) block(T.by_channel, info.channels.map(function (row) {
      return el("div", { class: "row" }, el("span", { text: row.label + " · " + row.orders }), el("span", { text: money(row.revenue) }));
    }));
    if (info.methods.length) block(T.by_method, info.methods.map(function (row) {
      return el("div", { class: "row" }, el("span", { text: row.label + " (" + row.count + ")" }), el("span", { text: money(row.amount) }));
    }));
    if (info.staff.length) block(T.by_staff, info.staff.map(function (row) {
      return el("div", { class: "row" }, el("span", { text: row.label }), el("span", { text: money(row.amount) }));
    }));
    page.appendChild(board);
  }

  function render() {
    if (!data) return;
    var page = el("div", { class: "page" });
    var head = el("div", { class: "staff-head" },
      el("div", null, el("h1", { text: data.venue.name }), el("div", { class: "who", text: T.title + " · " + data.employee })));
    if (data.venues.length) {
      var select = el("select", { "aria-label": T.change_point, onchange: function (event) {
        point = event.target.value; remember(); seen = null; data = null;
        tabData = { bills: null, tables: null, sale: null, report: null }; tab = "orders"; load();
      } });
      data.venues.forEach(function (venue) {
        var option = el("option", { value: venue.id, text: venue.name });
        if (venue.id === data.venue.id) option.selected = true;
        select.appendChild(option);
      });
      head.appendChild(select);
    }
    var tools = el("div", { style: "display:flex;gap:6px" });
    if (data.rights.invite) tools.appendChild(el("button", { type: "button", class: "chip", text: "👥",
                                                             "aria-label": T.invite, title: T.invite, onclick: invite }));
    tools.appendChild(el("button", { type: "button", class: "chip", text: "🔎", "aria-label": T.find_order,
                                     title: T.find_order, onclick: findOrder }));
    head.appendChild(tools);
    page.appendChild(head);
    var bar = tabsBar();
    if (bar) page.appendChild(bar);

    if (data.calls.length) {
      var calls = el("div", { class: "board" });
      data.calls.forEach(function (call) {
        calls.appendChild(el("div", { class: "call" },
          el("div", null,
            el("div", { class: "what", text: "🔔 " + t("table", { number: call.table }) }),
            el("div", { class: "meta", text: t("minutes", { minutes: minutesSince(call.created, call.minutes) }) })),
          el("button", { type: "button", class: "act primary", text: T.go, disabled: busy,
                         style: "flex:none", onclick: function () { takeCall(call); } })));
      });
      page.appendChild(calls);
    }

    if (tab === "sale") renderSale(page);
    else if (tab === "report") renderReport(page);
    else if (tab === "bills") renderBills(page);
    else if (tab === "tables") renderTables(page);
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
    window.scrollTo(0, scroll);
    fresh = {};
  }

  // --- start -------------------------------------------------------------------

  if (!initData) { fatal({ status: 401, data: {} }); return; }
  if (tg && tg.BackButton) tg.BackButton.hide();
  if (tg && tg.MainButton) tg.MainButton.hide();
  load().then(schedule);
})();
