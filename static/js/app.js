/* QueryMind front-end behaviour. No inline scripts (the CSP only allows this file and Bootstrap); untrusted values are
   only ever written with textContent / createTextNode, never as HTML. Nothing is stored in the browser. */
(function () {
  "use strict";

  function announce(form, message) {
    var live = form.querySelector("[data-qm-live]");
    if (live) { live.textContent = message; }
  }

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

  // Validation feedback and loading state for forms that declare data-qm-loading
  document.querySelectorAll("form[data-qm-loading]").forEach(function (form) {
    var button = form.querySelector('button[type="submit"]');
    var label = button && button.querySelector(".qm-btn-label");
    var original = label ? label.textContent : "";
    var icon = button && button.querySelector(".bi");
    var iconClass = icon ? icon.className : "";

    function reset() {
      if (!button) { return; }
      button.disabled = false;
      button.removeAttribute("aria-busy");
      if (label) { label.textContent = original; }
      if (icon) { icon.className = iconClass; }
      announce(form, "");
    }

    form.addEventListener("submit", function (event) {
      if (!form.checkValidity()) {
        event.preventDefault();
        form.classList.add("was-validated");
        var invalid = form.querySelector(":invalid");
        if (invalid) { invalid.focus(); }
        return;
      }
      var text = form.getAttribute("data-qm-loading").replace(/&hellip;/g, "…");
      if (button) {
        button.disabled = true;
        button.setAttribute("aria-busy", "true");
        if (label) { label.textContent = text; }
        if (icon) { icon.className = "spinner-border spinner-border-sm me-1"; }
      }
      announce(form, text);
    });
    window.addEventListener("pageshow", function (event) { if (event.persisted) { reset(); } });
  });

  // Ctrl/Cmd+Enter submits the question
  var question = document.getElementById("user_input");
  if (question) {
    question.addEventListener("keydown", function (event) {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        if (question.form.requestSubmit) { question.form.requestSubmit(); } else { question.form.submit(); }
      }
    });
    document.querySelectorAll("[data-qm-example]").forEach(function (chip) {
      chip.addEventListener("click", function () {
        question.value = chip.getAttribute("data-qm-example");
        question.focus();
      });
    });
  }

  // Copy generated SQL
  document.querySelectorAll("[data-qm-copy]").forEach(function (button) {
    var target = document.getElementById(button.getAttribute("data-qm-copy"));
    var label = button.querySelector(".qm-copy-label");
    var live = button.closest("section").querySelector("[data-qm-live]");
    function done(ok) {
      var message = ok ? "Copied" : "Copy failed";
      label.textContent = message;
      if (live) { live.textContent = ok ? "SQL copied to clipboard" : "Could not copy the SQL"; }
      window.setTimeout(function () { label.textContent = "Copy"; }, 2000);
    }
    function legacyCopy(text) {
      var area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.className = "visually-hidden";
      document.body.appendChild(area);
      area.select();
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
      document.body.removeChild(area);
      button.focus();
      return ok;
    }
    button.addEventListener("click", function () {
      var text = target.textContent;
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(function () { done(true); }, function () { done(legacyCopy(text)); });
      } else {
        done(legacyCopy(text));
      }
    });
  });

  // Table metadata dialog (Bootstrap modal; rows are built from text nodes)
  var modal = document.getElementById("table-modal");
  if (modal) {
    var title = document.getElementById("table-modal-title");
    var status = document.getElementById("table-modal-status");
    var body = document.getElementById("table-modal-body");

    function cell(row, text, className) {
      var td = document.createElement("td");
      if (className) { td.className = className; }
      td.textContent = text;
      row.appendChild(td);
      return td;
    }

    modal.addEventListener("show.bs.modal", function (event) {
      var name = event.relatedTarget.getAttribute("data-qm-table");
      title.textContent = name;
      body.textContent = "";
      status.textContent = "Loading…";
      status.hidden = false;
      fetch("/api/table/" + encodeURIComponent(name), { headers: { "Accept": "application/json" } })
        .then(function (response) { return response.json(); })
        .then(function (data) {
          if (data.error) { status.textContent = "Error: " + data.error; return; }
          status.hidden = true;
          data.columns.forEach(function (col) {
            var row = document.createElement("tr");
            cell(row, col.column, "font-monospace");
            cell(row, col.type, "font-monospace");
            cell(row, col.null);
            var keyCell = cell(row, "");
            if (col.key === "PRI" || col.key === "MUL" || col.key === "UNI") {
              var badge = document.createElement("span");
              badge.className = "qm-key";
              badge.textContent = col.key;
              keyCell.appendChild(badge);
            } else {
              keyCell.textContent = "-";
            }
            cell(row, col.default);
            body.appendChild(row);
          });
        })
        .catch(function () { status.textContent = "Failed to load the table's columns."; });
    });
  }
})();
