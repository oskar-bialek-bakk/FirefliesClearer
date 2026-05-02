# Resume prompt — local-cache rollout

Open a fresh Claude Code session at `C:\GIT\FirefliesClearer` and paste the prompt below verbatim. It contains everything needed to continue without revisiting prior context.

---

```
Resume the local-cache rollout for FirefliesClearer at C:\GIT\FirefliesClearer.

CONTEXT:
- Six-phase project. Spec at docs/superpowers/specs/2026-05-02-local-cache-design.md.
- Six implementation plans at docs/superpowers/plans/2026-05-02-local-cache-phase-{1..6}-*.md.
- Phase 1 (10 tasks) and Phase 2 (10 tasks) are fully implemented and committed locally.
- Phase 3 is partial: Tasks 1-2 of 6 are committed (0fa298b, f273a6c). Tasks 3-6 not started.
- Phases 4, 5, 6: plans written, implementations pending.
- 33 commits ahead of origin/main, NOT pushed.
- 694 tests pass, mypy + ruff clean.

YOUR JOB:
Continue from Phase 3 Task 3 through Phase 6 Task 6, autonomously. Do not ask between phases.
Dispatch one background agent per phase (or per remaining-tasks-of-phase-3 first), wait for
its completion notification, dispatch the next phase. Repeat until Phase 6 finishes.

PHASE 3 RESUMPTION:
Read docs/superpowers/plans/2026-05-02-local-cache-phase-3-read-flip.md.
Skip Tasks 1 and 2 (already committed). Start at Task 3 (CLI scan/run cmds → scan_repo).
Execute Tasks 3, 4, 5, 6 (TDD per task: failing test → red → impl → green → commit).

PHASES 4-6:
After Phase 3 finishes, dispatch Phase 4 (8 tasks), then Phase 5 (7 tasks), then Phase 6
(6 tasks). Each as its own background agent reading the corresponding plan file.

KNOWN GOTCHAS (from Phase 1 + Phase 2 deviations):
- Plan code samples may have non-canonical formatting; run ruff format inline before commit
  to avoid an extra format-chore commit.
- Do NOT use `# noqa: BLE001` or `# noqa: SLF001` (ruff doesn't select those rules; the
  noqa trips RUF100). Use block comments instead.
- For mypy strictness with `repo: object` typing, narrow with `cast(MeetingRepository, ...)`
  or add explicit type annotations on local variables.
- `asyncio.create_task(...)` results need to be parked on `app.state.<name>` to avoid
  garbage collection AND RUF006.
- The plan's `Manifest.open` migration ordering had a bug — the Phase 1 agent already
  fixed it; just be aware migration runs BEFORE `executescript` now.

VERIFICATION GATES:
After each task's commit:
  .venv/Scripts/pytest.exe --no-cov -q                           (must be green)
  .venv/Scripts/mypy.exe firefliesclearer                         (must be clean)
  .venv/Scripts/ruff.exe check firefliesclearer tests             (must be clean)
  .venv/Scripts/ruff.exe format --check firefliesclearer tests    (must pass)

NO PUSH. Commit everything locally; the user will push manually when ready.

START BY:
1. Reading .resume-firefliesclearer.md for full state.
2. Verifying clean working tree: git status (should be clean).
3. Verifying tests pass: .venv/Scripts/pytest.exe --no-cov -q
4. Dispatching the Phase 3 Tasks 3-6 background agent with the plan file as input.
```

---

## When the rollout finishes

After Phase 6 completes, the assistant should:
1. Run final verification (full pytest + mypy + ruff + coverage report).
2. Update `.resume-firefliesclearer.md` to mark all six phases done.
3. Report back to you with: total commits, total LOC added, test counts, coverage.
4. Wait for your decision on whether to push.

## Reference: phase-by-phase task counts

| Phase | Total tasks | Done before pause | Remaining |
|------:|------------:|------------------:|----------:|
| 1 | 10 | 10 | 0 |
| 2 | 11 | 10 (Task 11 = verify-only) | 0 |
| 3 | 6 | 2 | 4 |
| 4 | 8 | 0 | 8 |
| 5 | 7 | 0 | 7 |
| 6 | 6 | 0 | 6 |

Total remaining work: ~31 tasks across Phases 3-6.
