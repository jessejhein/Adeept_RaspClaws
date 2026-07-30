/**
 * Lightweight health chips in the stock Hardware (StatusMod) group.
 * Polls /api/assembly/health?light=1 slowly so video/servos stay smooth.
 */
(function () {
  // Stock get_info is every 5s; we stay slower and use the light endpoint.
  var POLL_MS = 12000;
  var RETRY_MS = 4000;
  var timer = null;
  var chips = null;

  function apiBase() {
    return window.location.protocol + "//" + window.location.hostname + ":5000";
  }

  function ensureStyle() {
    if (document.getElementById("hardware-health-style")) return;
    var s = document.createElement("style");
    s.id = "hardware-health-style";
    s.textContent =
      ".status-wrapper .hw-health-chip{" +
      "width:100%;margin:.4rem 0 0 0!important;padding:0 4%;" +
      "display:flex;justify-content:center;align-items:center;" +
      "box-shadow:#000 -0rem 0rem .1rem 1px;border-radius:16px;" +
      "min-height:32px;font-size:13px;color:#fff;" +
      "background:#2e7d32;}" +
      ".status-wrapper .hw-health-chip b{padding-right:.3rem;}" +
      ".status-wrapper .hw-health-chip.warn{background:#ef6c00;}" +
      ".status-wrapper .hw-health-chip.bad{background:#c62828;font-weight:600;}" +
      ".status-wrapper .hw-health-chip.muted{background:#546e7a;}";
    document.head.appendChild(s);
  }

  function findStatusWrapper() {
    return document.querySelector(".status-wrapper");
  }

  function ensureChips(wrapper) {
    if (chips && chips.parentNode === wrapper) return chips;
    ensureStyle();
    chips = document.createElement("div");
    chips.className = "hw-health-extra";
    chips.setAttribute("data-hw-health", "1");
    chips.innerHTML =
      '<div class="v-chip ma-2 chips hw-health-chip muted" data-role="volt">' +
        "<b class=\"chip-title\">Core Volt</b><span data-val>—</span>" +
      "</div>" +
      '<div class="v-chip ma-2 chips hw-health-chip muted" data-role="power">' +
        "<b class=\"chip-title\">Power</b><span data-val>—</span>" +
      "</div>";
    wrapper.appendChild(chips);
    return chips;
  }

  function setChip(role, text, level) {
    if (!chips) return;
    var el = chips.querySelector('[data-role="' + role + '"]');
    if (!el) return;
    var val = el.querySelector("[data-val]");
    if (val) val.textContent = text;
    el.classList.remove("bad", "warn", "muted");
    if (level === "bad") el.classList.add("bad");
    else if (level === "warn") el.classList.add("warn");
    else if (level === "ok") { /* green default */ }
    else el.classList.add("muted");
  }

  function applyHealth(h) {
    if (!h) return;
    var th = h.throttled || {};
    var v = h.voltage_core_v;
    var voltText = (v != null) ? (Number(v).toFixed(3) + "V") : "n/a";
    var voltLevel = "ok";
    if (th.currently_undervolt || th.currently_throttled) voltLevel = "bad";
    else if (th.history_undervolt) voltLevel = "warn";
    setChip("volt", voltText, voltLevel);

    var bits = [];
    if (th.currently_undervolt) bits.push("UNDERVOLT");
    else if (th.currently_throttled) bits.push("THROTTLED");
    else if (th.history_undervolt) bits.push("UV earlier");
    else bits.push("OK");
    if (th.raw != null) bits.push(String(th.raw));
    var powerLevel = (th.currently_undervolt || th.currently_throttled)
      ? "bad"
      : (th.history_undervolt ? "warn" : "ok");
    setChip("power", bits.join(" "), powerLevel);

    if (chips) {
      chips.title = h.voltage_note ||
        "Core rail + undervolt flags (not battery pack). Slow poll to avoid video stutter.";
    }
  }

  async function poll() {
    var wrapper = findStatusWrapper();
    if (!wrapper) return;
    ensureChips(wrapper);
    try {
      var res = await fetch(apiBase() + "/api/assembly/health?light=1", {
        cache: "no-store"
      });
      var data = await res.json();
      if (data && data.ok !== false) applyHealth(data);
    } catch (e) {
      setChip("volt", "offline", "muted");
      setChip("power", "offline", "muted");
    }
  }

  function install() {
    var attempts = 0;
    var boot = window.setInterval(function () {
      attempts += 1;
      var wrapper = findStatusWrapper();
      if (wrapper) {
        window.clearInterval(boot);
        ensureChips(wrapper);
        poll();
        if (timer) window.clearInterval(timer);
        timer = window.setInterval(poll, POLL_MS);
        // One early refresh after first stock get_info cycle
        window.setTimeout(poll, RETRY_MS);
      } else if (attempts > 80) {
        window.clearInterval(boot);
      }
    }, 250);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", install);
  } else {
    install();
  }
}());
