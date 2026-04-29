// firefliesclearer/web/static/app.js
(function () {
  function getCsrf() {
    const m = document.cookie.match(/ffc_csrf=([^;]+)/);
    return m ? m[1] : "";
  }

  // Heartbeat: POST /_alive every 10s using sendBeacon when possible.
  function ping() {
    const url = "/_alive";
    const csrf = getCsrf();
    const body = "_csrf=" + encodeURIComponent(csrf);
    if (navigator.sendBeacon) {
      // sendBeacon ignores Content-Type unless the body is a Blob with one.
      // Match the CSRF middleware's urlencoded parser (see security.py).
      navigator.sendBeacon(url, new Blob([body], { type: "application/x-www-form-urlencoded" }));
    } else {
      fetch(url, {
        method: "POST",
        body: body,
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
      });
    }
  }
  window.addEventListener("load", ping);
  setInterval(ping, 10000);

  // Quit button.
  document.addEventListener("click", function (e) {
    const btn = e.target.closest("[data-action='quit']");
    if (!btn) return;
    e.preventDefault();
    fetch("/_quit", { method: "POST", body: "_csrf=" + encodeURIComponent(getCsrf()), headers: { "Content-Type": "application/x-www-form-urlencoded" } });
    document.body.innerHTML = "<div style='padding:40px;text-align:center;font-family:sans-serif'>Server shutting down. You can close this tab.</div>";
  });

  // Side panel close on Esc.
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      const panel = document.querySelector(".side-panel.open");
      if (panel) panel.classList.remove("open");
    }
  });

  // Shift-click range select on tables with data-shift-select.
  document.addEventListener("click", function (e) {
    if (!e.shiftKey) return;
    const cb = e.target.closest("input[type='checkbox'][data-row-checkbox]");
    if (!cb) return;
    const table = cb.closest("table[data-shift-select]");
    if (!table) return;
    const all = Array.from(table.querySelectorAll("input[type='checkbox'][data-row-checkbox]"));
    const last = table.dataset.lastClickedIndex ? parseInt(table.dataset.lastClickedIndex, 10) : null;
    const idx = all.indexOf(cb);
    if (last !== null) {
      const [a, b] = [Math.min(last, idx), Math.max(last, idx)];
      for (let i = a; i <= b; i++) all[i].checked = cb.checked;
    }
    table.dataset.lastClickedIndex = idx;
  });
})();
