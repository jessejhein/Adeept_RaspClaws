(function () {
  function streamUrl() {
    return "/video_feed?fresh=" + Date.now();
  }

  function findComponent(component, name) {
    if (!component) {
      return null;
    }
    if (component.$options && component.$options.name === name) {
      return component;
    }
    for (var i = 0; i < (component.$children || []).length; i += 1) {
      var match = findComponent(component.$children[i], name);
      if (match) {
        return match;
      }
    }
    return null;
  }

  function disableLegacyCanvasStream(img) {
    var appElement = document.getElementById("app");
    var root = appElement && appElement.__vue__;
    var videoComponent = findComponent(root, "VedioMod");

    // The compiled component opens a new MJPEG Image 24 times per second.
    // Stop that detached-canvas timer so this <img> is the only connection.
    if (videoComponent && videoComponent.timmer) {
      window.clearInterval(videoComponent.timmer);
      videoComponent.timmer = null;
    }

    // A WebSocket reconnect otherwise calls setVedioTimmer() and recreates
    // the 24 Hz connection loop. Make reconnect refresh this one stream.
    if (root && root.$store) {
      root.$store.commit("changeSetVedioTimmer", function () {
        img.src = streamUrl();
      });
    }
  }

  function createMetrics() {
    var metrics = document.createElement("div");
    metrics.className = "video-metrics";
    metrics.setAttribute("aria-live", "off");
    metrics.innerHTML =
      '<span title="Camera frames encoded per second">FPS <b data-video-fps>--</b></span>' +
      '<span title="Estimated fresh-frame latency; browser video buffering is not measurable with native MJPEG">Latency est. <b data-video-latency>--</b></span>';
    return metrics;
  }

  function updateMetrics(metrics) {
    if (document.hidden || !metrics.isConnected) {
      return;
    }

    var requestStarted = window.performance.now();
    fetch("/video_status?fresh=" + Date.now(), { cache: "no-store" })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("Video status unavailable");
        }
        return response.json();
      })
      .then(function (status) {
        if (!status.ready) {
          return;
        }

        var roundTripMs = window.performance.now() - requestStarted;
        var estimatedLatencyMs = status.frame_age_ms + roundTripMs / 2;
        metrics.querySelector("[data-video-fps]").textContent =
          Number(status.fps || 0).toFixed(1);
        metrics.querySelector("[data-video-latency]").textContent =
          Math.round(estimatedLatencyMs) + " ms";
        metrics.title =
          "JPEG encode " + Number(status.encode_ms || 0).toFixed(1) +
          " ms · status round trip " + Math.round(roundTripMs) + " ms";
      })
      .catch(function () {
        metrics.querySelector("[data-video-fps]").textContent = "--";
        metrics.querySelector("[data-video-latency]").textContent = "--";
      });
  }

  function replaceCanvas(canvas) {
    if (!canvas) {
      return false;
    }

    if (canvas.dataset.mjpegFixed === "1") {
      return true;
    }

    var img = document.createElement("img");
    img.className = canvas.className + " mjpeg-stream";
    img.alt = "Camera stream";
    img.src = streamUrl();
    img.style.display = "block";
    img.style.width = "100%";
    img.style.height = "auto";
    var metrics = createMetrics();
    disableLegacyCanvasStream(img);

    // Recover from stalled/broken MJPEG (browser or Pi overload)
    img.onerror = function () {
      window.setTimeout(function () {
        img.src = streamUrl();
      }, 1500);
    };

    canvas.dataset.mjpegFixed = "1";
    canvas.parentNode.replaceChild(img, canvas);
    img.parentNode.appendChild(metrics);
    updateMetrics(metrics);
    window.setInterval(function () {
      updateMetrics(metrics);
    }, 2000);
    return true;
  }

  function install() {
    var attempts = 0;
    var timer = window.setInterval(function () {
      attempts += 1;
      var canvas = document.querySelector(".vedio-wrapper canvas");
      if (replaceCanvas(canvas) || attempts > 100) {
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
