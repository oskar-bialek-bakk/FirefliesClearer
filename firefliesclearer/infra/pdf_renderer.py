"""reportlab-based PDF renderer for meeting summaries.

reportlab is pure-Python with no native runtime dependencies, which makes
distribution to colleagues frictionless. WeasyPrint was the original choice
but its GTK runtime is not available on stock Windows machines.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Flowable,
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)

from firefliesclearer.core.models import Meeting


class _FontCache:
    """Module-level singleton cache for the registered Unicode font name."""

    name: str | None = None


def _register_unicode_font() -> str:
    """Register a Unicode TTF; return the font name (cached)."""
    if _FontCache.name is not None:
        return _FontCache.name
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    candidates = [
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                pdfmetrics.registerFont(TTFont("UnicodeFont", path))
                _FontCache.name = "UnicodeFont"
                return _FontCache.name
            except Exception:
                continue
    _FontCache.name = "Helvetica"
    return _FontCache.name


_ACCENT = HexColor("#1d4ed8")
_MUTED = HexColor("#555555")
_RULE = HexColor("#cccccc")


def _styles() -> dict[str, ParagraphStyle]:
    font = _register_unicode_font()
    base = getSampleStyleSheet()
    title = ParagraphStyle(
        "Title",
        parent=base["Title"],
        fontName=font,
        fontSize=22,
        leading=26,
        spaceAfter=4,
        textColor=HexColor("#111111"),
    )
    meta = ParagraphStyle(
        "Meta",
        parent=base["BodyText"],
        fontName=font,
        fontSize=10,
        leading=13,
        textColor=_MUTED,
        spaceAfter=4,
    )
    h2 = ParagraphStyle(
        "H2",
        parent=base["Heading2"],
        fontName=font,
        fontSize=13,
        leading=17,
        spaceBefore=14,
        spaceAfter=6,
        textColor=_ACCENT,
    )
    body = ParagraphStyle(
        "Body",
        parent=base["BodyText"],
        fontName=font,
        fontSize=11,
        leading=15,
    )
    italic = ParagraphStyle(
        "Italic",
        parent=body,
        fontName=font,
        textColor=_MUTED,
    )
    footer = ParagraphStyle(
        "Footer",
        parent=base["BodyText"],
        fontName=font,
        fontSize=8,
        leading=10,
        textColor=_MUTED,
    )
    return {
        "title": title,
        "meta": meta,
        "h2": h2,
        "body": body,
        "italic": italic,
        "footer": footer,
    }


class ReportlabSummaryRenderer:
    """Renders summary payloads to PDF via reportlab."""

    def render(
        self,
        summary_payload: dict[str, Any],
        *,
        meeting_title: str,
        meeting: Meeting | None = None,
        source_url: str | None = None,
    ) -> bytes:
        buf = io.BytesIO()
        doc = SimpleDocTemplate(
            buf,
            pagesize=A4,
            leftMargin=20 * mm,
            rightMargin=20 * mm,
            topMargin=22 * mm,
            bottomMargin=22 * mm,
            title=meeting_title,
        )
        styles = _styles()

        story: list[Any] = []
        story.append(Paragraph(_escape(meeting_title), styles["title"]))

        # Metadata band — date · duration · host · participants. Gives the
        # reader essential context without scrolling, addressing the
        # "no formatting, very little sense" feedback (2026-05-03). Each
        # field renders only when populated so the line stays clean for
        # legacy rows that lack snapshot fields.
        meta_parts = _meta_parts(meeting)
        if meta_parts:
            story.append(Paragraph(" · ".join(meta_parts), styles["meta"]))
        if source_url:
            # Attribute and text contexts have different escape rules — use
            # ``_escape_attr`` for the href value (covers quotes too) and
            # ``_escape`` for the visible link text. Mixing them up would
            # break reportlab's XML parser if a URL ever contained ``"`` or
            # ``'`` (Copilot review on PR #19).
            story.append(
                Paragraph(
                    f'<link href="{_escape_attr(source_url)}">{_escape(source_url)}</link>',
                    styles["meta"],
                )
            )
        story.append(Spacer(1, 8))
        story.append(_HRule())
        story.append(Spacer(1, 6))

        story.append(Paragraph("Overview", styles["h2"]))
        overview = summary_payload.get("overview")
        story.append(
            Paragraph(
                _escape(overview) if overview else "(Fireflies returned no overview text.)",
                styles["body"],
            )
        )

        action_items = summary_payload.get("action_items") or []
        if action_items:
            story.append(Spacer(1, 6))
            story.append(Paragraph("Action items", styles["h2"]))
            story.append(
                ListFlowable(
                    [ListItem(Paragraph(_escape(item), styles["body"])) for item in action_items],
                    bulletType="bullet",
                    leftIndent=14,
                )
            )

        keywords = summary_payload.get("keywords") or []
        if keywords:
            story.append(Spacer(1, 6))
            story.append(Paragraph("Keywords", styles["h2"]))
            story.append(Paragraph(_escape(", ".join(keywords)), styles["italic"]))

        # Footer disclaimer — sets expectations: this PDF mirrors what
        # Fireflies generated, no post-processing on our side. Saves the
        # user from blaming us for a thin summary.
        story.append(Spacer(1, 18))
        story.append(_HRule())
        story.append(Spacer(1, 4))
        story.append(
            Paragraph(
                "Generated by FirefliesClearer from the Fireflies API summary. "
                "See transcript.md alongside this file for the full transcript.",
                styles["footer"],
            )
        )

        doc.build(story, onFirstPage=_draw_page_number, onLaterPages=_draw_page_number)
        return buf.getvalue()


def _escape(text: str) -> str:
    """Escape XML special chars used by reportlab Paragraph markup."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_attr(text: str) -> str:
    """Escape a string for use inside a double-quoted XML attribute.

    Same rules as ``_escape`` plus ``"`` and ``'`` so a value containing
    quotes can't break out of the attribute and corrupt reportlab's XML
    parsing. Required for the ``<link href="...">`` build in
    :class:`ReportlabSummaryRenderer.render`.
    """
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _meta_parts(meeting: Meeting | None) -> list[str]:
    """Build the metadata band fragments. Only populated fields are emitted
    so this works for legacy meetings whose snapshot fields are still empty
    (pre-Phase-1 sync rows)."""
    if meeting is None:
        return []
    parts: list[str] = []
    # Date in ISO with no time component — easier to scan; the time of day
    # rarely matters for archival reading.
    if meeting.meeting_date is not None:
        parts.append(meeting.meeting_date.strftime("%Y-%m-%d"))
    duration = getattr(meeting, "duration_minutes", None)
    if duration:
        mins = round(float(duration))
        parts.append(f"{mins} min")
    host = getattr(meeting, "host_email", "") or ""
    if host:
        parts.append(_escape(host))
    pcount = getattr(meeting, "participant_count", None)
    if pcount:
        suffix = "participant" if pcount == 1 else "participants"
        parts.append(f"{pcount} {suffix}")
    return parts


class _HRule(Flowable):  # type: ignore[misc]  # reportlab is untyped
    """Thin horizontal rule flowable for inline use in the story.

    Inherits from ``Flowable`` so reportlab's keepWithNext bookkeeping has
    the methods it expects (the bare-class version raised
    ``AttributeError: '_HRule' object has no attribute 'getKeepWithNext'``).
    """

    def __init__(self) -> None:
        super().__init__()
        self._width: float = 0.0

    def wrap(self, available_width: float, _available_height: float) -> tuple[float, float]:
        self._width = available_width
        return available_width, 1

    def draw(self) -> None:  # pragma: no cover — drawing exercised by build()
        self.canv.setStrokeColor(_RULE)
        self.canv.setLineWidth(0.5)
        self.canv.line(0, 0, self._width, 0)


def _draw_page_number(canvas: Any, doc: Any) -> None:
    """Draw a small page number at the bottom-center of every page."""
    canvas.saveState()
    canvas.setFont(_FontCache.name or "Helvetica", 8)
    canvas.setFillColor(_MUTED)
    page_text = f"{doc.page}"
    canvas.drawCentredString(doc.pagesize[0] / 2.0, 12 * mm, page_text)
    canvas.restoreState()
