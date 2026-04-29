"""Application services — shared orchestration consumed by both CLI and web layers.

These services depend only on `core/` and `ports/` (never on Typer or FastAPI),
so they are reusable across presentation layers and trivially testable with the
existing fakes in `tests/fakes/`.
"""
