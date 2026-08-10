(function () {
  "use strict";

  var socket;
  var reconnectTimer;
  var status;
  var danceButton;

  function setStatus(text, bad) {
    if (!status) return;
    status.textContent = text;
    status.classList.toggle("bad", Boolean(bad));
  }

  function setEnabled(enabled) {
    if (danceButton) danceButton.disabled = !enabled;
  }

  function connect() {
    if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) return;
    socket = new WebSocket("ws://" + window.location.hostname + ":8888");
    socket.onopen = function () {
      socket.send("admin:123456");
      setEnabled(true);
      setStatus("Dance controls ready", false);
    };
    socket.onclose = function () {
      setEnabled(false);
      setStatus("Dance connection unavailable", true);
      window.clearTimeout(reconnectTimer);
      reconnectTimer = window.setTimeout(connect, 2000);
    };
    socket.onerror = function () { setStatus("Dance connection unavailable", true); };
  }

  function send(command) {
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      setStatus("Dance connection unavailable", true);
      connect();
      return false;
    }
    socket.send(command);
    return true;
  }

  function buildPanel() {
    if (document.getElementById("dance-panel")) return true;
    var titles = Array.prototype.slice.call(document.querySelectorAll(".mod-title"));
    var moveTitle = titles.find(function (title) { return title.textContent.trim() === "Move Control"; });
    var target = moveTitle && moveTitle.parentElement.querySelector(".mod-wrapper");
    if (!target) return false;

    var panel = document.createElement("section");
    panel.id = "dance-panel";
    panel.className = "dance-panel";
    panel.innerHTML =
      "<div class=\"dance-header\"><h3>Dances &amp; Poses</h3><span>stationary</span></div>" +
      "<h4>Dance</h4>" +
      "<button type=\"button\" class=\"dance-button\">Leg tap round</button>" +
      "<p class=\"dance-description\">Front-left around to front-right, pause, then reverse.</p>" +
      "<div class=\"dance-footer\"><span class=\"dance-status\">Connecting...</span>" +
      "<button type=\"button\" class=\"dance-stop\">Stop</button></div>" +
      "<h4 class=\"pose-heading\">Poses</h4>" +
      "<div class=\"pose-controls\">" +
      "<button type=\"button\" data-pose=\"poseFront\">Front legs forward</button>" +
      "<button type=\"button\" data-pose=\"poseRear\">Rear legs back</button>" +
      "<button type=\"button\" data-pose=\"poseStable\">Stable hexagon</button>" +
      "</div><p class=\"pose-empty\">Poses center all other joints first.</p>";
    target.appendChild(panel);

    status = panel.querySelector(".dance-status");
    danceButton = panel.querySelector(".dance-button");
    danceButton.addEventListener("click", function () {
      if (send("danceLegTap")) setStatus("Running leg tap round", false);
    });
    panel.querySelector(".dance-stop").addEventListener("click", function () {
      if (send("danceStop")) setStatus("Stopping dance", false);
    });
	panel.querySelectorAll("[data-pose]").forEach(function (button) {
		button.addEventListener("click", function () {
			if (send(button.getAttribute("data-pose"))) setStatus("Applying " + button.textContent.toLowerCase(), false);
		});
	});
    setEnabled(false);
    connect();
    return true;
  }

  function install() {
    var attempts = 0;
    var timer = window.setInterval(function () {
      attempts += 1;
      if (buildPanel() || attempts > 100) window.clearInterval(timer);
    }, 100);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", install);
  else install();
}());
