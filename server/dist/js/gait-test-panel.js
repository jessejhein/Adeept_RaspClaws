(function () {
  "use strict";

  var panelId = "gait-test-panel";
  var socket;
  var reconnectTimer;
  var durationInput;
  var durationValue;
  var statusText;
  var testButtons;

  function setStatus(text, bad) {
    if (!statusText) {
      return;
    }
    statusText.textContent = text;
    statusText.classList.toggle("bad", Boolean(bad));
  }

  function setButtonsEnabled(enabled) {
    if (!testButtons) {
      return;
    }
    testButtons.forEach(function (button) {
      button.disabled = !enabled;
    });
  }

  function connect() {
    if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
      return;
    }

    socket = new WebSocket("ws://" + window.location.hostname + ":8888");
    socket.onopen = function () {
      socket.send("admin:123456");
      setButtonsEnabled(true);
      setStatus("Test connection ready", false);
    };
    socket.onclose = function () {
      setButtonsEnabled(false);
      setStatus("Test connection unavailable", true);
      window.clearTimeout(reconnectTimer);
      reconnectTimer = window.setTimeout(connect, 2000);
    };
    socket.onerror = function () {
      setStatus("Test connection unavailable", true);
    };
  }

  function send(command) {
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      setStatus("Test connection unavailable", true);
      connect();
      return false;
    }
    socket.send(command);
    return true;
  }

  function makeButton(label, direction) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = "gait-test-button";
    button.dataset.direction = direction;
    button.innerHTML = "<span aria-hidden=\"true\">" + label[0] + "</span> " + label[1];
    button.title = "Run a short " + label[1].toLowerCase() + " gait test";
    button.addEventListener("click", function () {
      var duration = Number(durationInput.value);
      if (send("gaitTest " + direction + " " + duration)) {
        setStatus("Running " + label[1].toLowerCase() + " test", false);
      }
    });
    return button;
  }

  function buildPanel() {
    if (document.getElementById(panelId)) {
      return true;
    }

    var titles = Array.prototype.slice.call(document.querySelectorAll(".mod-title"));
    var moveTitle = titles.find(function (title) {
      return title.textContent.trim() === "Move Control";
    });
    var target = moveTitle && moveTitle.parentElement.querySelector(".mod-wrapper");
    if (!target) {
      return false;
    }

    var panel = document.createElement("section");
    panel.id = panelId;
    panel.className = "gait-test-panel";
    panel.setAttribute("aria-labelledby", "gait-test-title");
    panel.innerHTML =
      "<div class=\"gait-test-header\"><h3 id=\"gait-test-title\">Walking Test</h3>" +
      "<span class=\"gait-test-warning\">short tap</span></div>" +
      "<div class=\"gait-test-controls\"></div>" +
      "<label class=\"gait-test-duration\" for=\"gait-test-duration-input\">" +
      "Tap duration <output id=\"gait-test-duration-value\">700 ms</output></label>" +
      "<input id=\"gait-test-duration-input\" type=\"range\" min=\"300\" max=\"1200\" step=\"50\" value=\"700\">" +
      "<div class=\"gait-test-footer\"><span class=\"gait-test-status\">Connecting...</span>" +
      "<button type=\"button\" class=\"gait-test-stop\">Stop</button></div>";
    target.appendChild(panel);

    durationInput = panel.querySelector("#gait-test-duration-input");
    durationValue = panel.querySelector("#gait-test-duration-value");
    statusText = panel.querySelector(".gait-test-status");
    testButtons = [
      makeButton(["↑", "Forward"], "forward"),
      makeButton(["↓", "Backward"], "backward"),
      makeButton(["←", "Left"], "left"),
      makeButton(["→", "Right"], "right")
    ];
    var controls = panel.querySelector(".gait-test-controls");
    testButtons.forEach(function (button) {
      controls.appendChild(button);
    });
    panel.querySelector(".gait-test-stop").addEventListener("click", function () {
      if (send("gaitTestStop")) {
        setStatus("Stopped", false);
      }
    });
    durationInput.addEventListener("input", function () {
      durationValue.textContent = durationInput.value + " ms";
    });
    setButtonsEnabled(false);
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
