(function () {
  var textReplacements = {
    "Adeept Bot Contorller": "Adeept Bot Controller",
    "Vedio": "Video",
    "Hard Ware": "Hardware",
    "PWM INIT SET": "PWM Setup",
    "FC Control": "Color Tracking",
    "Motion Get": "Motion Detect",
    "FAST/SLOW": "Fast / Slow",
    "POLICE LIGHT": "Police Light",
    "Track Line": "Line Tracking",
    "setPWM": "Set PWM",
    "Base Control": "Basic Controls",
    "Arm Control": "Camera Control",
    "About Us": "About Adeept",
    "Connect Failed": "Connection Failed",
    "Reconnecting": "Reconnecting"
  };

  var placeholderReplacements = {
    "Num Requier": "PWM Port"
  };

  var aboutText = "Adeept is a technical service team focused on open-source software and hardware. We apply internet and industrial technologies to open-source projects, providing hardware support and software services for makers and electronics enthusiasts around the world. We aim to create more possibilities through sharing and help bring ideas into reality.";

  function replaceTextNode(node) {
    var trimmed = node.nodeValue.trim();
    if (textReplacements[trimmed]) {
      node.nodeValue = node.nodeValue.replace(trimmed, textReplacements[trimmed]);
      return;
    }

    if (trimmed.indexOf("Adeept is a technical service team of open source software and hardware.") === 0) {
      node.nodeValue = node.nodeValue.replace(trimmed, aboutText);
    }
  }

  function polish(root) {
    var tree = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    var node;
    while ((node = tree.nextNode())) {
      replaceTextNode(node);
    }

    Object.keys(placeholderReplacements).forEach(function (placeholder) {
      document.querySelectorAll('[placeholder="' + placeholder + '"]').forEach(function (element) {
        element.setAttribute("placeholder", placeholderReplacements[placeholder]);
      });
    });
  }

  function install() {
    polish(document.body);

    var observer = new MutationObserver(function (mutations) {
      mutations.forEach(function (mutation) {
        mutation.addedNodes.forEach(function (node) {
          if (node.nodeType === Node.TEXT_NODE) {
            replaceTextNode(node);
          } else if (node.nodeType === Node.ELEMENT_NODE) {
            polish(node);
          }
        });
      });
    });

    observer.observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", install);
  } else {
    install();
  }
}());
