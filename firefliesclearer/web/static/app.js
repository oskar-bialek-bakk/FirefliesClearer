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
      const panel = document.querySelector(".side-panel:not([hidden])");
      if (panel) panel.setAttribute("hidden", "");
    }
  });

  // HTMX error feedback: by default, htmx ignores 4xx/5xx responses and
  // performs no swap, which makes failed requests look like the button "did
  // nothing" (user-reported on the dashboard retry buttons after they hit
  // 429s, 2026-05-03). Surface the error inline so the user sees what
  // happened — read FastAPI's ``detail`` from the JSON body when present,
  // fall back to a generic message keyed on status.
  document.addEventListener("htmx:beforeSwap", function (evt) {
    var xhr = evt.detail.xhr;
    if (xhr.status >= 400) {
      var msg = "Request failed (" + xhr.status + ")";
      try {
        var parsed = JSON.parse(xhr.responseText || "{}");
        if (parsed && parsed.detail) msg = parsed.detail;
      } catch (e) {
        if (xhr.responseText && xhr.responseText.length < 200) msg = xhr.responseText;
      }
      if (xhr.status === 429) {
        msg = "Rate limited by Fireflies. " + msg;
      }
      // Wrap in a small inline-error fragment matching the swap target's
      // expected shape (best-effort; works for `outerHTML` swaps).
      evt.detail.shouldSwap = true;
      evt.detail.serverResponse =
        '<div class="inline-error" role="alert" style="color:var(--hot,#c33);padding:6px 10px;border:1px solid var(--hot,#c33);border-radius:4px;font-size:13px">' +
        msg.replace(/[<&]/g, function (c) { return c === "<" ? "&lt;" : "&amp;"; }) +
        "</div>";
    }
  });

  // Sidebar active-nav: server renders aria-current on the initial full page,
  // but HTMX swaps only #page so the sidebar stays stale. Re-evaluate on every
  // HTMX settle and on browser back/forward.
  function updateActiveNav() {
    const path = window.location.pathname;
    document.querySelectorAll(".sidebar nav a[href]").forEach(function (a) {
      const href = a.getAttribute("href");
      const isActive = href === "/" ? path === "/" : path === href || path.startsWith(href + "/");
      if (isActive) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });
  }
  document.addEventListener("htmx:afterSettle", updateActiveNav);
  document.addEventListener("htmx:pushedIntoHistory", updateActiveNav);
  window.addEventListener("popstate", updateActiveNav);

  // Theme toggle: dark <-> light, persisted in localStorage.
  function syncThemeButton(theme) {
    document.querySelectorAll("[data-action='toggle-theme']").forEach(function (btn) {
      btn.setAttribute("aria-pressed", theme === "light" ? "true" : "false");
      btn.setAttribute(
        "aria-label",
        theme === "light" ? "Switch to dark theme" : "Switch to light theme"
      );
    });
  }
  // Hydrate the toggle's aria state to match whatever the inline pre-paint script chose.
  window.addEventListener("DOMContentLoaded", function () {
    syncThemeButton(document.documentElement.dataset.theme || "dark");
  });
  document.addEventListener("click", function (e) {
    const btn = e.target.closest("[data-action='toggle-theme']");
    if (!btn) return;
    e.preventDefault();
    const cur = document.documentElement.dataset.theme || "dark";
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("ffc-theme", next); } catch (e) { /* no-op */ }
    syncThemeButton(next);
  });
})();
