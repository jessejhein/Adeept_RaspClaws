(function () {
  var POLL_MS = 800;
  var panel = null;
  var pollTimer = null;
  var busy = false;

  function apiBase() {
    return window.location.protocol + "//" + window.location.hostname + ":5000";
  }

  function $(sel, root) {
    return (root || document).querySelector(sel);
  }

  async function api(path, opts) {
    var res = await fetch(apiBase() + path, Object.assign({
      headers: { "Content-Type": "application/json" }
    }, opts || {}));
    var data = null;
    try {
      data = await res.json();
    } catch (e) {
      data = { ok: false, error: "invalid JSON" };
    }
    if (!res.ok && data && !data.error) {
      data.error = "HTTP " + res.status;
    }
    return data;
  }

  function setStatus(msg, isErr) {
    var el = $("#assembly-status");
    if (!el) return;
    el.textContent = msg || "";
    el.className = "assembly-status" + (isErr ? " err" : "");
  }

  function jointLabel(joint) {
    if (joint === "shoulder") return "shoulder";
    if (joint === "knee") return "knee";
    if (joint === "pan") return "pan";
    if (joint === "tilt") return "tilt";
    return joint || "";
  }

  function renderMotors(motors) {
    var tbody = $("#assembly-motor-body");
    if (!tbody) return;
    var html = "";
    (motors || []).forEach(function (m) {
      if (!m.enabled) return;
      var lim = (m.min == null ? "—" : m.min) + " / " + (m.max == null ? "—" : m.max);
      var ch = (m.channel != null ? m.channel : m.id);
      var chNote = (ch !== m.id) ? (m.id + "→" + ch) : String(m.id);
      html += "<tr data-id=\"" + m.id + "\">" +
        "<td class=\"name\">" + escapeHtml(m.name) + "</td>" +
        "<td title=\"logical id → physical HAT port\">" + chNote + "</td>" +
        "<td>" + escapeHtml(jointLabel(m.joint)) + "</td>" +
        "<td class=\"center\">" + m.center + "</td>" +
        "<td class=\"current\">" + m.current + "</td>" +
        "<td class=\"deg\">" + m.degrees_from_center + "°</td>" +
        "<td class=\"lim\">" + lim + "</td>" +
        "<td class=\"actions\">" +
          "<button type=\"button\" class=\"btn-nudge\" data-id=\"" + m.id + "\" data-delta=\"-5\">−5</button>" +
          "<button type=\"button\" class=\"btn-nudge\" data-id=\"" + m.id + "\" data-delta=\"-1\">−1</button>" +
          "<button type=\"button\" class=\"btn-nudge\" data-id=\"" + m.id + "\" data-delta=\"1\">+1</button>" +
          "<button type=\"button\" class=\"btn-nudge\" data-id=\"" + m.id + "\" data-delta=\"5\">+5</button> " +
          "<button type=\"button\" class=\"btn-test\" data-id=\"" + m.id + "\">Test</button> " +
          "<button type=\"button\" class=\"btn-center\" data-id=\"" + m.id + "\" title=\"Set center to current position\">Set center</button>" +
        "</td>" +
        "</tr>";
    });
    tbody.innerHTML = html;
  }

  function renderLeds(leds) {
    var grid = $("#assembly-led-grid");
    if (!grid) return;
    var count = (leds && leds.count) || 0;
    var states = (leds && leds.states) || [];
    var html = "";
    for (var i = 0; i < count; i++) {
      var st = states[i] || { on: false, r: 0, g: 0, b: 0 };
      var bg = st.on ? ("rgb(" + st.r + "," + st.g + "," + st.b + ")") : "#222";
      html += "<div class=\"led-cell\" data-id=\"" + i + "\">" +
        "<div class=\"led-swatch\" style=\"background:" + bg + "\"></div>" +
        "<div class=\"led-label\">LED " + i + "</div>" +
        "<div class=\"led-btns\">" +
          "<button type=\"button\" data-led=\"" + i + "\" data-color=\"#ff0000\">R</button>" +
          "<button type=\"button\" data-led=\"" + i + "\" data-color=\"#00ff00\">G</button>" +
          "<button type=\"button\" data-led=\"" + i + "\" data-color=\"#0000ff\">B</button>" +
          "<button type=\"button\" data-led=\"" + i + "\" data-color=\"#ffffff\">W</button>" +
          "<button type=\"button\" data-led=\"" + i + "\" data-state=\"off\">Off</button>" +
        "</div></div>";
    }
    grid.innerHTML = html;
  }

  function renderHealth(th) {
    var el = $("#assembly-health");
    if (!el || !th) return;
    var bits = [];
    if (th.raw != null) bits.push("throttled=" + th.raw);
    if (th.currently_undervolt) bits.push("UNDERVOLT NOW");
    if (th.currently_throttled) bits.push("THROTTLED NOW");
    if (th.history_undervolt && !th.currently_undervolt) bits.push("undervolt earlier this boot");
    if (!bits.length) bits.push("power OK");
    el.textContent = bits.join(" · ");
    el.className = "assembly-health" +
      ((th.currently_undervolt || th.currently_throttled) ? " bad" : "");
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function refresh() {
    if (!panel || panel.classList.contains("hidden")) return;
    try {
      var st = await api("/api/assembly/status");
      if (!st.ok) {
        setStatus(st.error || "status failed", true);
        return;
      }
      renderMotors(st.motors);
      renderLeds(st.leds);
      setStatus("Updated " + new Date().toLocaleTimeString());
    } catch (e) {
      setStatus(String(e), true);
    }
    try {
      var h = await api("/api/assembly/health");
      if (h.ok) renderHealth(h.throttled);
    } catch (e2) { /* optional */ }
  }

  async function onOpen() {
    try {
      await api("/api/assembly/leds/pause_effects", { method: "POST", body: "{}" });
    } catch (e) { /* ignore */ }
    refresh();
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(refresh, POLL_MS);
  }

  function onClose() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  async function runAction(label, fn) {
    if (busy) {
      setStatus("Busy…", true);
      return;
    }
    busy = true;
    setStatus(label + "…");
    try {
      var data = await fn();
      if (!data || !data.ok) {
        setStatus((data && data.error) || (label + " failed"), true);
      } else {
        setStatus(label + " OK");
        refresh();
      }
    } catch (e) {
      setStatus(String(e), true);
    } finally {
      busy = false;
    }
  }

  function buildPanel() {
    if (panel) return panel;
    panel = document.createElement("div");
    panel.id = "assembly-panel";
    panel.className = "assembly-panel hidden";
    panel.innerHTML =
      "<div class=\"assembly-header\">" +
        "<strong>Assembly / Calibrate</strong>" +
        "<button type=\"button\" id=\"assembly-close\" aria-label=\"Close\">×</button>" +
      "</div>" +
      "<div id=\"assembly-health\" class=\"assembly-health\">power: …</div>" +
      "<div class=\"assembly-toolbar\">" +
        "<button type=\"button\" id=\"assembly-home\">Home all</button>" +
        "<button type=\"button\" id=\"assembly-test-shoulders\">Test shoulders</button>" +
        "<button type=\"button\" id=\"assembly-test-knees\">Test knees</button>" +
        "<button type=\"button\" id=\"assembly-save\">Save config</button>" +
        "<button type=\"button\" id=\"assembly-leds-off\">All LEDs off</button>" +
      "</div>" +
      "<div class=\"assembly-scroll\">" +
        "<h4>Motors</h4>" +
        "<table class=\"assembly-table\">" +
          "<thead><tr>" +
            "<th>Name</th><th>Port</th><th>Joint</th><th>Center</th><th>Current</th><th>Est. °</th><th>Min/Max</th><th>Nudge / Test</th>" +
          "</tr></thead>" +
          "<tbody id=\"assembly-motor-body\"></tbody>" +
        "</table>" +
        "<h4>LEDs</h4>" +
        "<div id=\"assembly-led-grid\" class=\"assembly-led-grid\"></div>" +
      "</div>" +
      "<div id=\"assembly-status\" class=\"assembly-status\"></div>";

    document.body.appendChild(panel);

    $("#assembly-close", panel).addEventListener("click", function () {
      panel.classList.add("hidden");
      onClose();
    });

    $("#assembly-home", panel).addEventListener("click", function () {
      runAction("Home all", function () {
        return api("/api/assembly/home", { method: "POST", body: "{}" });
      });
    });

    $("#assembly-test-shoulders", panel).addEventListener("click", function () {
      runAction("Test shoulders", function () {
        return api("/api/assembly/motors/test_group", {
          method: "POST",
          body: JSON.stringify({ group: "shoulders", cycles: 2, amplitude_pwm: 25 })
        });
      });
    });

    $("#assembly-test-knees", panel).addEventListener("click", function () {
      runAction("Test knees", function () {
        return api("/api/assembly/motors/test_group", {
          method: "POST",
          body: JSON.stringify({ group: "knees", cycles: 2, amplitude_pwm: 25 })
        });
      });
    });

    $("#assembly-save", panel).addEventListener("click", function () {
      runAction("Save config", function () {
        return api("/api/assembly/config/save", { method: "POST", body: "{}" });
      });
    });

    $("#assembly-leds-off", panel).addEventListener("click", function () {
      runAction("LEDs off", function () {
        return api("/api/assembly/leds/all", {
          method: "POST",
          body: JSON.stringify({ state: "off" })
        });
      });
    });

    panel.addEventListener("click", function (ev) {
      var t = ev.target;
      if (!t || !t.getAttribute) return;

      if (t.classList.contains("btn-nudge")) {
        var nid = t.getAttribute("data-id");
        var delta = parseInt(t.getAttribute("data-delta"), 10);
        runAction("Nudge " + nid + " " + (delta > 0 ? "+" : "") + delta, function () {
          return api("/api/assembly/motors/" + nid + "/nudge", {
            method: "POST",
            body: JSON.stringify({ delta: delta })
          });
        });
        return;
      }

      if (t.classList.contains("btn-test")) {
        var id = t.getAttribute("data-id");
        runAction("Test motor " + id, function () {
          return api("/api/assembly/motors/" + id + "/test", {
            method: "POST",
            body: JSON.stringify({ cycles: 3, amplitude_pwm: 30 })
          });
        });
        return;
      }

      if (t.classList.contains("btn-center")) {
        var id2 = t.getAttribute("data-id");
        runAction("Set center " + id2, function () {
          return api("/api/assembly/motors/" + id2 + "/center", {
            method: "POST",
            body: JSON.stringify({})
          });
        });
        return;
      }

      if (t.getAttribute("data-led") != null) {
        var led = t.getAttribute("data-led");
        var state = t.getAttribute("data-state");
        var color = t.getAttribute("data-color");
        var body = state === "off"
          ? { state: "off" }
          : { state: "on", color: color };
        runAction("LED " + led, function () {
          return api("/api/assembly/leds/" + led, {
            method: "POST",
            body: JSON.stringify(body)
          });
        });
      }
    });

    return panel;
  }

  function buildToggle() {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.id = "assembly-toggle";
    btn.className = "assembly-toggle";
    btn.textContent = "Assembly";
    btn.addEventListener("click", function () {
      var p = buildPanel();
      var opening = p.classList.contains("hidden");
      p.classList.toggle("hidden");
      if (opening) onOpen();
      else onClose();
    });
    document.body.appendChild(btn);
  }

  function install() {
    buildToggle();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", install);
  } else {
    install();
  }
}());
