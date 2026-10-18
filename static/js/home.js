/* Home page: question shortcuts, example chips, and the table explorer (columns are fetched when a table is first opened). */
(function () {
  "use strict";

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

  var explorer = document.querySelector("[data-qm-explorer]");
  if (!explorer) { return; }

  // Filter long table lists
  var filter = document.querySelector("[data-qm-filter]");
  var status = document.querySelector("[data-qm-filter-status]");
  var noMatch = document.querySelector("[data-qm-no-match]");
  var items = Array.prototype.slice.call(explorer.querySelectorAll("[data-qm-item]"));
  if (filter) {
    filter.addEventListener("input", function () {
      var term = filter.value.trim().toLowerCase();
      var visible = 0;
      items.forEach(function (item) {
        var match = !term || item.getAttribute("data-qm-name").indexOf(term) !== -1;
        item.hidden = !match;
        if (match) { visible += 1; }
      });
      noMatch.hidden = visible !== 0;
      status.textContent = term ? visible + " of " + items.length + " tables shown" : "";
    });
  }

  // Lazy column loading
  function addColumn(list, col) {
    var li = document.createElement("li");
    var name = document.createElement("span");
    name.className = "qm-col-name";
    name.textContent = col.column;
    var type = document.createElement("span");
    type.className = "qm-col-type";
    type.textContent = col.type;
    li.appendChild(name);
    li.appendChild(type);
    if (col.key === "PRI" || col.key === "MUL" || col.key === "UNI") {
      var badge = document.createElement("span");
      badge.className = "qm-key";
      badge.textContent = col.key;
      li.appendChild(badge);
    }
    list.appendChild(li);
  }

  function message(list, text, linkHref, linkText) {
    list.textContent = "";
    var li = document.createElement("li");
    li.textContent = text + " ";
    if (linkHref) {
      var a = document.createElement("a");
      a.href = linkHref;
      a.textContent = linkText;
      li.appendChild(a);
    }
    list.appendChild(li);
  }

  explorer.querySelectorAll("[data-qm-table]").forEach(function (button) {
    var panel = document.querySelector(button.getAttribute("data-bs-target"));
    var list = panel.querySelector(".qm-columns");
    var loaded = false;
    panel.addEventListener("show.bs.collapse", function () {
      if (loaded) { return; }
      loaded = true;
      fetch("/api/table/" + encodeURIComponent(button.getAttribute("data-qm-table")), { headers: { "Accept": "application/json" } })
        .then(function (response) {
          if (response.status === 401) { throw new Error("expired"); }
          return response.json();
        })
        .then(function (data) {
          if (data.error) { loaded = false; message(list, "Could not load the columns."); return; }
          list.textContent = "";
          data.columns.forEach(function (col) { addColumn(list, col); });
        })
        .catch(function (error) {
          loaded = false;
          if (error && error.message === "expired") { message(list, "Your session expired.", "/login", "Sign in again"); }
          else { message(list, "Could not load the columns. Try again."); }
        });
    });
  });
})();
