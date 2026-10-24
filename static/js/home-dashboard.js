/* لوحة المؤشرات التنفيذية — تحميل الودجتات حسب الصلاحية ورسم SVG بلا مكتبات */
(function () {
  "use strict";

  var root = document.querySelector("[data-hx]");
  if (!root) return;

  var API = root.getAttribute("data-api") || "";
  var DATE_FROM = root.getAttribute("data-date-from") || "";
  var DATE_TO = root.getAttribute("data-date-to") || "";
  var MONTHS = (root.getAttribute("data-months") || "").split(",").filter(Boolean);
  var WIDGETS = new Set(
    (root.getAttribute("data-widgets") || "").trim().split(/\s+/).filter(Boolean)
  );
  var MONTH_NAMES = [
    "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
    "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر",
  ];
  var SVG_NS = "http://www.w3.org/2000/svg";

  /* ── تنسيق ─────────────────────────────────────────────────────── */
  var nf = {};
  function num(v, digits) {
    var d = digits || 0;
    if (!nf[d]) {
      nf[d] = new Intl.NumberFormat("en-US", {
        minimumFractionDigits: d,
        maximumFractionDigits: d,
      });
    }
    return nf[d].format(Number(v) || 0);
  }

  function compact(v) {
    var n = Number(v) || 0;
    var a = Math.abs(n);
    if (a >= 1e6) return { text: num(n / 1e6, 2), unit: "مليون" };
    if (a >= 1e4) return { text: num(n / 1e3, 1), unit: "ألف" };
    return { text: num(n, 0), unit: "" };
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function moneyHtml(v) {
    var c = compact(v);
    return (
      '<span class="hx-num">' + c.text + "</span><small>" +
      (c.unit ? c.unit + " " : "") + "ر.س</small>"
    );
  }

  function moneyText(v) {
    var c = compact(v);
    return c.text + (c.unit ? " " + c.unit : "") + " ر.س";
  }

  function moneyFull(v) {
    return num(v, 2) + " ر.س";
  }

  function signed(v, digits) {
    var n = Number(v) || 0;
    return (n > 0 ? "+" : n < 0 ? "−" : "") + num(Math.abs(n), digits);
  }

  function chgClass(v) {
    if (v == null) return "is-flat";
    return v > 0 ? "is-up" : v < 0 ? "is-down" : "is-flat";
  }

  function chgHtml(v) {
    if (v == null) return '<span class="hx-chg is-flat" title="لا توجد مقارنة">—</span>';
    return '<span class="hx-chg ' + chgClass(v) + '">' + signed(v, 1) + "%</span>";
  }

  function setPill(el, value, suffix) {
    if (!el) return;
    el.classList.remove("is-up", "is-down");
    if (value == null) {
      el.textContent = "لا مقارنة";
      return;
    }
    el.classList.add(value > 0 ? "is-up" : value < 0 ? "is-down" : "is-flat");
    el.textContent = signed(value, 1) + suffix;
  }

  /* ── هيكل الودجت ───────────────────────────────────────────────── */
  function cardsFor(name) {
    return Array.prototype.slice.call(
      root.querySelectorAll('[data-hx-card="' + name + '"]')
    );
  }

  function slot(card, name) {
    if (card.getAttribute("data-slot") === name) return card;
    var all = card.querySelectorAll('[data-slot="' + name + '"]');
    for (var i = 0; i < all.length; i++) {
      if (all[i].closest("[data-hx-card]") === card) return all[i];
    }
    return null;
  }

  function skeleton(lines) {
    var html = "";
    for (var i = 0; i < lines; i++) {
      html += '<span class="hx-skel" style="width:' + (92 - i * 13) + '%"></span>';
    }
    return html;
  }

  function stateHtml(text, isError) {
    return '<p class="hx-state' + (isError ? " hx-state-error" : "") + '" role="status">' +
      esc(text) + "</p>";
  }

  function markLoading() {
    root.querySelectorAll("[data-hx-card]").forEach(function (card) {
      var body = slot(card, "body");
      if (body && !body.innerHTML.trim()) body.innerHTML = skeleton(4);
      var value = slot(card, "value");
      if (value) value.innerHTML = '<span class="hx-skel" style="width:70%;height:1.6rem"></span>';
    });
  }

  function showError(name, message) {
    cardsFor(name).forEach(function (card) {
      card.classList.remove("is-loading");
      card.classList.add("is-error");
      var body = slot(card, "body");
      if (body) body.innerHTML = stateHtml(message || "تعذّر التحميل.", true);
      var value = slot(card, "value");
      if (value) value.textContent = "—";
      var hint = slot(card, "hint");
      if (hint) hint.textContent = message || "تعذّر التحميل";
    });
  }

  function dispatch(name, data) {
    cardsFor(name).forEach(function (card) {
      card.classList.remove("is-loading", "is-error");
      var fn = RENDER[card.getAttribute("data-hx-render")];
      if (!fn) return;
      try {
        fn(card, data);
      } catch (err) {
        var body = slot(card, "body");
        if (body) body.innerHTML = stateHtml("تعذّر عرض البيانات.", true);
        if (window.console) console.error("home widget render", name, err);
      }
    });
  }

  /* ── الشبكة ────────────────────────────────────────────────────── */
  function widgetUrl(name, extra) {
    var url = API.replace("WIDGET", encodeURIComponent(name));
    var params = new URLSearchParams({ date_from: DATE_FROM, date_to: DATE_TO });
    if (extra) Object.keys(extra).forEach(function (k) { params.set(k, extra[k]); });
    return url + "?" + params.toString();
  }

  function fetchWidget(name, extra) {
    return fetch(widgetUrl(name, extra), {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    }).then(function (res) {
      // الجلسة تغيّرت (دخول/خروج) أثناء التحميل: أعد تحميل الصفحة بدل عرض خطأ
      if (res.status === 401) {
        window.location.reload();
        throw new Error("انتهت الجلسة.");
      }
      return res
        .json()
        .catch(function () { return { ok: false, error: "استجابة غير متوقعة." }; })
        .then(function (body) {
          if (!res.ok || !body || !body.ok) {
            throw new Error((body && body.error) || "تعذّر التحميل.");
          }
          return body.data;
        });
    });
  }

  var queue = [];
  var active = 0;
  var MAX_ACTIVE = 3;

  function enqueue(task) {
    queue.push(task);
    pump();
  }

  function pump() {
    while (active < MAX_ACTIVE && queue.length) {
      var task = queue.shift();
      active += 1;
      task().then(done, done);
    }
  }

  function done() {
    active -= 1;
    pump();
  }

  function load(name, after) {
    enqueue(function () {
      return fetchWidget(name)
        .then(function (data) {
          dispatch(name, data);
        })
        .catch(function (err) {
          showError(name, err.message);
        })
        .then(function () {
          if (after) after();
        });
    });
  }

  /* ── رسومات صغيرة ──────────────────────────────────────────────── */
  function el(tag, attrs) {
    var node = document.createElementNS(SVG_NS, tag);
    Object.keys(attrs || {}).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    return node;
  }

  /* المحور الزمني من اليمين (الأقدم) إلى اليسار (الأحدث) — اتجاه القراءة */
  function sparkline(host, values) {
    var pts = values.filter(function (v) { return v != null; });
    if (!host || pts.length < 2) return;
    var w = 120, h = 36, pad = 3;
    var max = Math.max.apply(null, pts), min = Math.min.apply(null, pts);
    var span = max - min || 1;
    var step = (w - pad * 2) / (values.length - 1);
    var coords = [];
    values.forEach(function (v, i) {
      if (v == null) return;
      var x = w - pad - i * step;
      var y = h - pad - ((v - min) / span) * (h - pad * 2);
      coords.push([x, y]);
    });
    var line = coords.map(function (c, i) { return (i ? "L" : "M") + c[0].toFixed(1) + " " + c[1].toFixed(1); }).join(" ");
    var area = line + " L" + coords[coords.length - 1][0].toFixed(1) + " " + h + " L" + coords[0][0].toFixed(1) + " " + h + " Z";
    var svg = el("svg", { viewBox: "0 0 " + w + " " + h, preserveAspectRatio: "none" });
    svg.appendChild(el("path", { d: area, class: "hx-spark-area" }));
    svg.appendChild(el("path", { d: line, class: "hx-spark-line" }));
    host.innerHTML = "";
    host.appendChild(svg);
  }

  function donut(segments, total, centerHtml) {
    var r = 40, c = 2 * Math.PI * r, offset = 0;
    var svg = '<svg viewBox="0 0 100 100" aria-hidden="true"><circle cx="50" cy="50" r="' + r +
      '" stroke="#EEF1F2"></circle>';
    segments.forEach(function (s) {
      var frac = total > 0 ? Math.max(0, s.value) / total : 0;
      var len = frac * c;
      if (len <= 0) return;
      var gap = segments.length > 1 && len > 2 ? 1.2 : 0;
      svg += '<circle cx="50" cy="50" r="' + r + '" stroke="' + s.color +
        '" stroke-dasharray="' + Math.max(0, len - gap).toFixed(2) + " " + c.toFixed(2) +
        '" stroke-dashoffset="' + (-offset).toFixed(2) + '"></circle>';
      offset += len;
    });
    svg += "</svg>";
    return '<div class="hx-donut">' + svg + '<div class="hx-donut-center">' + centerHtml + "</div></div>";
  }

  var AGE_CLASSES = ["hx-age-0", "hx-age-1", "hx-age-2", "hx-age-3"];
  var CHANNEL_COLORS = { pos: "#117C77", credit: "#72B7B1", cash_extra: "#C37A19" };

  /* ── العرض ─────────────────────────────────────────────────────── */
  var RENDER = {
    kpiSales: function (card, d) {
      slot(card, "value").innerHTML = moneyHtml(d.total);
      slot(card, "value").title = moneyFull(d.total);
      setPill(slot(card, "pill"), d.change_pct, "%");
      slot(card, "hint").textContent = "السابقة " + moneyText(d.prev_total);
    },

    channelsPanel: function (card, d) {
      var segs = (d.channels || []).map(function (c) {
        return { value: c.amount, color: CHANNEL_COLORS[c.key] || "#96A2A4" };
      });
      var total = Number(d.total) || 0;
      if (total <= 0) {
        slot(card, "body").innerHTML = stateHtml("لا مبيعات في هذه الفترة.");
        return;
      }
      var c = compact(total);
      var keys = (d.channels || []).map(function (ch) {
        return (
          '<li><i class="hx-dot" style="background:' + (CHANNEL_COLORS[ch.key] || "#96A2A4") + '"></i>' +
          '<span class="hx-key-name">' + esc(ch.label) + "</span>" +
          '<span class="hx-key-pct">' + num(ch.share_pct, 1) + "%</span>" +
          '<span class="hx-key-meta"><span class="hx-num">' + esc(moneyText(ch.amount)) + "</span> · " +
          chgHtml(ch.change_pct) + "</span></li>"
        );
      }).join("");
      slot(card, "body").innerHTML =
        '<div class="hx-donut-wrap">' +
        donut(segs, total, "<strong>" + c.text + "</strong><span>" + (c.unit ? c.unit + " " : "") + "ر.س</span>") +
        '<ul class="hx-keylist">' + keys + "</ul></div>" +
        '<p class="hx-foot-note">' + num(d.pos_invoices) + " فاتورة نقاط بيع في الفترة</p>";
    },

    kpiPurchases: function (card, d) {
      slot(card, "value").innerHTML = moneyHtml(d.total);
      slot(card, "value").title = moneyFull(d.total);
      setPill(slot(card, "pill"), d.change_pct, "%");
      slot(card, "hint").textContent = "السابقة " + moneyText(d.prev_total);
    },

    vendorsPanel: function (card, d) {
      var vendors = d.vendors || [];
      var list = vendors.length
        ? '<ul class="hx-vendors">' + vendors.map(function (v) {
            var initial = (String(v.name || "").trim().replace(/^(شركة|مؤسسة|الشركة|مصنع)\s+/, "").charAt(0)) || "•";
            return (
              '<li><span class="hx-avatar" aria-hidden="true">' + esc(initial) + "</span>" +
              '<span class="hx-v-name" title="' + esc(v.name) + '">' + esc(v.name) + "</span>" +
              '<span class="hx-v-amt"><span class="hx-num">' + esc(moneyText(v.amount)) + "</span>" +
              "<span>" + num(v.share_pct, 1) + "% من المشتريات</span></span>" +
              '<span class="hx-v-meta">' + num(v.invoice_count) + " فاتورة</span></li>"
            );
          }).join("") + "</ul>"
        : stateHtml("لا مشتريات في هذه الفترة.");
      slot(card, "body").innerHTML =
        list +
        '<div class="hx-stats">' +
        '<div class="hx-stat"><strong>' + num(d.invoice_count) + "</strong><span>فاتورة شراء</span></div>" +
        '<div class="hx-stat"><strong>' + num(d.return_count) + "</strong><span>مرتجع شراء</span></div>" +
        '<div class="hx-stat"><strong>' + num(d.vendor_count) + "</strong><span>مورد نشط</span></div>" +
        "</div>";
    },

    kpiInventory: function (card, d) {
      slot(card, "value").innerHTML = moneyHtml(d.stock_value);
      slot(card, "value").title = moneyFull(d.stock_value);
      var old = (d.aging || []).filter(function (a) { return a.key === "90_plus"; })[0];
      slot(card, "pill").textContent = old ? num(old.share_pct, 1) + "% أقدم من 90 يوماً" : "";
      slot(card, "hint").textContent = num(d.branch_count) + " فرع";
      var mini = slot(card, "mini");
      var max = Math.max.apply(null, (d.aging || []).map(function (a) { return a.share_pct; }).concat([1]));
      mini.innerHTML = (d.aging || []).map(function (a, i) {
        var cls = i === 0 ? "is-strong" : i === 3 ? "is-warn" : "";
        return '<span class="' + cls + '" style="height:' + Math.max(8, (a.share_pct / max) * 100).toFixed(0) + '%"></span>';
      }).join("");
    },

    inventoryPanel: function (card, d) {
      var aging = d.aging || [];
      var stack = aging.map(function (a, i) {
        return '<span class="' + AGE_CLASSES[i] + '" style="width:' + a.share_pct + '%" title="' +
          esc(a.label) + " · " + num(a.share_pct, 1) + '%"></span>';
      }).join("");
      var list = aging.map(function (a, i) {
        return (
          '<li><i class="' + AGE_CLASSES[i] + '"></i><strong>' + esc(a.label) + "</strong>" +
          "<span>" + esc(moneyText(a.value)) + " · " + num(a.share_pct, 1) + "% · " + num(a.item_count) + " صنف</span></li>"
        );
      }).join("");
      slot(card, "body").innerHTML =
        '<div class="hx-inv-head"><span class="hx-inv-value">' + moneyHtml(d.stock_value) +
        '</span><span class="hx-kpi-hint">' + num(d.branch_count) + " فرع · بالتكلفة بعد خصم غير المرحّل</span></div>" +
        '<div class="hx-stack" role="img" aria-label="توزيع أعمار المخزون">' + stack + "</div>" +
        '<ul class="hx-age-list">' + list + "</ul>";
    },

    turnoverStrip: function (card, d) {
      if (d.turnover == null) {
        card.innerHTML = stateHtml("لا تكفي البيانات لحساب الدوران.");
        return;
      }
      card.innerHTML =
        '<div class="hx-stat"><strong>' + num(d.turnover, 1) + " مرة</strong><span>معدل الدوران السنوي</span></div>" +
        '<div class="hx-stat"><strong>' + num(d.days_on_hand) + " يوم</strong><span>أيام تغطية المخزون</span></div>" +
        '<p class="hx-note"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></svg>' +
        "<span>محسوب من " + esc(d.basis_label) + " لآخر " + num(d.days) + " يوماً مُحوّلة لسنة ÷ قيمة المخزون الحالية.</span></p>";
    },

    kpiProfit: function (card, d) {
      var v = slot(card, "value");
      v.innerHTML = d.margin_pct == null ? "—" : '<span class="hx-num">' + num(d.margin_pct, 1) + "%</span>";
      setPill(slot(card, "pill"), d.margin_delta_pts, " نقطة");
      slot(card, "hint").textContent = "مجمل ربح " + moneyText(d.profit);
      var mini = slot(card, "mini");
      var prev = Number(d.prev_margin_pct) || 0;
      var cur = Number(d.margin_pct) || 0;
      var max = Math.max(prev, cur, 1);
      mini.innerHTML =
        '<span style="height:' + ((prev / max) * 100).toFixed(0) + '%" title="السابقة"></span>' +
        '<span class="is-strong" style="height:' + ((cur / max) * 100).toFixed(0) + '%" title="الحالية"></span>';
    },

    profitPanel: function (card, d) {
      var sales = Number(d.sales) || 0;
      if (sales <= 0) {
        slot(card, "body").innerHTML = stateHtml("لا مبيعات في هذه الفترة.");
        return;
      }
      var costPct = (Number(d.cost) / sales) * 100;
      var profitPct = (Number(d.profit) / sales) * 100;
      var deltaCls = chgClass(d.margin_delta_pts);
      var trendWord = d.profit_change_pct == null ? "" :
        d.profit_change_pct >= 0 ? "ارتفع مجمل الربح " + num(d.profit_change_pct, 1) + "%" :
        "انخفض مجمل الربح " + num(Math.abs(d.profit_change_pct), 1) + "%";
      slot(card, "body").innerHTML =
        '<div class="hx-profit-top"><span class="hx-profit-pct">' +
        (d.margin_pct == null ? "—" : num(d.margin_pct, 1) + "%") + "</span>" +
        '<span class="hx-pill ' + deltaCls + '">' +
        (d.margin_delta_pts == null ? "لا مقارنة" : signed(d.margin_delta_pts, 1) + " نقطة") + "</span></div>" +
        '<p class="hx-profit-prev">هامش الفترة السابقة ' +
        (d.prev_margin_pct == null ? "—" : num(d.prev_margin_pct, 1) + "%") + "</p>" +
        '<ul class="hx-bars">' +
        barRow("صافي المبيعات", d.sales, 100, "") +
        barRow("تكلفة المبيعات", d.cost, costPct, "is-muted") +
        barRow("مجمل الربح", d.profit, profitPct, "is-mid") +
        "</ul>" +
        '<p class="hx-note"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9V16h7v-2.1A6 6 0 0 0 12 3z"/></svg>' +
        "<span>كل 100 ر.س مبيعات تحقق " + num(profitPct, 1) + " ر.س مجمل ربح" +
        (trendWord ? "، و" + trendWord + " عن الفترة السابقة" : "") +
        ". لا يشمل المصروفات التشغيلية.</span></p>";
    },

    groupsPanel: function (card, d) {
      var rows = d.rows || [];
      if (!rows.length) {
        slot(card, "body").innerHTML = stateHtml("لا مبيعات مجموعات في هذه الفترة.");
        return;
      }
      var max = Math.max.apply(null, rows.map(function (r) { return Math.max(r.amount, r.prev_amount); }).concat([1]));
      var html = '<ul class="hx-cmp">' + rows.map(function (r) {
        return (
          '<li><span class="hx-cmp-name" title="' + esc(r.name) + '">' + esc(r.name) + "</span>" + chgHtml(r.change_pct) +
          '<span class="hx-cmp-bars"><span style="width:' + ((r.amount / max) * 100).toFixed(1) + '%"></span>' +
          '<span class="is-prev" style="width:' + ((r.prev_amount / max) * 100).toFixed(1) + '%"></span></span>' +
          '<span class="hx-cmp-val"><span>الحالية ' + esc(moneyText(r.amount)) + "</span><span>السابقة " +
          esc(moneyText(r.prev_amount)) + "</span></span></li>"
        );
      }).join("") + "</ul>";
      if (!d.exact) {
        html += '<p class="hx-foot-note">التوزيع تقريبي حتى يكتمل تجميع شهور المجموعات — الإجمالي مطابق للفروع.</p>';
      }
      slot(card, "body").innerHTML = html;
    },

    shortageBlock: function (card, d) {
      card.classList.toggle("is-critical", d.item_count > 0);
      var items = (d.top || []).map(function (t) {
        return "<li><span title=\"" + esc(t.name) + "\">" + esc(t.name) + "</span><span>بيع " + num(t.sold_qty) + "</span></li>";
      }).join("");
      card.innerHTML =
        '<div class="hx-alert-head"><span class="hx-alert-title">' +
        '<svg class="hx-ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4 2.5 20h19L12 4z"/><path d="M12 10v4M12 17h.01"/></svg>' +
        'نقص حرج</span><span class="hx-alert-count">' + num(d.item_count) + "</span></div>" +
        '<p class="hx-alert-desc">صنف بيع في نقاط البيع خلال آخر ' + num(d.lookback_days) +
        " أيام ورصيده صفر في كل المخازن — لا يغطيه تحويل.</p>" +
        (items ? '<ul class="hx-alert-items">' + items + "</ul>" : "");
      if (d.item_count > 0) bell();
    },

    recentPanel: function (card, d) {
      var rows = d.rows || [];
      if (!rows.length) {
        slot(card, "body").innerHTML = stateHtml("لا مستندات حديثة.");
        return;
      }
      var body = rows.map(function (r) {
        var time = r.at ? r.at.replace("T", " ").slice(5) : "";
        var value = r.amount != null ? esc(moneyFull(r.amount)) : num(r.qty) + " وحدة";
        return (
          '<tr><td><span class="hx-ref">' + esc(r.ref) + '</span><span class="hx-time" dir="ltr">' + esc(time) + "</span></td>" +
          '<td><span class="hx-type">' + esc(r.type_label) + "</span></td>" +
          "<td>" + esc(r.location) + "</td>" +
          '<td><span class="hx-status ' + (r.posted ? "is-posted" : "is-open") + '">' +
          (r.posted ? "مرحّل" : "غير مرحّل") + "</span></td>" +
          '<td class="hx-td-num">' + value + "</td></tr>"
        );
      }).join("");
      slot(card, "body").innerHTML =
        '<div class="hx-table-wrap"><table class="hx-table"><thead><tr>' +
        '<th scope="col">المرجع</th><th scope="col">النوع</th><th scope="col">الفرع / الجهة</th>' +
        '<th scope="col">الحالة</th><th scope="col" class="hx-td-num">القيمة</th>' +
        "</tr></thead><tbody>" + body + "</tbody></table></div>";
    },

    topItemsPanel: function (card, d) {
      var rows = d.rows || [];
      if (!rows.length) {
        slot(card, "body").innerHTML = stateHtml("لا مبيعات أصناف في هذه الفترة.");
        return;
      }
      slot(card, "body").innerHTML = '<ol class="hx-rank">' + rows.map(function (r) {
        return (
          '<li><span class="hx-rank-no">' + r.rank + "</span>" +
          '<span class="hx-rank-name" title="' + esc(r.name) + '">' + esc(r.name) + "</span>" +
          '<span class="hx-rank-amt"><span class="hx-num">' + esc(moneyText(r.amount)) + "</span>" + chgHtml(r.change_pct) + "</span>" +
          '<span class="hx-rank-meta"><bdi>' + esc(r.code) + "</bdi> · " + num(r.qty) + " وحدة</span></li>"
        );
      }).join("") + "</ol>";
    },
  };

  function barRow(label, value, pct, cls) {
    var w = Math.max(0, Math.min(100, Number(pct) || 0));
    return (
      '<li class="hx-bar-row"><span class="hx-bar-label">' + esc(label) + "</span>" +
      '<span class="hx-money hx-num">' + esc(moneyText(value)) + "</span>" +
      '<span class="hx-bar-track"><span class="hx-bar-fill ' + cls + '" style="width:' + w.toFixed(1) + '%"></span></span></li>'
    );
  }

  function bell() {
    var dot = root.querySelector("[data-hx-bell-dot]");
    if (dot) dot.hidden = false;
  }

  /* ── الاتجاه الشهري ────────────────────────────────────────────── */
  var trendHost = root.querySelector("[data-hx-trend]");
  var trendSeries = trendHost ? (trendHost.getAttribute("data-series") || "").split(",").filter(Boolean) : [];
  var trendPoints = {};
  var trendFailed = 0;

  function monthLabel(key) {
    var m = parseInt(key.slice(5, 7), 10);
    return MONTH_NAMES[m - 1] || key;
  }

  function seriesValues(name) {
    return MONTHS.map(function (m) {
      var p = trendPoints[m];
      return p && p[name] != null ? Number(p[name]) : null;
    });
  }

  function drawTrend() {
    if (!trendHost) return;
    var W = 640, H = 240, padT = 14, padB = 30, padL = 10, padR = 58;
    var plotW = W - padL - padR, plotH = H - padT - padB;
    var all = [];
    trendSeries.forEach(function (s) { all = all.concat(seriesValues(s).filter(function (v) { return v != null; })); });
    if (!all.length) {
      trendHost.innerHTML = stateHtml(trendFailed ? "تعذّر تحميل الأشهر." : "جارٍ تحميل الأشهر…", !!trendFailed);
      return;
    }
    var max = Math.max.apply(null, all) * 1.1 || 1;
    var niceStep = Math.pow(10, Math.floor(Math.log10(max / 4)));
    var step = Math.ceil(max / 4 / niceStep) * niceStep;
    var top = step * 4;
    var n = MONTHS.length;
    var dx = n > 1 ? plotW / (n - 1) : 0;
    var xAt = function (i) { return padL + plotW - i * dx; };
    var yAt = function (v) { return padT + plotH - (v / top) * plotH; };

    var svg = el("svg", { viewBox: "0 0 " + W + " " + H, preserveAspectRatio: "none" });
    var defs = el("defs");
    var grad = el("linearGradient", { id: "hx-trend-fill", x1: "0", y1: "0", x2: "0", y2: "1" });
    grad.appendChild(el("stop", { offset: "0%", "stop-color": "#117C77", "stop-opacity": "0.16" }));
    grad.appendChild(el("stop", { offset: "100%", "stop-color": "#117C77", "stop-opacity": "0" }));
    defs.appendChild(grad);
    svg.appendChild(defs);

    for (var g = 0; g <= 4; g++) {
      var gy = yAt(step * g);
      svg.appendChild(el("line", { x1: padL, x2: padL + plotW, y1: gy, y2: gy, class: "hx-trend-grid" }));
      var lbl = el("text", { x: W - 2, y: gy + 3.5, "text-anchor": "end", class: "hx-trend-axis" });
      var c = compact(step * g);
      lbl.textContent = g === 0 ? "0" : c.text + (c.unit === "مليون" ? "م" : c.unit === "ألف" ? "ك" : "");
      svg.appendChild(lbl);
    }
    MONTHS.forEach(function (m, i) {
      var t = el("text", { x: xAt(i), y: H - 8, "text-anchor": "middle", class: "hx-trend-axis" });
      t.textContent = monthLabel(m);
      svg.appendChild(t);
    });

    trendSeries.forEach(function (s) {
      var vals = seriesValues(s);
      var segs = [], cur = [];
      vals.forEach(function (v, i) {
        if (v == null) { if (cur.length) segs.push(cur); cur = []; return; }
        cur.push([xAt(i), yAt(v)]);
      });
      if (cur.length) segs.push(cur);
      segs.forEach(function (seg) {
        var d = seg.map(function (p, i) { return (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1); }).join(" ");
        if (s === "sales" && seg.length > 1) {
          var area = d + " L" + seg[seg.length - 1][0].toFixed(1) + " " + (padT + plotH) + " L" + seg[0][0].toFixed(1) + " " + (padT + plotH) + " Z";
          svg.appendChild(el("path", { d: area, class: "hx-trend-area" }));
        }
        svg.appendChild(el("path", { d: d, class: "hx-trend-line is-" + s }));
      });
      vals.forEach(function (v, i) {
        if (v == null) {
          svg.appendChild(el("circle", { cx: xAt(i), cy: padT + plotH, r: 3, class: "hx-trend-dot is-pending" }));
          return;
        }
        svg.appendChild(el("circle", { cx: xAt(i), cy: yAt(v), r: 3.6, class: "hx-trend-dot is-" + s }));
      });
    });

    var tip = document.createElement("div");
    tip.className = "hx-trend-tip";
    tip.hidden = true;
    MONTHS.forEach(function (m, i) {
      var hit = el("rect", {
        x: xAt(i) - dx / 2, y: 0, width: Math.max(dx, 20), height: H, fill: "transparent",
      });
      hit.addEventListener("mouseenter", function () {
        var p = trendPoints[m];
        if (!p) { tip.hidden = true; return; }
        var html = "<strong>" + esc(monthLabel(m)) + " " + m.slice(0, 4) + (p.partial ? " · حتى اليوم" : "") + "</strong>";
        if (p.sales != null) html += '<div><i style="background:#117C77"></i>المبيعات ' + esc(moneyText(p.sales)) + "</div>";
        if (p.purchases != null) html += '<div><i style="background:#C37A19"></i>المشتريات ' + esc(moneyText(p.purchases)) + "</div>";
        tip.innerHTML = html;
        tip.hidden = false;
        var rect = trendHost.getBoundingClientRect();
        var px = (xAt(i) / W) * rect.width;
        tip.style.left = Math.min(Math.max(px - tip.offsetWidth, 0), rect.width - tip.offsetWidth) + "px";
        tip.style.top = "0px";
        tip.style.transform = "none";
      });
      hit.addEventListener("mouseleave", function () { tip.hidden = true; });
      svg.appendChild(hit);
    });

    trendHost.innerHTML = "";
    trendHost.appendChild(svg);
    trendHost.appendChild(tip);
    trendHost.setAttribute(
      "aria-label",
      MONTHS.map(function (m) {
        var p = trendPoints[m] || {};
        return monthLabel(m) + ": " + trendSeries.map(function (s) {
          return (s === "sales" ? "مبيعات " : "مشتريات ") + (p[s] != null ? moneyText(p[s]) : "غير محمّل");
        }).join("، ");
      }).join(" | ")
    );

    trendSeries.forEach(function (s) {
      sparkline(root.querySelector('[data-spark="' + s + '"]'), seriesValues(s));
    });
  }

  function loadTrend() {
    if (!trendHost || !WIDGETS.has("trend")) return;
    MONTHS.slice().reverse().forEach(function (m) {
      enqueue(function () {
        return fetchWidget("trend", { month: m })
          .then(function (data) { trendPoints[m] = data; drawTrend(); })
          .catch(function () { trendFailed += 1; drawTrend(); });
      });
    });
  }

  /* ── تشغيل ─────────────────────────────────────────────────────── */
  function initHeader() {
    var now = new Date();
    var greet = root.querySelector("[data-hx-greeting]");
    if (greet) greet.textContent = now.getHours() < 12 ? "صباح الخير" : "مساء الخير";
    var today = root.querySelector("[data-hx-today]");
    if (today) {
      try {
        today.textContent = new Intl.DateTimeFormat("ar-SA-u-ca-gregory-nu-latn", {
          weekday: "long", day: "numeric", month: "long", year: "numeric",
        }).format(now);
      } catch (e) {
        today.textContent = now.toISOString().slice(0, 10);
      }
      today.setAttribute("datetime", now.toISOString().slice(0, 10));
    }
    var printBtn = root.querySelector("[data-hx-print]");
    if (printBtn) printBtn.addEventListener("click", function () { window.print(); });
  }

  function start() {
    initHeader();
    markLoading();
    // الأسرع أولاً حتى تمتلئ الشاشة بسرعة؛ الأثقل (الأصناف، الهامش) آخراً
    ["sales", "purchases", "inventory", "recent", "groups", "shortage"].forEach(function (name) {
      if (WIDGETS.has(name)) load(name);
    });
    loadTrend();
    if (WIDGETS.has("top_items")) load("top_items");
    if (WIDGETS.has("profit")) {
      // الدوران يعيد استخدام كاش الهامش — بعده لا بالتوازي معه
      load("profit", function () {
        if (WIDGETS.has("turnover")) load("turnover");
      });
    }
    if (WIDGETS.has("recent")) {
      window.setInterval(function () {
        if (document.visibilityState !== "visible") return;
        fetchWidget("recent").then(function (data) { dispatch("recent", data); }).catch(function () {});
      }, 60000);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
