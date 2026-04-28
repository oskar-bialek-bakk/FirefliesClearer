"""reportlab-based PDF renderer for meeting summaries.

reportlab is pure-Python with no native runtime dependencies, which makes
distribution to colleagues frictionless. WeasyPrint was the original choice
but its GTK runtime is not available on stock Windows machines.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)


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


def _styles() -> dict[str, ParagraphStyle]:
    font = _register_unicode_font()
    base = getSampleStyleSheet()
    title = ParagraphStyle(
        "Title",
        parent=base["Title"],
        fontName=font,
        fontSize=20,
        leading=24,
        spaceAfter=14,
    )
    h2 = ParagraphStyle(
        "H2",
        parent=base["Heading2"],
        fontName=font,
        fontSize=13,
        leading=16,
        spaceBefore=12,
        spaceAfter=6,
    )
    body = ParagraphStyle(
        "Body",
        parent=base["BodyText"],
        fontName=font,
        fontSize=11,
        leading=14,
    )
    italic = ParagraphStyle(
        "Italic",
        parent=body,
        fontName=font,
        textColor="#555555",
    )
    return {"title": title, "h2": h2, "body": body, "italic": italic}


class ReportlabSummaryRenderer:
    """Renders summary payloads to PDF via reportlab."""

    def render(self, summary_payload: dict[str, Any], *, meeting_title: str) -> bytes:
        buf = io.BytesIO()
        doc = SimpleDocTemplate(
            buf,
            pagesize=A4,
            leftMargin=18 * mm,
            rightMargin=18 * mm,
            topMargin=22 * mm,
            bottomMargin=22 * mm,
            title=meeting_title,
        )
        styles = _styles()

        story: list[Any] = []
        story.append(Paragraph(_escape(meeting_title), styles["title"]))

        story.append(Paragraph("Overview", styles["h2"]))
        overview = summary_payload.get("overview")
        story.append(Paragraph(_escape(overview) if overview else "(no overview)", styles["body"]))

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

        doc.build(story)
        return buf.getvalue()


def _escape(text: str) -> str:
    """Escape XML special chars used by reportlab Paragraph markup."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
