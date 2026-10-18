/* Shared behaviour for every page: password toggle, form validation, loading state.
   No inline scripts (the CSP only allows this server's files). Untrusted values are never written as HTML,
   and nothing is stored in the browser. */
(function () {
  "use strict";

  // Password show/hide
  document.querySelectorAll("[data-qm-toggle-password]").forEach(function (button) {
    var input = document.getElementById(button.getAttribute("data-qm-toggle-password"));
    var icon = button.querySelector(".bi");
    button.addEventListener("click", function () {
      var show = input.type === "password";
      input.type = show ? "text" : "password";
      button.setAttribute("aria-pressed", String(show));
      button.setAttribute("aria-label", show ? "Hide password" : "Show password");
      icon.className = show ? "bi bi-eye-slash" : "bi bi-eye";
    });
  });

  // Forms that declare data-qm-loading: validation feedback, one submission at a time, processing state
  document.querySelectorAll("form[data-qm-loading]").forEach(function (form) {
    var button = form.querySelector('button[type="submit"]');
    var label = button && button.querySelector(".qm-btn-label");
    var icon = button && button.querySelector(".bi");
    var original = label ? label.textContent : "";
    var iconClass = icon ? icon.className : "";
    var panel = document.getElementById(form.getAttribute("data-qm-processing") || "");
    var panelText = panel && (panel.querySelector("[data-qm-processing-text]") || panel);
    var panelOriginal = panelText ? panelText.textContent : "";
    var slowTimer = null;
    var submitting = false;

    function reset() {
      submitting = false;
      window.clearTimeout(slowTimer);
      if (button) { button.disabled = false; button.removeAttribute("aria-busy"); }
      if (label) { label.textContent = original; }
      if (icon) { icon.className = iconClass; }
      if (panel) { panel.hidden = true; }
      if (panelText) { panelText.textContent = panelOriginal; }
    }

    form.addEventListener("submit", function (event) {
      if (submitting) { event.preventDefault(); return; }               // no duplicate submissions
      if (!form.checkValidity()) {
        event.preventDefault();
        form.classList.add("was-validated");
        var invalid = form.querySelector(":invalid");
        if (invalid) { invalid.focus(); }
        return;
      }
      submitting = true;
      var text = form.getAttribute("data-qm-loading").replace(/&hellip;/g, "…");
      if (button) { button.disabled = true; button.setAttribute("aria-busy", "true"); }
      if (label) { label.textContent = text; }
      if (icon) { icon.className = "spinner-border spinner-border-sm me-1"; }
      if (panel) {
        panel.hidden = false;
        if (panelText && !panelText.hasAttribute("data-qm-processing-text")) { panelText.textContent = text; }
        // no invented progress: after a while we only say that the work is still going on
        slowTimer = window.setTimeout(function () {
          if (panelText) { panelText.textContent = "Still working. Larger models and longer questions take more time; keep this page open."; }
        }, 45000);
      }
    });
    window.addEventListener("pageshow", function (event) { if (event.persisted) { reset(); } });
  });
})();
