# FirefliesClearer

Safely archive and clean up [Fireflies AI](https://fireflies.ai) meetings.

For each meeting matched by configurable rules, FirefliesClearer:

1. Lists candidates
2. Downloads artifacts to local disk: `summary.pdf` (rendered locally), `audio.mp3`, `transcript.md`, `metadata.json`
3. Verifies the archive on disk
4. Only then deletes the meeting from Fireflies

Every step is recorded in a local SQLite manifest for audit and safe re-runs.

## Status
Pre-implementation. See [design spec](docs/superpowers/specs/2026-04-28-firefliesclearer-design.md).

## License
TBD
