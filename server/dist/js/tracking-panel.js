/**
 * Face / hand tracking controls injected next to stock CV function UI.
 * Sends WebSocket commands: faceTrack, handTrack, nextTrackTarget, stopCV.
 */
(function () {
  "use strict";

  var panelId = "tracking-panel";
  var socket;
  var reconnectTimer;
  var statusText;
  var modeButtons = {};
  var activeMode = "none";

  function setStatus(text, bad) {
    if (!statusText) {
      return;
    }
    statusText.textContent = text;
    statusText.classList.toggle("bad", Boolean(bad));
  }

  function setConnected(ok) {
    Object.keys(modeButtons).forEach(function (key) {
      modeButtons[key].disabled = !ok;
    });
    var nextBtn = document.querySelector("#tracking-panel [data-cmd='nextTrackTarget']");
    var stopBtn = document.querySelector("#tracking-panel [data-cmd='stopCV']");
    if (nextBtn) {
      nextBtn.disabled = !ok;
    }
    if (stopBtn) {
      stopBtn.disabled = !ok;
    }
  }

  function highlightMode(mode) {
    activeMode = mode;
    ["faceTrack", "handTrack"].forEach(function (m) {
      var btn = modeButtons[m];
      if (!btn) {
        return;
      }
      btn.classList.toggle("active", mode === m);
    });
  }

  function connect() {
    if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
      return;
    }

    socket = new WebSocket("ws://" + window.location.hostname + ":8888");
    socket.onopen = function () {
      socket.send("admin:123456");
      setConnected(true);
      setStatus("Tracking ready", false);
    };
    socket.onclose = function () {
      setConnected(false);
      setStatus("Tracking connection unavailable", true);
      window.clearTimeout(reconnectTimer);
      reconnectTimer = window.setTimeout(connect, 2000);
    };
    socket.onerror = function () {
      setStatus("Tracking connection unavailable", true);
    };
  }

  function send(command) {
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      setStatus("Tracking connection unavailable", true);
      connect();
      return false;
    }
    socket.send(command);
    return true;
  }

  function makeModeButton(label, mode) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = "tracking-button";
    button.dataset.mode = mode;
    button.textContent = label;
    button.title = "Toggle " + label;
    button.addEventListener("click", function () {
      if (activeMode === mode) {
        if (send("stopCV")) {
          highlightMode("none");
          setStatus("CV stopped", false);
        }
        return;
      }
      if (send(mode)) {
        highlightMode(mode);
        setStatus(label + " on — pan/tilt only", false);
      }
    });
    modeButtons[mode] = button;
    return button;
  }

  function makeActionButton(label, command, className) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = className || "tracking-button";
    button.dataset.cmd = command;
    button.textContent = label;
    button.addEventListener("click", function () {
      if (!send(command)) {
        return;
      }
      if (command === "stopCV") {
        highlightMode("none");
        setStatus("CV stopped", false);
      } else if (command === "nextTrackTarget") {
        setStatus("Next target", false);
      }
    });
    return button;
  }

  function findFunctionMount() {
    var titles = Array.prototype.slice.call(document.querySelectorAll(".mod-title"));
    var preferred = [
      "FC Control",
      "Color Tracking",
      "Motion Get",
      "Motion Detect",
      "Track Line",
      "Line Tracking",
      "Function",
      "CVFL"
    ];
    var i;
    var t;
    for (i = 0; i < preferred.length; i++) {
      t = titles.find(function (title) {
        return title.textContent.trim() === preferred[i];
      });
      if (t && t.parentElement) {
        var wrap = t.parentElement.querySelector(".mod-wrapper") || t.parentElement;
        return wrap;
      }
    }
    // Fall back next to Move Control (same as gait-test panel).
    t = titles.find(function (title) {
      return title.textContent.trim() === "Move Control";
    });
    if (t && t.parentElement) {
      return t.parentElement.querySelector(".mod-wrapper") || t.parentElement;
    }
    return null;
  }

  function buildPanel() {
    if (document.getElementById(panelId)) {
      return true;
    }

    var target = findFunctionMount();
    if (!target) {
      return false;
    }

    var panel = document.createElement("section");
    panel.id = panelId;
    panel.className = "tracking-panel";
    panel.setAttribute("aria-labelledby", "tracking-panel-title");
    panel.innerHTML =
      "<div class=\"tracking-header\">" +
      "<h3 id=\"tracking-panel-title\">Face / Hand Tracking</h3>" +
      "<span class=\"tracking-note\">camera only</span>" +
      "</div>" +
      "<div class=\"tracking-controls\" data-role=\"modes\"></div>" +
      "<div class=\"tracking-controls\" data-role=\"actions\"></div>" +
      "<div class=\"tracking-footer\">" +
      "<span class=\"tracking-status\">Connecting…</span>" +
      "</div>";
    target.appendChild(panel);

    statusText = panel.querySelector(".tracking-status");
    var modes = panel.querySelector('[data-role="modes"]');
    var actions = panel.querySelector('[data-role="actions"]');
    modes.appendChild(makeModeButton("Face Track", "faceTrack"));
    modes.appendChild(makeModeButton("Hand Track", "handTrack"));
    actions.appendChild(makeActionButton("Next Target", "nextTrackTarget"));
    actions.appendChild(makeActionButton("Stop CV", "stopCV", "tracking-button tracking-stop"));

    setConnected(false);
    connect();
    return true;
  }

  function install() {
    var attempts = 0;
    var timer = window.setInterval(function () {
      attempts += 1;
      if (buildPanel() || attempts > 100) {
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
