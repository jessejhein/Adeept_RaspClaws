(function () {
  function streamUrl() {
    return window.location.protocol + "//" + window.location.hostname + ":5000/video_feed?rand=" + Date.now();
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

    // Recover from stalled/broken MJPEG (browser or Pi overload)
    img.onerror = function () {
      window.setTimeout(function () {
        img.src = streamUrl();
      }, 1500);
    };

    canvas.dataset.mjpegFixed = "1";
    canvas.parentNode.replaceChild(img, canvas);
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
