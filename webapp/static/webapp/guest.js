/* Telegram Mini App for guests (PQ-048): menu, cart, checkout.
   Every action goes to the API (webapp/views.py), which calls the bot's
   services; this page only shows what the API answers. Texts come from the
   API in the guest's language. Data is put into the page as text nodes only. */
(function () {
  "use strict";

  var tg = window.Telegram && window.Telegram.WebApp;
  var root = document.getElementById("app");
  var API = root.dataset.api;
  var initData = tg ? tg.initData : "";

  var T = {};
  var state = null;
  var menu = null;
  var checkout = null;
  var view = "menu";
  var query = "";
  var form = { phone: "", address: "", carrier: "", destination: "", slot: "", payment: "",
               shippingCost: "", useBonuses: false };
  var busy = false;
  var mainHandler = null;
  var backHandler = null;

  // --- small helpers ------------------------------------------------------

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
    var shown = number % 1 === 0 ? String(number) : number.toFixed(2);
    return shown + " " + (T.currency || "");
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
      if (tg && tg.HapticFeedback) {
        if (kind === "error") tg.HapticFeedback.notificationOccurred("error");
        else if (kind === "success") tg.HapticFeedback.notificationOccurred("success");
        else tg.HapticFeedback.impactOccurred("light");
      }
    } catch (ignored) { /* older Telegram */ }
  }

  function api(path, body, params) {
    var url = API + path;
    if (params) url += "?" + new URLSearchParams(params).toString();
    var options = { headers: { "X-Telegram-Init-Data": initData } };
    if (body !== undefined) {
      options.method = "POST";
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    return fetch(url, options).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        return { ok: response.ok, status: response.status, data: data };
      });
    }, function () {
      return { ok: false, status: 0, data: { error: T.error } };
    });
  }

  function refused(result) {
    haptic("error");
    toast((result.data && result.data.error) || T.error || "Error");
  }

  // --- Telegram buttons (a plain button outside Telegram) --------------------

  function setMain(text, handler, disabled) {
    if (tg && tg.MainButton) {
      if (mainHandler) tg.MainButton.offClick(mainHandler);
      mainHandler = null;
      if (!text) { tg.MainButton.hide(); return; }
      mainHandler = function () { if (!busy && !disabled) handler(); };
      tg.MainButton.setText(text);
      tg.MainButton.onClick(mainHandler);
      if (disabled) tg.MainButton.disable(); else tg.MainButton.enable();
      tg.MainButton.show();
      return;
    }
    var old = document.querySelector(".bottom");
    if (old) old.remove();
    if (!text) return;
    document.body.appendChild(el("div", { class: "bottom" },
      el("button", { type: "button", text: text, disabled: disabled || busy,
                     onclick: function () { if (!busy) handler(); } })));
  }

  function setBack(handler) {
    if (!(tg && tg.BackButton)) return;
    if (backHandler) tg.BackButton.offClick(backHandler);
    backHandler = handler;
    if (handler) { tg.BackButton.onClick(handler); tg.BackButton.show(); }
    else tg.BackButton.hide();
  }

  function working(on) {
    busy = on;
    if (tg && tg.MainButton) {
      if (on) tg.MainButton.showProgress(false); else tg.MainButton.hideProgress();
    }
  }

  function show(node) {
    root.replaceChildren(node);
  }

  // --- loading -------------------------------------------------------------

  function loadState() {
    return api("state").then(function (result) {
      if (!result.ok) { fatal(result); return false; }
      state = result.data;
      T = state.texts || T;
      return true;
    });
  }

  function fatal(result) {
    setMain(null);
    setBack(null);
    var message = (result.data && result.data.error) || T.error ||
      "Откройте меню из бота в Telegram. / Open the menu from the bot in Telegram.";
    show(el("div", { class: "center" }, el("div", null,
      el("p", { text: message }),
      result.status === 0 || result.status >= 500
        ? el("button", { class: "chip", type: "button", text: T.retry || "↻", onclick: start })
        : null)));
  }

  function start() {
    show(el("div", { class: "center" }, el("div", { class: "spinner", role: "status" })));
    loadState().then(function (ok) {
      if (!ok) return;
      if (state.needs_point) { renderPoints(); return; }
      return api("menu").then(function (result) {
        if (!result.ok) { fatal(result); return; }
        menu = result.data;
        view = "menu";
        render();
      });
    });
  }

  function render() {
    if (view === "cart") renderCart();
    else if (view === "checkout") renderCheckout();
    else renderMenu();
  }

  // --- venue of a network ----------------------------------------------------

  function renderPoints() {
    setMain(null);
    setBack(state.venue ? function () { view = "menu"; render(); } : null);
    var list = el("div", { class: "section" });
    state.points.forEach(function (point) {
      list.appendChild(el("button", {
        type: "button", class: "line", style: "width:100%;background:none;border:0;text-align:left",
        onclick: function () {
          api("point", { point: point.id }).then(function (result) {
            if (!result.ok) { refused(result); return; }
            start();
          });
        }
      }, el("div", { class: "grow" },
        el("div", { text: point.name }),
        point.address ? el("div", { class: "muted", text: point.address }) : null)));
    });
    show(el("div", { class: "page" }, el("h2", { class: "screen-title", text: T.pick_point }), list));
  }

  // --- menu ------------------------------------------------------------------

  function cartQuantity(itemId) {
    var line = state.cart.items.filter(function (item) { return item.item === itemId; })[0];
    return line ? line.quantity : 0;
  }

  function changeCart(body) {
    if (busy) return;
    working(true);
    api("cart", body).then(function (result) {
      working(false);
      if (!result.ok) { refused(result); return; }
      state.cart = result.data.cart;
      haptic();
      render();
    });
  }

  function chooseWay(way) {
    if (!way.enabled) { toast(way.hint || T.choose_way_first); return; }
    if (state.cart.channel === way.code || busy) return;
    working(true);
    api("channel", { channel: way.code }).then(function (result) {
      working(false);
      if (!result.ok) { refused(result); return; }
      state.cart = result.data.cart;
      if (result.data.removed && result.data.removed.length) {
        toast(T.not_sold_here + ": " + result.data.removed.join(", "));
      }
      haptic();
      render();
    });
  }

  function itemCard(item) {
    var channel = state.cart.channel;
    var off = channel && item.sold_in.indexOf(channel) === -1;
    var quantity = cartQuantity(item.id);
    var action;
    if (!state.can_order) action = null;
    else if (off) action = el("span", { class: "note", text: T.not_sold_here });
    else if (quantity > 0) {
      action = el("span", { class: "stepper" },
        el("button", { type: "button", "aria-label": "−", text: "−",
                       onclick: function () { changeCart({ action: "change", item: item.id, delta: -1 }); } }),
        el("span", { text: String(quantity) }),
        el("button", { type: "button", "aria-label": "+", text: "+",
                       onclick: function () { changeCart({ action: "change", item: item.id, delta: 1 }); } }));
    } else {
      action = el("button", { type: "button", class: "add", text: "+",
                              "aria-label": T.add + " " + item.name,
                              onclick: function () {
                                if (!channel) { toast(T.choose_way_first); haptic("error"); return; }
                                changeCart({ action: "add", item: item.id });
                              } });
    }
    var photo = el("div", { class: "photo" });
    if (item.photo) {
      photo.appendChild(el("img", { src: item.photo, alt: "", loading: "lazy",
                                    style: "width:100%;height:100%;object-fit:cover;display:block" }));
    } else {
      photo.textContent = "🍽";
    }
    return el("div", { class: "card" + (off ? " off" : "") },
      photo,
      el("div", { class: "body" },
        el("div", { class: "name", text: item.name }),
        item.description ? el("div", { class: "desc", text: item.description }) : null,
        el("div", { class: "foot" }, el("span", { class: "price", text: money(item.price) }), action)));
  }

  function renderMenu() {
    setBack(null);
    var page = el("div", { class: "page" });
    var header = el("div", { class: "venue" },
      el("div", { class: "row" },
        el("div", null,
          el("h1", { text: state.venue.name }),
          state.venue.address ? el("div", { class: "address", text: state.venue.address }) : null),
        state.points.length ? el("button", { type: "button", class: "link-button", text: T.change_point,
                                             onclick: renderPoints }) : null),
      state.bonus_balance !== null && Number(state.bonus_balance) > 0
        ? el("div", { class: "balance" }, T.bonus_balance + ": ", el("b", { text: money(state.bonus_balance) }))
        : null);
    page.appendChild(header);

    if (state.can_order && state.ways.length) {
      var ways = el("div", { class: "ways", role: "group", "aria-label": T.choose_way });
      state.ways.forEach(function (way) {
        ways.appendChild(el("button", {
          type: "button", class: "chip", text: way.title,
          "aria-pressed": String(state.cart.channel === way.code),
          disabled: !way.enabled && state.cart.channel !== way.code,
          onclick: function () { chooseWay(way); }
        }));
      });
      page.appendChild(ways);
      if (!state.cart.channel) {
        var hints = [T.choose_way];
        state.ways.forEach(function (way) { if (way.hint) hints.push(way.hint); });
        page.appendChild(el("div", { class: "way-hint", text: hints.join(" ") }));
      }
    }

    var categories = menu.categories;
    var needle = query.trim().toLowerCase();
    var sticky = el("div", { class: "sticky" });
    var input = el("input", { type: "search", placeholder: T.search, value: query, "aria-label": T.search,
                              oninput: function (event) { query = event.target.value; renderList(); } });
    sticky.appendChild(el("div", { class: "search" }, input));
    var tabs = el("div", { class: "tabs", role: "tablist" });
    categories.forEach(function (category, index) {
      tabs.appendChild(el("button", {
        type: "button", class: "tab", role: "tab", text: category.name,
        "aria-selected": String(index === 0),
        onclick: function (event) {
          tabs.querySelectorAll(".tab").forEach(function (tab) { tab.setAttribute("aria-selected", "false"); });
          event.currentTarget.setAttribute("aria-selected", "true");
          var target = document.getElementById("c-" + category.id);
          if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
        }
      }));
    });
    if (categories.length > 1) sticky.appendChild(tabs);
    page.appendChild(sticky);
    var list = el("div");
    page.appendChild(list);

    function renderList() {
      needle = query.trim().toLowerCase();
      list.replaceChildren();
      var shown = 0;
      categories.forEach(function (category) {
        var items = category.items.filter(function (item) {
          return !needle || (item.name + " " + item.description).toLowerCase().indexOf(needle) !== -1;
        });
        if (!items.length) return;
        shown += items.length;
        var grid = el("div", { class: "grid" });
        items.forEach(function (item) { grid.appendChild(itemCard(item)); });
        list.appendChild(el("section", { class: "category", id: "c-" + category.id },
          el("h2", { text: category.name }), grid));
      });
      if (!shown) list.appendChild(el("div", { class: "empty", text: categories.length ? T.nothing_found : T.menu_empty }));
    }
    renderList();
    var scroll = window.scrollY;
    show(page);
    window.scrollTo(0, scroll);
    if (document.activeElement !== input && query) input.focus();

    if (state.cart.count > 0) {
      setMain(T.cart + " · " + state.cart.count + " · " + money(state.cart.total),
              function () { view = "cart"; window.scrollTo(0, 0); render(); });
    } else {
      setMain(null);
    }
  }

  // --- cart ------------------------------------------------------------------

  function renderCart() {
    setBack(function () { view = "menu"; render(); });
    var page = el("div", { class: "page" }, el("h2", { class: "screen-title", text: T.cart }));
    if (!state.cart.items.length) {
      page.appendChild(el("div", { class: "empty", text: T.cart_empty }));
      show(page);
      setMain(T.back, function () { view = "menu"; render(); });
      return;
    }
    var section = el("div", { class: "section" });
    state.cart.items.forEach(function (line) {
      var thumb = el("div", { class: "thumb" });
      if (line.photo) {
        thumb.appendChild(el("img", { src: line.photo, alt: "", loading: "lazy",
                                      style: "width:100%;height:100%;object-fit:cover;border-radius:10px" }));
      }
      section.appendChild(el("div", { class: "line" },
        thumb,
        el("div", { class: "grow" },
          el("div", { text: line.name }),
          el("div", { class: "muted", text: money(line.unit_price) })),
        el("span", { class: "stepper" },
          el("button", { type: "button", text: "−", "aria-label": "−",
                         onclick: function () { changeCart({ action: "change", item: line.item, delta: -1 }); } }),
          el("span", { text: String(line.quantity) }),
          el("button", { type: "button", text: "+", "aria-label": "+",
                         onclick: function () { changeCart({ action: "change", item: line.item, delta: 1 }); } }))));
    });
    section.appendChild(el("div", { class: "sum total" }, el("span", { text: T.total }),
                           el("span", { text: money(state.cart.total) })));
    page.appendChild(section);
    page.appendChild(el("div", { class: "section" },
      el("button", { type: "button", class: "danger-button", text: T.clear_cart,
                     onclick: function () { changeCart({ action: "clear" }); } })));
    show(page);
    setMain(T.to_checkout, function () { view = "checkout"; checkout = null; window.scrollTo(0, 0); render(); });
  }

  // --- checkout --------------------------------------------------------------

  function loadCheckout() {
    var params = { use_bonuses: form.useBonuses ? "1" : "0" };
    if (form.shippingCost) params.shipping_cost = form.shippingCost;
    return api("checkout", undefined, params).then(function (result) {
      if (!result.ok) { refused(result); view = "cart"; render(); return false; }
      checkout = result.data;
      if (!form.phone) form.phone = checkout.phone || "";
      if (!form.address) form.address = checkout.delivery_address || "";
      if (!form.carrier) form.carrier = checkout.shipping_carrier ||
        (checkout.carriers && checkout.carriers.length ? checkout.carriers[0].code : "");
      if (!form.destination) form.destination = checkout.shipping_destination || "";
      form.shippingCost = checkout.shipping_cost || "";
      var slots = (checkout.slots || []).map(function (slot) { return slot.code; });
      if (slots.indexOf(form.slot) === -1) form.slot = slots[0] || "";
      var modes = (checkout.payment_modes || []).map(function (mode) { return mode.code; });
      if (modes.indexOf(form.payment) === -1) form.payment = modes[0] || "";
      return true;
    });
  }

  function optionChips(options, current, onPick) {
    var box = el("div", { class: "options" });
    options.forEach(function (option) {
      box.appendChild(el("button", { type: "button", class: "chip", text: option.label,
                                     "aria-pressed": String(option.code === current),
                                     onclick: function () { onPick(option.code); } }));
    });
    return box;
  }

  function field(label, value, onInput, attrs) {
    var multiline = attrs && attrs.multiline;
    var input = el(multiline ? "textarea" : "input", Object.assign({
      oninput: function (event) { onInput(event.target.value); }
    }, attrs && attrs.input));
    input.value = value || "";
    return el("label", { class: "field" }, el("span", { text: label }), input);
  }

  function renderCheckout() {
    setBack(function () { view = "cart"; render(); });
    if (!checkout) {
      show(el("div", { class: "center" }, el("div", { class: "spinner", role: "status" })));
      setMain(null);
      loadCheckout().then(function (ok) { if (ok) renderCheckout(); });
      return;
    }
    var data = checkout;
    var page = el("div", { class: "page" });

    if (data.channel === "table") {
      page.appendChild(el("h2", { class: "screen-title", text: T.order_to_table }));
      var lines = el("div", { class: "section" });
      data.cart.items.forEach(function (line) {
        lines.appendChild(el("div", { class: "sum" }, el("span", { text: line.name + " × " + line.quantity }),
                             el("span", { text: money(line.total) })));
      });
      lines.appendChild(el("div", { class: "sum total" }, el("span", { text: T.total }),
                           el("span", { text: money(data.total) })));
      page.appendChild(lines);
      show(page);
      setMain(T.order_to_table, function () { place({}); });
      return;
    }

    page.appendChild(el("h2", { class: "screen-title", text: data.title }));
    var contact = el("div", { class: "section" },
      field(T.phone, form.phone, function (value) { form.phone = value; },
            { input: { type: "tel", autocomplete: "tel", placeholder: "+380", inputmode: "tel" } }));
    if (data.channel === "delivery") {
      contact.appendChild(field(T.address, form.address, function (value) { form.address = value; },
                                { multiline: true, input: { rows: "2", placeholder: T.address_hint } }));
    }
    if (data.channel === "shipping") {
      if (data.carriers.length > 1) {
        contact.appendChild(el("h3", { text: T.carrier }));
        contact.appendChild(optionChips(data.carriers, form.carrier,
          function (code) { form.carrier = code; renderCheckout(); }));
      }
      contact.appendChild(field(T.destination, form.destination, function (value) { form.destination = value; },
                                { multiline: true, input: { rows: "3", placeholder: T.destination_hint } }));
    }
    if (data.terms) contact.appendChild(el("div", { class: "notice", text: data.terms }));
    page.appendChild(contact);

    var options = el("div", { class: "section" });
    if (data.slots && data.slots.length) {
      options.appendChild(el("h3", { text: T.time }));
      options.appendChild(optionChips(data.slots, form.slot, function (code) { form.slot = code; renderCheckout(); }));
    }
    if (data.closed) options.appendChild(el("div", { class: "warn", text: data.closed }));
    if (data.shipping_cost_options && data.shipping_cost_options.length > 1) {
      options.appendChild(el("h3", { text: T.shipping_cost }));
      options.appendChild(optionChips(data.shipping_cost_options, form.shippingCost, function (code) {
        form.shippingCost = code; checkout = null; renderCheckout();
      }));
    }
    if (Number(data.bonus_available) > 0) {
      var toggle = el("input", { type: "checkbox", onchange: function (event) {
        form.useBonuses = event.target.checked; checkout = null; renderCheckout();
      } });
      toggle.checked = form.useBonuses;
      options.appendChild(el("label", { class: "switch" },
        el("span", { text: T.use_bonuses + " (" + money(data.bonus_available) + ")" }), toggle));
    }
    if (data.payment_modes.length > 1) {
      options.appendChild(el("h3", { text: T.payment }));
      options.appendChild(optionChips(data.payment_modes, form.payment,
        function (code) { form.payment = code; renderCheckout(); }));
    }
    if (options.childNodes.length) page.appendChild(options);

    var summary = el("div", { class: "section" });
    summary.appendChild(el("div", { class: "sum" }, el("span", { text: T.items_total }),
                           el("span", { text: money(data.items_total) })));
    if (Number(data.delivery_fee) > 0) {
      summary.appendChild(el("div", { class: "sum" },
        el("span", { text: data.channel === "shipping" ? T.shipping_cost : T.delivery_fee }),
        el("span", { text: money(data.delivery_fee) })));
    }
    if (Number(data.bonus_to_spend) > 0) {
      summary.appendChild(el("div", { class: "sum" }, el("span", { text: T.bonuses }),
                             el("span", { text: "− " + money(data.bonus_to_spend) })));
    }
    summary.appendChild(el("div", { class: "sum total" }, el("span", { text: T.to_pay }),
                           el("span", { text: money(data.to_pay) })));
    var prepaid = form.payment === "prepaid" && Number(data.to_pay) > 0;
    if (prepaid) {
      summary.appendChild(el("h3", { text: T.transfer_details }));
      summary.appendChild(el("div", { class: "details", text: data.payment_details }));
    }
    page.appendChild(summary);
    show(page);

    var closed = !!data.closed;
    setMain(prepaid ? T.i_paid + " · " + money(data.to_pay) : T.confirm_order, function () {
      var body = { phone: form.phone };
      if (data.channel === "delivery") body.address = form.address;
      if (data.channel === "shipping") { body.carrier = form.carrier; body.destination = form.destination; }
      working(true);
      api("details", body).then(function (result) {
        if (!result.ok) { working(false); refused(result); return; }
        place({
          slot: form.slot, payment: form.payment, shipping_cost: form.shippingCost,
          use_bonuses: form.useBonuses, expected_total: data.total, expected_to_pay: data.to_pay
        });
      });
    }, closed);
  }

  function place(body) {
    working(true);
    api("place", body).then(function (result) {
      working(false);
      if (!result.ok) {
        refused(result);
        checkout = null;  // amounts may have changed: show the new ones
        renderCheckout();
        return;
      }
      haptic("success");
      renderDone(result.data.text);
      loadState();
    });
  }

  function renderDone(text) {
    setBack(null);
    show(el("div", { class: "done" },
      el("div", { class: "mark", text: "✅" }),
      el("h2", { text: T.placed }),
      el("p", { text: text }),
      el("p", { text: T.status_in_bot })));
    setMain(T.close, function () { if (tg) tg.close(); else start(); });
  }

  // --- start -------------------------------------------------------------------

  if (!initData) {
    fatal({ status: 401, data: {} });
    return;
  }
  tg.ready();
  tg.expand();
  start();
})();
