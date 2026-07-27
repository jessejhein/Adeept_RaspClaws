(function () {
  // Keep this slow: aggressive polling starved camera MJPEG + WS281x on the Pi.
  var POLL_MS = 3000;
  var panel = null;
  var pollTimer = null;
  var busy = false;
  var lastMotorSig = "";
  var lastLedStruct = "";

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

  function renderPatterns(leds) {
    var bar = $("#assembly-pattern-bar");
    if (!bar) return;
    var patterns = (leds && leds.patterns) || [];
    var active = (leds && leds.pattern) || "";
    if (!bar.dataset.built) {
      var html = "<span class=\"pattern-label\">Patterns:</span> ";
      patterns.forEach(function (p) {
        html += "<button type=\"button\" class=\"btn-pattern\" data-pattern=\"" +
          escapeHtml(p.id) + "\" title=\"" + escapeHtml(p.note || p.label) + "\">" +
          escapeHtml(p.label) + "</button> ";
      });
      bar.innerHTML = html;
      bar.dataset.built = "1";
    }
    bar.querySelectorAll(".btn-pattern").forEach(function (btn) {
      btn.classList.toggle("active", btn.getAttribute("data-pattern") === active);
    });
  }

  function renderLeds(leds) {
    var grid = $("#assembly-led-grid");
    if (!grid) return;
    var count = (leds && leds.count) || 0;
    var states = (leds && leds.states) || [];
    renderPatterns(leds);

    var struct = count + "|" + states.map(function (s) {
      return (s.id + ":" + (s.name || ""));
    }).join(",");

    if (struct !== lastLedStruct) {
      lastLedStruct = struct;
      var html = "";
      for (var i = 0; i < count; i++) {
        var st = states[i] || {
          on: false, r: 0, g: 0, b: 0, brightness: 255,
          name: "led_" + i, group: ""
        };
        var br = (st.brightness != null) ? st.brightness : 255;
        var label = st.name ? st.name : ("LED " + i);
        var group = st.group ? (" · " + st.group) : "";
        html += "<div class=\"led-cell\" data-id=\"" + i + "\">" +
          "<div class=\"led-swatch\" data-swatch></div>" +
          "<div class=\"led-label\" title=\"#" + i + group + "\">#" + i + " " +
            escapeHtml(label) + "</div>" +
          "<div class=\"led-btns\">" +
            "<button type=\"button\" data-led=\"" + i + "\" data-color=\"#ff0000\">R</button>" +
            "<button type=\"button\" data-led=\"" + i + "\" data-color=\"#00ff00\">G</button>" +
            "<button type=\"button\" data-led=\"" + i + "\" data-color=\"#0000ff\">B</button>" +
            "<button type=\"button\" data-led=\"" + i + "\" data-color=\"#ffffff\">W</button>" +
            "<button type=\"button\" data-led=\"" + i + "\" data-state=\"off\">Off</button>" +
          "</div>" +
          "<div class=\"led-bright\">" +
            "<button type=\"button\" class=\"btn-br\" data-led=\"" + i + "\" data-delta=\"-10\">−</button>" +
            "<input type=\"range\" class=\"led-slider\" data-led=\"" + i +
              "\" min=\"0\" max=\"255\" value=\"" + br + "\" />" +
            "<button type=\"button\" class=\"btn-br\" data-led=\"" + i + "\" data-delta=\"10\">+</button>" +
            "<span class=\"led-br-val\" data-led=\"" + i + "\">" + br + "</span>" +
          "</div></div>";
      }
      grid.innerHTML = html;
    }

    // Live update swatches / brightness without killing sliders in use
    for (var j = 0; j < count; j++) {
      var st2 = states[j] || { on: false, r: 0, g: 0, b: 0, brightness: 255 };
      var cell = grid.querySelector('.led-cell[data-id="' + j + '"]');
      if (!cell) continue;
      var sw = cell.querySelector("[data-swatch]");
      if (sw) {
        var scale = ((st2.brightness != null) ? st2.brightness : 255) / 255;
        var rr = st2.on ? Math.round(st2.r * scale) : 0;
        var gg = st2.on ? Math.round(st2.g * scale) : 0;
        var bb = st2.on ? Math.round(st2.b * scale) : 0;
        sw.style.background = st2.on ? ("rgb(" + rr + "," + gg + "," + bb + ")") : "#222";
      }
      var slider = cell.querySelector(".led-slider");
      var valEl = cell.querySelector(".led-br-val");
      var br2 = (st2.brightness != null) ? st2.brightness : 255;
      if (slider && document.activeElement !== slider) {
        slider.value = String(br2);
      }
      if (valEl && !(slider && document.activeElement === slider)) {
        valEl.textContent = String(br2);
      }
    }
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
      // Avoid rewriting the whole table unless values changed (saves browser + flash).
      var sig = JSON.stringify(st.motors || []);
      if (sig !== lastMotorSig) {
        lastMotorSig = sig;
        renderMotors(st.motors);
      } else {
        // light update of current/deg cells only
        (st.motors || []).forEach(function (m) {
          if (!m.enabled) return;
          var row = panel.querySelector('tr[data-id="' + m.id + '"]');
          if (!row) return;
          var cur = row.querySelector(".current");
          var deg = row.querySelector(".deg");
          var cen = row.querySelector(".center");
          if (cur) cur.textContent = m.current;
          if (deg) deg.textContent = m.degrees_from_center + "°";
          if (cen) cen.textContent = m.center;
        });
      }
      renderLeds(st.leds);
      if (st.throttled) renderHealth(st.throttled);
      setStatus("Updated " + new Date().toLocaleTimeString());
    } catch (e) {
      setStatus(String(e), true);
    }
  }

  async function onOpen() {
    // Stop breath so pixel tests are visible; do NOT clear strip to black.
    try {
      await api("/api/assembly/leds/pause_effects", {
        method: "POST",
        body: JSON.stringify({ clear: false })
      });
    } catch (e) { /* ignore */ }
    lastMotorSig = "";
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
        "<button type=\"button\" id=\"assembly-leds-breath\">Resume breath</button>" +
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
        "<div id=\"assembly-pattern-bar\" class=\"assembly-pattern-bar\"></div>" +
        "<p class=\"assembly-led-hint\">Brightness 0–255 (decimal). Interior LEDs are not line-of-sight — " +
          "use <em>Identify groups</em> / <em>Interior scan</em> (face panel echoes the zone).</p>" +
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

    $("#assembly-leds-breath", panel).addEventListener("click", function () {
      runAction("Resume breath", function () {
        return api("/api/assembly/leds/pattern", {
          method: "POST",
          body: JSON.stringify({ name: "breath" })
        });
      });
    });

    panel.addEventListener("input", function (ev) {
      var t = ev.target;
      if (!t || !t.classList || !t.classList.contains("led-slider")) return;
      var led = t.getAttribute("data-led");
      var br = parseInt(t.value, 10);
      var valEl = panel.querySelector('.led-br-val[data-led="' + led + '"]');
      if (valEl) valEl.textContent = String(br);
    });

    panel.addEventListener("change", function (ev) {
      var t = ev.target;
      if (!t || !t.classList || !t.classList.contains("led-slider")) return;
      var led = t.getAttribute("data-led");
      var br = parseInt(t.value, 10);
      runAction("Brightness " + led + " → " + br, function () {
        return api("/api/assembly/leds/" + led + "/brightness", {
          method: "POST",
          body: JSON.stringify({ brightness: br })
        });
      });
    });

    panel.addEventListener("click", function (ev) {
      var t = ev.target;
      if (!t || !t.getAttribute) return;

      if (t.classList.contains("btn-pattern")) {
        var pname = t.getAttribute("data-pattern");
        runAction("Pattern " + pname, function () {
          return api("/api/assembly/leds/pattern", {
            method: "POST",
            body: JSON.stringify({ name: pname })
          });
        });
        return;
      }

      if (t.classList.contains("btn-br")) {
        var bled = t.getAttribute("data-led");
        var bdelta = parseInt(t.getAttribute("data-delta"), 10);
        runAction("Brightness " + bled + " " + (bdelta > 0 ? "+" : "") + bdelta, function () {
          return api("/api/assembly/leds/" + bled + "/brightness", {
            method: "POST",
            body: JSON.stringify({ delta: bdelta })
          });
        });
        return;
      }

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
