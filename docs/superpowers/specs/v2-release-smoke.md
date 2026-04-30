# v2 release smoke checklist

Manual checks that automated tests can't cover. Tick every item before merging `version/v2` → `main`.

## Setup & first-run

- [ ] `pip install -e .[dev]` on a clean Python 3.12 venv installs without error.
- [ ] `firefliesclearer serve` on a clean machine (no existing config) → browser opens to `http://127.0.0.1:<port>/?token=<...>` automatically.
- [ ] Setup wizard step 1 (welcome) → step 2 (API key paste + Test) → step 3 (archive root) → step 4 (defaults) → lands on Dashboard.
- [ ] Real Fireflies API key validates successfully on step 2.

## Cleanup wizard end-to-end

- [ ] Step 1: each filter input updates the live count within 500ms.
- [ ] Step 2: select-all, deselect-all, invert-selection, individual toggles all work; row click opens side panel without losing selection.
- [ ] Step 3 (Archive): progress bar advances; on done, `<archive_root>/<year>/<month>/<meeting_id>/{summary.pdf,audio.mp3,transcript.md,metadata.json}` exists for each successful meeting.
- [ ] Step 4 (Purge): typed-count gate enforces threshold; in-progress card streams via SSE; deleted meetings disappear from fireflies.ai.

## Dashboard & retry

- [ ] State-count cards reflect the previous run.
- [ ] Last-activity rows show the latest archived/deleted meetings.
- [ ] Needs-attention list shows any failed meetings.
- [ ] Retry button on a failed meeting completes the operation via the same SSE flow.

## History & side panel

- [ ] `/history` lists archived + deleted meetings, paginated 50/page.
- [ ] Filters: date range presets, custom range, state multiselect, title search — all narrow results correctly.
- [ ] URL query string round-trips (paste URL into a new tab → same filtered view).
- [ ] Clicking a row title opens the side panel with the full state-log timeline.

## Presets

- [ ] `/presets` lists existing presets; ★ marks default.
- [ ] Creating a new preset from the page persists across `serve` restarts.
- [ ] "Save as preset" inline form on cleanup wizard Step 1 saves the current filters.
- [ ] `firefliesclearer run --preset NAME` (CLI) finds matches against the named preset.
- [ ] `firefliesclearer run` (no flag) uses the default preset; if no default, errors with a clear message.
- [ ] **v1 migration:** if upgrading from v1, the first `serve` startup creates an "Auto cleanup" default preset from `[rules.auto]` and writes `<config>.v1.bak`.

## Settings

- [ ] Connection: API key is masked; "Test connection" returns the user's email.
- [ ] Archive: changing the root warns about unmoved content.
- [ ] Defaults: concurrency / age / threshold round-trip.
- [ ] Logs: "View today's log" renders the JSON-lines log; "Open archive folder" opens the OS file explorer.
- [ ] Danger zone: typing "RESET" deletes `config.toml` and redirects to setup wizard; manifest + archive untouched.

## Lifecycle & security

- [ ] Closing the browser tab → server exits within ~60 seconds.
- [ ] Re-opening `serve` recovers and shows the dashboard.
- [ ] Two `serve` processes against the same archive root: second exits cleanly with a "another instance is running" message naming the first URL.
- [ ] Sidebar Quit button shuts the server down within 5 seconds when no op is running.
- [ ] `firefliesclearer serve --host 0.0.0.0` without `--i-know-what-im-doing` refuses to bind.
- [ ] No API key appears in any log file or HTTP response body (grep `<archive_root>/logs/*.log`).

## Distribution

- [ ] `python -m build` produces a wheel < 5 MB.
- [ ] `pip install <wheel>` on a fresh venv exposes `firefliesclearer serve`.
