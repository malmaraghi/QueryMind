/* Results page: SQL syntax highlighting (built from text nodes, never HTML) and the copy button. */
(function () {
  "use strict";

  var KEYWORDS = ("select from where group by order having limit offset join left right inner outer full cross on as and or not in is null like " +
    "between exists distinct union all case when then else end asc desc with over partition count sum avg min max").split(" ");
  var TOKEN = /(--[^\n]*|#[^\n]*|\/\*[\s\S]*?\*\/)|('(?:[^'\\]|\\.|'')*')|("(?:[^"\\]|\\.)*")|(\b\d+(?:\.\d+)?\b)|(\b[A-Za-z_]+\b)/g;

  var code = document.querySelector("[data-qm-sql]");
  if (code) {
    var source = code.textContent;
    var fragment = document.createDocumentFragment();
    var last = 0;
    var match;

    function span(className, text) {
      var node = document.createElement("span");
      node.className = className;
      node.textContent = text;
      return node;
    }

    TOKEN.lastIndex = 0;
    while ((match = TOKEN.exec(source)) !== null) {
      var kind = null;
      if (match[1]) { kind = "qm-cmt"; }
      else if (match[2] || match[3]) { kind = "qm-str"; }
      else if (match[4]) { kind = "qm-num"; }
      else if (match[5] && KEYWORDS.indexOf(match[5].toLowerCase()) !== -1) { kind = "qm-kw"; }
      if (!kind) { continue; }
      if (match.index > last) { fragment.appendChild(document.createTextNode(source.slice(last, match.index))); }
      fragment.appendChild(span(kind, match[0]));
      last = match.index + match[0].length;
    }
    if (last < source.length) { fragment.appendChild(document.createTextNode(source.slice(last))); }
    code.textContent = "";
    code.appendChild(fragment);               // textContent of <code> is unchanged, so copying still yields the exact SQL
  }

  document.querySelectorAll("[data-qm-copy]").forEach(function (button) {
    var target = document.getElementById(button.getAttribute("data-qm-copy"));
    var label = button.querySelector(".qm-copy-label");
    var live = button.closest("section").querySelector("[data-qm-live]");
    function done(ok) {
      label.textContent = ok ? "Copied" : "Copy failed";
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
})();
