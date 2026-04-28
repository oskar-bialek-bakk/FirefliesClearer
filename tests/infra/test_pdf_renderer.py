"""Tests for the reportlab-based PDF renderer."""

from __future__ import annotations

from firefliesclearer.infra.pdf_renderer import ReportlabSummaryRenderer


def _render(payload: dict, *, title: str = "Test") -> bytes:
    return ReportlabSummaryRenderer().render(payload, meeting_title=title)


def test_output_starts_with_pdf_magic() -> None:
    payload = {
        "overview": "We discussed Q2 plans.",
        "action_items": ["Ship feature", "Update docs"],
        "keywords": ["q2", "roadmap"],
    }
    out = _render(payload)
    assert out.startswith(b"%PDF")


def test_polish_characters_render_without_error() -> None:
    payload = {
        "overview": "Zażółć gęślą jaźń. Spotkanie projektowe.",
        "action_items": ["Wysłać podsumowanie"],
        "keywords": ["projekt"],
    }
    out = _render(payload, title="Spotkanie ABC")
    assert out.startswith(b"%PDF")
    assert len(out) > 500


def test_empty_payload_produces_placeholder_pdf() -> None:
    out = _render({})
    assert out.startswith(b"%PDF")


def test_missing_action_items_does_not_crash() -> None:
    out = _render({"overview": "Just an overview"})
    assert out.startswith(b"%PDF")


def test_renderer_implements_summary_renderer_port() -> None:
    """Sanity: returned object is a SummaryRenderer (duck typing)."""
    r = ReportlabSummaryRenderer()
    assert hasattr(r, "render")
    out = r.render({}, meeting_title="X")
    assert isinstance(out, bytes)
