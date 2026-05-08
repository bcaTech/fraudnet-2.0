"""Minimal dependency-free PDF generator for regulator packs.

Uses a small subset of PDF primitives — text-only, single-column,
page-break-aware. We deliberately do not depend on ReportLab here:
the regulator outputs are simple structured documents, the runtime
should not pull a 30 MB PDF library, and the format we emit is
easy to swap to ReportLab later without changing the call sites.

The output is a valid PDF parseable by pdfminer, Adobe Reader, etc.
Page size is A4 (595 × 842 pt). One built-in font (Helvetica) is
referenced; the fallback engine in any modern PDF reader handles
substitution.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Iterable

from compliance.regulators.base import Field, RegulatorReport


_PAGE_WIDTH = 595
_PAGE_HEIGHT = 842
_LEFT_MARGIN = 50
_RIGHT_MARGIN = 50
_TOP_MARGIN = 50
_BOTTOM_MARGIN = 50
_LINE_HEIGHT = 14
_TITLE_LINE_HEIGHT = 22
_SECTION_LINE_HEIGHT = 18


@dataclass
class _Line:
    text: str
    font_size: int = 10
    bold: bool = False


def render_report_pdf(report: RegulatorReport) -> bytes:
    """Return a PDF (bytes) representation of the report."""
    lines: list[_Line] = [
        _Line(
            text=f"{report.regulator.upper()} — {report.template_id}",
            font_size=16,
            bold=True,
        ),
        _Line(
            text=f"Period: {report.period_start} to {report.period_end}",
            font_size=10,
        ),
        _Line(text=""),
    ]
    if report.review_field_count:
        lines.append(
            _Line(
                text=(
                    f"NOTE: {report.review_field_count} field(s) need human "
                    "review before submission."
                ),
                font_size=10,
                bold=True,
            )
        )
        lines.append(_Line(text=""))

    for section in report.sections:
        lines.append(_Line(text=section.title, font_size=12, bold=True))
        for f in section.fields:
            lines.extend(_render_field(f))
        lines.append(_Line(text=""))

    return _emit_pdf(lines)


def _render_field(f: Field) -> Iterable[_Line]:
    label_line = f"  {f.label}"
    if f.needs_review:
        label_line += "   [REVIEW REQUIRED]"
    yield _Line(text=label_line, font_size=10, bold=True)
    if f.value is None and f.note:
        yield _Line(text=f"      Note: {f.note}", font_size=9)
    elif isinstance(f.value, dict):
        for k, v in f.value.items():
            yield _Line(text=f"      {k}: {v}", font_size=9)
    elif isinstance(f.value, list):
        for v in f.value:
            yield _Line(text=f"      - {v}", font_size=9)
    else:
        yield _Line(text=f"      {f.value if f.value is not None else '(blank)'}",
                    font_size=9)


# ---------------------------------------------------------------------------
# Lightweight PDF emitter
# ---------------------------------------------------------------------------


def _emit_pdf(lines: list[_Line]) -> bytes:
    pages: list[list[_Line]] = [[]]
    cursor_y = _PAGE_HEIGHT - _TOP_MARGIN
    for line in lines:
        height = _line_height(line)
        if cursor_y - height < _BOTTOM_MARGIN:
            pages.append([])
            cursor_y = _PAGE_HEIGHT - _TOP_MARGIN
        pages[-1].append(line)
        cursor_y -= height

    objects: list[bytes] = []
    # 1 = Catalog, 2 = Pages, 3..N = Page + Contents pairs, last = Font
    page_obj_ids: list[int] = []
    content_obj_ids: list[int] = []

    next_id = 3
    for page_lines in pages:
        page_obj_ids.append(next_id)
        next_id += 1
        content_obj_ids.append(next_id)
        next_id += 1
    font_id = next_id
    bold_font_id = next_id + 1

    objects.append(_obj(1, b"<< /Type /Catalog /Pages 2 0 R >>"))
    kids = b" ".join(f"{oid} 0 R".encode() for oid in page_obj_ids)
    objects.append(
        _obj(
            2,
            b"<< /Type /Pages /Count "
            + str(len(pages)).encode()
            + b" /Kids ["
            + kids
            + b"] >>",
        )
    )

    for i, page_lines in enumerate(pages):
        page_id = page_obj_ids[i]
        content_id = content_obj_ids[i]
        objects.append(
            _obj(
                page_id,
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 "
                + f"{_PAGE_WIDTH} {_PAGE_HEIGHT}".encode()
                + b"] /Contents "
                + f"{content_id} 0 R".encode()
                + b" /Resources << /Font << /F1 "
                + f"{font_id} 0 R".encode()
                + b" /F2 "
                + f"{bold_font_id} 0 R".encode()
                + b" >> >> >>",
            )
        )
        stream = _content_stream(page_lines)
        objects.append(
            _obj(
                content_id,
                b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
                + stream
                + b"\nendstream",
            )
        )

    objects.append(
        _obj(
            font_id,
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        )
    )
    objects.append(
        _obj(
            bold_font_id,
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
        )
    )

    return _assemble(objects, len(objects))


def _line_height(line: _Line) -> float:
    if line.font_size >= 16:
        return _TITLE_LINE_HEIGHT
    if line.font_size >= 12:
        return _SECTION_LINE_HEIGHT
    return _LINE_HEIGHT


def _content_stream(lines: list[_Line]) -> bytes:
    buf = io.BytesIO()
    cursor_y = _PAGE_HEIGHT - _TOP_MARGIN
    for line in lines:
        height = _line_height(line)
        font = b"F2" if line.bold else b"F1"
        size = line.font_size
        text = _escape_pdf_text(line.text)
        buf.write(
            b"BT /"
            + font
            + b" "
            + str(size).encode()
            + b" Tf "
            + str(_LEFT_MARGIN).encode()
            + b" "
            + str(int(cursor_y)).encode()
            + b" Td ("
            + text
            + b") Tj ET\n"
        )
        cursor_y -= height
    return buf.getvalue()


def _escape_pdf_text(text: str) -> bytes:
    """Encode text for a PDF literal string. Escapes ( ) \\ and trims to
    ASCII; non-ASCII chars are dropped (regulator templates are ASCII)."""
    safe = text.encode("ascii", "replace").decode("ascii")
    safe = safe.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    # Truncate very long lines so the content stream stays manageable.
    return safe[:600].encode("latin-1")


def _obj(obj_id: int, body: bytes) -> bytes:
    return f"{obj_id} 0 obj\n".encode() + body + b"\nendobj\n"


def _assemble(objects: list[bytes], total_objects: int) -> bytes:
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets: list[int] = [0] * (total_objects + 1)
    for obj in objects:
        # Each obj's leading "<id> 0 obj" identifies its id.
        header = obj.split(b" ", 1)[0]
        idx = int(header)
        offsets[idx] = out.tell()
        out.write(obj)
    xref_start = out.tell()
    out.write(f"xref\n0 {total_objects + 1}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for i in range(1, total_objects + 1):
        out.write(f"{offsets[i]:010d} 00000 n \n".encode())
    out.write(
        b"trailer\n<< /Size "
        + str(total_objects + 1).encode()
        + b" /Root 1 0 R >>\nstartxref\n"
        + str(xref_start).encode()
        + b"\n%%EOF\n"
    )
    return out.getvalue()
