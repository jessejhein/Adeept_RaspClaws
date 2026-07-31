/**
 * Virtual camera joystick: drag the knob in a circle for proportional pan/tilt.
 * Replaces the stock Up/Down/Left/Right head pad in Arm / Camera Control.
 *
 * WebSocket: headJoy <nx> <ny>  (each -1..1), headJoyStop on release.
 * nx>0 look right, ny>0 look up. Distance from center controls speed.
 */
(function () {
  "use strict";

  var panelId = "head-joystick-panel";
  var socket;
  var reconnectTimer;
  var pad;
  var knob;
  var statusEl;
  var dragging = false;
  var activePointerId = null;
  var lastSend = 0;
  var lastNx = 0;
  var lastNy = 0;
  var SEND_MS = 40;
  var DEADZONE = 0.08;

  function connect() {
    if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
      return;
    }
    socket = new WebSocket("ws://" + window.location.hostname + ":8888");
    socket.onopen = function () {
      socket.send("admin:123456");
      setStatus("Joystick ready");
    };
    socket.onclose = function () {
      setStatus("Joystick offline");
      window.clearTimeout(reconnectTimer);
      reconnectTimer = window.setTimeout(connect, 2000);
    };
    socket.onerror = function () {
      setStatus("Joystick offline");
    };
  }

  function setStatus(text) {
    if (statusEl) {
      statusEl.textContent = text;
    }
  }

  function send(cmd) {
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      connect();
      return false;
    }
    socket.send(cmd);
    return true;
  }

  function sendJoy(nx, ny, force) {
    var now = Date.now();
    if (!force && now - lastSend < SEND_MS) {
      return;
    }
    // Quantize a bit to cut chatter.
    var qx = Math.round(nx * 100) / 100;
    var qy = Math.round(ny * 100) / 100;
    if (!force && qx === lastNx && qy === lastNy) {
      return;
    }
    lastNx = qx;
    lastNy = qy;
    lastSend = now;
    send("headJoy " + qx + " " + qy);
  }

  function stopJoy() {
    lastNx = 0;
    lastNy = 0;
    send("headJoyStop");
    setKnob(0, 0);
  }

  function setKnob(nx, ny) {
    if (!pad || !knob) {
      return;
    }
    var rect = pad.getBoundingClientRect();
    var radius = Math.min(rect.width, rect.height) / 2;
    var maxTravel = radius * 0.72;
    var x = nx * maxTravel;
    var y = -ny * maxTravel; // screen y is down; ny up is negative screen offset
    knob.style.transform = "translate(" + x + "px," + y + "px)";
  }

  function eventToNorm(clientX, clientY) {
    var rect = pad.getBoundingClientRect();
    var cx = rect.left + rect.width / 2;
    var cy = rect.top + rect.height / 2;
    var radius = Math.min(rect.width, rect.height) / 2;
    var maxTravel = radius * 0.72;
    var dx = clientX - cx;
    var dy = clientY - cy;
    var dist = Math.sqrt(dx * dx + dy * dy) || 1;
    if (dist > maxTravel) {
      dx = (dx / dist) * maxTravel;
      dy = (dy / dist) * maxTravel;
    }
    var nx = dx / maxTravel;
    var ny = -dy / maxTravel;
    if (Math.abs(nx) < DEADZONE && Math.abs(ny) < DEADZONE) {
      return { nx: 0, ny: 0 };
    }
    // Clamp magnitude to 1 (already limited by maxTravel).
    nx = Math.max(-1, Math.min(1, nx));
    ny = Math.max(-1, Math.min(1, ny));
    return { nx: nx, ny: ny };
  }

  function onPointerDown(e) {
    dragging = true;
    activePointerId = e.pointerId;
    try {
      pad.setPointerCapture(e.pointerId);
    } catch (err) {
      /* ignore */
    }
    e.preventDefault();
    var n = eventToNorm(e.clientX, e.clientY);
    setKnob(n.nx, n.ny);
    sendJoy(n.nx, n.ny, true);
    setStatus("Moving");
  }

  function onPointerMove(e) {
    if (!dragging || e.pointerId !== activePointerId) {
      return;
    }
    e.preventDefault();
    var n = eventToNorm(e.clientX, e.clientY);
    setKnob(n.nx, n.ny);
    sendJoy(n.nx, n.ny, false);
  }

  function onPointerUp(e) {
    if (e.pointerId !== activePointerId && activePointerId !== null) {
      return;
    }
    dragging = false;
    activePointerId = null;
    stopJoy();
    setStatus("Joystick ready");
  }

  function hideStockPad(root) {
    if (!root) {
      return;
    }
    var labels = ["Up", "Down", "Left", "Right"];
    root.querySelectorAll("button, .v-btn").forEach(function (btn) {
      var text = (btn.textContent || "").replace(/\s+/g, " ").trim();
      if (labels.indexOf(text) !== -1) {
        btn.style.display = "none";
        btn.setAttribute("aria-hidden", "true");
        // Hide empty grid cell chrome if the parent only holds this button.
        var cell = btn.closest(".col, .v-col, .flex, .layout") || btn.parentElement;
        if (cell && cell !== root) {
          var otherVisible = false;
          cell.querySelectorAll("button, .v-btn").forEach(function (other) {
            if (other !== btn && other.style.display !== "none") {
              otherVisible = true;
            }
          });
          if (!otherVisible) {
            cell.style.display = "none";
          }
        }
      }
    });
  }

  function findArmMount() {
    var titles = Array.prototype.slice.call(document.querySelectorAll(".mod-title"));
    var preferred = ["Camera Control", "Arm Control"];
    var i;
    for (i = 0; i < preferred.length; i++) {
      var title = titles.find(function (el) {
        return el.textContent.trim() === preferred[i];
      });
      if (title && title.parentElement) {
        return title.parentElement.querySelector(".mod-wrapper") || title.parentElement;
      }
    }
    return null;
  }

  function buildPanel() {
    if (document.getElementById(panelId)) {
      return true;
    }
    var mount = findArmMount();
    if (!mount) {
      return false;
    }

    hideStockPad(mount);

    var panel = document.createElement("section");
    panel.id = panelId;
    panel.className = "head-joystick-panel";
    panel.innerHTML =
      "<div class=\"head-joystick-header\">" +
      "<h3>Camera stick</h3>" +
      "<span class=\"head-joystick-hint\">drag · farther = faster</span>" +
      "</div>" +
      "<div class=\"head-joystick-pad\" role=\"application\" aria-label=\"Camera pan tilt joystick\" tabindex=\"0\">" +
      "<div class=\"head-joystick-ring\"></div>" +
      "<div class=\"head-joystick-cross\"></div>" +
      "<div class=\"head-joystick-knob\"></div>" +
      "</div>" +
      "<div class=\"head-joystick-footer\">" +
      "<span class=\"head-joystick-status\">Connecting…</span>" +
      "<button type=\"button\" class=\"head-joystick-center\">Center stop</button>" +
      "</div>";

    mount.appendChild(panel);

    pad = panel.querySelector(".head-joystick-pad");
    knob = panel.querySelector(".head-joystick-knob");
    statusEl = panel.querySelector(".head-joystick-status");

    pad.addEventListener("pointerdown", onPointerDown);
    pad.addEventListener("pointermove", onPointerMove);
    pad.addEventListener("pointerup", onPointerUp);
    pad.addEventListener("pointercancel", onPointerUp);
    pad.addEventListener("lostpointercapture", onPointerUp);

    panel.querySelector(".head-joystick-center").addEventListener("click", function () {
      stopJoy();
      setStatus("Stopped");
    });

    // Re-hide stock buttons if Vue re-renders them.
    var observer = new MutationObserver(function () {
      hideStockPad(mount);
    });
    observer.observe(mount, { childList: true, subtree: true });

    setKnob(0, 0);
    connect();
    return true;
  }

  function install() {
    var attempts = 0;
    var timer = window.setInterval(function () {
      attempts += 1;
      if (buildPanel() || attempts > 120) {
        window.clearInterval(timer);
      }
    }, 100);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", install);
  } else {
    install();
  }
}());
