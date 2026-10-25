/* فلتر المبيعات: فترات سريعة تضبط التاريخين وتطبّق، وتُظهر الفترة الحالية مفعّلة */
(function () {
  "use strict";
  var form = document.querySelector("[data-sales-filter]");
  if (!form) return;
  var from = form.querySelector('input[name="date_from"]');
  var to = form.querySelector('input[name="date_to"]');
  var chips = form.querySelectorAll("[data-range]");

  // تاريخ محلي بصيغة YYYY-MM-DD (toISOString يحسب بالتوقيت العالمي فيزحزح اليوم)
  function iso(d) {
    var m = String(d.getMonth() + 1).padStart(2, "0");
    var day = String(d.getDate()).padStart(2, "0");
    return d.getFullYear() + "-" + m + "-" + day;
  }
  function addDays(d, n) {
    var c = new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
    return c;
  }
  function rangeFor(key) {
    var t = new Date();
    t = new Date(t.getFullYear(), t.getMonth(), t.getDate());
    switch (key) {
      case "today": return [t, t];
      case "yesterday": var y = addDays(t, -1); return [y, y];
      case "week": return [addDays(t, -6), t];
      case "month": return [new Date(t.getFullYear(), t.getMonth(), 1), t];
      case "lastmonth":
        return [new Date(t.getFullYear(), t.getMonth() - 1, 1), new Date(t.getFullYear(), t.getMonth(), 0)];
      case "year": return [new Date(t.getFullYear(), 0, 1), t];
    }
    return null;
  }

  function markActive() {
    chips.forEach(function (chip) {
      var r = rangeFor(chip.getAttribute("data-range"));
      var on = !!r && iso(r[0]) === from.value && iso(r[1]) === to.value;
      chip.classList.toggle("is-on", on);
      chip.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  chips.forEach(function (chip) {
    chip.addEventListener("click", function () {
      var r = rangeFor(chip.getAttribute("data-range"));
      if (!r) return;
      from.value = iso(r[0]);
      to.value = iso(r[1]);
      markActive();
      if (form.requestSubmit) form.requestSubmit(); else form.submit();
    });
  });
  [from, to].forEach(function (el) { el.addEventListener("change", markActive); });
  markActive();
})();
